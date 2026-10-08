"""Verify the shipped support bundle can run its fakes from an ordinary checkout."""
from pathlib import Path
import subprocess
import sys
import unittest


class IndividualStopSupportTests(unittest.TestCase):
    def test_isolated_bundle_checks(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, '-B', str(
                root/'scripts/research/individual_stop_support/check.py')],
            cwd=root, capture_output=True, text=True, timeout=65,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
