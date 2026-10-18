"""Global native tools use injected services; never discover or start a real owner."""

import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from smr_launcher.collection import RemoteMap
from smr_launcher.native_backend import NativeBackendWindow
from smr_launcher.native_tools import NativeTools
from smr_launcher.updates import Release


def owner(directory):
    app = SimpleNamespace(
        library=Path(directory),
        profiles=SimpleNamespace(state_file=Mock(exists=Mock(return_value=True))),
        _stopped=Mock(),
        import_archive=Mock(),
        download_archive=Mock(),
        setup=Mock(),
    )
    result = SimpleNamespace(
        app=app,
        busy=False,
        initializing=False,
        available_update=None,
        pending_update=None,
        pending_update_manual=False,
        auto_updates=Mock(get=Mock(return_value=False)),
        crash_monitor=None,
        update_library=Path(directory),
        close_when_idle=False,
        _controls=Mock(),
        _background=Mock(),
        _render_gallery=Mock(),
        _library_chosen=Mock(),
        root=Mock(),
        _close=Mock(),
        native_library=SimpleNamespace(remote_records=()),
    )
    return result


def finish(window, ok=True, value=None):
    operation, callback = window._background.call_args.args
    callback(ok, operation() if ok and value is None else value)


class NativeToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.owner = owner(self.temp.name)
        self.tools = NativeTools(self.owner)

    def test_missing_consent_and_stale_release_refuse_without_work(self):
        self.owner.available_update = Release(
            "99.0.0", "a.zip", "synthetic", "a" * 64, 5, "synthetic"
        )
        for params in ({}, {"consent": "true"}, {"consent": True, "sha256": "b" * 64}):
            with self.assertRaises(ValueError):
                self.tools.start("update.stage", params)
        self.owner._background.assert_not_called()

    def test_captured_release_version_and_size_must_still_match(self):
        release = Release("99.0.0", "app.zip", "synthetic", "a" * 64, 5, "synthetic")
        self.owner.available_update = release
        for changed in (dict(version="98.0.0"), dict(size=6)):
            with self.assertRaises(ValueError):
                self.tools.start(
                    "update.stage", dict(consent=True, sha256=release.sha256, **changed)
                )
        self.owner._background.assert_not_called()

    def test_running_game_refuses_before_collection_network_read(self):
        remote = RemoteMap("A.7z", 5, "a" * 40)
        self.owner.native_library.remote_records = (remote,)
        self.owner.app._stopped.side_effect = ValueError("synthetic running game refusal")
        with patch("smr_launcher.native_tools.download_all_maps") as download:
            self.tools.start(
                "collection.import",
                dict(
                    consent=True,
                    records=[dict(name=remote.name, size=remote.size, sha1=remote.sha1)],
                ),
            )
            operation, callback = self.owner._background.call_args.args
            with self.assertRaises(ValueError) as error:
                operation()
            callback(False, error.exception)
        download.assert_not_called()
        self.assertEqual(self.tools.job["state"], "failed")

    def test_cancel_before_update_work_never_downloads(self):
        release = Release("99.0.0", "app.zip", "synthetic", "a" * 64, 5, "synthetic")
        self.owner.available_update = release
        with patch("smr_launcher.native_tools.download_release") as download:
            self.tools.start("update.stage", dict(consent=True, sha256=release.sha256))
            self.tools.cancel(self.tools.job["job_id"])
            finish(self.owner)
        download.assert_not_called()
        self.assertEqual(self.tools.job["state"], "cancelled")

    def test_collection_identity_refused_and_no_download(self):
        remote = RemoteMap("Example.7z", 5, "a" * 40)
        self.owner.native_library.remote_records = (remote,)
        for records in (
            [dict(name=remote.name, sha1="b" * 40, size=5)],
            [dict(name=remote.name, sha1=remote.sha1, size=6)],
            [],
        ):
            with self.assertRaises(ValueError):
                self.tools.start("collection.import", dict(consent=True, records=records))
        self.owner._background.assert_not_called()

    def test_collection_partial_failure_and_cancel_preserve_outcomes(self):
        records = (RemoteMap("A.7z", 5, "a" * 40), RemoteMap("B.7z", 6, "b" * 40))
        self.owner.native_library.remote_records = records
        selection = [dict(name=r.name, size=r.size, sha1=r.sha1) for r in records]

        def download(selected, directory, progress, cancelled, *, importer, downloader):
            self.assertIs(downloader, self.owner.app.download_archive)
            self.assertTrue(self.owner.app._stopped.called)
            progress(records[0], "Imported", 5, 5, "")
            progress(records[1], "Failed", 0, 6, "/private/raw-token-crash.ips")
            return 1, 1, 0

        with patch("smr_launcher.native_tools.download_all_maps", side_effect=download):
            started = self.tools.start("collection.import", dict(consent=True, records=selection))
            with self.assertRaises(ValueError):
                self.tools.start("update.check", {})
            self.tools.cancel(started["job_id"])
            self.assertTrue(self.tools.cancelled.is_set())
            self.assertEqual(self.tools.status()["job"]["state"], "running")
            finish(self.owner)
        status = self.tools.status()["job"]
        self.assertEqual(status["items"]["A.7z"]["state"], "Imported")
        self.assertEqual(status["items"]["B.7z"]["state"], "Failed")
        self.assertIn("1 failed", status["message"])
        self.assertNotIn("raw-token", json.dumps(status))
        self.owner._render_gallery.assert_called_once()
        with self.assertRaises(ValueError):
            self.tools.cancel(started["job_id"])
        with self.assertRaises(ValueError):
            self.tools.status("unknown")

    def test_setup_requires_consent_and_uses_existing_operation(self):
        with self.assertRaises(ValueError):
            self.tools.start("library.setup", {})
        self.tools.start("library.setup", dict(consent=True))
        self.assertFalse(self.tools.job["cancellable"])
        with self.assertRaises(ValueError):
            self.tools.cancel(self.tools.job["job_id"])
        finish(self.owner)
        self.owner.app.setup.assert_called_once()

    def test_update_cancel_after_download_never_stages_or_quits(self):
        release = Release("99.0.0", "app.zip", "synthetic", "a" * 64, 5, "synthetic")
        self.owner.available_update = release
        with (
            patch("smr_launcher.native_tools.trusted_team_from_bundle", return_value="TEAM"),
            patch(
                "smr_launcher.native_tools.current_app_bundle", return_value=Path("/synthetic/app")
            ),
            patch(
                "smr_launcher.native_tools.download_release",
                side_effect=lambda *args: (self.tools.cancelled.set(), Path("/synthetic/archive"))[
                    1
                ],
            ) as download,
            patch("smr_launcher.native_tools.stage_release") as stage,
            patch("smr_launcher.native_tools.prepare_install") as prepare,
        ):
            job = self.tools.start("update.stage", dict(consent=True, sha256=release.sha256))
            self.assertEqual(job["job_id"], self.tools.job["job_id"])
            finish(self.owner)
            download.assert_called_once_with(release, self.owner.update_library / "updates")
            stage.assert_not_called()
            prepare.assert_not_called()
        self.assertEqual(self.tools.job["state"], "cancelled")
        self.assertIsNone(self.owner.pending_update)
        self.owner._close.assert_not_called()

    def test_update_publisher_identity_and_install_intent_are_preserved(self):
        release = Release("99.0.0", "app.zip", "synthetic", "a" * 64, 5, "synthetic")
        self.owner.available_update = release
        with (
            patch("smr_launcher.native_tools.trusted_team_from_bundle", return_value="TEAM"),
            patch(
                "smr_launcher.native_tools.current_app_bundle", return_value=Path("/synthetic/app")
            ),
            patch(
                "smr_launcher.native_tools.download_release",
                return_value=Path("/synthetic/archive"),
            ),
            patch(
                "smr_launcher.native_tools.stage_release", return_value=Path("/synthetic/staged")
            ) as stage,
            patch(
                "smr_launcher.native_tools.prepare_install", return_value=Path("/synthetic/pending")
            ) as prepare,
        ):
            self.tools.start("update.stage", dict(consent=True, sha256=release.sha256))
            finish(self.owner)
            stage.assert_called_once_with(
                Path("/synthetic/archive"), release, self.owner.update_library, "TEAM"
            )
            self.assertTrue(prepare.call_args.kwargs["manual_install"])
        self.assertEqual(self.owner.pending_update, Path("/synthetic/pending"))
        self.assertTrue(self.owner.pending_update_manual)
        self.owner._close.assert_not_called()

    def test_failures_never_project_raw_diagnostics_or_retry(self):
        self.owner.crash_monitor = SimpleNamespace(
            poll_once=Mock(), status="RAW diagnostic /private/a.ips", root=Path("/private/raw")
        )
        self.tools.start("crash.reconcile", dict(consent=True))
        finish(self.owner, False, RuntimeError("raw secret token /private/a.ips"))
        payload = json.dumps(self.tools.status())
        for forbidden in ("secret", "RAW", "a.ips", "/private"):
            self.assertNotIn(forbidden, payload)
        self.assertEqual(self.tools.job["state"], "failed")
        self.assertEqual(self.owner._background.call_count, 1)

    def test_crash_status_only_projects_count_allowlist(self):
        store = Mock(
            pending=Mock(
                return_value=[
                    dict(publication=dict(status="unknown"), payload=dict(raw="SECRET")),
                    dict(publication=dict(status="invented")),
                ]
            )
        )
        self.owner.crash_monitor = SimpleNamespace(store=store)
        self.tools.start("crash.status", {})
        finish(self.owner)
        payload = self.tools.status()
        self.assertEqual(payload["crash_counts"]["unknown"], 1)
        self.assertNotIn("SECRET", json.dumps(payload))
        self.assertNotIn("invented", json.dumps(payload))

    def test_cancelled_collection_close_waits_for_completion_once(self):
        remote = RemoteMap("A.7z", 5, "a" * 40)
        self.owner.native_library.remote_records = (remote,)
        with patch("smr_launcher.native_tools.download_all_maps", return_value=(0, 0, 1)):
            started = self.tools.start(
                "collection.import",
                dict(
                    consent=True,
                    records=[dict(name=remote.name, size=remote.size, sha1=remote.sha1)],
                ),
            )
            self.owner.close_when_idle = True
            self.tools.cancel(started["job_id"])
            self.owner._close.assert_not_called()
            finish(self.owner)
        self.assertEqual(self.tools.job["state"], "cancelled")
        self.assertFalse(self.owner.busy)
        self.owner.root.after_idle.assert_called_once_with(self.owner._close)
        self.assertEqual(self.owner._background.call_count, 1)

    def test_discovery_calls_existing_discover_and_returns_application(self):
        self.owner.app = None
        application = object()
        with patch(
            "smr_launcher.native_tools.LauncherApplication.discover", return_value=application
        ) as discover:
            self.tools.start("library.discover", dict(consent=True))
            finish(self.owner)
        discover.assert_called_once_with()
        self.owner._library_chosen.assert_called_once_with(application)

    def test_automatic_staging_retains_nonmanual_intent_after_opt_out(self):
        release = Release("99.0.0", "app.zip", "synthetic", "a" * 64, 5, "synthetic")
        self.owner.available_update = release
        self.owner.auto_updates.get.return_value = True

        def downloaded(*args):
            self.owner.auto_updates.get.return_value = False
            return Path("/synthetic/archive")

        with (
            patch("smr_launcher.native_tools.trusted_team_from_bundle", return_value="TEAM"),
            patch(
                "smr_launcher.native_tools.current_app_bundle", return_value=Path("/synthetic/app")
            ),
            patch("smr_launcher.native_tools.download_release", side_effect=downloaded),
            patch(
                "smr_launcher.native_tools.stage_release", return_value=Path("/synthetic/staged")
            ),
            patch(
                "smr_launcher.native_tools.prepare_install", return_value=Path("/synthetic/pending")
            ) as prepare,
        ):
            self.tools.start(
                "update.stage", dict(consent=True, sha256=release.sha256, manual=False)
            )
            finish(self.owner)
            self.assertFalse(prepare.call_args.kwargs["manual_install"])
        self.assertFalse(self.owner.pending_update_manual)
        self.assertFalse(self.owner.auto_updates.get())
        self.owner._close.assert_not_called()

    def test_auto_stage_is_not_started_after_preference_opt_out(self):
        release = Release("99.0.0", "app.zip", "synthetic", "a" * 64, 5, "synthetic")
        self.owner.auto_updates.get.return_value = True
        with patch("smr_launcher.native_tools.fetch_latest_release", return_value=release):
            self.tools.start("update.check", {})
            finish(self.owner)
        callback = self.owner.root.after_idle.call_args.args[0]
        self.owner.auto_updates.get.return_value = False
        callback()
        self.assertEqual(self.owner._background.call_count, 1)
        self.assertEqual(self.tools.job["action"], "update.check")

    def test_completion_callback_failure_is_unknown_and_not_retried(self):
        self.owner.app.setup.return_value = "original-game"
        self.owner._render_gallery.side_effect = RuntimeError("synthetic local display failure")
        self.tools.start("library.setup", dict(consent=True))
        finish(self.owner)
        self.assertEqual(self.tools.job["state"], "unknown")
        self.owner.app.setup.assert_called_once()
        self.assertEqual(self.owner._background.call_count, 1)
        self.assertFalse(self.owner.busy)

    def test_update_check_is_current_and_unsafe_job_cancel_refuses(self):
        with patch("smr_launcher.native_tools.fetch_latest_release", return_value=None):
            started = self.tools.start("update.check", {})
            with self.assertRaises(ValueError):
                self.tools.cancel(started["job_id"])
            finish(self.owner)
        self.assertIn("No newer", self.tools.job["message"])


