"""Persistent local JSON-lines bridge for the native Mac presentation.

The hidden Tk owner retains startup, guarded workflows, crash observation and
safe shutdown. No socket or network service is exposed. Presentation snapshots
are session caches, never validation authority for a game operation.
"""

from collections import OrderedDict
from dataclasses import asdict, fields
import base64
import ctypes
import json
from pathlib import Path
from queue import Empty, Full, Queue
import sys
from threading import Event, Lock, Thread
import webbrowser

from .activation import ORIGINAL
from .annotations import AnnotationStore
from .gallery import MAX_SNAPSHOT_ART_BYTES, gallery_snapshot
from .collection import fetch_catalogue, read_cached_catalogue, write_cached_catalogue
from .gui import LauncherWindow, _checks_text
from .application import LauncherApplication
from .history_ui import confirm_checkpoint, save_digest, timeline_text
from .language import (
    LANGUAGES,
    TRANSLATIONS,
    set_language,
    open_translation_settings,
    translate_briefing,
)
from .voices import list_voices, open_voice_settings
from .play_history import (
    CHECKPOINTS,
    FAILURES,
    IDENTITIES,
    LABELS,
    SUCCESS,
    HistoryError,
    PlayHistoryStore,
    context,
    digest,
    same_map,
    same_scope,
    summarize,
)

MAX_REQUEST_BYTES = 1500 * 1024
# Keep output below the native reader limit even with unusually large source text.
MAX_RESPONSE_BYTES = 96 * 1024 * 1024


def set_activation_policy(advanced=False, *, allow_uninitialized=False):
    """Keep the hidden Mac service out of the Dock; expose advanced Tk menus.

    Read NSApp rather than creating NSApplication before Tk: Tk must create its
    own application subclass. All callers run on the Tk owner/main thread.
    """
    if sys.platform != "darwin":
        return
    policy = 0 if advanced else 1  # NSApplicationActivationPolicyRegular / Accessory.
    try:
        appkit = ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        application = ctypes.c_void_p.in_dll(appkit, "NSApp").value
        if not application:
            if allow_uninitialized:
                return
            raise RuntimeError("Tk did not initialize the macOS application")
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        # objc_msgSend needs a distinct ABI-correct signature for each selector.
        change = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(
            ("objc_msgSend", objc)
        )
        current = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", objc)
        )
        accepted = change(application, objc.sel_registerName(b"setActivationPolicy:"), policy)
        actual = current(application, objc.sel_registerName(b"activationPolicy"))
        if not accepted or actual != policy:
            raise RuntimeError(
                f"macOS refused launcher activation policy {policy} "
                f"(accepted={bool(accepted)}, actual={actual})"
            )
    except (OSError, AttributeError, ValueError) as exc:
        raise RuntimeError(
            "Cannot set the launcher's macOS activation policy: " + str(exc)
        ) from exc


def text(params, key, default=""):
    value = params.get(key, default)
    if not isinstance(value, str):
        raise ValueError(key + " must be text")
    return value


