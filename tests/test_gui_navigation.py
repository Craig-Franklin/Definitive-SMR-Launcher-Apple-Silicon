"""A completed remote fetch must preserve the tab chosen while it ran."""
from pathlib import Path
from unittest.mock import Mock
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.gui import LauncherWindow


class NavigationTests(unittest.TestCase):
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
