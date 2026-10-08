"""Boundary and missing-phase regressions for the root GUI phase gate."""
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('individual_phase', Path(__file__).resolve().parents[1] / 'scripts/research/individual_stop_support/phase.py')
phase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase)


class IndividualPhaseTests(unittest.TestCase):
    def test_frozen_boundary_cases(self):
        # Independently specified original-anchor expectations, not a timer reset.
        cases = [
            (1239.999, 'game', None, True), (1240, 'game', None, False),
            (1359.999, 'save_begin', None, True), (1360, 'save_begin', None, True),
            (1360.001, 'save_begin', None, False),
            (1367.814, 'save_begin', None, False),
            (1400, 'save', None, False), (1400, 'reload', 1360, True),
            (1539.999, 'continue', 1360, True), (1540, 'save', 1360, False),
            (1400, 'game', 1200, False), (1400, 'save_begin', 1200, False),
            (1200, 'save', 1300, False), (1400, 'save', 1360.001, False),
            (999, 'game', None, False), (1200, 'anything', None, False),
        ]
        for now, action, entry, allowed in cases:
            with self.subTest(now=now, action=action, entry=entry):
                if allowed:
                    actual = phase.admit_action(1000, now, action, entry)
                    self.assertEqual(actual, now if action == 'save_begin' else entry)
                else:
                    with self.assertRaises(ValueError):
                        phase.admit_action(1000, now, action, entry)

    def test_invalid_clock_inputs(self):
        for bad in (True, False, '1000', None, float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=repr(bad)):
                with self.assertRaises(ValueError):
                    phase.admit_action(bad, 1200, 'game')
                with self.assertRaises(ValueError):
                    phase.admit_action(1000, bad, 'game')
                if bad is not None:
                    with self.assertRaises(ValueError):
                        phase.admit_action(1000, 1400, 'save', bad)

    def test_entry_preserved_and_no_new_gameplay(self):
        entry = phase.admit_action(1000, 1220, 'save_begin')
        for action in ('save', 'reload', 'verify', 'continue'):
            self.assertEqual(phase.admit_action(1000, 1400, action, entry), entry)
        for action in ('game', 'save_begin', 'stop', 'restore'):
            with self.assertRaises(ValueError):
                phase.admit_action(1000, 1400, action, entry)
        with self.assertRaises(ValueError):
            phase.admit_action(1000, 1400, 'save', 999)


if __name__ == '__main__':
    unittest.main()
