"""Native global tools using existing protected operations and bounded job state.

The Tk owner starts and completes work on its existing worker queue. Only status
and cancellation bypass that queue; neither changes an ordinary request receipt.
Crash diagnostics and exception contents never enter this projection.
"""

from copy import deepcopy
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4
import subprocess
import hashlib
import re

from .activation import _atomic_json, _assert_no_symlink_ancestor
from .cold_storage import ColdStorageError, _canonical, _checkpoint
from .compact_library import _read

from . import APP_VERSION
from .application import LauncherApplication
from .collection import download_all_maps, fetch_catalogue
from .map_updates import find_map_updates
from .updates import (
    current_app_bundle,
    download_release,
    fetch_latest_release,
    prepare_install,
    stage_release,
    trusted_team_from_bundle,
)


class NativeTools:
    def __init__(self, owner):
        self.owner = owner
        self.cancelled = Event()
        self.lock = Lock()
        self.job = {}
        self.candidates = ()
        self.crash_counts = {}
        self.compact_proposal = {}

    def status(self, job_id=None):
        with self.lock:
            if job_id is not None and job_id != self.job.get("job_id"):
                raise ValueError("Unknown or expired tool job; inspect before repeating an action")
            job = deepcopy(self.job)
        owner = self.owner
        release = owner.available_update
        return dict(
            job=job,
            compact_proposal=dict(self.compact_proposal),
            version=APP_VERSION,
            available=owner.app is not None,
            enrolled=owner.app is not None and owner.app.profiles.state_file.exists(),
            update_available=release is not None,
            update_version=release.version if release else "",
            update_sha256=release.sha256 if release else "",
            update_size=release.size if release else 0,
            update_pending=owner.pending_update is not None,
            update_automatic=owner.auto_updates.get(),
            monitor_available=owner.crash_monitor is not None,
            crash_counts=dict(self.crash_counts),
            crash_notice="Full reports remain private on this Mac. Counts cover pending and uncertain reports only. Reconciliation uses saved publication preferences.",
            candidates=[
                dict(
                    name=c.remote.name,
                    sha1=c.remote.sha1,
                    size=c.remote.size,
                    installed_variant_id=c.installed_variant_id,
                    installed_filename=c.installed_filename,
                    kind=c.kind,
                )
                for c in self.candidates
            ],
        )

    def cancel(self, job_id):
        with self.lock:
            if job_id != self.job.get("job_id") or self.job.get("state") != "running":
                raise ValueError("Unknown or completed tool job; cancellation was not requested")
            if not self.job["cancellable"]:
                raise ValueError("This operation cannot be interrupted safely; wait for its result")
            self.cancelled.set()
            self.job["cancel_requested"] = True
        return dict(cancel_requested=True, job_id=job_id)

    def _phase(self, message):
        with self.lock:
            self.job["message"] = message

    def _progress(self, record, state, completed, total, detail):
        # Do not forward arbitrary exception text, paths or diagnostic payloads.
        with self.lock:
            self.job["items"][record.name] = dict(
                name=record.name,
                state=state,
                completed=completed,
                total=total,
                notice="Inspect local operation state before retrying." if detail else "",
            )

    @staticmethod
    def consent(params):
        if params.get("consent") is not True:
            raise ValueError("Explicit confirmation is required; no operation started")

    def start(self, action, params):
        owner = self.owner
        with self.lock:
            if self.job.get("state") == "running":
                raise ValueError("Another tool operation is still running")
        if owner.busy or owner.initializing:
            raise ValueError("Wait for the current operation before starting another tool")
        operation, completed, cancellable, records = self._operation(action, params)
        job_id = uuid4().hex
        self.cancelled.clear()
        with self.lock:
            self.job = dict(
                job_id=job_id,
                action=action,
                state="running",
                cancellable=cancellable,
                cancel_requested=False,
                message="Working…",
                items={
                    r.name: dict(name=r.name, state="Queued", completed=0, total=r.size, notice="")
                    for r in records
                },
            )
        owner.busy = True
        owner._controls()

        def finished(ok, value):
            try:
                if ok:
                    completed(value)
                with self.lock:
                    self.job["state"] = "completed" if ok else "failed"
                    self.job["message"] = (
                        value.get("message", "Operation completed.")
                        if ok and isinstance(value, dict)
                        else "Operation completed."
                        if ok
                        else "Operation failed. Inspect local state before retrying; no automatic retry occurred."
                    )
                    if ok and action == "update.check":
                        self.job["message"] = (
                            f"Version {value.version} is available from the personal fork."
                            if value
                            else "No newer stable release is available."
                        )
                    if not ok and action.startswith("update."):
                        self.job["message"] = (
                            "Update check or verification failed. Check network access, Developer ID signing and writable Applications location, then inspect staged state before retrying."
                        )
                    if ok and isinstance(value, dict) and value.get("cancelled"):
                        self.job["state"] = "cancelled"
            except Exception:
                with self.lock:
                    self.job["state"] = "unknown"
                    self.job["message"] = (
                        "Completion needs inspection. Do not repeat this operation automatically."
                    )
            finally:
                owner.busy = False
                owner._controls()
                if hasattr(owner, "_emit_native"):
                    owner._emit_native(dict(event="tools.changed", data=self.status()))
                if owner.close_when_idle:
                    owner.root.after_idle(owner._close)

        owner._background(operation, finished, label=None)
        if hasattr(owner, "_emit_native"):
            owner._emit_native(dict(event="tools.changed", data=self.status()))
        return dict(job_id=job_id)

    def _operation(self, action, params):
        owner = self.owner
        app = owner.app
        noop = lambda value: None
        if action in (
            "library.compact.plan",
            "library.compact.apply",
            "library.compact.rollback",
            "library.prepare",
        ):
            if app is None or not app.profiles.state_file.exists():
                raise ValueError("Complete setup before preparing stored maps")
            proposal = None
            if action == "library.compact.plan":
                for field in ("exclude", "include_profiles"):
                    values = params.get(field, [])
                    if (
                        not isinstance(values, list)
                        or len(values) > 10000
                        or any(not isinstance(value, str) or len(value) > 1024 for value in values)
                    ):
                        raise ValueError("Choose explicit bounded storage preservation identities")
            else:
                self.consent(params)
            if action in ("library.compact.apply", "library.compact.rollback"):
                seal = params.get("plan_sha256")
                if not isinstance(seal, str) or not re.fullmatch("[0-9a-f]{64}", seal):
                    raise ValueError("An exact reviewed storage proposal identity is required")
                proposal = app.library / "compact-proposals" / (seal + ".json")
            if action == "library.prepare":
                matches = [
                    record
                    for record in app.catalogue()
                    if record.profile_id == params.get("profile_id")
                ]
                if len(matches) != 1:
                    raise ValueError("Choose one current library map")
                record = matches[0]

            def progress(*values):
                # File/member names remain in private manifests, never job projections.
                self._phase("Preparing stored map data…")

            def operation():
                try:
                    _checkpoint(self.cancelled.is_set)
                    if action == "library.compact.plan":
                        self._phase("Reading stored map data and preservation constraints…")
                        candidate = app.plan_compaction(
                            exclude=params.get("exclude", ()),
                            include_profiles=params.get("include_profiles", ()),
                            cancelled=self.cancelled.is_set,
                            progress=progress,
                        )
                        seal = hashlib.sha256(_canonical(candidate)).hexdigest()
                        directory = app.library / "compact-proposals"
                        _assert_no_symlink_ancestor(directory)
                        directory.mkdir(exist_ok=True)
                        destination = directory / (seal + ".json")
                        _assert_no_symlink_ancestor(destination)
                        if destination.exists():
                            if _read(destination) != candidate:
                                raise ValueError("Stored storage proposal identity differs")
                        else:
                            _atomic_json(destination, candidate)
                        return dict(
                            message="Storage proposal ready for review.",
                            proposal=dict(
                                sha256=seal,
                                trees=len(candidate["trees"]),
                                excluded=len(candidate["excluded"]),
                                excluded_logical_bytes=sum(
                                    item["logical_bytes"] for item in candidate["excluded"]
                                ),
                            ),
                        )
                    if action == "library.prepare":
                        app.prepare_map(record, cancelled=self.cancelled.is_set, progress=progress)
                        return dict(message="Selected map is ready. No game was launched.")
                    candidate = _read(proposal)
                    if hashlib.sha256(_canonical(candidate)).hexdigest() != params["plan_sha256"]:
                        raise ValueError("Stored storage proposal identity differs")
                    function = (
                        app.compact_library
                        if action == "library.compact.apply"
                        else app.rollback_compaction
                    )
                    function(candidate, cancelled=self.cancelled.is_set, progress=progress)
                    return dict(
                        message="Stored map preparation completed. Game behavior remains unverified."
                    )
                except ColdStorageError as exc:
                    if self.cancelled.is_set() and str(exc) == "Storage preparation cancelled":
                        return dict(
                            cancelled=True,
                            message="Preparation cancelled. Stored data retained; resume or roll back its exact proposal if recovery is pending.",
                        )
                    raise

            def completed(value):
                if "proposal" in value:
                    self.compact_proposal = value["proposal"]
                owner._render_gallery()

            return operation, completed, True, ()
        if action == "update.check":
            if owner.pending_update is not None:
                raise ValueError(
                    "A verified update is already staged; inspect it before checking again"
                )

            def checked(value):
                owner.available_update = value
                if value is not None and owner.auto_updates.get():

                    def automatic_stage():
                        if (
                            owner.busy
                            or not owner.auto_updates.get()
                            or owner.pending_update is not None
                        ):
                            return
                        self.start(
                            "update.stage", dict(consent=True, sha256=value.sha256, manual=False)
                        )

                    owner.root.after_idle(automatic_stage)

            return lambda: fetch_latest_release(APP_VERSION), checked, False, ()
        if action == "update.stage":
            self.consent(params)
            manual = params.get("manual", True)
            if type(manual) is not bool or (not manual and not owner.auto_updates.get()):
                raise ValueError("Automatic staging requires the saved automatic update preference")
            release = owner.available_update
            if (
                release is None
                or params.get("sha256") != release.sha256
                or ("version" in params and params["version"] != release.version)
                or ("size" in params and params["size"] != release.size)
                or owner.pending_update is not None
            ):
                raise ValueError(
                    "Release identity changed or an update is already staged; check again"
                )

            def stage():
                if self.cancelled.is_set():
                    return dict(
                        cancelled=True,
                        message="Update cancelled before downloading. Launcher stays open.",
                    )
                self._phase("Checking installed publisher and update destination…")
                team = trusted_team_from_bundle()
                if not team:
                    raise ValueError(
                        "A Developer ID signed launcher is required for verified installation"
                    )
                installed = current_app_bundle()
                self._phase(
                    "Downloading the personal fork release and checking its archive identity…"
                )
                archive = download_release(release, owner.update_library / "updates")
                if self.cancelled.is_set():
                    return dict(
                        cancelled=True,
                        message="Download retained; staging cancelled. Launcher stays open.",
                    )
                self._phase("Verifying the staged app publisher…")
                staged = stage_release(archive, release, owner.update_library, team)
                if self.cancelled.is_set():
                    return dict(
                        cancelled=True,
                        message="Verified staging retained; installation preparation cancelled. Launcher stays open.",
                    )
                self._phase("Preparing a verified installation beside the current app…")
                pending = prepare_install(
                    staged,
                    installed,
                    release,
                    owner.update_library,
                    team,
                    APP_VERSION,
                    manual_install=manual,
                )
                return dict(
                    pending=pending,
                    message="Verified update ready. Review installation when quitting.",
                )

            def staged(value):
                if "pending" in value:
                    owner.pending_update = value["pending"]
                    owner.pending_update_manual = manual

            return stage, staged, True, ()
        if action in {"library.discover", "library.choose"}:
            self.consent(params)
            if app is not None and action == "library.discover":
                raise ValueError(
                    "Existing library is connected; choose a library explicitly to change it"
                )
            path = params.get("path")
            if action == "library.choose" and (not isinstance(path, str) or not path):
                raise ValueError("Choose a Steam library folder first")

            def operation():
                if app is not None:
                    app._stopped()
                return (
                    LauncherApplication.choose_steam_library(Path(path))
                    if action == "library.choose"
                    else LauncherApplication.discover()
                )

            return operation, owner._library_chosen, False, ()
        if action == "library.setup":
            self.consent(params)
            if app is None:
                raise ValueError("Find the Steam Mac game before setup")
            return app.setup, lambda value: owner._render_gallery(), False, ()
        if action == "crash.status":
            monitor = owner.crash_monitor
            if monitor is None:
                raise ValueError("Crash monitor is unavailable")

            def counts():
                result = {key: 0 for key in ("pending", "unknown")}
                for entry in monitor.store.pending():
                    state = entry.get("publication", {}).get("status")
                    if state in result:
                        result[state] += 1
                return result

            def counted(value):
                self.crash_counts = value

            return counts, counted, False, ()
        if action == "crash.reconcile":
            self.consent(params)
            monitor = owner.crash_monitor
            if monitor is None:
                raise ValueError("Crash monitor is unavailable")
            return lambda: monitor.poll_once(reconcile=True), noop, False, ()
        if action == "crash.folder":
            if owner.crash_monitor is None:
                raise ValueError("Crash monitor is unavailable")
            return (
                lambda: subprocess.Popen(["/usr/bin/open", str(owner.crash_monitor.root)]).pid,
                noop,
                False,
                (),
            )
        if action == "collection.updates":
            if app is None:
                raise ValueError("Find the Steam Mac game before checking map updates")

            def updates():
                remotes = fetch_catalogue()
                app.match_collection_sources(remotes)
                return remotes, find_map_updates(app.catalogue(), remotes)

            def found(value):
                owner.native_library.remote_records, self.candidates = value

            return updates, found, False, ()
        if action == "collection.import":
            self.consent(params)
            if app is None or not app.profiles.state_file.exists():
                raise ValueError("Complete setup before importing maps")
            requested = params.get("records")
            if not isinstance(requested, list) or not requested or len(requested) > 10000:
                raise ValueError("Select a bounded set of catalogue archives")
            available = {r.name: r for r in owner.native_library.remote_records}
            records = []
            seen = set()
            for item in requested:
                if not isinstance(item, dict):
                    raise ValueError("Invalid collection selection")
                remote = available.get(item.get("name"))
                if (
                    remote is None
                    or item.get("sha1") != remote.sha1
                    or item.get("size") != remote.size
                ):
                    raise ValueError("Catalogue archive identity changed; refresh and select again")
                if remote.name in seen:
                    raise ValueError("Duplicate collection selection")
                seen.add(remote.name)
                records.append(remote)
            records = tuple(records)

            def download():
                app._stopped()
                counts = download_all_maps(
                    records,
                    app.library / "downloads",
                    self._progress,
                    self.cancelled,
                    downloader=app.download_archive,
                    importer=lambda remote, path: app.import_archive(
                        path, original_filename=remote.name, remote=remote
                    ),
                )
                imported, failed, cancelled = counts
                return dict(
                    cancelled=bool(cancelled),
                    message=f"{imported} imported, {failed} failed, {cancelled} cancelled. Gameplay remains unverified.",
                )

            return download, lambda value: owner._render_gallery(), True, records
        raise ValueError("Unknown native tool action")
