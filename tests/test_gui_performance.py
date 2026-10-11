"""Thread/lifetime, staging, bounded drains and gallery batches without Tk windows."""
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread, get_ident
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import time

from smr_launcher.gui import LauncherWindow
from smr_launcher.ui_work import Mailbox, DownloadEvents
from smr_launcher.gallery import GalleryItem, gallery_snapshot, filter_snapshot, _read_art
from smr_launcher.map_metadata import MapMetadata, read_map_metadata
from smr_launcher.collection import RemoteMap, SavedCatalogue


class Root:
    def __init__(self):
        self.callbacks = []
    def after(self, delay, callback):
        self.callbacks.append((delay, callback))
        return len(self.callbacks)
    def after_cancel(self, key): pass
    def destroy(self): pass


def window():
    w = LauncherWindow.__new__(LauncherWindow)
    w.root = Root(); w.closed = False; w.busy = False; w.bulk_active = False
    w.close_when_idle = False; w.speech_process = None; w.pending_update = None
    w.app = Mock(); w.view = "collection"
    w.background_results = Mailbox(); w.results = Queue(); w.download_events = DownloadEvents()
    w.download_states = {}; w.imported_names = set(); w.remote_rows = {}; w.remote_row_ids = {}
    w.selected_remote = None; w.selected_remotes = (); w.selected_profile = None
    w.collection_loaded = False; w.collection_refreshing = False; w.collection_saved = False
    w.collection_notice = ""; w.community_notice = ""; w.community_index = {}
    w._collection_generation = 0; w._startup_generation = 0; w._gallery_generation = 0
    w._gallery_pending = None; w._gallery_snapshot = (); w._gallery_after_id = None
    w.status = Mock(); w.crash_status = Mock(); w._log = Mock(); w._controls = Mock()
    w.update_library = Path("/synthetic-never-read")
    w._collection_rows = Mock(); w._publish_collection_caption = Mock()
    w._stop_speech = Mock(); w.images = {}
    return w


