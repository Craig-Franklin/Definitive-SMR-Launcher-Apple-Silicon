"""A completed remote fetch must preserve the tab chosen while it ran."""
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.gui import LauncherWindow
from smr_launcher.collection import RemoteMap


class SelectionTree:
    def __init__(self):
        self.items = {}
        self.selected = ()

    def winfo_exists(self): return True
    def get_children(self): return tuple(self.items)
    def delete(self, key): self.items.pop(key)
    def insert(self, parent, position, iid, values): self.items[iid] = values
    def selection_set(self, keys): self.selected = tuple(keys)
    def selection(self): return self.selected


class ScheduledRoot:
    def __init__(self):
        self.callbacks = {}
        self.cancelled = []
        self.next_id = 0

    def after(self, delay, callback):
        self.next_id += 1
        key = f"after#{self.next_id}"
        self.callbacks[key] = (delay, callback)
        return key

    def after_cancel(self, key):
        self.cancelled.append(key)

    def destroy(self): pass


class NavigationTests(unittest.TestCase):
    def test_search_refresh_debounces_and_dispatches_to_view_current_at_firing(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.root = ScheduledRoot()
        window.closed = False
        window.view = "maps"
        window.app = object()
        window.grid_frame = object()
        window.collection_list = object()
        window._render_gallery = Mock()
        window._collection_rows = Mock()

        window._search_changed()
        first_id = window._search_refresh_after_id
        first_callback = window.root.callbacks[first_id][1]
        window._search_changed()
        second_id = window._search_refresh_after_id
        second_callback = window.root.callbacks[second_id][1]

        self.assertEqual(window.root.callbacks[first_id][0], 200)
        self.assertEqual(window.root.cancelled, [first_id])
        first_callback()  # A canceled callback that was already queued must be harmless.
        window.view = "collection"
        second_callback()

        window._render_gallery.assert_not_called()
        window._collection_rows.assert_called_once_with()

    def test_search_refresh_is_invalidated_by_navigation_and_close(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.root = ScheduledRoot()
        window.closed = False
        window.view = "maps"
        window.app = object()
        window.grid_frame = object()
        window._render_gallery = Mock()
        window._clear_content = Mock()
        window.sidebar = Mock(); window.sidebar_separator = Mock()
        window.search_entry = Mock(); window.search_label = Mock()
        window.content = Mock(); window.nav_buttons = {}
        window.steam_label = Mock(); window._activity_view = Mock()
        window._localize_widgets = Mock()
        window.pending_update = None
        window.busy = False; window.bulk_active = False
        window._stop_speech = Mock()

        window._search_changed()
        navigation_id = window._search_refresh_after_id
        navigation_callback = window.root.callbacks[navigation_id][1]
        window._show("activity")
        navigation_callback()
        window._render_gallery.assert_not_called()
        self.assertIn(navigation_id, window.root.cancelled)

        window.view = "maps"
        window._search_changed()
        close_id = window._search_refresh_after_id
        close_callback = window.root.callbacks[close_id][1]
        window._close()
        close_callback()
        window._render_gallery.assert_not_called()
        self.assertIn(close_id, window.root.cancelled)
        self.assertTrue(window.closed)

    def test_play_reports_verification_error_and_does_not_launch(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.app = Mock(); window.root = Mock(); window.status = Mock()
        record = SimpleNamespace(profile_id="map-example")
        window._records = Mock(return_value=(record,))
        window._submit = Mock()
        window.app.verification_for.side_effect = RuntimeError("Resource changed during inspection")
        with patch("smr_launcher.gui.messagebox.showerror") as error:
            window._play(record.profile_id)
            error.assert_called_once()
            self.assertIn("Resource changed", error.call_args.args[1])
        window._submit.assert_not_called()
        window.app.play.assert_not_called()

    def test_map_details_survives_resource_inspection_failure_without_second_read(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.root = Mock(); window.status = Mock(); window.secondary = "grey"
        window.app = Mock()
        window.app.verification_for.side_effect = RuntimeError("Resource changed during inspection")
        window.app.compatibility_recipes.return_value = ()
        window.community_index = {}
        window.language_preferences = SimpleNamespace(language="en")
        window._localize_widgets = Mock()
        window._metadata = Mock(return_value=SimpleNamespace(
            name="Example", author="", modified_by="", version="", created="", updated="",
            map_type="", source_urls=(), description="Briefing", raw_text=""))
        record = SimpleNamespace(name="Example", source_label="Local", archive_modified="",
            archive_filename="Example.7z", source_url="", archive_sha256="a" * 64,
            variant_id="b" * 64)
        with patch("smr_launcher.gui.tk") as tk, patch("smr_launcher.gui.ttk") as ttk, \
             patch("smr_launcher.gui._checks_text", return_value="Static checks"):
            window._map_details(record)
            window.app.verification_for.assert_called_once_with(record)
            self.assertTrue(any(c.kwargs.get("text") == "Needs inspection"
                                for c in ttk.Label.call_args_list))
            contents = [c.args[1] for c in tk.Text.return_value.insert.call_args_list]
            self.assertTrue(any("Resource changed during inspection" in text for text in contents))
            self.assertFalse(any("No local gameplay result recorded" in text for text in contents))

    def test_collection_rebuild_retains_visible_multiple_selection(self):
        window = LauncherWindow.__new__(LauncherWindow)
        a = RemoteMap("Alpha.7z", 2, "a" * 40)
        b = RemoteMap("Beta.7z", 2, "b" * 40)
        window.remote_records = (a, b)
        window.selected_remotes = (a, b)
        window.selected_remote = a
        window.remote_rows = {}
        window.collection_list = SelectionTree()
        window.collection_detail = Mock()
        window.download_states = {}
        window.search = Mock(); window.search.get.return_value = ""
        window._records = Mock(return_value=())
        window._controls = Mock()
        window._collection_rows()
        self.assertEqual(window.selected_remotes, (a, b))
        window.search.get.return_value = "Beta"
        window._collection_rows()
        self.assertEqual(window.selected_remotes, (b,))
        self.assertEqual(window.selected_remote, b)
        window.search.get.return_value = "Absent"
        window._collection_rows()
        self.assertEqual(window.selected_remotes, ())
        self.assertIsNone(window.selected_remote)

    def test_first_launch_routes_to_welcome_until_enrolled(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.app = None
        self.assertEqual(window._initial_view(), "welcome")
        window.app = Mock()
        window.app.profiles.state_file.exists.return_value = False
        self.assertEqual(window._initial_view(), "welcome")
        window.app.profiles.state_file.exists.return_value = True
        self.assertEqual(window._initial_view(), "maps")

    def test_get_started_uses_guarded_setup_and_only_navigates_on_success(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.app = Mock(); window.busy = False
        window._submit = Mock(); window._show = Mock(); window.status = Mock()
        window.setup()
        _, operation, finished = window._submit.call_args.args
        self.assertEqual(operation, window.app.setup)
        window._show.assert_not_called()
        finished("original-game")
        window._show.assert_called_once_with("collection")
        window.busy = True
        window._submit.reset_mock()
        window.setup()
        window._submit.assert_not_called()

    def test_collection_completion_does_not_override_later_navigation(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.view = "setup"
        window.status = Mock()
        window._show = Mock()
        records = (object(),)
        window._collection_loaded(records)
        self.assertEqual(window.remote_records, records)
        self.assertTrue(window.collection_loaded)
        window._show.assert_not_called()
        window.view = "collection"
        window._collection_loaded(records)
        window._show.assert_called_once_with("collection")


if __name__ == "__main__":
    unittest.main()
