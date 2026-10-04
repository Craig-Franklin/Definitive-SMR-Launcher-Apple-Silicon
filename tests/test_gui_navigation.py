"""A completed remote fetch must preserve the tab chosen while it ran."""
from pathlib import Path
from unittest.mock import Mock, patch
import subprocess
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


class NavigationTests(unittest.TestCase):
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

    def test_failed_open_after_install_keeps_source_window_alive(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.closed = False
        window.needs_installation = True
        window.status = Mock(); window.root = Mock(); window._controls = Mock()
        with patch("smr_launcher.gui.subprocess.run", side_effect=subprocess.CalledProcessError(1, "open")), patch("smr_launcher.gui.messagebox.showinfo"):
            window._application_installed(Path("/synthetic/Applications/Launcher.app"))
        self.assertFalse(window.closed)
        self.assertFalse(window.needs_installation)
        window.root.destroy.assert_not_called()
        self.assertIn("Installation completed", window.status.set.call_args.args[0])

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