def deliver(w, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ok, value, finished = w.background_results.get_nowait()
        except Empty:
            time.sleep(.001)
            continue
        finished(ok, value)
        return
    raise AssertionError("worker did not deliver")


class WorkerLifetimeTests(TestCase):
    def test_constructor_only_schedules_bootstrap_and_paints_shell(self):
        with patch("smr_launcher.gui.tk") as tk, patch("smr_launcher.gui.ttk") as ttk, \
             patch("smr_launcher.gui.TranslatedChoice"), patch("smr_launcher.gui.LauncherApplication.discover") as discover, \
             patch("smr_launcher.gui.read_cached_community_index") as community, \
             patch("smr_launcher.gui.LanguagePreferences") as language, \
             patch("smr_launcher.gui.UpdatePreferences") as updates:
            tk.Tk.return_value.winfo_rgb.return_value = (0, 0, 0)
            w = LauncherWindow()
            self.assertTrue(w.initializing)
            self.assertIsNone(w.app)
            discover.assert_not_called(); community.assert_not_called()
            language.return_value.load.assert_not_called(); updates.return_value.automatic.assert_not_called()
            self.assertTrue(ttk.Label.called)
            self.assertEqual([call.args[0] for call in w.root.after.call_args_list], [10, 0])

    def test_worker_and_application_callback_use_distinct_threads(self):
        w = window(); ui = get_ident(); observed = []; operation_done = Event()
        def operation():
            observed.append(("worker", get_ident())); operation_done.set(); return ("plain",)
        def finished(ok, result):
            observed.append(("ui", get_ident())); self.assertTrue(ok); self.assertEqual(result, ("plain",))
        w._background(operation, finished)
        self.assertTrue(operation_done.wait(1)); self.assertEqual(len(observed), 1)
        deliver(w)
        self.assertNotEqual(observed[0][1], ui); self.assertEqual(observed[1][1], ui)

    def test_close_disposes_late_and_already_queued_services_without_callbacks(self):
        for queued in (False, True):
            w = window(); entered = Event(); release = Event(); finished = Mock(); disposed = Event()
            monitor = Mock(); monitor.close.side_effect = disposed.set
            def operation():
                entered.set(); release.wait(1); return {"monitor": monitor}
            w._background(operation, finished, dispose=w._dispose_bootstrap)
            self.assertTrue(entered.wait(1))
            if queued:
                release.set()
                deadline = time.monotonic() + 1
                while not w.background_results._items and time.monotonic() < deadline:
                    time.sleep(.001)
            w._close(); release.set()
            self.assertTrue(disposed.wait(1)); finished.assert_not_called(); monitor.close.assert_called_once()
            w._poll()  # No callbacks after destruction.

    def test_discovery_failure_and_unexpected_startup_error_reach_welcome_on_ui(self):
        w = window(); w.language_preferences = SimpleNamespace(load=Mock(), language="en")
        w.update_preferences = SimpleNamespace(automatic=Mock(return_value=False))
        with patch("smr_launcher.gui.read_cached_community_index", return_value={}), \
             patch("smr_launcher.gui.trusted_team_from_bundle", return_value=False):
            data = w._bootstrap(Mock(side_effect=RuntimeError("synthetic missing game")))
        w.loading_shell = Mock(); w.auto_updates = Mock(); w.auto_updates.get.return_value = False
        w.update_status = Mock(); w._menu = Mock(); w._layout = Mock(); w._show = Mock(); w.sort_order = Mock(); w.map_filter = Mock()
        w._startup_ready(0, True, data)
        self.assertIsNone(w.app); w._show.assert_called_with("welcome")
        self.assertIn("synthetic missing game", w.status.set.call_args.args[0])
        w._startup_ready(0, False, RuntimeError("unexpected bootstrap"))
        self.assertIn("unexpected bootstrap", w.status.set.call_args.args[0])
        w.closed = True; monitor = Mock()
        w._startup_ready(0, True, {"monitor": monitor})
        monitor.close.assert_called_once()

    def test_startup_bootstrap_never_writes_tk_variables_and_monitor_failure_is_visible(self):
        w = window(); w.language_preferences = SimpleNamespace(load=Mock(), language="en")
        w.update_preferences = SimpleNamespace(automatic=Mock(return_value=False))
        app = Mock(); app.catalogue.return_value = ()
        monitor = Mock(); monitor.start.side_effect = RuntimeError("monitor unavailable")
        with patch("smr_launcher.gui.read_cached_community_index", return_value={}), \
             patch("smr_launcher.gui.trusted_team_from_bundle", return_value=False), \
             patch("smr_launcher.gui.CrashMonitor", return_value=monitor):
            data = w._bootstrap(lambda: app)
        self.assertIs(data["app"], app); self.assertTrue(data["monitor_error"])
        monitor.close.assert_called_once(); w.status.set.assert_not_called(); w.crash_status.set.assert_not_called()


class CollectionStagingTests(TestCase):
    def test_saved_catalogue_needs_no_network_and_labels_source(self):
        w = window(); records = (RemoteMap("Map.7z", 4, "a" * 40),)
        with patch("smr_launcher.gui.read_cached_catalogue", return_value=SavedCatalogue(records, "2026-01-01T00:00:00+00:00")), \
             patch("smr_launcher.gui.fetch_catalogue") as network:
            w._load_saved_collection(); deliver(w)
            network.assert_not_called()
        self.assertEqual(w.remote_records, records); self.assertTrue(w.collection_saved)
        self.assertIn("saved", w._collection_caption())

    def test_fresh_publication_independent_of_blocked_maintenance_and_errors_retained(self):
        w = window(); records = (RemoteMap("Map.7z", 4, "a" * 40),)
        entered = Event(); release = Event(); original_app = w.app
        original_app.match_collection_sources.side_effect = lambda records: (entered.set(), release.wait(2))
        original_app.catalogue.return_value = ()
        community_entered = Event()
        def community(path):
            community_entered.set(); release.wait(2); raise RuntimeError("community offline")
        try:
            with patch("smr_launcher.gui.fetch_catalogue", return_value=records), \
                 patch("smr_launcher.gui.write_cached_catalogue"), \
                 patch("smr_launcher.gui.fetch_community_index", side_effect=community):
                w._refresh_collection(); deliver(w)
                self.assertEqual(w.remote_records, records)
                self.assertFalse(w.collection_refreshing); self.assertFalse(w.busy)
                self.assertTrue(entered.wait(1)); self.assertTrue(community_entered.wait(1))
                self.assertEqual(w.community_index, {})
                release.set()
                for _ in range(3): deliver(w)
                self.assertIn("community refresh unavailable", w.collection_notice)
        finally:
            release.set(); w.background_results.close()

    def test_stale_fetch_and_community_after_library_switch_or_close_ignored(self):
        for stale in ("library", "generation", "close"):
            w = window(); operations = []
            w._background = lambda operation, finished, **kw: operations.append((operation, finished))
            w._refresh_collection(); callback = operations.pop()[1]
            if stale == "library": w.app = Mock()
            elif stale == "generation": w._collection_generation += 1
            else: w.closed = True
            callback(True, (RemoteMap("Old.7z", 1, "a" * 40),))
            self.assertFalse(w.collection_loaded)
        w = window(); operations = []
        w._background_latest = lambda lane, operation, finished: operations.append((lane, finished))
        w._collection_maintenance(0, w.app, ())
        w._collection_generation += 1
        for lane, callback in operations: callback(True, {"old": "state"})
        self.assertEqual(w.community_index, {})

    def test_failed_refresh_preserves_visible_last_good_list_and_notice(self):
        w = window(); records = (RemoteMap("Map.7z", 4, "a" * 40),)
        w.remote_records = records; w.collection_loaded = True; w.collection_saved = True
        with patch("smr_launcher.gui.fetch_catalogue", side_effect=RuntimeError("offline")):
            w._refresh_collection(); deliver(w)
        self.assertEqual(w.remote_records, records)
        self.assertIn("refresh failed", w._collection_caption())
        self.assertIn("offline", w.status.set.call_args.args[0])

    def test_maintenance_lane_has_only_one_worker_and_latest_pending_request(self):
        w = window(); operations = []
        w._background = lambda operation, finished, **kw: operations.append((operation, finished))
        finish = Mock()
        for i in range(100): w._background_latest("community", lambda i=i: i, finish)
        self.assertEqual(len(operations), 1); self.assertEqual(len(w._maintenance_pending), 1)
        operations[0][1](True, 0)
        self.assertEqual(len(operations), 2); self.assertEqual(operations[1][0](), 99)


class ProgressTests(TestCase):
    def test_coalescing_terminal_order_retry_and_late_progress(self):
        events = DownloadEvents(); record = RemoteMap("Map.7z", 10000, "a" * 40)
        for count in range(10000): events.put((record, "Downloading", count, 10000, ""))
        self.assertEqual(events.qsize(), 1)
        events.put((record, "Archive verified", 10000, 10000, ""))
        events.put((record, "Importing", 10000, 10000, ""))
        events.put((record, "Imported", 10000, 10000, "done"))
        events.put((record, "Downloading", 0, 10000, "late"))
        self.assertEqual(events.qsize(), 4)
        rows = [events.get_nowait() for _ in range(4)]
        self.assertEqual([r[1] for r in rows], ["Downloading", "Archive verified", "Importing", "Imported"])
        self.assertEqual(rows[0][2], 9999)
        events.begin((record,))
        events.put((record, "Downloading", 0, 10000, "retry"))
        events.put((record, "Failed", 0, 10000, "error details"))
        self.assertEqual(events.get_nowait()[1], "Downloading")
        self.assertEqual(events.get_nowait()[4], "error details")

    def test_active_producer_backpressure_is_bounded_and_close_releases_waiters(self):
        events = DownloadEvents(capacity=4); done = Event()
        def produce():
            for i in range(100): events.put((RemoteMap(f"Map{i}.7z", 1, "a" * 40), "Failed", 0, 1, "retained"))
            done.set()
        worker = Thread(target=produce); worker.start()
        deadline = time.monotonic() + 1
        while events.qsize() < 4 and time.monotonic() < deadline: time.sleep(.001)
        self.assertEqual(events.qsize(), 4); self.assertFalse(done.is_set())
        events.close(); self.assertTrue(done.wait(1)); worker.join(1)

    def test_ui_drain_logs_lifecycle_once_and_updates_each_visible_row_once_hidden_retained(self):
        w = window(); a = RemoteMap("A.7z", 100, "a" * 40); b = RemoteMap("B.7z", 100, "b" * 40)
        w.collection_list = Mock(); w.remote_row_ids = {a.name: "0"}; w.selected_remote = a
        w._collection_selected = Mock()
        for count in range(100): w._download_event(a, "Downloading", count, 100, "")
        w._download_event(a, "Imported", 100, 100, "ready")
        w._download_event(b, "Canceled", 0, 100, "archive retained")
        w._update_download_rows()
        self.assertEqual(w.download_states[a.name][0], "Imported")
        self.assertEqual(w.download_states[b.name][3], "archive retained")
        w.collection_list.set.assert_called_once_with("0", "progress", "Imported")
        w._collection_selected.assert_called_once()
        self.assertEqual(w._log.call_count, 2)
        w.download_events.begin((a,)); w._download_event(a, "Downloading", 0, 100, "")
        w._download_event(a, "Failed", 0, 100, "retry failure")
        w._update_download_rows()
        w._log.assert_called_with("A.7z", "Failed", "retry failure")

    def test_bounded_drain_yields_and_completion_cannot_overtake_terminal_states(self):
        w = window(); w.view = "activity"; final = Mock()
        records = tuple(RemoteMap(f"Map{i}.7z", 1, "a" * 40) for i in range(200))
        for record in records: w._download_event(record, "Imported", 1, 1, "")
        w.results.put((True, (200, 0, 0), final))
        w._poll()
        self.assertLessEqual(len(w.download_states), 64); final.assert_not_called()
        while not w.download_events.empty(): w._continue_download_rows()
        w._poll(); final.assert_called_once_with((200, 0, 0))
        self.assertEqual(len(w.download_states), 200); self.assertEqual(w._log.call_count, 200)

    def test_bulk_cancel_and_final_counts_keep_existing_semantics(self):
        w = window(); w.bulk_active = True; w.bulk_cancel = Event()
        w._download_all()
        self.assertTrue(w.bulk_cancel.is_set())
        w._download_all_finished((7, 2, 3))
        self.assertFalse(w.bulk_active)
        self.assertIn("7 imported, 2 failed, 3 canceled", w.status.set.call_args.args[0])


class GalleryTests(TestCase):
    def item(self, name="Alpha", author="Ada", status="Not verified", created="2025-01-01"):
        record = SimpleNamespace(name=name, profile_id=name, source_label="Local", archive_filename=name+".7z")
        return GalleryItem(record, MapMetadata(author=author, created=created, map_type="Single player"), status)

    def test_snapshot_uses_background_only_plain_data_and_fresh_metadata_verification(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "mapInfo.txt"
            record = self.item().record
            app = SimpleNamespace(catalogue=lambda: (record,), map_metadata=lambda _: read_map_metadata(path),
                                  verification_for=Mock(return_value=None), map_icon=lambda _: None)
            first = gallery_snapshot(app, Event()); self.assertIsNone(first[0].metadata.author)
            path.write_text("Author: Ada\n")
            second = gallery_snapshot(app, Event()); self.assertEqual(second[0].metadata.author, "Ada")
            path.write_text("Author: Bea\n")
            app.verification_for.return_value = SimpleNamespace(status="Known issue")
            third = gallery_snapshot(app, Event()); self.assertEqual(third[0].metadata.author, "Bea")
            self.assertEqual(third[0].verification, "Known issue")
            app.verification_for.side_effect = ValueError("changed resource")
            fourth = gallery_snapshot(app, Event()); self.assertEqual(fourth[0].verification, "Needs inspection")
            self.assertIn("changed resource", fourth[0].notice)
            cancel = Event(); cancel.set(); self.assertEqual(gallery_snapshot(app, cancel), ())
            self.assertEqual(app.verification_for.call_count, 4)

    def test_filter_sort_snapshot_labels_author_dates_and_unknowns(self):
        a = self.item("Alpha", "Zoe", "Verified", "2025-01-01")
        b = self.item("Beta", "Ada", "Known issue", None)
        c = self.item("Gamma", "Bea", "Not verified", "2026-01-01")
        snapshot = (a, b, c)
        self.assertEqual(filter_snapshot(snapshot, "ada", "All maps", "Name A–Z"), (b,))
        self.assertEqual(filter_snapshot(snapshot, "", "Known issue", "Author"), (b,))
        self.assertEqual(filter_snapshot(snapshot, "", "Verified", "Author"), (a,))
        self.assertEqual(filter_snapshot(snapshot, "", "Single player", "Created newest"), (c, a, b))
        self.assertEqual(filter_snapshot(snapshot, "", "Multiplayer", "Name A–Z"), ())

    def test_cancellable_batches_bound_creation_destruction_and_keep_selection_scroll(self):
        w = window(); w.view = "maps"; w.canvas = Mock(); w.canvas.yview.return_value = (.4, .7)
        w.grid_frame = Mock(); old = [Mock() for _ in range(25)]
        w.grid_frame.winfo_children.return_value = old
        w._gallery_snapshot = tuple(self.item(f"Map{i}") for i in range(25))
        w.search = Mock(); w.search.get.return_value = ""
        w.map_filter = Mock(); w.map_filter.get.return_value = "All maps"
        w.sort_order = Mock(); w.sort_order.get.return_value = "Name A–Z"
        w._create_gallery_card = Mock(); w._selection_details = Mock(); w.selected_profile = "Map9"
        w._begin_gallery_batches(0)
        self.assertLessEqual(sum(o.destroy.call_count for o in old), 8)
        self.assertEqual(w._create_gallery_card.call_count, 0)
        count = 0
        while w._gallery_old_cards or w._gallery_index < len(w._gallery_items):
            before = w._create_gallery_card.call_count
            w._gallery_batch(0)
            self.assertLessEqual(w._create_gallery_card.call_count-before, 8)
            count += 1
            self.assertLess(count, 20)
        self.assertEqual(w._create_gallery_card.call_count, 25)
        w.canvas.yview_moveto.assert_called_once_with(.4)
        self.assertEqual(w.selected_profile, "Map9")
        w._cancel_gallery(); previous = w._create_gallery_card.call_count
        w._gallery_batch(0); self.assertEqual(w._create_gallery_card.call_count, previous)
        w.closed = True; w._gallery_batch(w._gallery_generation)
        self.assertEqual(w._create_gallery_card.call_count, previous)

    def test_stale_snapshot_on_navigation_search_library_switch_and_close_never_renders(self):
        for stale in ("navigation", "search", "library", "close"):
            w = window(); w.view = "maps"; w.grid_frame = Mock()
            w._begin_gallery_batches = Mock(); pending = []
            w._background = lambda operation, finished, **kw: pending.append(finished)
            w._render_gallery()
            if stale == "navigation": w.view = "activity"; w._cancel_gallery()
            elif stale == "search": w._search_changed()
            elif stale == "library": w.app = Mock(); w._cancel_gallery()
            else: w.closed = True; w._cancel_gallery()
            pending[0](True, (self.item(),))
            w._begin_gallery_batches.assert_not_called()

    def test_repeated_search_has_one_snapshot_worker_and_latest_request(self):
        w = window(); w.view = "maps"; w.grid_frame = Mock(); pending = []
        w._background = lambda operation, finished, **kw: pending.append(finished)
        for _ in range(50): w._render_gallery()
        self.assertEqual(len(pending), 1)
        pending[0](True, ())
        self.assertEqual(len(pending), 2)

    def test_art_secure_bytes_change_on_replacement_and_photoimage_only_on_ui(self):
        import struct
        png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 2, 2) + b"fake"
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); path = root / "icon.png"
            self.assertEqual(_read_art(path), b"")
            path.write_bytes(png); self.assertEqual(_read_art(path), png)
            path.write_bytes(png+b"replacement"); self.assertEqual(_read_art(path), png+b"replacement")
            link = root / "link.png"; link.symlink_to(path)
            self.assertEqual(_read_art(link), b"")
            ancestor = root / "alias"; ancestor.symlink_to(root, target_is_directory=True)
            self.assertEqual(_read_art(ancestor / "icon.png"), b"")
            path.write_bytes(png[:16] + struct.pack(">II", 90000, 90000))
            self.assertEqual(_read_art(path), b"")
        w = window(); item = GalleryItem(self.item().record, MapMetadata(), "Not verified", art=png, art_key="fresh")
        ui = get_ident()
        with patch("smr_launcher.gui.tk.PhotoImage") as photo:
            photo.side_effect = lambda **kw: (self.assertEqual(get_ident(), ui) or Mock(width=lambda: 2, height=lambda: 2))
            w._art(item); w._art(item)
            photo.assert_called_once()

    def test_card_preserves_native_bindings_and_snapshot_badge_without_revalidation(self):
        w = window(); w.grid_frame = Mock(); w.selected_profile = "Alpha"; w.columns = 3; w.cards = {}
        w.accent = "blue"; w.border = "gray"; w.surface = "white"; w.secondary = "gray"; w.text = "black"
        w._art = Mock(return_value=None); item = self.item(status="Known issue")
        with patch("smr_launcher.gui.tk") as tk:
            w._create_gallery_card(0, item)
            binds = [c.args[0] for c in tk.Frame.return_value.bind.call_args_list]
            self.assertEqual(binds, ["<Button-1>", "<Double-Button-1>", "<MouseWheel>", "<Return>", "<space>"])
            self.assertTrue(any(c.kwargs.get("text") == "Known issue" for c in tk.Label.call_args_list))
            w.app.verification_for.assert_not_called()

    def test_snapshot_worker_reads_app_off_ui_and_applies_snapshot_on_ui(self):
        w = window(); w.view = "maps"; w.grid_frame = Mock(); w.gallery_count = Mock()
        w._begin_gallery_batches = Mock(); ui = get_ident(); calls = []
        record = self.item().record
        def observed(value):
            calls.append(get_ident()); return value
        w.app = SimpleNamespace(catalogue=lambda: observed((record,)),
                                map_metadata=lambda r: observed(MapMetadata(author="Ada")),
                                verification_for=lambda r: observed(None), map_icon=lambda r: observed(None))
        with patch("smr_launcher.gui.tk.PhotoImage", side_effect=AssertionError("worker must not create Tk images")):
            w._render_gallery(); deliver(w)
        self.assertTrue(calls); self.assertTrue(all(thread != ui for thread in calls))
        self.assertEqual(w._gallery_snapshot[0].metadata.author, "Ada")
        w._begin_gallery_batches.assert_called_once()

    def test_scroll_position_retained_on_filter_and_manual_scroll_during_batches(self):
        w = window(); w.view = "maps"; w.canvas = Mock(); w.canvas.yview.return_value = (.4, .7)
        w.grid_frame = Mock(); w.grid_frame.winfo_children.return_value = []
        w._gallery_snapshot = tuple(self.item(f"Map{i}") for i in range(25))
        w.search = Mock(); w.search.get.return_value = ""; w.map_filter = Mock(); w.map_filter.get.return_value = "All maps"
        w.sort_order = Mock(); w.sort_order.get.return_value = "Name A–Z"
        w._gallery_restore_scroll = .6; w._create_gallery_card = Mock(); w._selection_details = Mock()
        w._begin_gallery_batches(0)
        self.assertEqual(w._gallery_scroll, .6)
        w.canvas.yview.return_value = (.8, 1.0)
        w._gallery_yview("moveto", .8)
        while w._gallery_index < len(w._gallery_items): w._gallery_batch(0)
        w.canvas.yview_moveto.assert_called_once_with(.8)

    def test_safe_operation_cancels_snapshot_and_refreshes_current_maps_after_completion(self):
        w = window(); w.view = "maps"; w._render_gallery = Mock(); w._gallery_cancel = Event()
        class ImmediateThread:
            def __init__(self, target, daemon): self.target = target
            def start(self): self.target()
        with patch("smr_launcher.gui.Thread", ImmediateThread):
            w._submit("Synthetic safe operation", lambda: "complete")
        self.assertTrue(w._gallery_cancel.is_set()); self.assertTrue(w.busy)
        w._poll()
        self.assertFalse(w.busy); w._render_gallery.assert_called_once()

    def test_art_source_dimension_and_aggregate_snapshot_budgets(self):
        import struct
        from smr_launcher import gallery
        png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 360, 360) + b"synthetic"
        with TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "icon.png"
            path.write_bytes(png)
            records = tuple(self.item(f"Map{i}").record for i in range(4))
            app = SimpleNamespace(catalogue=lambda: records, map_metadata=lambda _: MapMetadata(),
                                  verification_for=lambda _: None, map_icon=lambda _: path)
            self.assertEqual(_read_art(path), png)
            with patch.object(gallery, "MAX_SNAPSHOT_ART_BYTES", len(png)*2):
                snapshot = gallery_snapshot(app, Event())
            self.assertEqual([item.art for item in snapshot], [png, png, b"", b""])
            self.assertEqual(sum(len(item.art) for item in snapshot), len(png)*2)
            # One byte shy cannot admit even a single image.
            with patch.object(gallery, "MAX_SNAPSHOT_ART_BYTES", len(png)-1):
                self.assertTrue(all(not item.art for item in gallery_snapshot(app, Event())))
            path.write_bytes(png[:16]+struct.pack(">II", 361, 360))
            self.assertEqual(_read_art(path), b"")
            path.write_bytes(png+b"x"*gallery.MAX_ART_BYTES)
            self.assertEqual(_read_art(path), b"")


