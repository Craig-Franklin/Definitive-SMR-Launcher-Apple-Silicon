"""Loading feedback survives real worker overlap, delays and UI lifetime changes."""

from threading import Event, get_ident
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from smr_launcher.ui_work import Activities, LatestValue
from smr_launcher.map_metadata import MapMetadata
import test_gui_performance as performance
from test_gui_performance import window, deliver


class ActivityTests(TestCase):
    def test_foreground_progress_and_overlapping_background_settle_independently(self):
        state = Activities()
        fetch = state.begin("Loading collection")
        maps = state.begin("Loading maps", 5)
        state.update(maps, "Checking maps", 2, 10)
        self.assertEqual(state.display(), ("Checking maps", 2, 10, 2))
        download = state.begin("Importing", 10)
        state.end(fetch)
        self.assertEqual(state.display(), ("Importing", None, None, 2))
        state.end(download)
        self.assertEqual(state.display(), ("Checking maps", 2, 10, 1))
        state.end(maps)
        state.update(maps, "stale", 3, 10)
        state.end(maps)
        self.assertIsNone(state.display())

    def test_worker_progress_coalesces_to_one_latest_value(self):
        slot = LatestValue()
        for n in range(10000):
            slot.put((7, n, 10000))
        self.assertEqual(slot.take(), (7, 9999, 10000))
        self.assertIsNone(slot.take())

    def test_feedback_starts_before_blocked_background_and_completion_is_on_ui(self):
        w = window()
        entered = Event()
        release = Event()
        ui = get_ident()
        called = []

        def work():
            called.append(get_ident())
            entered.set()
            release.wait(2)
            return "ready"

        def ready(ok, value):
            self.assertEqual(get_ident(), ui)
            self.assertTrue(ok)
            self.assertEqual(value, "ready")
            self.assertIsNotNone(w._activities.display())

        try:
            w._background(work, ready, label="Loading history…")
            self.assertTrue(entered.wait(1))
            self.assertEqual(w._activities.display()[0], "Loading history…")
            self.assertNotEqual(called, [ui])
            release.set()
            deliver(w)
            self.assertIsNone(w._activities.display())
        finally:
            release.set()
            w.background_results.close()

    def test_worker_error_callback_error_and_thread_start_error_clear_only_own_activity(self):
        for kind in ("operation", "callback", "start"):
            w = window()
            other = w._activity_begin("Other operation")

            def work():
                if kind == "operation":
                    raise ValueError("delayed IO failed")

            def ready(ok, value):
                if kind == "operation":
                    self.assertFalse(ok)
                if kind == "callback":
                    raise ValueError("display failed")

            if kind == "start":
                with patch("smr_launcher.gui.Thread") as thread:
                    thread.return_value.start.side_effect = RuntimeError("cannot start")
                    failed = Mock()
                    w._background(work, failed)
                    self.assertFalse(failed.call_args.args[0])
                    self.assertIsInstance(failed.call_args.args[1], RuntimeError)
            else:
                w._background(work, ready)
                if kind == "callback":
                    with self.assertRaises(ValueError):
                        deliver(w)
                else:
                    deliver(w)
            self.assertEqual(w._activities.display(), ("Other operation", None, None, 1))
            w._activity_end(other)

    def test_indicator_animates_unknown_work_then_counts_without_restarting_and_returns_ready(self):
        w = window()
        frame, label, bar = Mock(), Mock(), Mock()
        bar._activity_mode = None
        w._activity_widgets = [(frame, label, bar)]
        token = w._activity_begin("Loading maps…")
        bar.start.assert_called_once_with(20)
        w._activity_update(token, "Checking maps…", 1, 3)
        self.assertIn("1 of 3", label.configure.call_args.kwargs["text"])
        self.assertEqual(bar.configure.call_args.kwargs, {"maximum": 3, "value": 1})
        w._activity_update(token, "Checking maps…", 2, 3)
        bar.start.assert_called_once()
        w._activity_end(token)
        self.assertEqual(label.configure.call_args.kwargs["text"], "Ready")
        bar.grid_remove.assert_called_once()
        frame.winfo_exists.return_value = False
        w._activity_begin("Next task")
        self.assertEqual(w._activity_widgets, [])

    def test_localization_does_not_replace_dynamic_activity_label_with_ready(self):
        w = window()
        child = Mock()
        child._dynamic_text = True
        child.winfo_children.return_value = []
        parent = Mock()
        parent.winfo_children.return_value = [child]
        w._localize_widgets(parent)
        child.configure.assert_not_called()


