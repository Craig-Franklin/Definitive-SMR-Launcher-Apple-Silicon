"""Exercise baseline enforcement against real, isolated Git histories."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_quality import baseline_errors


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q")
        (self.root / "src").mkdir()
        (self.root / "src/legacy.py").write_text("value=1\n")
        self.commit()

    def git(self, *args):
        return (
            subprocess.check_output(
                ["git", *args], cwd=self.root, env=self.env, stderr=subprocess.PIPE
            )
            .decode()
            .strip()
        )

    def commit(self):
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        )

    def test_invalid_or_missing_history_fails(self):
        self.assertTrue(baseline_errors("missing-base", set(), self.root))

    def test_initial_baseline_only_accepts_existing_files(self):
        self.assertEqual(baseline_errors("HEAD", {"src/legacy.py"}, self.root), [])
        self.assertTrue(baseline_errors("HEAD", {"src/new.py"}, self.root))

    def test_existing_baseline_can_shrink_but_cannot_expand(self):
        (self.root / ".ruff-format-baseline").write_text("src/legacy.py\n")
        (self.root / "src/adopted.py").write_text("value = 2\n")
        self.commit()
        self.assertEqual(baseline_errors("HEAD", set(), self.root), [])
        self.assertTrue(baseline_errors("HEAD", {"src/legacy.py", "src/adopted.py"}, self.root))


if __name__ == "__main__":
    unittest.main()
