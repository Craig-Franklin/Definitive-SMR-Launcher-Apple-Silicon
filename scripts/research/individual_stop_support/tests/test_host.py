"""Exit-read race controls against the unchanged observer and stop component."""

import json
import importlib
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_wrapper
import wrapper


class ExitReadTests(unittest.TestCase):
    def setUp(self):
        global host
        previous = {name: sys.modules.get(name) for name in ("host", "frozen_mac_processes")}

        def restore_modules():
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.addCleanup(restore_modules)
        host = importlib.import_module("host")
        self.fixture = test_wrapper.Cases("test_ready_bound_receipts")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.mode = "live"
        self.reads = 0
        self.sleeps = []
        self.backend = object.__new__(host.ExitSettlingProcesses)
        self.enterContext(
            patch.object(host.MacProcesses, "inspect", lambda backend, pid: self.read(backend, pid))
        )
        self.enterContext(
            patch.object(
                host.ExitSettlingProcesses, "stop", side_effect=AssertionError("No OS signals")
            )
        )
        self.enterContext(patch.object(host.time, "sleep", self.sleep))
        f = self.fixture
        self.owner = wrapper.Wrapper(
            f.a,
            f.packet,
            f.approval,
            f.accept,
            lambda executable: self.backend,
            f.clock,
            f.clock.sleep,
        )
        self.assertTrue(self.owner.run_once().ready)
        f.inputs()
        f.clock.now = 101
        self.assertTrue(self.owner.run_once().bound)

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.fixture.clock.sleep(duration)

    def read(self, backend, pid):
        self.assertEqual(pid, 42)
        self.reads += 1
        if self.mode == "unknown":
            raise host.SafetyError("Unrecognized inspection failure")
        if self.mode == "persistent" or self.mode != "live" and self.reads == 1:
            raise host.SafetyError("Cannot read process birth identity")
        if self.mode == "exited":
            return None
        if self.mode == "foreign":
            return SimpleNamespace(pid=42, birth_us=54321, executable="/fake/game")
        return self.fixture.fake.current

    def poll(self, mode):
        self.mode, self.reads = mode, 0
        self.fixture.clock.now = 102
        return self.owner.run_once()

    def result(self):
        return json.loads((self.fixture.packet / "result.json").read_text())["result"]

    def test_exit_race_reaches_existing_exact_observer_exit_result(self):
        state = self.poll("exited")
        self.assertEqual(state.reason, "bound_identity_unknown_or_mismatch")
        self.assertEqual(self.sleeps, [0.05])
        self.assertEqual(self.fixture.a.anchor, 100)
        result = self.result()
        self.assertEqual(result["state"], "observed_exited")
        self.assertFalse(result["stop_requested"])
        self.assertTrue(result["terminal"])
        self.assertEqual(result["reason"], "invalid_clock")

    def test_persistent_error_stays_unknown_without_signal(self):
        self.poll("persistent")
        result = self.result()
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["reason"], "invalid_clock:inspect_error")
        self.assertFalse(result["stop_requested"])
        self.assertEqual(self.sleeps, [0.05, 0.05])

    def test_unknown_error_has_no_read_retry_or_normal_exit(self):
        self.poll("unknown")
        self.assertEqual(self.result()["state"], "unknown")
        self.assertEqual(self.sleeps, [])

    def test_replacement_identity_never_becomes_normal_exit(self):
        self.poll("foreign")
        result = self.result()
        self.assertEqual(result["state"], "identity_denied")
        self.assertFalse(result["stop_requested"])
        self.assertEqual(self.sleeps, [0.05])

    def test_live_identity_after_transient_error_stays_bound(self):
        state = self.poll("transient_live")
        self.assertTrue(state.bound)
        self.assertFalse(state.terminal)
        self.assertEqual(self.sleeps, [0.05])
        self.assertFalse(self.result()["terminal"])
        self.assertFalse(self.result()["stop_requested"])


if __name__ == "__main__":
    unittest.main()
