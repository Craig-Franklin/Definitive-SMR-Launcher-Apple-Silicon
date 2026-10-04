"""Synthetic prepared profiles; no downloaded map or game asset is committed."""
from pathlib import Path
import hashlib
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.variants import prepare_original_variant


class VariantPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.clean = root / "clean"
        self.imported = root / "imported"
        for name in ("CustomAssets", "UserMaps", "Saves"):
            (self.clean / name).mkdir(parents=True, exist_ok=True)
        (self.clean / "Settings.ini").write_bytes(b"stock setting")
        (self.clean / "Saves/stock.sav").write_bytes(b"stock save")
        (self.imported / "CustomAssets").mkdir(parents=True)
        (self.imported / "UserMaps/Example").mkdir(parents=True)
        (self.imported / "UserMaps/Example/RRT_Scenario_User_Example.xml").write_bytes(b"<Scenario/>")
        self.root = root

    def test_profile_contains_exact_map_assets_but_no_stock_save(self):
        archive = hashlib.sha256(b"archive").hexdigest()
        game = hashlib.sha256(b"game executable").hexdigest()
        first = self.root / "prepared-a"
        second = self.root / "prepared-b"
        one = prepare_original_variant(self.clean, self.imported, first, archive, game)
        two = prepare_original_variant(self.clean, self.imported, second, archive, game)
        self.assertEqual(one.variant_id, two.variant_id)
        self.assertEqual((first / "Settings.ini").read_bytes(), b"stock setting")
        self.assertEqual(list((first / "Saves").iterdir()), [])
        self.assertEqual((self.clean / "Saves/stock.sav").read_bytes(), b"stock save")
        asset = "UserMaps/Example/RRT_Scenario_User_Example.xml"
        self.assertEqual((first / asset).read_bytes(), b"<Scenario/>")
        self.assertNotEqual((first / asset).stat().st_ino, (self.imported / asset).stat().st_ino)
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o500)


if __name__ == "__main__":
    unittest.main()
