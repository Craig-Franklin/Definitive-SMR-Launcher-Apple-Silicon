"""Cooperative queue controls use synthetic ledgers, never a game or live owner."""

from pathlib import Path
import sys
import os
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/research"))
from active_job_control import ControlRefused
from game_queue import GameQueue, game_is_free


class GameQueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name).resolve() / "queue.json"
        self.queue = GameQueue(self.path)
        self.queue.enqueue("first", "owner1")
        self.queue.enqueue("second", "owner2")

    def test_fifo_and_busy_or_unknown_availability_never_admit_successor(self):
        self.assertIsNone(self.queue.claim("second", "owner2", lambda: True))
        self.assertIsNone(self.queue.claim("first", "owner1", lambda: False))
        with self.assertRaisesRegex(ControlRefused, "availability is unknown"):
            self.queue.claim("first", "owner1", lambda: None)
        lease = self.queue.claim("first", "owner1", lambda: True)
        self.assertIsNone(GameQueue(self.path).claim("second", "owner2", lambda: True))
        self.queue.release(lease, lambda: True)
        self.assertEqual(self.queue.claim("second", "owner2", lambda: True)["owner"], "owner2")

    def test_owner_nonce_and_cleanup_failure_preserve_active_lease(self):
        with self.assertRaisesRegex(ControlRefused, "Unknown ticket owner"):
            self.queue.claim("first", "stranger", lambda: True)
        lease = self.queue.claim("first", "owner1", lambda: True)
        with self.assertRaisesRegex(ControlRefused, "cleanup is not verified"):
            self.queue.release(lease, lambda: False)
        reopened = GameQueue(self.path)
        reopened.guard(lease)
        with self.assertRaisesRegex(ControlRefused, "Stale game lease"):
            reopened.release(dict(lease, nonce="0" * 32), lambda: True)
        self.assertIsNone(reopened.claim("second", "owner2", lambda: True))
        reopened.release(lease, lambda: True)
        with self.assertRaisesRegex(ControlRefused, "not waiting"):
            reopened.claim("first", "owner1", lambda: True)

    def test_failed_cleanup_callback_does_not_reclaim_after_reopen(self):
        lease = self.queue.claim("first", "owner1", lambda: True)

        def failed():
            raise OSError("synthetic cleanup failure")

        with self.assertRaises(OSError):
            self.queue.release(lease, failed)
        GameQueue(self.path).guard(lease)
        with self.assertRaisesRegex(ControlRefused, "cannot be cancelled"):
            self.queue.cancel_waiter("first", "owner1")

    def test_read_only_game_census_refuses_unknown_and_preserves_unowned_game(self):
        processes = SimpleNamespace(games=lambda: [object()])
        self.assertFalse(game_is_free(SimpleNamespace(game_running=lambda: False), processes))
        with self.assertRaisesRegex(ControlRefused, "availability is unknown"):
            game_is_free(SimpleNamespace(game_running=lambda: None), processes)


class LifecycleSourceTests(unittest.TestCase):
    def test_real_helper_pins_accept_private_staging_and_reject_drift(self):
        import agent_calibration as common

        source = ROOT / "scripts/research/individual_stop_support"
        names = (
            "wrapper.py",
            "adapter.py",
            "observer.py",
            "individual_stop.py",
            "frozen_mac_processes.py",
            "proposal.json",
            "pins.json",
            "host.py",
        )
        closure = [
            {
                "path": str(source / name),
                "bytes": (source / name).stat().st_size,
                "sha256": common.digest((source / name).read_bytes()),
            }
            for name in names
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            staged = common.stage_stop_support(root / "stop-support", closure)
            self.assertEqual(staged.stat().st_mode & 0o777, 0o700)
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "import wrapper,sys; wrapper.pins(sys.argv[1])",
                    common.digest((staged / "pins.json").read_bytes()),
                ],
                cwd=root,
                env=dict(os.environ, PYTHONPATH=str(staged)),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(common.stage_stop_support(staged, closure), staged)
            (staged / "host.py").write_bytes(b"changed private copy")
            with self.assertRaisesRegex(common.Refused, "copy drift"):
                common.stage_stop_support(staged, closure)
            self.assertEqual((staged / "host.py").read_bytes(), b"changed private copy")
            wrong = root / "exposed"
            wrong.mkdir(mode=0o755)
            with self.assertRaisesRegex(ValueError, "private0700"):
                common.stage_stop_support(wrong, closure)
            bad = [dict(row) for row in closure]
            bad[0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(common.Refused, "source drift"):
                common.stage_stop_support(root / "wrong-source", bad)

    def test_relocated_native_proof_paths_remain_exact_and_required(self):
        import agent_calibration as common

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            helper, proof, release = [directory / name for name in ("helper", "proof", "release")]
            for path in (helper, proof, release):
                path.write_bytes(path.name.encode())
            fixed = dict(
                schema=1,
                native_helper_path=str(helper),
                native_helper_sha256=common.digest(helper.read_bytes()),
                proof_sha256=common.digest(proof.read_bytes()),
                release_match_sha256=common.digest(release.read_bytes()),
            )
            owner = common.UKCalibration.__new__(common.UKCalibration)
            owner.job = {
                "preinput_refusal_contract": dict(
                    fixed, proof_path=str(proof), release_match_path=str(release)
                )
            }
            with patch.object(common, "PREINPUT_CONTRACT", fixed):
                owner.verify_preinput_contract()
                proof.write_bytes(b"changed")
                with self.assertRaisesRegex(common.Refused, "build/proof changed"):
                    owner.verify_preinput_contract()
                owner.job["preinput_refusal_contract"]["proof_path"] = "relative-proof"
                with self.assertRaisesRegex(common.Refused, "pre-input contract required"):
                    owner.verify_preinput_contract()

    def test_actual_lifecycle_closure_refuses_changed_source(self):
        import agent_calibration as common

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "source.py"
            path.write_bytes(b"synthetic reviewed source")
            owner = common.UKCalibration.__new__(common.UKCalibration)
            owner.job = {
                "closure": [
                    {
                        "path": str(path),
                        "bytes": path.stat().st_size,
                        "sha256": common.digest(path.read_bytes()),
                    }
                ]
            }
            owner.verify_code()
            path.write_bytes(b"different source")
            with self.assertRaisesRegex(common.Refused, "Frozen code changed"):
                owner.verify_code()


if __name__ == "__main__":
    unittest.main()
