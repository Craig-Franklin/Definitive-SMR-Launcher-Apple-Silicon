"""Synthetic bridge, private presentation, history and lifecycle checks; no game."""

import base64
from dataclasses import replace
import io
import os
from pathlib import Path
from queue import Queue
import struct
from tempfile import TemporaryDirectory, gettempdir
from threading import Event
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import zlib

from smr_launcher.annotations import AnnotationStore, validate_image
from smr_launcher.application import MapRecord
from smr_launcher.map_metadata import MapMetadata
from smr_launcher.native_backend import NativeBackendWindow, NativeLibrary, main
from smr_launcher.play_history import HistoryError, PlayHistoryStore, context, digest


def png(width=1, height=1):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    value = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    )
    value += chunk(b"IDAT", zlib.compress(b"\0\0\0\0\xff")) + chunk(b"IEND", b"")
    return base64.b64encode(value).decode()


def record():
    return MapRecord(
        "Synthetic", "a" * 64, "b" * 64, "synthetic", ("Scenario.xml",), "/synthetic-only", "c" * 64
    )


def app(library):
    result = SimpleNamespace(
        library=Path(library), installation=SimpleNamespace(executable_sha256="c" * 64)
    )
    result.catalogue = Mock(return_value=(record(),))
    result.map_metadata = Mock(
        return_value=MapMetadata(name="Package title", description="Original package text")
    )
    result.verification_for = Mock(return_value=None)
    result.map_icon = Mock(return_value=None)
    result.import_checks = Mock(return_value=None)
    return result


def window():
    result = NativeBackendWindow.__new__(NativeBackendWindow)
    result.native_library = NativeLibrary()
    result._native_requests = Queue(maxsize=64)
    result._native_completed = {}
    result._native_input_ended = Event()
    result._native_inflight = None
    result._native_last_status = None
    result.closed = result.busy = result.initializing = False
    result.root = SimpleNamespace(
        after=Mock(),
        after_idle=Mock(),
        withdraw=Mock(),
        deiconify=Mock(),
        title=Mock(),
        protocol=Mock(),
    )
    result._native_status = Mock(
        return_value=dict(
            available=True, enrolled=True, status="Synthetic", crash_status="Not armed"
        )
    )
    result._emit_native = Mock()
    result._controls = Mock()
    result.crash_monitor = None
    return result


