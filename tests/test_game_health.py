"""Injected resource observations; never launch or signal a real game."""
import signal
import unittest
from unittest.mock import Mock

from smr_launcher.game_health import ResourceGuard, stop_game, GIB

PROCESS = dict(pid=123, birth_us=456, executable="/Synthetic/Game")


class ResourceControls(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.probe = Mock(return_value=9 * GIB)
        self.stop = Mock()
        self.retain = Mock()
        self.guard = ResourceGuard(probe=self.probe, stopper=self.stop, clock=lambda: self.now)

    def observe(self, process=PROCESS):
        return self.guard.observe(process, self.retain)

    def test_sustained_limit_and_durability_before_stop(self):
        self.observe()
        self.now = 4.99
        self.observe()
        self.stop.assert_not_called()
        self.stop.side_effect = lambda p: self.retain.assert_called_once_with(p, 9 * GIB)
        self.now = 5
        self.assertEqual(self.observe(), 9 * GIB)
        self.observe()
        self.stop.assert_called_once_with(PROCESS)

    def test_hard_limit_stops_immediately(self):
        self.probe.return_value = 16 * GIB
        self.observe()
        self.stop.assert_called_once()

    def test_transient_growth_and_unknown_observation_reset_timer(self):
        self.observe()
        self.now = 4
        self.probe.return_value = None
        self.observe()
        self.probe.return_value = 9 * GIB
        self.now = 5
        self.observe()
        self.now = 9
        self.observe()
        self.stop.assert_not_called()
        self.probe.return_value = 2 * GIB
        self.now = 10000
        self.observe()
        self.stop.assert_not_called()

    def test_inspection_failure_resets_timer(self):
        self.observe()
        self.now = 4
        self.probe.side_effect = OSError()
        with self.assertRaises(OSError): self.observe()
        self.probe.side_effect = None
        self.now = 5
        self.observe()
        self.stop.assert_not_called()

    def test_replacement_never_inherits_previous_timer(self):
        self.observe()
        self.now = 5
        self.observe(dict(PROCESS, birth_us=789))
        self.stop.assert_not_called()

    def test_full_disk_does_not_prevent_resource_protection(self):
        self.probe.return_value = 200 * GIB
        self.retain.side_effect = OSError()
        self.observe()
        self.stop.assert_called_once_with(PROCESS)
        self.assertTrue(self.guard.handled)
        self.assertTrue(self.guard.retention_failed)

    def test_stop_failure_is_not_repeated(self):
        self.probe.return_value = 200 * GIB
        self.stop.side_effect = OSError()
        with self.assertRaises(OSError): self.observe()
        self.observe()
        self.stop.assert_called_once()

    def test_stop_rechecks_identity_before_signals_and_escalation(self):
        now = [0]
        def wait(seconds): now[0] += seconds
        probe = Mock(return_value=PROCESS)
        send = Mock()
        def signal_sent(pid, sig):
            if sig == signal.SIGKILL: probe.return_value = None
        send.side_effect = signal_sent
        stop_game(PROCESS, probe=probe, send_signal=send, wait=wait, clock=lambda: now[0])
        self.assertEqual([c.args for c in send.call_args_list],
                         [(123, signal.SIGTERM), (123, signal.SIGKILL)])
        for current in (dict(PROCESS, birth_us=789), dict(PROCESS, executable="/Other/Game")):
            send.reset_mock()
            with self.assertRaises(OSError):
                stop_game(PROCESS, probe=lambda exe: current, send_signal=send)
            send.assert_not_called()

    def test_replacement_after_term_refuses_kill(self):
        probe = Mock(side_effect=[PROCESS, dict(PROCESS, birth_us=789)])
        send = Mock()
        with self.assertRaises(OSError): stop_game(PROCESS, probe=probe, send_signal=send)
        send.assert_called_once_with(123, signal.SIGTERM)

    def test_missing_or_unreadable_process_never_signaled(self):
        send = Mock()
        stop_game(PROCESS, probe=lambda exe: None, send_signal=send)
        with self.assertRaises(OSError):
            stop_game(PROCESS, probe=Mock(side_effect=OSError()), send_signal=send)
        send.assert_not_called()

    def test_stop_has_a_finite_deadline(self):
        now = [0]
        def wait(seconds): now[0] += seconds
        send = Mock()
        with self.assertRaises(OSError):
            stop_game(PROCESS, probe=lambda exe: PROCESS, send_signal=send,
                      wait=wait, clock=lambda: now[0])
        self.assertLess(now[0], 5.3)
        self.assertEqual(send.call_count, 2)
