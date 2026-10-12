"""Compile and run injected map-tool state checks without any visible UI."""

from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == "Darwin", "Native map tools target macOS")
class NativeMapToolsFrontendTests(unittest.TestCase):
    def test_native_consent_targets_and_play_review(self):
        self.check_native("ConsentChecks")

    def test_native_map_tool_drafts_errors_and_unknown_holds(self):
        self.check_native("MapToolsChecks")

    def check_native(self, name):
        with tempfile.TemporaryDirectory(prefix="smr-map-tools-check-") as directory:
            executable = Path(directory) / name
            subprocess.run(
                [
                    "xcrun",
                    "swiftc",
                    "-parse-as-library",
                    "-swift-version",
                    "5",
                    "-target",
                    "arm64-apple-macosx13.0",
                    "src/native/Backend.swift",
                    "src/native/LauncherModel.swift",
                    "src/native/NativeMapTools.swift",
                    f"tests/native/{name}.swift",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                check=True,
                timeout=90,
            )
            subprocess.run(
                [str(executable), sys.executable, str(ROOT / "tests/native/map_tools_fixture.py")],
                cwd=ROOT,
                check=True,
                timeout=30,
            )