class AnnotationTests(TestCase):
    def test_identity_scoped_overrides_leave_package_facts_alone(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            store, r = AnnotationStore(folder), record()
            store.save(r, title="Personal title", notes="Personal notes", image_png=png())
            store.save(r, title="Updated", notes="Updated notes")
            self.assertEqual(store.load(r)["image_png"], png())
            self.assertEqual(store.load(replace(r, variant_id="d" * 64))["title"], "")
            self.assertEqual(r.name, "Synthetic")
            store.save(r, title="", notes="", image_png="")
            self.assertEqual(store.load(r), dict(title="", notes="", image_png=""))
            self.assertEqual(os.stat(store._path(r)).st_mode & 0o777, 0o600)

    def test_invalid_image_and_text_preserve_previous_document(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            store, r = AnnotationStore(folder), record()
            store.save(r, title="Retain", notes="Notes")
            before = store._path(r).read_bytes()
            for image in (
                "not PNG",
                png(361),
                png()[:-8],
                base64.b64encode(b"\x89PNG\r\n\x1a\n").decode(),
            ):
                with self.subTest(image=image[:20]), self.assertRaises(ValueError):
                    store.save(r, title="Changed", notes="", image_png=image)
            with self.assertRaises(ValueError):
                store.save(r, title="a" * 257, notes="")
            self.assertEqual(store._path(r).read_bytes(), before)

    def test_symlink_parent_or_destination_and_hardlink_are_rejected(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            root = Path(folder)
            target = root / "outside"
            target.mkdir()
            (root / "map-annotations").symlink_to(target, target_is_directory=True)
            store = AnnotationStore(root)
            with self.assertRaises(OSError):
                store.save(record(), title="", notes="")
            (root / "map-annotations").unlink()
            store.save(record(), title="", notes="")
            path = store._path(record())
            path.unlink()
            victim = target / "victim"
            victim.write_text("Keep")
            path.symlink_to(victim)
            with self.assertRaises(OSError):
                store.save(record(), title="", notes="")
            self.assertEqual(victim.read_text(), "Keep")
            path.unlink()
            os.link(victim, path)
            with self.assertRaises(ValueError):
                store.load(record())

    def test_failed_atomic_replacement_preserves_previous_and_removes_temporary(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            store = AnnotationStore(folder)
            store.save(record(), title="Retain", notes="Notes")
            before = store._path(record()).read_bytes()
            with patch(
                "smr_launcher.cache_files.os.replace", side_effect=OSError("Synthetic disk failure")
            ):
                with self.assertRaises(OSError):
                    store.save(record(), title="Changed", notes="")
            self.assertEqual(store._path(record()).read_bytes(), before)
            self.assertEqual(len(list(store.root.iterdir())), 1)
            store.root.chmod(0o755)
            with self.assertRaises(ValueError):
                store.load(record())

    def test_invalid_identity_and_checksum_rejected(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            with self.assertRaises(ValueError):
                AnnotationStore(folder).load(replace(record(), variant_id="../escape"))
        data = bytearray(base64.b64decode(png()))
        data[-1] ^= 1
        with self.assertRaises(ValueError):
            validate_image(base64.b64encode(data).decode())


class NativeLibraryTests(TestCase):
    def test_snapshot_cached_until_explicit_refresh_but_annotations_update(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            model, application = NativeLibrary(), app(folder)
            first = model.snapshot(application)
            self.assertEqual(first[0]["metadata"]["name"], "Package title")
            self.assertEqual(first[0]["summary"]["opens"], 0)
            self.assertNotIn("events", first[0]["summary"])
            self.assertEqual(model.snapshot(application), first)
            self.assertEqual(application.map_metadata.call_count, 1)
            model.annotations_save(
                application,
                dict(
                    profile_id="synthetic",
                    title="Mine",
                    notes="Private",
                    archive_sha256="a" * 64,
                    variant_id="b" * 64,
                ),
            )
            self.assertEqual(model.snapshot(application)[0]["annotation"]["title"], "Mine")
            self.assertEqual(application.map_metadata.call_count, 1)
            model.snapshot(application, True)
            self.assertEqual(application.map_metadata.call_count, 2)

    def test_corrupt_annotations_surface_notice_without_green_history(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            application = app(folder)
            store = AnnotationStore(folder)
            store.save(record(), title="Mine", notes="")
            store._path(record()).write_text("corrupt")
            item = NativeLibrary().snapshot(application)[0]
            self.assertIn("need inspection", item["notice"])
            self.assertEqual(item["verification"], "Not verified")

    def test_history_rejects_invented_open_and_scenario_without_writes(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            model, application = NativeLibrary(), app(folder)
            params = dict(
                **{k: v for k, v in context(record(), "c" * 64).items() if k != "scenario"},
                profile_id="synthetic",
                kind="playable",
                scenario="Scenario.xml",
                attempt_id=digest("invented"),
            )
            with self.assertRaises(HistoryError):
                model.history_record(application, params)
            params["scenario"] = "Other.xml"
            with self.assertRaises(HistoryError):
                model.history_record(application, params)
            self.assertEqual(PlayHistoryStore(folder).records(), ())

    def test_history_requires_exact_save_and_bound_game_for_completion(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            model, application = NativeLibrary(), app(folder)
            store, ctx, attempt = (
                PlayHistoryStore(folder),
                context(record(), "c" * 64),
                digest("open"),
            )
            store.append(
                store.event(ctx, "process_confirmed", attempt_id=attempt, source="observed")
            )
            params = dict(
                **{k: v for k, v in context(record(), "c" * 64).items() if k != "scenario"},
                profile_id="synthetic",
                kind="goals_met",
                scenario="Scenario.xml",
                attempt_id=attempt,
            )
            with self.assertRaises(HistoryError):
                model.history_record(application, params)
            self.assertEqual(len(store.records()), 1)
            params["game_id"] = "new"
            self.assertEqual(model.history_record(application, params)["recorded"], 2)
            self.assertEqual(store.records()[-1]["source"], "player_confirmed")
            with self.assertRaises(HistoryError):
                model.history_record(
                    application,
                    {
                        **params,
                        "game_id": "",
                        "kind": "save_reopened",
                        "save_id": digest("not recorded"),
                    },
                )
            details = model.details(application, dict(profile_id="synthetic"))
            self.assertEqual(details["history"][-1]["label"], "All scenario goals met")
            self.assertEqual(details["context"], ctx)

    def test_retained_failure_cannot_be_attributed_to_another_open(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            application, model = app(folder), NativeLibrary()
            store, ctx = PlayHistoryStore(folder), context(record(), "c" * 64)
            first, second, incident = digest("first"), digest("second"), digest("incident")
            store.append(
                *(
                    store.event(ctx, "process_confirmed", attempt_id=a, source="observed")
                    for a in (first, second)
                )
            )
            store.append(store.event(ctx, "crash_unknown", attempt_id=first, incident_id=incident))
            with self.assertRaises(HistoryError):
                model.history_record(
                    application,
                    dict(
                        **{k: v for k, v in context(record(), "c" * 64).items() if k != "scenario"},
                        profile_id="synthetic",
                        kind="crash_unknown",
                        attempt_id=second,
                        failure_id=incident,
                    ),
                )
            self.assertEqual(len(store.records()), 3)

    def test_combined_personal_and_package_art_budget_preserves_stored_overrides(self):
        from smr_launcher.gallery import GalleryItem

        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            application, model = app(folder), NativeLibrary()
            records = (record(), replace(record(), variant_id="d" * 64, profile_id="second"))
            application.catalogue.return_value = records
            image, binary = png(), base64.b64decode(png())
            store = AnnotationStore(folder)
            for r in records:
                store.save(r, title="Mine", notes="Retain", image_png=image)
            model.cached_app = application
            model.cached_items = tuple(
                GalleryItem(r, MapMetadata(), "Not verified", art=binary) for r in records
            )
            with patch("smr_launcher.native_backend.MAX_SNAPSHOT_ART_BYTES", len(binary) + 1):
                result = model.snapshot(application)
            total = sum(
                len(base64.b64decode(m["art"]))
                + len(base64.b64decode(m["annotation"]["image_png"]))
                for m in result
            )
            self.assertLessEqual(total, len(binary) + 1)
            self.assertEqual(result[0]["annotation"]["image_png"], image)
            self.assertEqual(result[1]["annotation"]["image_png"], "")
            self.assertIn("Personal image omitted", result[1]["notice"])
            self.assertEqual(store.load(records[1])["image_png"], image)
            model.annotations_save(
                application,
                dict(
                    profile_id="second",
                    archive_sha256="a" * 64,
                    variant_id="d" * 64,
                    title="Edited",
                    notes="Retain",
                ),
            )
            self.assertEqual(store.load(records[1])["image_png"], image)

    def test_native_empty_game_reuses_only_unique_binding_and_rejects_ambiguity(self):
        from smr_launcher.history_ui import confirm_checkpoint

        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            model, application = NativeLibrary(), app(folder)
            store, ctx, attempt = (
                PlayHistoryStore(folder),
                context(record(), "c" * 64, "Scenario.xml"),
                digest("bound open"),
            )
            store.append(
                store.event(ctx, "process_confirmed", attempt_id=attempt, source="observed")
            )
            params = dict(**ctx, profile_id="synthetic", kind="playable", attempt_id=attempt)
            model.history_record(application, params)
            self.assertEqual(store.records()[-1]["game_id"], "")
            with self.assertRaisesRegex(HistoryError, "before recording completion"):
                model.history_record(application, {**params, "kind": "goals_met"})
            confirm_checkpoint(store, ctx, "playable", attempt, game="new")
            game = store.records()[-1]["game_id"]
            model.history_record(application, {**params, "kind": "goals_met"})
            self.assertEqual(store.records()[-1]["game_id"], game)
            confirm_checkpoint(store, ctx, "playable", attempt, game="new")
            before = store.path.read_bytes()
            with self.assertRaisesRegex(HistoryError, "Several games"):
                model.history_record(application, {**params, "kind": "quit"})
            self.assertEqual(store.path.read_bytes(), before)
            model.history_record(application, {**params, "kind": "quit", "game_id": game})
            self.assertEqual(store.records()[-1]["game_id"], game)

    def test_native_reopen_continued_flag_is_validated_and_records_both(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            model, application = NativeLibrary(), app(folder)
            store, ctx, attempt, save = (
                PlayHistoryStore(folder),
                context(record(), "c" * 64, "Scenario.xml"),
                digest("open"),
                digest("save"),
            )
            store.append(
                store.event(ctx, "process_confirmed", attempt_id=attempt, source="observed")
            )
            store.append(store.event(ctx, "save_made", attempt_id=attempt, save_id=save))
            params = dict(
                **ctx,
                profile_id="synthetic",
                kind="save_reopened",
                attempt_id=attempt,
                save_id=save,
                continued=True,
            )
            result = model.history_record(application, params)
            self.assertEqual(result["recorded"], 2)
            self.assertEqual(
                [e["kind"] for e in store.records()[-2:]], ["save_reopened", "continued_play"]
            )
            before = store.path.read_bytes()
            for override in (dict(kind="playable"), dict(continued="yes")):
                with self.assertRaises(HistoryError):
                    model.history_record(application, {**params, **override})
            self.assertEqual(store.path.read_bytes(), before)

    def test_mutations_require_loaded_exact_scope_and_reject_changed_edition_or_build(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            model, application = NativeLibrary(), app(folder)
            annotation = dict(
                profile_id="synthetic",
                title="Mine",
                notes="",
                archive_sha256="a" * 64,
                variant_id="b" * 64,
            )
            model.annotations_save(application, annotation)
            original = AnnotationStore(folder)._path(record()).read_bytes()
            with self.assertRaisesRegex(ValueError, "refresh details"):
                model.annotations_save(
                    application, dict(profile_id="synthetic", title="Missing scope", notes="")
                )
            application.catalogue.return_value = (replace(record(), variant_id="d" * 64),)
            with self.assertRaisesRegex(ValueError, "refresh details"):
                model.annotations_save(application, annotation)
            self.assertEqual(AnnotationStore(folder)._path(record()).read_bytes(), original)
            application.catalogue.return_value = (record(),)
            store, ctx, attempt = (
                PlayHistoryStore(folder),
                context(record(), "c" * 64),
                digest("current build open"),
            )
            store.append(
                store.event(ctx, "process_confirmed", attempt_id=attempt, source="observed")
            )
            params = dict(**ctx, profile_id="synthetic", kind="quit", attempt_id=attempt)
            application.installation.executable_sha256 = "d" * 64
            with self.assertRaisesRegex(ValueError, "refresh details"):
                model.history_record(application, params)
            self.assertEqual(len(store.records()), 1)

    def test_details_recorder_excludes_other_executable_build_events(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            application, model = app(folder), NativeLibrary()
            store = PlayHistoryStore(folder)
            for executable in ("c" * 64, "d" * 64):
                store.append(
                    store.event(
                        context(record(), executable),
                        "process_confirmed",
                        attempt_id=digest(executable),
                        source="observed",
                    )
                )
            details = model.details(application, dict(profile_id="synthetic"))
            self.assertEqual(len(details["history"]), 1)
            self.assertEqual(details["history"][0]["game_executable_sha256"], "c" * 64)

    def test_collection_saved_projection_never_takes_frontend_url(self):
        from smr_launcher.collection import RemoteMap, SavedCatalogue

        model = NativeLibrary()
        remote = RemoteMap("Synthetic.7z", 100, "a" * 40)
        saved = SavedCatalogue((remote,), "2026-10-11T00:00:00+00:00")
        with (
            patch("smr_launcher.native_backend.read_cached_catalogue", return_value=saved),
            patch("smr_launcher.native_backend.fetch_catalogue") as fetch,
        ):
            result = model.collection("/synthetic-not-written")
        self.assertEqual(result["records"][0]["name"], remote.name)
        self.assertTrue(result["records"][0]["source_url"].startswith("https://archive.org/"))
        fetch.assert_not_called()


class NativeProtocolTests(TestCase):
    def test_real_hidden_owner_startup_protocol_and_close_with_isolated_fakes(self):
        import json
        import tkinter as tk

        bootstrap = dict(
            app=None,
            monitor=None,
            community={},
            imported_names=set(),
            language="en",
            automatic=False,
            pending=None,
            pending_manual=False,
            update_status="Synthetic update state",
            monitor_error=False,
            discovery_error="Synthetic game unavailable",
        )
        requests = io.BytesIO(
            b'{"id":"snapshot","method":"snapshot","params":{}}\n'
            b'{"id":"prefs","method":"prefs","params":{}}\n'
            b'{"id":"quit","method":"workflow","params":{"action":"quit"}}\n'
        )
        output = io.StringIO()
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            with (
                patch("pathlib.Path.home", return_value=Path(folder)),
                patch.object(NativeBackendWindow, "_bootstrap", return_value=bootstrap),
                patch(
                    "smr_launcher.gui.LauncherApplication.discover",
                    side_effect=AssertionError("Live discovery forbidden"),
                ),
                patch(
                    "smr_launcher.gui.CrashMonitor",
                    side_effect=AssertionError("Live monitor forbidden"),
                ),
                patch(
                    "smr_launcher.native_backend.fetch_catalogue",
                    side_effect=AssertionError("Network forbidden"),
                ),
                patch(
                    "smr_launcher.gui.fetch_community_index",
                    side_effect=AssertionError("Network forbidden"),
                ),
                patch("smr_launcher.native_backend.list_voices", return_value=()),
            ):
                try:
                    owner = NativeBackendWindow(requests, output)
                except tk.TclError as exc:
                    if not any(
                        message in str(exc).lower()
                        for message in ("no display name", "couldn't connect to display")
                    ):
                        raise
                    self.skipTest("Native Tk display unavailable: " + str(exc))
                self.assertEqual(owner.root.state(), "withdrawn")
                expired = []

                def watchdog():
                    expired.append(True)
                    owner._finish_close()

                owner.root.after(3000, watchdog)
                owner.run()
                self.assertFalse(expired, "Synthetic owner did not complete its lifecycle")
                self.assertTrue(owner.closed)
                self.assertTrue(owner._native_input_ended.wait(1))
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(any(m.get("event") == "ready" for m in messages))
        self.assertTrue(any(m.get("event") == "closed" for m in messages))
        snapshot = next(m for m in messages if m.get("id") == "snapshot")
        self.assertTrue(snapshot["ok"])
        self.assertFalse(snapshot["data"]["available"])
        self.assertEqual(snapshot["data"]["maps"], [])
        prefs = next(m for m in messages if m.get("id") == "prefs")
        self.assertTrue(prefs["ok"])
        self.assertEqual(prefs["data"]["language"], "en")

    def test_oversized_response_returns_structured_error_without_disconnect(self):
        import json
        from threading import Lock

        w = window()
        w._native_output = io.StringIO()
        w._native_output_lock = Lock()
        w.close_when_idle = False
        with patch("smr_launcher.native_backend.MAX_RESPONSE_BYTES", 300):
            NativeBackendWindow._emit_native(
                w, dict(id="large", ok=True, data=dict(raw_text="x" * 1000))
            )
        response = json.loads(w._native_output.getvalue())
        self.assertEqual(response["id"], "large")
        self.assertFalse(response["ok"])
        self.assertIn("display limit", response["error"])
        self.assertFalse(w.close_when_idle)

    def test_reader_validates_envelopes_and_signals_eof_without_launch(self):
        w = window()
        stream = io.StringIO('{"id":"1","method":"snapshot","params":{}}\n[]\n')
        w._read_native_requests(stream)
        self.assertEqual(w._native_requests.get_nowait()["method"], "snapshot")
        self.assertEqual(w._native_requests.get_nowait()["method"], "invalid")
        self.assertIsNone(w._native_requests.get_nowait())
        self.assertTrue(w._native_input_ended.is_set())

    def test_oversized_line_is_drained_before_next_request(self):
        w = window()
        stream = io.StringIO("x" * 200 + '\n{"id":"2","method":"prefs"}\n')
        with patch("smr_launcher.native_backend.MAX_REQUEST_BYTES", 100):
            w._read_native_requests(stream)
        self.assertEqual(w._native_requests.get_nowait()["method"], "invalid")
        self.assertEqual(w._native_requests.get_nowait()["id"], "2")

    def test_busy_defers_mutation_and_same_request_replays_without_execution(self):
        from collections import OrderedDict

        w = window()
        w._native_completed = OrderedDict()
        request = dict(
            id="mutation", method="workflow", params=dict(action="play", profile_id="synthetic")
        )
        w._native_requests.put(request)
        w.busy = True
        w._native_dispatch = Mock()
        w._native_tick()
        w._native_dispatch.assert_not_called()
        w.busy = False
        w._native_reply(request, True, dict(accepted=True))
        w._native_tick()
        w._native_dispatch.assert_not_called()
        self.assertEqual(w._emit_native.call_args.args[0]["data"], dict(accepted=True))
        w._native_requests.put({**request, "params": dict(action="setup")})
        w._native_tick()
        self.assertFalse(w._emit_native.call_args.args[0]["ok"])

    def test_snapshot_heavy_reads_are_deferred_to_worker_and_reply_has_status(self):
        from threading import Thread, get_ident
        from collections import OrderedDict

        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            w = window()
            w._native_completed = OrderedDict()
            w.app = app(folder)
            threads = []
            w.app.map_metadata.side_effect = lambda r: threads.append(get_ident()) or MapMetadata()
            work = []
            w._background = lambda operation, completed, **kwargs: work.append(
                (operation, completed)
            )
            w._native_dispatch(dict(id="snapshot", method="snapshot", params={}))
            self.assertTrue(w.busy)
            w.app.map_metadata.assert_not_called()
            result = []
            worker = Thread(target=lambda: result.append(work[0][0]()))
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertNotEqual(threads, [get_ident()])
            work[0][1](True, result[0])
            self.assertFalse(w.busy)
            response = w._emit_native.call_args.args[0]
            self.assertTrue(response["ok"])
            self.assertTrue(response["data"]["enrolled"])
            self.assertEqual(response["data"]["maps"][0]["id"], "synthetic")

    def test_contextual_exit_offer_is_once_and_checks_exact_identities(self):
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            w = window()
            w.app = app(folder)
            w.status = Mock()
            store, ctx = PlayHistoryStore(folder), context(record(), "c" * 64)
            attempt = digest("exit open")
            store.append(
                store.event(ctx, "process_confirmed", attempt_id=attempt, source="observed")
            )
            w._background = lambda operation, completed, **kwargs: completed(True, operation())
            event = {**ctx, "attempt_id": attempt}
            w._history_exit_prompt(event)
            w._history_exit_prompt(event)
            emitted = [
                call.args[0]
                for call in w._emit_native.call_args_list
                if call.args[0].get("event") == "history.exit"
            ]
            self.assertEqual(len(emitted), 1)
            self.assertEqual(emitted[0]["data"]["attempt_id"], attempt)
            self.assertEqual(len(store.records()), 2)
            w._history_exit_prompt({**event, "archive_sha256": "d" * 64})
            self.assertEqual(len(store.records()), 2)

    def test_shutdown_keeps_backend_alive_until_native_input_eof(self):
        fake = SimpleNamespace(run=Mock(), _native_input_ended=Mock())
        order = []
        fake.run.side_effect = lambda: order.append("closed event and Tk lifecycle")
        fake._native_input_ended.wait.side_effect = lambda: order.append("native pipe EOF")
        with patch("smr_launcher.native_backend.NativeBackendWindow", return_value=fake):
            main()
        self.assertEqual(order, ["closed event and Tk lifecycle", "native pipe EOF"])

    def test_tk_quit_requests_native_review_and_approved_or_eof_quit_uses_existing_guard(self):
        w = window()
        with patch("smr_launcher.native_backend.LauncherWindow._close") as guarded_close:
            w._close()
            guarded_close.assert_not_called()
            self.assertEqual(
                w._emit_native.call_args.args[0], dict(event="close.requested", data={})
            )
            w._native_workflow(dict(action="quit"))
            self.assertTrue(w._native_parent_approved_close)
            w.root.after_idle.call_args.args[0]()
            guarded_close.assert_called_once_with()
        w = window()
        w._native_input_ended.set()
        with patch("smr_launcher.native_backend.LauncherWindow._close") as guarded_close:
            w._close()
            guarded_close.assert_called_once_with()
        w._emit_native.assert_not_called()

    def test_cancelled_guarded_close_resets_native_approval_for_future_drafts(self):
        w = window()
        w._native_parent_approved_close = True
        w._native_shutdown_started = True
        w.close_when_idle = False
        w.busy = True
        w._native_tick()
        self.assertTrue(w._native_parent_approved_close)
        w.busy = False
        w._native_tick()
        self.assertFalse(w._native_parent_approved_close)
        self.assertTrue(
            any(
                call.args[0].get("event") == "close.cancelled"
                for call in w._emit_native.call_args_list
            )
        )
        with patch("smr_launcher.native_backend.LauncherWindow._close") as guarded_close:
            w._close()
            guarded_close.assert_not_called()
        self.assertEqual(w._emit_native.call_args.args[0]["event"], "close.requested")

    def test_advanced_red_close_only_hides_tools_and_output_disconnect_allows_close(self):
        from threading import Lock

        w = window()
        w._show = Mock()
        w._native_workflow(dict(action="preferences"))
        name, red_close = w.root.protocol.call_args.args
        self.assertEqual(name, "WM_DELETE_WINDOW")
        with patch("smr_launcher.native_backend.LauncherWindow._close") as guarded_close:
            red_close()
            guarded_close.assert_not_called()
        self.assertFalse(w._native_advanced)
        w.root.withdraw.assert_called_once_with()
        w._native_output, w._native_output_lock = Mock(), Lock()
        w._native_output.write.side_effect = BrokenPipeError("Synthetic disconnected parent")
        NativeBackendWindow._emit_native(w, dict(event="status", data={}))
        self.assertTrue(w._native_disconnected)
        with patch("smr_launcher.native_backend.LauncherWindow._close") as guarded_close:
            w._close()
            guarded_close.assert_called_once_with()

    def test_quit_and_play_use_existing_guarded_paths(self):
        w = window()
        w._play, w._close = Mock(), Mock()
        w.app = SimpleNamespace(
            profiles=SimpleNamespace(state_file=SimpleNamespace(exists=lambda: True))
        )
        w._native_workflow(dict(action="play", profile_id="synthetic"))
        w._play.assert_called_once_with("synthetic")
        w._native_workflow(dict(action="quit"))
        self.assertEqual(w.root.after_idle.call_args.args, (w._close,))

    def test_download_requires_enrollment_and_trusted_catalogue(self):
        from smr_launcher.collection import RemoteMap

        w = window()
        w.app = SimpleNamespace(
            profiles=SimpleNamespace(state_file=SimpleNamespace(exists=lambda: True))
        )
        w._download_selected = Mock()
        with self.assertRaises(ValueError):
            w._native_workflow(
                dict(action="download", name="Untrusted.7z", source_url="https://evil.invalid")
            )
        remote = RemoteMap("Synthetic.7z", 100, "a" * 40)
        w.native_library.remote_records = (remote,)
        w._native_workflow(dict(action="download", name=remote.name))
        w._download_selected.assert_called_once_with()
        self.assertEqual(w.selected_remote, remote)

    def test_setup_only_shows_existing_consent_view_and_choose_path_keeps_guard(self):
        w = window()
        w.app = Mock()
        w._show, w.setup, w._submit, w._library_chosen = Mock(), Mock(), Mock(), Mock()
        w._native_workflow(dict(action="setup", stock_verified=True))
        w._show.assert_called_once_with("setup")
        w.setup.assert_not_called()
        w._native_workflow(dict(action="choose-library", path="/synthetic/steamapps"))
        operation, completed = w._submit.call_args.args[1:]
        with patch(
            "smr_launcher.native_backend.LauncherApplication.choose_steam_library",
            return_value="checked",
        ) as choose:
            self.assertEqual(operation(), "checked")
        choose.assert_called_once_with(Path("/synthetic/steamapps"))
        self.assertEqual(completed, w._library_chosen)

    def test_activity_reply_is_object_and_monitor_history_failure_stays_explicit(self):
        from collections import OrderedDict

        w = window()
        w._native_completed = OrderedDict()
        w.activity = SimpleNamespace(records=lambda: (dict(action="Synthetic", state="Complete"),))
        w._background = lambda operation, completed, **kwargs: completed(True, operation())
        w._native_dispatch(dict(id="activity", method="activity", params={}))
        self.assertEqual(
            w._emit_native.call_args.args[0]["data"]["records"][0]["action"], "Synthetic"
        )
        with TemporaryDirectory(dir=Path(gettempdir()).resolve()) as folder:
            model, application = NativeLibrary(), app(folder)
            snapshot = model.snapshot(
                application, history_error="Synthetic monitor history failure"
            )
            self.assertEqual(snapshot[0]["history_error"], "Synthetic monitor history failure")

    def test_preferences_reuse_existing_public_translation_catalogue(self):
        from smr_launcher.language import TRANSLATIONS

        w = window()
        w.language_preferences = SimpleNamespace(language="fr", voice="")
        w.auto_updates = SimpleNamespace(get=lambda: False)
        w.voices = ()
        preferences = w._native_preferences()
        self.assertEqual(preferences["strings"], TRANSLATIONS["fr"])
        self.assertEqual(preferences["strings"]["Map Library"], "Bibliothèque de cartes")
        preferences["strings"]["Map Library"] = "Changed copy"
        self.assertEqual(TRANSLATIONS["fr"]["Map Library"], "Bibliothèque de cartes")
        w.language_preferences.save = Mock()
        saved = w._native_save_preferences(dict(language="de"))()
        self.assertEqual(saved["strings"]["Map Library"], TRANSLATIONS["de"]["Map Library"])

    def test_preferences_prevalidate_all_choices_before_any_write(self):
        w = window()
        w._native_preferences = Mock(
            return_value=dict(
                language="en",
                voice="",
                automatic_updates=False,
                crash_enabled=True,
                crash_automatic=False,
                resource_protection=True,
            )
        )
        w.language_preferences = Mock()
        w.update_preferences = Mock()
        w.crash_monitor = None
        with self.assertRaises(ValueError):
            w._native_save_preferences(dict(language="fr", crash_enabled=True))
        w.language_preferences.save.assert_not_called()
        with self.assertRaises(ValueError):
            w._native_save_preferences(dict(automatic_updates="yes"))
        w.update_preferences.set_automatic.assert_not_called()