class GalleryFeedbackTests(TestCase):
    def test_progress_is_visible_during_preparation_and_stale_progress_cannot_revive_cancelled_load(
        self,
    ):
        w = window()
        w.view = "maps"
        w.grid_frame = Mock()
        w.gallery_count = Mock()
        w._begin_gallery_batches = Mock()
        second = Event()
        release = Event()
        records = (
            performance.GalleryTests().item("One").record,
            performance.GalleryTests().item("Two").record,
        )

        def metadata(record):
            if record is records[1]:
                second.set()
                release.wait(2)
            return MapMetadata()

        w.app = SimpleNamespace(
            catalogue=lambda: records,
            map_metadata=metadata,
            verification_for=lambda _: None,
            map_icon=lambda _: None,
        )
        try:
            w._render_gallery()
            self.assertTrue(second.wait(1))
            w._poll_gallery_progress()
            self.assertEqual(w._activities.display()[:3], ("Checking maps…", 1, 2))
            self.assertIn("1 of 2", w.gallery_count.configure.call_args.kwargs["text"])
            w._cancel_gallery()
            w._poll_gallery_progress()
            self.assertIsNone(w._activities.display())
            release.set()
            deliver(w)
            w._begin_gallery_batches.assert_not_called()
        finally:
            release.set()
            w.background_results.close()

    def test_empty_library_and_multiple_render_batches_stop_feedback_only_at_completion(self):
        for count in (0, 25):
            w = window()
            w.view = "maps"
            w.canvas = Mock()
            w.canvas.yview.return_value = (0, 1)
            w.grid_frame = Mock()
            w.grid_frame.winfo_children.return_value = []
            w.gallery_count = Mock()
            w._selection_details = Mock()
            w._create_gallery_card = Mock()
            w.secondary = "gray"
            w._gallery_snapshot = tuple(
                performance.GalleryTests().item(f"Map{n}") for n in range(count)
            )
            w.search = Mock()
            w.search.get.return_value = ""
            w.map_filter = Mock()
            w.map_filter.get.return_value = "All maps"
            w.sort_order = Mock()
            w.sort_order.get.return_value = "Name A–Z"
            w._gallery_activity = w._activity_begin("Loading maps…")
            with patch("smr_launcher.gui.ttk"):
                w._begin_gallery_batches(0)
                if count:
                    self.assertIsNotNone(w._activities.display())
                    self.assertLess(w._gallery_index, count)
                while w._gallery_index < count:
                    w._gallery_batch(0)
            self.assertEqual(w._create_gallery_card.call_count, count)
            self.assertIsNone(w._activities.display())
            self.assertEqual(
                w.gallery_count.configure.call_args.kwargs["text"],
                f"{count} shown · {count} imported",
            )

    def test_snapshot_and_render_failures_clear_loading_and_show_error(self):
        w = window()
        w.view = "maps"
        w.grid_frame = Mock()
        w.gallery_count = Mock()
        w.app.catalogue.side_effect = OSError("unreadable catalogue")
        w._render_gallery()
        deliver(w)
        self.assertIsNone(w._activities.display())
        self.assertEqual(w.gallery_count.configure.call_args.kwargs["text"], "Could not load maps")
        w._gallery_activity = w._activity_begin("Displaying maps…")
        w._render_gallery_batch = Mock(side_effect=RuntimeError("invalid card"))
        w._gallery_batch(w._gallery_generation)
        self.assertIsNone(w._activities.display())
        self.assertIn("invalid card", w.status.set.call_args.args[0])