class MainIntegrationTests(TestCase):
    def test_issue_revision_refreshes_gallery_when_idle_and_guard_warning_stays_on_ui(self):
        w = window(); w.view = "maps"; w._render_gallery = Mock(); w.busy = True
        events = ["Synthetic resource guard stopped the game"]
        w.crash_monitor = SimpleNamespace(status="Guard ready", issue_revision=1,
                                         take_events=lambda: events[:])
        ui = get_ident()
        with patch("smr_launcher.gui.messagebox.showwarning") as warning:
            warning.side_effect = lambda *args, **kwargs: self.assertEqual(get_ident(), ui)
            w._poll()
            warning.assert_called_once()
        self.assertFalse(hasattr(w, "_crash_issue_revision"))
        w._render_gallery.assert_not_called()
        w._log.assert_called_with("Game resource protection", "Stopped", events[0])
        events.clear(); w.busy = False
        w._poll()
        self.assertEqual(w._crash_issue_revision, 1); w._render_gallery.assert_called_once()
        w._poll(); w._render_gallery.assert_called_once()

    def test_memory_protection_preference_control_preserves_monitor_contract(self):
        w = window(); w.crash_monitor = SimpleNamespace(enabled=True, automatic_reporting=False,
                                                       protect_resources=False, configure=Mock())
        variables = []
        class Value:
            def __init__(self, value): self.value=value; variables.append(self)
            def get(self): return self.value
        with patch("smr_launcher.gui.tk") as tk, patch("smr_launcher.gui.ttk") as ttk:
            tk.BooleanVar.side_effect = Value
            w.crash_reports()
            protection = next(call for call in ttk.Checkbutton.call_args_list
                              if call.kwargs.get("text") == "Stop Railroads if its memory use runs away")
            self.assertFalse(protection.kwargs["variable"].get())
            protection.kwargs["variable"].value = True
            protection.kwargs["command"]()
        w.crash_monitor.configure.assert_called_once_with(enabled=True, automatic_reporting=False,
                                                          protect_resources=True)


class PersistentIssueGalleryTests(TestCase):
    from test_application import ApplicationTests
    setUp = ApplicationTests.setUp

    def test_persistent_issue_is_fresh_in_snapshot_filter_and_details_after_restart(self):
        from smr_launcher.application import LauncherApplication
        from smr_launcher.map_issues import MapIssueStore
        app = self.application
        app.setup(); record = app.import_archive(self.archive)
        self.assertEqual(gallery_snapshot(app, Event())[0].verification, "Not verified")
        MapIssueStore(app.library).record(dict(archive_sha256=record.archive_sha256,
            variant_id=record.variant_id, game_executable_sha256=app.installation.executable_sha256),
            incident_id="b"*64, occurred_at="2026-10-11T14:00:00+00:00", kind="resource_limit",
            detail="Synthetic memory protection failure")
        restarted = LauncherApplication(app.installation, app.library)
        snapshot = gallery_snapshot(restarted, Event())
        self.assertEqual(snapshot[0].verification, "Known issue")
        self.assertEqual(filter_snapshot(snapshot, "", "Known issue", "Name A–Z"), snapshot)
        self.assertIn("Synthetic memory protection failure", LauncherWindow._verification_details(restarted.verification_for(record)))