class NativeControlTests(unittest.TestCase):
    def window(self):
        w = object.__new__(NativeBackendWindow)
        w.closed = False
        w.busy = True
        w.initializing = False
        w.close_when_idle = False
        w._native_inflight = "ordinary"
        w._native_completed = {}
        w._native_controls = Queue(64)
        w._native_requests = Queue(64)
        w._native_queued_ids = set()
        w._native_queue_lock = Lock()
        w._native_last_status = None
        w._native_status = Mock(return_value={})
        w._emit_native = Mock()
        w.root = Mock()
        w.native_tools = SimpleNamespace(
            status=Mock(return_value=dict(job={})),
            cancel=Mock(return_value=dict(cancel_requested=True)),
        )
        return w

    def test_controls_while_busy_never_clear_inflight_or_busy(self):
        w = self.window()
        for method in ("tools.status", "tools.cancel"):
            w._queue_native_request(dict(id=method, method=method, params=dict(job_id="job")))
        w._native_tick()
        self.assertEqual(w._native_inflight, "ordinary")
        self.assertTrue(w.busy)
        self.assertEqual(w._native_completed, {})
        w.native_tools.status.assert_called_once_with("job")
        w.native_tools.cancel.assert_called_once_with("job")

    def test_overlapping_control_is_not_an_ordinary_reply(self):
        w = self.window()
        w._native_controls.put(
            dict(id="ordinary", method="tools.cancel", params=dict(job_id="job"))
        )
        w._native_tick()
        w.native_tools.cancel.assert_not_called()
        self.assertEqual(w._native_inflight, "ordinary")
        self.assertEqual(
            w._emit_native.call_args_list[0].args[0]["event"], "tools.control_rejected"
        )

    def test_queued_mutation_identity_cannot_be_used_for_control(self):
        w = self.window()
        w._queue_native_request(dict(id="queued", method="tools.start", params={}))
        w._queue_native_request(dict(id="queued", method="tools.status", params={}))
        w._native_tick()
        w.native_tools.status.assert_not_called()
        self.assertEqual(w._native_requests.qsize(), 1)
        self.assertTrue(w.busy)

    def test_start_receipt_deduplicates_without_starting_another_job(self):
        w = self.window()
        w.busy = False
        w._native_inflight = None
        w.native_tools.start = Mock(return_value=dict(job_id="synthetic"))
        request = dict(id="one-start", method="tools.start", params=dict(action="update.check"))
        w._queue_native_request(request)
        w._native_tick()
        w._queue_native_request(request)
        w._native_tick()
        w.native_tools.start.assert_called_once_with("update.check", dict(action="update.check"))
        self.assertEqual(w._native_inflight, None)
        self.assertNotIn("one-start", w._native_queued_ids)
        w._queue_native_request(
            dict(id="one-start", method="tools.start", params=dict(action="library.setup"))
        )
        w._native_tick()
        self.assertEqual(w.native_tools.start.call_count, 1)
        self.assertFalse(w._emit_native.call_args.args[0]["ok"])

    def test_duplicate_queued_read_keeps_one_owner_and_rejects_colliding_control(self):
        w = self.window()
        request = dict(id="read", method="activity", params={})
        w._queue_native_request(request)
        w._queue_native_request(request)
        self.assertEqual(w._native_requests.qsize(), 1)
        self.assertEqual(w._emit_native.call_args.args[0]["event"], "tools.request_rejected")
        w._queue_native_request(dict(id="read", method="tools.cancel", params=dict(job_id="job")))
        w._native_tick()
        w.native_tools.cancel.assert_not_called()
        self.assertIn("read", w._native_queued_ids)
        w._native_reply(w._native_requests.get_nowait(), True, {})
        self.assertNotIn("read", w._native_queued_ids)
        self.assertEqual(
            sum(call.args[0].get("id") == "read" for call in w._emit_native.call_args_list), 1
        )

    def test_native_warning_and_operation_error_do_not_echo_raw_payload(self):
        w = self.window()
        w._operation_warning("private title", "SECRET raw crash /private/trace.ips")
        w._operation_error("private title", RuntimeError("SECRET raw crash /private/trace.ips"))
        for call in w._emit_native.call_args_list:
            data = json.dumps(call.args[0])
            self.assertNotIn("SECRET", data)
            self.assertNotIn("trace.ips", data)
            self.assertEqual(call.args[0]["event"], "tools.alert")

    def test_resource_warning_preserves_unconfirmed_stop(self):
        for outcome in (
            "Railroads was stopped after exceeding the resource limit",
            "Stopping the game could not be confirmed: SECRET /private/trace.ips",
        ):
            with self.subTest(outcome=outcome):
                w = self.window()
                w._operation_warning("Railroads memory protection", outcome)
                message = w._emit_native.call_args.args[0]["data"]["message"]
                self.assertIn("confirm whether the game stopped", message)
                self.assertNotIn("stopped a runaway game", message)
                self.assertNotIn("SECRET", message)
                self.assertNotIn("trace.ips", message)

    def test_failed_update_shutdown_keeps_owner_alive_and_resets_approval(self):
        w = self.window()
        w._native_input_ended = Event()
        w._native_disconnected = False
        w._native_parent_approved_close = True
        w._native_shutdown_started = False
        w._native_update_choice = "install"
        w.pending_update = Path("/synthetic/pending")
        w.pending_update_manual = True
        w.auto_updates = Mock(get=Mock(return_value=False))
        w.update_library = Path("/synthetic/library")
        w.busy = w.bulk_active = False
        w._background = Mock()
        w._finish_close = Mock()
        w._close()
        callback = w._background.call_args.args[1]
        callback(False, RuntimeError("private raw failure"))
        w._finish_close.assert_not_called()
        self.assertFalse(w._native_parent_approved_close)
        self.assertFalse(w.closed)
        message = w._emit_native.call_args.args[0]
        self.assertEqual(message["event"], "close.cancelled")
        self.assertNotIn("private raw", json.dumps(message))
        with self.assertRaises(ValueError):
            w._native_workflow(dict(action="quit"))

    def test_disconnected_auto_opt_out_never_installs_pending_update(self):
        w = self.window()
        w._native_input_ended = Event()
        w._native_disconnected = True
        w._native_parent_approved_close = False
        w._native_update_choice = ""
        w.pending_update = Path("/synthetic/pending")
        w.pending_update_manual = False
        w.auto_updates = Mock(get=Mock(return_value=False))
        w.busy = w.bulk_active = False
        w._finish_close = Mock()
        with patch("smr_launcher.native_backend.start_install_helper") as install:
            w._close()
        install.assert_not_called()
        w._finish_close.assert_called_once()

    def test_eof_installer_failure_closes_without_hidden_dialog_or_retry(self):
        w = self.window()
        w._native_input_ended = Event()
        w._native_input_ended.set()
        w._native_disconnected = False
        w._native_parent_approved_close = False
        w._native_update_choice = "install"
        w.pending_update = Path("/synthetic/pending")
        w.pending_update_manual = True
        w.auto_updates = Mock(get=Mock(return_value=False))
        w.update_library = Path("/synthetic/library")
        w.busy = w.bulk_active = False
        w._background = Mock()
        w._finish_close = Mock()
        w._close()
        w._background.assert_called_once()
        w._background.call_args.args[1](False, RuntimeError("synthetic private failure"))
        w._finish_close.assert_called_once()
        self.assertEqual(w._native_update_choice, "")
        self.assertFalse(
            any(
                call.args[0].get("event") == "close.cancelled"
                for call in w._emit_native.call_args_list
            )
        )

    def test_parent_loss_does_not_approve_manually_staged_installation(self):
        for automatic in (False, True):
            for eof in (False, True):
                with self.subTest(automatic=automatic, eof=eof):
                    w = self.window()
                    w._native_input_ended = Event()
                    if eof:
                        w._native_input_ended.set()
                    w._native_disconnected = not eof
                    w._native_parent_approved_close = False
                    w._native_update_choice = ""
                    w.pending_update = Path("/synthetic/pending")
                    w.pending_update_manual = True
                    w.auto_updates = Mock(get=Mock(return_value=automatic))
                    w.busy = w.bulk_active = False
                    w._background = Mock()
                    w._finish_close = Mock()
                    w._close()
                    w._background.assert_not_called()
                    w._finish_close.assert_called_once()

    def test_parent_loss_preserves_saved_automatic_install_preference(self):
        w = self.window()
        w._native_input_ended = Event()
        w._native_input_ended.set()
        w._native_disconnected = False
        w._native_parent_approved_close = False
        w._native_update_choice = ""
        w.pending_update = Path("/synthetic/pending")
        w.pending_update_manual = False
        w.auto_updates = Mock(get=Mock(return_value=True))
        w.busy = w.bulk_active = False
        w._background = Mock()
        w._close()
        w._background.assert_called_once()

    def test_skip_staged_update_never_starts_installer(self):
        w = self.window()
        w._native_input_ended = Event()
        w._native_disconnected = False
        w._native_parent_approved_close = True
        w._native_shutdown_started = False
        w._native_update_choice = "skip"
        w.pending_update = Path("/synthetic/pending")
        w.pending_update_manual = True
        w.auto_updates = Mock(get=Mock(return_value=True))
        w.busy = w.bulk_active = False
        w._finish_close = Mock()
        with patch("smr_launcher.native_backend.start_install_helper") as install:
            w._close()
        install.assert_not_called()
        w._finish_close.assert_called_once()
