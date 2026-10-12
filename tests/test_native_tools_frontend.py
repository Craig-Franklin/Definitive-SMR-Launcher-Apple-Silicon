"""Compile native tool model checks against a synthetic subprocess only."""

from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == "Darwin", "Native tools target macOS")
class NativeToolsFrontendTests(unittest.TestCase):
    def test_controls_consent_and_unknown_results_preserve_drafts(self):
        with tempfile.TemporaryDirectory(prefix="smr-tools-check-") as directory:
            executable = Path(directory) / "ToolsChecks"
            subprocess.run(
                [
                    "xcrun",
                    "swiftc",
                    "-parse-as-library",
                    "-swift-version",
                    "5",
                    "src/native/Backend.swift",
                    "src/native/LauncherModel.swift",
                    "tests/native/ToolsChecks.swift",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                check=True,
                timeout=90,
            )
            subprocess.run(
                [str(executable), sys.executable, str(ROOT / "tests/native/tools_pipe_fixture.py")],
                cwd=ROOT,
                check=True,
                timeout=30,
            )
