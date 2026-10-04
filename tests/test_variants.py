"""Synthetic prepared profiles; no downloaded map or game asset is committed."""
from pathlib import Path
import hashlib
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.rules import CompatibilityRule, TokenPatch
from smr_launcher.extraction import _freeze_tree, _unfreeze_directories
from smr_launcher.variants import prepare_original_variant, prepare_compatibility_variant


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

    def test_compatibility_edition_has_distinct_repeatable_identity_and_keeps_original(self):
        archive = hashlib.sha256(b"archive").hexdigest()
        game = hashlib.sha256(b"game executable").hexdigest()
        asset = "UserMaps/Example/RRT_Scenario_User_Example.xml"
        before = b"<Scenario/>"
        after = b"<Scenario version='1'/>"
        patch = TokenPatch(asset, hashlib.sha256(before).hexdigest(),
                           hashlib.sha256(after).hexdigest(), before, after)
        rule = CompatibilityRule("example-attribute", 1, "synthetic fixture",
                                 "Explicit sample repair", archive, game, (patch,))
        original = prepare_original_variant(self.clean, self.imported, self.root / "original",
                                            archive, game)
        _freeze_tree(self.imported)
        try:
            one = prepare_compatibility_variant(self.clean, self.imported, self.root / "compatible-a",
                                                archive, game, (rule,))
            two = prepare_compatibility_variant(self.clean, self.imported, self.root / "compatible-b",
                                                archive, game, (rule,))
        finally:
            _unfreeze_directories(self.imported)
        self.assertNotEqual(one.variant_id, original.variant_id)
        self.assertEqual(one.variant_id, two.variant_id)
        self.assertEqual(one.rules, ("example-attribute@1",))
        self.assertEqual((self.root / "compatible-a" / asset).read_bytes(), after)
        self.assertEqual((self.root / "original" / asset).read_bytes(), before)
        self.assertEqual((self.imported / asset).read_bytes(), before)
        self.assertEqual((self.clean / "Saves/stock.sav").read_bytes(), b"stock save")


if __name__ == "__main__":
    unittest.main()
