"""Compile native presentation checks on Mac without launching the real service."""

from pathlib import Path
import platform
import select
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == "Darwin", "SwiftUI frontend targets macOS")
class NativeFrontendTests(unittest.TestCase):
    def test_one_owner_across_processes_and_unsafe_lock_refusal(self):
        with tempfile.TemporaryDirectory(prefix="smr-instance-check-") as directory:
            root = Path(directory)
            executable, lock = root / "LeaseChecks", root / "instance.lock"
            subprocess.run(
                [
                    "xcrun",
                    "swiftc",
                    "-parse-as-library",
                    "src/native/InstanceLease.swift",
                    "tests/native/LeaseChecks.swift",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                check=True,
                timeout=90,
            )

            def probe(path=lock):
                return subprocess.run(
                    [str(executable), str(path)],
                    input="",
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

            # Stale metadata does not reserve ownership without a held lock.
            lock.write_text("stale owner")
            lock.chmod(0o600)
            holder = subprocess.Popen(
                [str(executable), str(lock)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertTrue(select.select([holder.stdout], [], [], 10)[0])
                self.assertEqual(holder.stdout.readline().strip(), f"owned:{holder.pid}")
                blocked = probe()
                self.assertEqual(blocked.returncode, 2)
                self.assertEqual(blocked.stdout.strip(), f"occupied:{holder.pid}")
                # Unreadable owner metadata never permits another service owner.
                lock.write_text("invalid focus hint")
                blocked = probe()
                self.assertEqual(blocked.returncode, 2)
                self.assertEqual(blocked.stdout.strip(), "occupied:unknown")
            finally:
                holder.terminate()
                holder.communicate(timeout=10)
            self.assertEqual(probe().returncode, 0, "Process exit must release the lock")
            sentinel = root / "sentinel"
            sentinel.write_text("keep")
            linked = root / "linked.lock"
            linked.symlink_to(sentinel)
            self.assertEqual(probe(linked).returncode, 3)
            self.assertEqual(sentinel.read_text(), "keep")
            self.assertTrue(linked.is_symlink())
            lock.chmod(0o644)
            self.assertEqual(probe().returncode, 3)

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
                    "tests/native/PerformanceChecks.swift",
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
