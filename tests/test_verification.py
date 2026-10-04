"""A gameplay badge must follow the exact played inputs, never a map name."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.verification import CHECKS, GameplayVerification, VerificationStore


class VerificationTests(unittest.TestCase):
    def test_badge_invalidates_for_changed_archive_game_variant_or_assets(self):
        with tempfile.TemporaryDirectory() as root:
            store = VerificationStore(Path(root).resolve() / "private-verification.json")
            played = GameplayVerification(
                "a" * 64, "b" * 64, "c" * 64, "d" * 64,
                "2026-10-04T17:45:30-04:00", "Test player",
                dict.fromkeys(CHECKS, True), "Synthetic short play and save reload")
            store.append(played)
            self.assertEqual(store.latest("a" * 64, "b" * 64, "c" * 64, "d" * 64).status,
                             "Verified")
            for changed in range(4):
                identity = ["a" * 64, "b" * 64, "c" * 64, "d" * 64]
                identity[changed] = "e" * 64
                self.assertIsNone(store.latest(*identity))

    def test_unfinished_gameplay_is_not_verified(self):
        checks = dict.fromkeys(CHECKS, True)
        checks["manual_save_reloaded"] = False
        partial = GameplayVerification(
            "a" * 64, "b" * 64, "c" * 64, "d" * 64,
            "2026-10-04T17:45:30-04:00", "Test player", checks, "Synthetic load only")
        self.assertEqual(partial.status, "Not verified")


if __name__ == "__main__":
    unittest.main()
