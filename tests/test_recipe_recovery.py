"""Compiled-map validation must follow recovery of interrupted profile moves."""
from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import test_application as fixtures
from smr_launcher.activation import MARKER, ORIGINAL


class SyntheticInterruption(RuntimeError):
    pass


class RecipeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ApplicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.application
        self.installation = self.fixture.installation
        (self.installation.profile_root / "Settings.ini").write_text(
            "[User Settings]\nPlayerName = Synthetic\nSkipOpeningMovies = 0\nQuickstart = 1\n")
        self.original, rule, binding, _stock = self.fixture.compiled_fixture()
        self.compiled = self.fixture.compiled_prepare(self.original, rule, binding)

        self.app.activate(self.compiled.profile_id)
        (self.installation.profile_root / "Saves/compiled.sav").write_bytes(b"compiled progress")
        self.app.activate(self.original.profile_id)
        (self.installation.profile_root / "Saves/original.sav").write_bytes(b"original progress")
        self.original_saves = self.fixture.snapshot_files(self.installation.profile_root / "Saves")
        self.compiled_saves = self.fixture.snapshot_files(
            self.app.profiles._profile_path(self.compiled.profile_id) / "Saves")
        self.stock_saves = self.fixture.snapshot_files(
            self.app.profiles._profile_path(ORIGINAL) / "Saves")

    def exercise_retry(self, event, *, play):
        reached = []

        def interrupt(phase):
            if phase == event:
                reached.append(phase)
                raise SyntheticInterruption(phase)

        self.app.profiles.fault_hook = interrupt
        with self.assertRaisesRegex(SyntheticInterruption, event):
            self.app.activate(self.compiled.profile_id)
        self.app.profiles.fault_hook = lambda _phase: None
        self.assertEqual(reached, [event])
        self.assertTrue(self.app.profiles.journal.exists())
        self.assertEqual(self.app.profiles._state()["active"], self.original.profile_id)
        self.assertEqual(json.loads((self.installation.profile_root / MARKER).read_text())["profile_id"],
                         self.compiled.profile_id)

        launched = []

        def launch():
            # Called inside the application/profile locks; inspect files directly.
            self.assertFalse(self.app.profiles.journal.exists())
            self.assertEqual(self.app.profiles._state()["active"], self.compiled.profile_id)
            self.assertEqual(self.fixture.snapshot_files(self.installation.profile_root / "Saves"),
                             self.compiled_saves)
            self.assertEqual(self.fixture.snapshot_files(
                self.app.profiles._profile_path(self.original.profile_id) / "Saves"), self.original_saves)
            launched.append(True)
            self.installation.running = True

        if play:
            try:
                result = self.app.play(self.compiled.profile_id, launch=launch)
            finally:
                self.installation.running = False
            self.assertEqual(launched, [True])
        else:
            result = self.app.activate(self.compiled.profile_id)
        self.assertEqual(result, self.compiled.profile_id)
        self.assertFalse(self.app.profiles.journal.exists())
        self.assertEqual(self.fixture.snapshot_files(self.installation.profile_root / "Saves"),
                         self.compiled_saves)

        # Normal switches after recovery must restore the exact independent saves.
        self.app.activate(self.original.profile_id)
        self.assertEqual(self.fixture.snapshot_files(self.installation.profile_root / "Saves"),
                         self.original_saves)
        self.app.activate(self.compiled.profile_id)
        self.assertEqual(self.fixture.snapshot_files(self.installation.profile_root / "Saves"),
                         self.compiled_saves)
        self.app.activate(ORIGINAL)
        self.assertEqual(self.fixture.snapshot_files(self.installation.profile_root / "Saves"),
                         self.stock_saves)

    def test_activate_recovers_after_exchange_before_recipe_validation(self):
        self.exercise_retry("after_exchange", play=False)

    def test_activate_recovers_after_outgoing_saved_before_recipe_validation(self):
        self.exercise_retry("after_outgoing_saved", play=False)

    def test_play_recovers_after_exchange_before_recipe_validation(self):
        self.exercise_retry("after_exchange", play=True)

    def test_play_recovers_after_outgoing_saved_before_recipe_validation(self):
        self.exercise_retry("after_outgoing_saved", play=True)


if __name__ == "__main__":
    unittest.main()