class NativeLibrary:
    """Worker-only presentation and explicit local history/annotation operations."""

    def __init__(self):
        self.cached_app = None
        self.cached_history_error = ""
        self.cached_items = None
        self.remote_records = ()
        self.collection_notice = ""

    def collection(self, library, refresh=False):
        path = Path(library) / "collection-catalogue.json"
        if refresh or not self.remote_records:
            saved = None if refresh else read_cached_catalogue(path)
            if saved is not None:
                self.remote_records = saved.records
                self.collection_notice = (
                    "Saved catalogue · " + saved.fetched_at[:10] + "; Refresh checks online"
                )
            else:
                self.remote_records = fetch_catalogue()
                self.collection_notice = "Internet Archive catalogue"
                try:
                    write_cached_catalogue(path, self.remote_records)
                except (OSError, ValueError) as exc:
                    self.collection_notice += "; saved catalogue needs inspection: " + str(exc)
        return dict(
            records=[
                dict(
                    name=r.name,
                    size=r.size,
                    sha1=r.sha1,
                    archive_modified=r.archive_modified,
                    source_url=r.source_url,
                )
                for r in self.remote_records
            ],
            notice=self.collection_notice,
        )

    def invalidate(self):
        self.cached_items = None

    @staticmethod
    def record(app, profile_id):
        record = next((r for r in app.catalogue() if r.profile_id == profile_id), None)
        if record is None:
            raise ValueError("This map is no longer in the library; refresh before continuing")
        return record

    def snapshot(self, app, refresh=False, *, history_error=""):
        if (
            self.cached_app is not app
            or refresh
            or self.cached_items is None
            or self.cached_history_error != history_error
        ):
            self.cached_items = gallery_snapshot(app, Event(), history_error=history_error)
            self.cached_app = app
            self.cached_history_error = history_error
        store, maps = AnnotationStore(app.library), []
        remaining_art = MAX_SNAPSHOT_ART_BYTES
        for item in self.cached_items:
            record = item.record
            summary = item.history or summarize(
                (), context(record, app.installation.executable_sha256)
            )
            annotation_error = ""
            try:
                annotation = store.load(record)
            except (OSError, ValueError, UnicodeError) as exc:
                annotation = dict(title="", notes="", image_png="")
                annotation_error = "Personal display details need inspection: " + str(exc)
            notices = [item.notice, annotation_error]
            image = annotation["image_png"]
            image_padding = len(image) - len(image.rstrip("="))
            image_size = len(image) // 4 * 3 - image_padding
            if image_size <= remaining_art:
                remaining_art -= image_size
            else:
                annotation["image_png"] = ""
                notices.append(
                    "Personal image omitted from this snapshot to keep the library responsive; its stored original is unchanged."
                )
            art = item.art
            if len(art) <= remaining_art:
                remaining_art -= len(art)
            else:
                art = b""
                notices.append(
                    "Package image omitted from this snapshot to keep the library responsive; its original is unchanged."
                )
            maps.append(
                dict(
                    id=record.profile_id,
                    name=record.name,
                    archive_sha256=record.archive_sha256,
                    variant_id=record.variant_id,
                    metadata=asdict(item.metadata),
                    verification=item.verification,
                    notice="\n".join(s for s in notices if s),
                    art=base64.b64encode(art).decode("ascii"),
                    summary={k: v for k, v in asdict(summary).items() if k != "events"},
                    history_error=item.history_error,
                    annotation=annotation,
                )
            )
        return maps

    def details(self, app, params):
        record = self.record(app, text(params, "profile_id"))
        ctx = context(record, app.installation.executable_sha256)
        rows = tuple(e for e in PlayHistoryStore(app.library).records() if same_map(e, ctx))
        summary = summarize(rows, ctx)
        try:
            verification = app.verification_for(record)
            verification_text = (
                LauncherWindow._verification_details(verification)
                if verification
                else "Not verified"
            )
        except Exception as exc:
            verification_text = "Gameplay record needs inspection: " + str(exc)
        try:
            checks = _checks_text(app.import_checks(record))
        except Exception as exc:
            checks = "Import checks need inspection: " + str(exc)
        return dict(
            profile_id=record.profile_id,
            scenarios=list(record.scenarios),
            languages=[dict(id=k, name=v) for k, v in LANGUAGES.items()],
            context=ctx,
            history=[{**e, "label": LABELS[e["kind"]]} for e in rows],
            summary={
                field.name: getattr(summary, field.name)
                for field in fields(summary)
                if field.name != "events"
            },
            history_text=timeline_text(rows),
            verification_text=verification_text,
            checks_text=checks,
        )

    @staticmethod
    def require_scope(app, record, params, *, build=False):
        expected = context(record, app.installation.executable_sha256)
        keys = IDENTITIES if build else ("archive_sha256", "variant_id")
        if any(text(params, key) != expected[key] for key in keys):
            raise ValueError(
                "The selected map edition or game build changed; refresh details before continuing"
            )

    def annotations_save(self, app, params):
        record = self.record(app, text(params, "profile_id"))
        self.require_scope(app, record, params)
        image = params.get("image_png")
        if params.get("reset_image") is True:
            image = ""
        if image is not None and not isinstance(image, str):
            raise ValueError("image_png must be base64 text")
        AnnotationStore(app.library).save(
            record, title=text(params, "title"), notes=text(params, "notes"), image_png=image
        )
        return dict(saved=True)

    def briefing_translate(self, app, params):
        record = self.record(app, text(params, "profile_id"))
        self.require_scope(app, record, params)
        source, target = text(params, "source"), text(params, "target")
        if source not in LANGUAGES or target not in LANGUAGES:
            raise ValueError("Choose a supported translation language")
        briefing = app.map_metadata(record).description
        translated = translate_briefing(briefing, target, source)
        return dict(
            profile_id=record.profile_id,
            archive_sha256=record.archive_sha256,
            variant_id=record.variant_id,
            source=source,
            target=target,
            text=translated,
        )

    def history_record(self, app, params):
        record = self.record(app, text(params, "profile_id"))
        self.require_scope(app, record, params, build=True)
        scenario, kind = text(params, "scenario"), text(params, "kind")
        if scenario and scenario not in record.scenarios:
            raise HistoryError("Choose a scenario from this exact map edition")
        if kind not in ("game_started", "game_resumed", *CHECKPOINTS):
            raise HistoryError("Choose an explicit player-confirmed checkpoint")
        if kind not in ("quit", "cancelled", "crash_unknown", "resource_stop") and not scenario:
            raise HistoryError("Choose the scenario for this checkpoint")
        continued = params.get("continued", False)
        if type(continued) is not bool or (continued and kind != "save_reopened"):
            raise HistoryError(
                "Continued play can only accompany an exact save reopened confirmation"
            )
        checkpoint = text(params, "checkpoint")
        if checkpoint and (kind not in FAILURES or checkpoint not in SUCCESS):
            raise HistoryError("Choose a valid failed checkpoint")
        ctx = context(record, app.installation.executable_sha256, scenario)
        store, attempt = PlayHistoryStore(app.library), text(params, "attempt_id")
        rows = tuple(e for e in store.records() if same_map(e, ctx))
        if not any(
            e["attempt_id"] == attempt and e["kind"] in ("process_confirmed", "launch_attempted")
            for e in rows
        ):
            raise HistoryError("Choose a recorded open from this exact edition/build")
        failure_id = text(params, "failure_id")
        failure = (
            next(
                (
                    e
                    for e in reversed(rows)
                    if e["kind"] in FAILURES and e["incident_id"] == failure_id
                ),
                None,
            )
            if failure_id
            else None
        )
        if failure_id and (
            failure is None
            or failure["attempt_id"] != attempt
            or (failure["scenario"] and failure["scenario"] != scenario)
        ):
            raise HistoryError("Choose a failure from the selected open and scenario")
        if failure and kind not in FAILURES:
            raise HistoryError("Failure attribution requires a failure checkpoint")
        game = text(params, "game_id")
        if not game:
            bound_games = {
                e["game_id"]
                for e in rows
                if e["kind"] in ("game_started", "game_resumed")
                and same_scope(e, ctx)
                and e["attempt_id"] == attempt
            }
            if len(bound_games) > 1:
                raise HistoryError("Several games are bound to this open; choose the exact game")
            game = next(iter(bound_games), "")
        if not game and kind in ("retired", "goals_met"):
            raise HistoryError(
                "Choose a known game or explicitly identify a new game before recording completion"
            )
        added = confirm_checkpoint(
            store,
            ctx,
            kind,
            attempt,
            game=game,
            save_id=text(params, "save_id"),
            failure=failure,
            checkpoint=checkpoint,
            continued=continued,
        )
        self.invalidate()
        return dict(recorded=len(added))