class RegionalFeedbackTests(TestCase):
    def test_thread_start_failure_settles_gallery_collection_and_lane_for_retry(self):
        w = window()
        w.view = "maps"
        w.grid_frame = Mock()
        w.gallery_count = Mock()
        w.canvas = Mock()
        w._loading_panel = Mock(side_effect=lambda *_: tuple(Mock() for _ in range(4)))
        with patch("smr_launcher.gui.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("no thread")
            for _ in range(2):
                w._render_gallery()
                self.assertFalse(w._gallery_loading)
                self.assertIsNone(w._activities.display())
                self.assertEqual(
                    w._gallery_panel[1].configure.call_args.kwargs["text"], "Could not load maps"
                )
            self.assertEqual(thread.return_value.start.call_count, 2)
            w.view = "collection"
            w.remote_records = ()
            w.collection_list = Mock()
            for _ in range(2):
                w._refresh_collection()
                self.assertFalse(w.collection_refreshing)
                self.assertIsNone(w._activities.display())
            finished = Mock()
            for _ in range(2):
                w._background_latest("test", Mock(), finished)
            self.assertNotIn("test", w._maintenance_running)
            self.assertEqual(finished.call_count, 2)
            self.assertFalse(finished.call_args.args[0])

    def test_thread_start_failure_settles_startup_and_protection(self):
        w = window()
        w.initializing = True
        w.auto_updates = Mock()
        w.auto_updates.get.return_value = False
        w.loading_shell = Mock()
        w._menu = Mock()
        w._layout = Mock()
        w._show = Mock()
        w._initial_view = Mock(return_value="maps")
        w._startup_activity = w._activity_begin("Starting…")
        with patch("smr_launcher.gui.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("no thread")
            w._begin_startup()
            self.assertFalse(w.initializing)
            self.assertIsNone(w._activities.display())
            self.assertIn("Startup needs inspection", w.status.set.call_args.args[0])
            w._start_crash_monitor_async()
            self.assertFalse(w.busy)
            self.assertIsNone(w.crash_monitor)
            self.assertIsNone(w._activities.display())

    def test_foreground_start_failure_uses_specific_failure_delivery(self):
        w = window()
        failed = Mock()
        operation = Mock()
        with patch("smr_launcher.gui.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("no thread")
            w._submit("Loading details…", operation, failed=failed)
            self.assertTrue(w.busy)
            self.assertIsNotNone(w._activities.display())
            failed.assert_not_called()
            operation.assert_not_called()
            w._poll()
        self.assertFalse(w.busy)
        self.assertIsNone(w._activities.display())
        self.assertIsInstance(failed.call_args.args[0], RuntimeError)

    def test_foreground_wait_replaces_cancelled_panel_in_empty_gallery(self):
        w = window()
        w.view = "maps"
        w.canvas = Mock()
        w.cards = {}
        old = tuple(Mock() for _ in range(4))
        new = tuple(Mock() for _ in range(4))
        w._gallery_panel = old
        w._loading_panel = Mock(return_value=new)
        w._render_gallery = Mock()
        entered = Event()
        release = Event()

        def work():
            entered.set()
            release.wait(2)

        try:
            w._submit("Checking import…", work)
            self.assertTrue(entered.wait(1))
            old[0].destroy.assert_called_once()
            w._loading_panel.assert_called_once_with(w.canvas, "Checking import…")
            new[0].destroy.assert_not_called()
            release.set()
            item = w.results.get(timeout=2)
            w.results.put(item)
            w._poll()
            new[0].destroy.assert_called_once()
        finally:
            release.set()

    def test_gallery_cancel_retires_inline_panel_and_render_error_recreates_error(self):
        for counted in (False, True):
            w = window()
            w.canvas = Mock()
            w.gallery_count = Mock()
            panel = tuple(Mock() for _ in range(4))
            w._gallery_panel = panel
            if counted:
                w._loading_panel_update(panel, "Checking maps…", 5, 50)
            w._cancel_gallery()
            panel[3].stop.assert_called()
            panel[0].destroy.assert_called_once()
            self.assertIsNone(w._gallery_panel)
            replacement = tuple(Mock() for _ in range(4))
            w._loading_panel = Mock(return_value=replacement)
            w._render_gallery_batch = Mock(side_effect=RuntimeError("bad card"))
            w._gallery_batch(w._gallery_generation)
            self.assertIs(w._gallery_panel, replacement)
            replacement[1].configure.assert_called_with(text="Could not display maps")
            replacement[3].pack_forget.assert_called_once()

    def test_collection_reentry_restores_regional_feedback_during_refresh(self):
        for loaded in (False, True):
            for records in ((), (object(),)):
                w = window()
                w.content = Mock()
                w.secondary = "gray"
                w.collection_loaded = loaded
                w.collection_refreshing = True
                w.remote_records = records
                w._collection_feedback = Mock()
                w._load_saved_collection = Mock()
                with patch("smr_launcher.gui.tk"), patch("smr_launcher.gui.ttk"):
                    w._collection_view()
                w._collection_feedback.assert_called_once_with(True)
                w._load_saved_collection.assert_not_called()

    def test_quit_during_launch_inspection_never_prompts_or_launches(self):
        for issue in (None, "Known issue"):
            w = window()
            w.root = Mock()
            entered = Event()
            release = Event()
            record = performance.GalleryTests().item().record
            w.app.catalogue.return_value = (record,)

            def verify(_):
                entered.set()
                release.wait(2)
                return SimpleNamespace(issue=issue)

            w.app.verification_for.side_effect = verify
            w._play_with_crash_monitor = Mock()
            try:
                with patch("smr_launcher.gui.messagebox.askyesno") as consent:
                    w._play(record.profile_id)
                    self.assertTrue(entered.wait(1))
                    w._close()
                    self.assertTrue(w.close_when_idle)
                    release.set()
                    successful, value, finished = w.results.get(timeout=2)
                    w.results.put((successful, value, finished))
                    w._poll()
                    consent.assert_not_called()
                    w._play_with_crash_monitor.assert_not_called()
                    self.assertTrue(w.closed)
            finally:
                release.set()

    def test_import_checks_run_off_ui_with_feedback_before_delayed_hashing(self):
        w = window()
        entered = Event()
        release = Event()
        threads = []
        ui = get_ident()
        from smr_launcher.application import MapRecord

        record = MapRecord(
            "Map", "a" * 64, "b" * 64, "map-test", (), "/synthetic/prepared", "c" * 64
        )
        w._show = Mock()

        def checks(_):
            threads.append(get_ident())
            entered.set()
            release.wait(2)
            return {"warnings": ["check"]}

        w.app.import_checks.side_effect = checks
        try:
            w._imported(record)
            self.assertTrue(entered.wait(1))
            self.assertEqual(w._activities.display()[0], "Reading import checks…")
            self.assertNotEqual(threads, [ui])
            w._show.assert_not_called()
            release.set()
            item = w.results.get(timeout=2)
            w.results.put(item)
            w._poll()
            w._show.assert_called_once_with("maps")
            self.assertIn("1 static findings", w.status.set.call_args.args[0])
        finally:
            release.set()

    def test_bulk_preflight_is_indicated_off_ui_and_refusal_prevents_network(self):
        from smr_launcher.collection import RemoteMap

        w = window()
        entered = Event()
        release = Event()
        threads = []
        ui = get_ident()
        record = RemoteMap("Map.7z", 10, "a" * 40)
        w.remote_records = (record,)
        w.collection_loaded = True
        w.bulk_cancel = Event()

        def stopped():
            threads.append(get_ident())
            entered.set()
            release.wait(2)
            raise ValueError("game running")

        w.app._stopped.side_effect = stopped
        try:
            with (
                patch("smr_launcher.gui.messagebox.askyesno", return_value=True),
                patch("smr_launcher.gui.messagebox.showerror"),
                patch("smr_launcher.gui.download_all_maps") as download,
            ):
                w._download_all()
                self.assertTrue(entered.wait(1))
                self.assertIsNotNone(w._activities.display())
                self.assertNotEqual(threads, [ui])
                download.assert_not_called()
                release.set()
                item = w.results.get(timeout=2)
                w.results.put(item)
                w._poll()
                download.assert_not_called()
                self.assertFalse(w.bulk_active)
                self.assertEqual(w.download_states[record.name][0], "Failed")
                self.assertIsNone(w._activities.display())
        finally:
            release.set()

    def test_collection_error_is_in_empty_list_and_retry_restarts_its_indicator(self):
        w = window()
        w.remote_records = ()
        w.collection_list = Mock()
        w.collection_progress = Mock()
        panel = tuple(Mock() for _ in range(4))
        w._loading_panel = Mock(return_value=panel)
        w._collection_feedback(True)
        w._loading_panel.assert_called_once_with(w.collection_list, "Loading the map collection…")
        w._collection_feedback(False, error=True)
        panel[1].configure.assert_called_with(text="Could not load the collection")
        panel[3].pack_forget.assert_called_once()
        w._collection_feedback(True)
        panel[1].configure.assert_called_with(text="Loading the map collection…")
        panel[3].start.assert_called_once_with(20)
        w.remote_records = (object(),)
        w._collection_feedback(False)
        panel[0].destroy.assert_called_once()
        self.assertIsNone(w._collection_panel)

    def test_first_card_replaces_center_panel_but_remaining_cards_still_have_progress(self):
        w = window()
        w.view = "maps"
        w.canvas = Mock()
        w.canvas.yview.return_value = (0, 1)
        w.grid_frame = Mock()
        w.grid_frame.winfo_children.return_value = []
        w.gallery_count = Mock()
        w._selection_details = Mock()
        w._create_gallery_card = Mock()
        w._gallery_snapshot = tuple(performance.GalleryTests().item(f"Map{n}") for n in range(25))
        w.search = Mock()
        w.search.get.return_value = ""
        w.map_filter = Mock()
        w.map_filter.get.return_value = "All maps"
        w.sort_order = Mock()
        w.sort_order.get.return_value = "Name A–Z"
        panel = tuple(Mock() for _ in range(4))
        w._gallery_panel = panel
        w._gallery_activity = w._activity_begin("Loading maps…")
        w._begin_gallery_batches(0)
        panel[0].destroy.assert_called_once()
        self.assertIsNone(w._gallery_panel)
        self.assertGreater(w._create_gallery_card.call_count, 0)
        self.assertLess(w._create_gallery_card.call_count, 25)
        self.assertEqual(w._activities.display()[0], "Displaying maps…")
        self.assertLess(w._activities.display()[1], 25)

    def test_details_opens_loading_panel_before_reads_and_late_result_does_not_reopen_closed_dialog(
        self,
    ):
        w = window()
        w._submit = Mock()
        w._attach_activity = Mock()
        w._loading_panel = Mock()
        w._display_map_details = Mock()
        w._loading_panel_end = Mock()
        record = performance.GalleryTests().item().record
        with patch("smr_launcher.gui.tk.Toplevel") as popup:
            w._map_details(record)
            w._loading_panel.assert_called_once_with(popup.return_value, "Loading map details…")
            w.app.map_metadata.assert_not_called()
            w.app.verification_for.assert_not_called()
            _label, prepare, finished = w._submit.call_args.args
            data = prepare()
            w.app.verification_for.assert_called_once_with(record)
            popup.return_value.winfo_exists.return_value = False
            finished(data)
            w._display_map_details.assert_not_called()
            w._loading_panel_end.assert_not_called()

    def test_quit_update_validation_is_indicated_and_failure_can_keep_launcher_open(self):
        w = window()
        w.root = Mock()
        w.pending_update = "/synthetic/update"
        w.pending_update_manual = True
        w._submit = Mock()
        with (
            patch("smr_launcher.gui.start_install_helper") as helper,
            patch("smr_launcher.gui.messagebox.askyesno", return_value=False),
        ):
            w._close()
            helper.assert_not_called()
            w.root.destroy.assert_not_called()
            label, prepare, finished = w._submit.call_args.args
            self.assertIn("Verifying update", label)
            prepare()
            helper.assert_called_once_with(w.pending_update, w.update_library)
            w._submit.call_args.kwargs["failed"](ValueError("staged update changed"))
            self.assertFalse(w.closed)
            w.root.destroy.assert_not_called()
            self.assertEqual(w.pending_update, "/synthetic/update")
            finished(None)
            self.assertTrue(w.closed)
            w.root.destroy.assert_called_once()
