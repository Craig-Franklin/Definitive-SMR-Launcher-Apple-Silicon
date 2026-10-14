"""Synthetic outcome and cleanup checks for the shared finite lifecycle."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/research"))
from active_job_control import ControlRefused, UserCancelled
from finite_operation import run_calibration


class FiniteOperationTests(unittest.TestCase):
    def adapter(self, failure=None):
        events = []

        def observe():
            events.append("observe")
            if failure is not None:
                raise failure
            return {"status": "safety_stop", "cause": "synthetic_observation"}

        adapter = SimpleNamespace(
            prepare=Mock(side_effect=lambda: events.append("prepare")),
            launch=Mock(side_effect=lambda: events.append("launch")),
            observe=observe,
            persist_outcome=Mock(side_effect=lambda value: events.append("raw_outcome")),
            stop=Mock(side_effect=lambda: events.append("stop") or True),
            restore=Mock(side_effect=lambda: events.append("restore") or True),
            finish=Mock(side_effect=lambda *args: events.append("finish")),
        )
        return adapter, events

    def test_operation_error_survives_complete_failed_terminal_settlement(self):
        adapter, events = self.adapter(ControlRefused("synthetic operation failure"))
        result = run_calibration(adapter)
        self.assertTrue(result["terminal_finished"], result["errors"])
        self.assertEqual(
            result["errors"], ["operation: ControlRefused: synthetic operation failure"]
        )
        self.assertEqual(
            result["outcome"],
            {"status": "automation_failed", "cause": "ControlRefused: synthetic operation failure"},
        )
        adapter.persist_outcome.assert_called_once_with(result["outcome"])
        adapter.finish.assert_called_once_with(result["outcome"], True, True)
        self.assertEqual(
            events, ["prepare", "launch", "observe", "raw_outcome", "stop", "restore", "finish"]
        )

    def test_each_failed_boundary_keeps_terminal_pending_and_stop_is_unconditional(self):
        for boundary in ("persist_outcome", "stop", "restore", "finish"):
            for failure in (False, OSError("synthetic boundary failure")):
                with self.subTest(boundary=boundary, failure=failure):
                    adapter, _ = self.adapter(ControlRefused("synthetic operation failure"))
                    hook = getattr(adapter, boundary)
                    hook.side_effect = failure if isinstance(failure, BaseException) else None
                    hook.return_value = False
                    result = run_calibration(adapter)
                    self.assertFalse(result["terminal_finished"])
                    self.assertEqual(result["outcome"]["status"], "automation_failed")
                    self.assertEqual(
                        result["errors"][0],
                        "operation: ControlRefused: synthetic operation failure",
                    )
                    self.assertEqual(len(result["errors"]), 2)
                    adapter.stop.assert_called_once()
                    if boundary in ("persist_outcome", "stop"):
                        adapter.restore.assert_not_called()
                    if boundary != "finish":
                        adapter.finish.assert_not_called()

    def test_stop_and_restoration_require_literal_positive_proof(self):
        for boundary in ("stop", "restore"):
            for value in (None, 1, "true"):
                with self.subTest(boundary=boundary, value=value):
                    adapter, _ = self.adapter()
                    hook = getattr(adapter, boundary)
                    hook.side_effect, hook.return_value = None, value
                    result = run_calibration(adapter)
                    self.assertFalse(result["terminal_finished"])
                    adapter.finish.assert_not_called()

    def test_user_cancellation_preserves_unknown_gameplay_and_completes_cleanup(self):
        adapter, events = self.adapter(UserCancelled("synthetic handback"))
        result = run_calibration(adapter)
        self.assertTrue(result["terminal_finished"], result["errors"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["outcome"]["cause"], "user_requested_game_handback")
        self.assertEqual(result["outcome"]["world_loaded"], "unknown")
        self.assertFalse(result["outcome"]["gameplay_credit"])
        self.assertFalse(result["outcome"]["save_reopen_credit"])
        self.assertEqual(events[-4:], ["raw_outcome", "stop", "restore", "finish"])

    def test_prepare_failure_prevents_launch_and_still_settles_failure(self):
        adapter, _ = self.adapter()
        adapter.prepare.side_effect = OSError("synthetic preparation failure")
        result = run_calibration(adapter)
        adapter.launch.assert_not_called()
        adapter.stop.assert_called_once()
        self.assertTrue(result["terminal_finished"], result["errors"])
        self.assertEqual(result["outcome"]["status"], "automation_failed")