class NativeBackendWindow(LauncherWindow):
    def __init__(self, input_stream=None, output_stream=None):
        self.native_library = NativeLibrary()
        self._native_requests = Queue(maxsize=64)
        self._native_input_ended = Event()
        self._native_parent_approved_close = False
        self._native_shutdown_started = False
        self._native_disconnected = False
        self._native_completed = OrderedDict()
        self._native_output = output_stream or sys.stdout
        self._native_output_lock = Lock()
        self._native_advanced = False
        self._native_inflight = None
        self._native_last_status = None
        self._native_voice_notice = ""
        set_activation_policy(allow_uninitialized=True)
        super().__init__()
        self.root.withdraw()
        set_activation_policy()
        Thread(
            target=self._read_native_requests,
            args=(input_stream or getattr(sys.stdin, "buffer", sys.stdin),),
            daemon=True,
        ).start()
        self.root.after(25, self._native_tick)

    def _emit_native(self, payload):
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
                message = "This response exceeds the native display limit. Use Advanced Tools to inspect current state. If this was an action, its outcome needs inspection; do not repeat it."
                error = (
                    dict(id=payload["id"], ok=False, error=message)
                    if "id" in payload
                    else dict(event="error", data=dict(message=message))
                )
                encoded = json.dumps(error, ensure_ascii=False, separators=(",", ":"))
            with self._native_output_lock:
                self._native_output.write(encoded + "\n")
                self._native_output.flush()
        except (OSError, ValueError):
            # The parent vanished. Preserve the normal transaction/update close path.
            self._native_disconnected = True
            self.close_when_idle = True

    def _close(self):
        # Tk menus and dialogs must let the native owner review unsaved drafts.
        # Once the native owner approves, preserve the existing transaction and
        # pending-update close path. Disconnected channels cannot request review.
        if (
            getattr(self, "_native_parent_approved_close", False)
            or getattr(self, "_native_disconnected", False)
            or self._native_input_ended.is_set()
        ):
            self._native_shutdown_started = True
            return super()._close()
        self._emit_native(dict(event="close.requested", data={}))

    def _show(self, view):
        if self._native_advanced:
            return super()._show(view)
        self.view = view
        self._controls()

    def _render_gallery(self):
        self.native_library.invalidate()
        self._emit_native(dict(event="library.changed", data={}))
        if self._native_advanced:
            return super()._render_gallery()
        self._maps_dirty = False

    def _startup_ready(self, generation, successful, data):
        super()._startup_ready(generation, successful, data)
        if not self.closed:
            self._emit_native(dict(event="ready", data=self._native_status()))

    def _library_chosen(self, result):
        self.native_library.invalidate()
        super()._library_chosen(result)
        self._emit_native(dict(event="library.changed", data={}))

    def _native_status(self):
        return dict(
            available=self.app is not None,
            enrolled=self.app is not None and self.app.profiles.state_file.exists(),
            initializing=self.initializing,
            busy=self.busy,
            status=self.status.get(),
            crash_status=self.crash_status.get(),
        )

    def _queue_native_request(self, request):
        while not self.closed:
            try:
                self._native_requests.put(request, timeout=0.1)
                return
            except Full:
                continue

    def _read_native_requests(self, stream):
        while True:
            line = stream.readline(MAX_REQUEST_BYTES + 1)
            if not line:
                self._native_input_ended.set()
                self._queue_native_request(None)
                return
            newline = b"\n" if isinstance(line, bytes) else "\n"
            try:
                payload = line if isinstance(line, bytes) else line.encode("utf-8")
                if len(payload) > MAX_REQUEST_BYTES or not line.endswith(newline):
                    raise ValueError("Native request exceeds its size limit")
                request = json.loads(payload)
                if (
                    not isinstance(request, dict)
                    or not isinstance(request.get("id"), str)
                    or not 0 < len(request["id"]) <= 128
                    or not isinstance(request.get("method"), str)
                    or not isinstance(request.get("params", {}), dict)
                ):
                    raise ValueError("Invalid native request envelope")
                self._queue_native_request(request)
            except (ValueError, UnicodeError, RecursionError) as exc:
                self._queue_native_request(dict(id="", method="invalid", params={}, error=str(exc)))
                if not line.endswith(newline):
                    # Do not interpret trailing bytes of a rejected oversized line.
                    while line and not line.endswith(newline):
                        line = stream.readline(MAX_REQUEST_BYTES + 1)

    def _native_reply(self, request, ok, value):
        response = dict(id=request["id"], ok=ok)
        response["data" if ok else "error"] = value if ok else str(value)
        if request["method"] in {"workflow", "annotations.save", "history.record", "prefs.save"}:
            self._native_completed[request["id"]] = (
                digest(json.dumps(request, sort_keys=True)),
                response,
            )
        while len(self._native_completed) > 64:
            self._native_completed.popitem(last=False)
        self._native_inflight = None
        self._emit_native(response)

    def _native_tick(self):
        if self.closed:
            return
        if (
            getattr(self, "_native_parent_approved_close", False)
            and getattr(self, "_native_shutdown_started", False)
            and not self.busy
            and not self.close_when_idle
        ):
            # A pending-update failure dialog can cancel the existing close flow.
            # New close attempts must review any subsequently edited native draft.
            self._native_parent_approved_close = False
            self._native_shutdown_started = False
            self._emit_native(dict(event="close.cancelled", data={}))
        status = self._native_status()
        if status != self._native_last_status:
            self._native_last_status = status
            self._emit_native(dict(event="status", data=status))
        if not self.busy and not self.initializing and self._native_inflight is None:
            try:
                request = self._native_requests.get_nowait()
            except Empty:
                request = False
            if request is None:
                self._close()
            elif request:
                previous = self._native_completed.get(request["id"])
                if previous:
                    if previous[0] == digest(json.dumps(request, sort_keys=True)):
                        self._emit_native(previous[1])
                    else:
                        self._emit_native(
                            dict(
                                id=request["id"],
                                ok=False,
                                error="Request identity was already used for another operation",
                            )
                        )
                else:
                    self._native_dispatch(request)
        if not self.closed:
            self.root.after(25, self._native_tick)

    def _native_dispatch(self, request):
        method, params = request["method"], request.get("params", {})
        try:
            if request.get("error"):
                raise ValueError(request["error"])
            if method == "workflow":
                self._native_reply(request, True, self._native_workflow(params))
                return
            if method == "snapshot" and self.app is None:
                self._native_reply(request, True, {**self._native_status(), "maps": []})
                return
            if method == "prefs":

                def operation():
                    try:
                        return list_voices(), ""
                    except Exception as exc:
                        return (), "Voices need inspection: " + str(exc)
            elif method == "prefs.save":
                operation = self._native_save_preferences(params)
            elif method == "collection":
                if "refresh" in params and type(params["refresh"]) is not bool:
                    raise ValueError("refresh must be Boolean")
                operation = lambda: self.native_library.collection(
                    self.update_library, params.get("refresh", False)
                )
            elif method == "activity":
                operation = lambda: dict(records=list(reversed(self.activity.records())))
            elif method == "history.save_digest":
                operation = lambda: dict(save_id=save_digest(text(params, "path")))
            else:
                app = self.app
                if app is None:
                    raise ValueError("Find the Steam Mac game before opening this library")
                if method == "snapshot":
                    if "refresh" in params and type(params["refresh"]) is not bool:
                        raise ValueError("refresh must be Boolean")
                    operation = lambda: dict(
                        maps=self.native_library.snapshot(
                            app,
                            params.get("refresh", False),
                            history_error=getattr(self.crash_monitor, "history_error", ""),
                        )
                    )
                elif method == "details":
                    operation = lambda: self.native_library.details(app, params)
                elif method == "briefing.translate":
                    operation = lambda: self.native_library.briefing_translate(app, params)
                elif method == "annotations.save":
                    operation = lambda: self.native_library.annotations_save(app, params)
                elif method == "history.record":
                    operation = lambda: self.native_library.history_record(app, params)
                else:
                    raise ValueError("Unknown native method: " + method)
            self._native_inflight = request["id"]
            self.busy = True
            self._controls()

            def completed(ok, value):
                self.busy = False
                self._controls()
                if ok and method == "prefs":
                    self.voices, self._native_voice_notice = value
                    value = self._native_preferences()
                if ok and method == "snapshot":
                    value = {**self._native_status(), **value}
                if ok and method == "prefs.save":
                    set_language(self.language_preferences.language)
                    self.auto_updates.set(value["automatic_updates"])
                    if params.get("automatic_updates") is True and self.pending_update is None:
                        self.root.after(0, self.check_for_updates)
                self._native_reply(request, ok, value)

            self._background(operation, completed, label=None)
        except Exception as exc:
            self._native_reply(request, False, exc)

    def _native_preferences(self):
        monitor = self.crash_monitor
        return dict(
            language=self.language_preferences.language,
            strings=dict(TRANSLATIONS[self.language_preferences.language]),
            voice=self.language_preferences.voice,
            automatic_updates=self.auto_updates.get(),
            crash_enabled=monitor.enabled if monitor else None,
            crash_automatic=monitor.automatic_reporting if monitor else None,
            resource_protection=monitor.protect_resources if monitor else None,
            languages=[dict(id=k, name=v) for k, v in LANGUAGES.items()],
            voice_notice=getattr(self, "_native_voice_notice", ""),
            voices=[dict(id=v.identifier, name=v.name, language=v.language) for v in self.voices],
        )

    def _native_save_preferences(self, params):
        current = self._native_preferences()
        supplied = set(params)
        allowed = {
            "language",
            "voice",
            "automatic_updates",
            "crash_enabled",
            "crash_automatic",
            "resource_protection",
        }
        if supplied - allowed:
            raise ValueError("Unknown native preference")
        values = {**current, **params}
        if (
            values["language"] not in LANGUAGES
            or not isinstance(values["voice"], str)
            or len(values["voice"]) > 256
        ):
            raise ValueError("Invalid language or voice preference")
        for key in supplied - {"language", "voice"}:
            if type(values[key]) is not bool:
                raise ValueError(key + " must be Boolean")
        values["strings"] = dict(TRANSLATIONS[values["language"]])
        monitor = self.crash_monitor
        if (
            supplied & {"crash_enabled", "crash_automatic", "resource_protection"}
            and monitor is None
        ):
            raise ValueError("Crash monitor is unavailable; its settings were not changed")

        def save():
            if supplied & {"language", "voice"}:
                self.language_preferences.save(values["language"], values["voice"])
            if "automatic_updates" in supplied:
                self.update_preferences.set_automatic(values["automatic_updates"])
            if supplied & {"crash_enabled", "crash_automatic", "resource_protection"}:
                monitor.configure(
                    enabled=values["crash_enabled"],
                    automatic_reporting=values["crash_automatic"],
                    protect_resources=values["resource_protection"],
                )
            return values

        return save

    def _native_workflow(self, params):
        action = text(params, "action")
        if action == "quit":
            self._native_parent_approved_close = True
            self._native_shutdown_started = False
            self.root.after_idle(self._close)
            return dict(accepted=True)
        if action == "hide-advanced":
            self._native_advanced = False
            self.view = "maps"
            self.root.withdraw()
            set_activation_policy()
            return dict(accepted=True)
        if action == "play":
            if self.app is None or not self.app.profiles.state_file.exists():
                raise ValueError("Complete setup before playing")
            profile_id = text(params, "profile_id", ORIGINAL)
            self._play(profile_id)
        elif action == "download":
            if self.app is None or not self.app.profiles.state_file.exists():
                raise ValueError("Complete setup before downloading a map")
            remote = next(
                (r for r in self.native_library.remote_records if r.name == text(params, "name")),
                None,
            )
            if remote is None:
                raise ValueError("Refresh the trusted collection before downloading this map")
            self.selected_remote = remote
            self.selected_remotes = (remote,)
            self._download_selected()
        elif action == "import" and "path" in params:
            if self.app is None or not self.app.profiles.state_file.exists():
                raise ValueError("Complete setup before importing a map")
            path = Path(text(params, "path"))
            app = self.app
            self._submit(
                "Inspecting and preparing " + path.name + "…",
                lambda: app.import_archive(path),
                self._imported,
            )
        elif action == "import":
            self.import_map()
        elif action == "choose-library":
            if "path" in params:
                path = Path(text(params, "path"))
                self._submit(
                    "Checking the selected Steam library…",
                    lambda: LauncherApplication.choose_steam_library(path),
                    self._library_chosen,
                )
            else:
                self.choose_steam_library()
        elif action == "read":
            if self.app is None:
                raise ValueError("Find the Steam Mac game before reading a briefing")
            app = self.app
            profile_id = text(params, "profile_id")
            self._submit(
                "Reading map briefing…",
                lambda: app.map_metadata(self.native_library.record(app, profile_id)).description,
                self._read_briefing,
            )
        elif action == "stop-reading":
            self._stop_speech()
        elif action == "voice-settings":
            self._submit("Opening macOS voice settings…", open_voice_settings)
        elif action == "translation-settings":
            self._submit("Opening translation settings…", open_translation_settings)
        elif action == "installation-help":
            webbrowser.open(
                "https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/blob/main/MACOS_README.md"
            )
        else:
            if action not in {
                "collection",
                "details",
                "legacy-test",
                "fix-test",
                "crash-reports",
                "updates",
                "setup",
                "preferences",
                "advanced",
            }:
                raise ValueError("Unknown workflow: " + action)
            set_activation_policy(advanced=True)
            self._native_advanced = True
            self.root.deiconify()
            self.root.title("SMR Launcher — Advanced Tools")
            self.root.protocol(
                "WM_DELETE_WINDOW", lambda: self._native_workflow(dict(action="hide-advanced"))
            )
            if action in ("details", "legacy-test", "fix-test"):
                if self.app is None:
                    raise ValueError("Find the Steam Mac game before opening map details")
                app, profile_id = self.app, text(params, "profile_id")
                self._submit(
                    "Opening advanced map tools…",
                    lambda: self.native_library.record(app, profile_id),
                    self._map_details,
                )
            elif action == "crash-reports":
                self.crash_reports()
            elif action == "updates":
                self._show("setup")
                self.check_for_updates()
            else:
                self._show(action if action != "advanced" else "maps")
        self.native_library.invalidate()
        return dict(accepted=True)

    def _history_exit_prompt(self, event):
        if self.closed or not self.app:
            return
        if self.busy or self.initializing or self._native_inflight is not None:
            self.root.after(250, lambda: self._history_exit_prompt(event))
            return
        app = self.app
        self.busy = True
        self._native_inflight = "history.exit"

        def offer():
            record = next(
                (
                    r
                    for r in app.catalogue()
                    if all(
                        context(r, app.installation.executable_sha256)[k] == event[k]
                        for k in IDENTITIES
                    )
                ),
                None,
            )
            if record is None:
                return None
            store = PlayHistoryStore(app.library)
            prompt_id = digest(event["attempt_id"], "prompt")
            if any(e["event_id"] == prompt_id for e in store.records()):
                return None
            store.once(
                {k: event[k] for k in (*IDENTITIES, "scenario")},
                "prompt_offered",
                event_id=prompt_id,
                attempt_id=event["attempt_id"],
                source="observed",
            )
            return dict(
                profile_id=record.profile_id,
                attempt_id=event["attempt_id"],
                scenario=event["scenario"],
                **{k: event[k] for k in IDENTITIES},
            )

        def offered(ok, value):
            self.busy = False
            self._native_inflight = None
            if not ok:
                self.status.set("History prompt needs inspection: " + str(value))
            elif value and self.app is app and not self.closed:
                self.native_library.invalidate()
                self._emit_native(dict(event="history.exit", data=value))

        self._background(offer, offered, label=None)

    def _finish_close(self):
        super()._finish_close()
        self._emit_native(dict(event="closed", data={}))


def main():
    window = NativeBackendWindow()
    window.run()
    # The updater waits for this Python owner. Keep it alive until the native
    # frontend has closed its input pipe, so an app replacement cannot race it.
    window._native_input_ended.wait()


if __name__ == "__main__":
    main()
