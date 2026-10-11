"""Compile native presentation checks on Mac without launching the real service."""

from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == "Darwin", "SwiftUI frontend targets macOS")
class NativeFrontendTests(unittest.TestCase):
    def test_model_state_and_subprocess_failure_boundaries(self):
        with tempfile.TemporaryDirectory(prefix="smr-native-check-") as directory:
            executable = Path(directory) / "ModelChecks"
            subprocess.run(
                [
                    "xcrun",
                    "swiftc",
                    "-parse-as-library",
                    "-swift-version",
                    "5",
                    "-O",
                    "src/native/Backend.swift",
                    "src/native/LauncherModel.swift",
                    "tests/native/ModelChecks.swift",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                check=True,
                timeout=90,
            )
            subprocess.run(
                [str(executable), sys.executable, str(ROOT / "tests/native/pipe_fixture.py")],
                check=True,
                timeout=30,
            )


if __name__ == "__main__":
    unittest.main()
