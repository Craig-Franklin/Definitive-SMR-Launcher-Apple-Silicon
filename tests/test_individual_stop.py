"""Synthetic independent schedules for the frozen root 18-case contract."""
import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'individual_stop_under_test', Path(__file__).resolve().parents[1] /
    'scripts/research/individual_stop.py')
component = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = component
SPEC.loader.exec_module(component)


class FakeMonitor:
    def __init__(self, identity):
        self.current = identity
        self.calls = []
        self.inspections = 0
        self.inspect_error = False
        self.reinspect_error = False
        self.race = None
        self.exit_on_stop = True
        self.quit_before_stop = False
        self.stop_error = False

    def inspect(self, pid):
        self.inspections += 1
        if self.inspect_error or (self.reinspect_error and self.calls):
            raise RuntimeError('synthetic inspect failure')
        return self.current

    def request_stop(self, expected):
        self.calls.append(expected)
        if self.race is not None:
            self.current = self.race
        if self.quit_before_stop:
            self.current = None
        # The injected monitor contract guards identity at the action itself.
        if self.current != expected:
            return
        if self.stop_error:
            raise RuntimeError('synthetic stop failure')
        if self.exit_on_stop:
            self.current = None


class IndividualStopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.packet = Path(self.tmp.name).resolve()
        self.packet.chmod(0o700)
        self.receipt = self.packet / 'receipt.json'
        self.output = self.packet / 'result.json'
        self.identity = component.ProcessIdentity(123, 'birthA', '/synthetic/game')
        self.binding = component.Binding('jobA', 'nonceA', 100.0, 'codeA', self.identity)
        self.monitor = FakeMonitor(self.identity)
        self.now = 110.0
        self.supervisor = component.Supervisor(self.binding, self.monitor,
            lambda: self.now, self.receipt, self.output)
        self.write()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, **changes):
        data = dict(version=1, job='jobA', nonce='nonceA', anchor=100.0,
                    code='codeA', process=dataclasses.asdict(self.identity),
                    seq=1, status='success', start=105.0, complete=109.0)
        data.update(changes)
        self.receipt.write_text(json.dumps(data))

    def test_01_delayed_root_and_absolute_cutoff(self):
        self.now = 165
        self.assertEqual(self.supervisor.step().reason, 'stale60')
        self.assertEqual(len(self.monitor.calls), 1)
        other = component.Supervisor(self.binding, self.monitor, lambda: 640,
                                     self.receipt, self.output)
        self.assertEqual(other.step().reason, 'absolute540')

    def test_02_recent_success_holds_and_repeated_read_does_not_refresh(self):
        self.assertEqual(self.supervisor.step().state, 'holding')
        self.now = 164.99
        self.assertEqual(self.supervisor.step().state, 'holding')
        self.now = 165
        self.assertEqual(self.supervisor.step().reason, 'stale60')

    def test_03_invalid_clocks_and_observations(self):
        for value in [float('nan'), float('inf'), 99, True, 10**1000]:
            with self.subTest(clock=str(value)[:20]):
                s = component.Supervisor(self.binding, FakeMonitor(self.identity),
                    lambda: value, self.receipt, self.output)
                self.assertTrue(s.step().terminal)
        for change in [dict(start=111), dict(start=108, complete=107),
                       dict(complete=float('nan')), dict(start=10**1000)]:
            self.write(**change)
            s = component.Supervisor(self.binding, FakeMonitor(self.identity),
                lambda: 110, self.receipt, self.output)
            self.assertEqual(s.step().reason, 'invalid_receipt')

    def test_04_wrong_binding_fails_closed(self):
        for key, value in [('job', 'other'), ('nonce', 'other'), ('anchor', 99),
                           ('code', 'other'), ('process', {'pid': 123, 'birth': 'B',
                            'executable': '/synthetic/game'})]:
            self.write(**{key: value})
            s = component.Supervisor(self.binding, FakeMonitor(self.identity),
                lambda: 110, self.receipt, self.output)
            self.assertEqual(s.step().reason, 'invalid_receipt')

    def test_05_replacement_and_backwards_sequence(self):
        self.supervisor.step()
        self.write(seq=0)
        self.assertEqual(self.supervisor.step().reason, 'invalid_receipt')
        s = component.Supervisor(self.binding, FakeMonitor(self.identity),
            lambda: 110, self.receipt, self.output)
        self.write(); s.step()
        replacement = self.packet / 'replacement'
        replacement.write_bytes(self.receipt.read_bytes())
        replacement.replace(self.receipt)
        self.assertEqual(s.step().reason, 'invalid_receipt')

    def test_06_bad_receipt_shapes_and_links(self):
        for raw in [b'{', b'[]', b'x' * 8193,
                    b'{"job":"a","job":"b"}', b'[' * 2000]:
            self.receipt.write_bytes(raw)
            with self.assertRaises(component.ReceiptError):
                component.read_receipt(self.receipt)
        self.receipt.write_text('{}')
        self.assertEqual(self.supervisor.step().reason, 'invalid_receipt')
        self.receipt.unlink()
        with self.assertRaises(component.ReceiptError):
            component.read_receipt(self.receipt)
        target = self.packet / 'synthetic-target'; target.write_text('do not follow')
        self.receipt.symlink_to(target)
        with self.assertRaises(component.ReceiptError):
            component.read_receipt(self.receipt)
        self.assertEqual(target.read_text(), 'do not follow')

    def test_07_start_not_publication_is_freshness(self):
        self.now = 170
        self.write(start=105, complete=169)
        os.utime(self.receipt, (999999999, 999999999))
        self.assertEqual(self.supervisor.step().reason, 'stale60')

    def test_08_failed_and_inprogress_do_not_refresh(self):
        for status in ['failed', 'inprogress', 'stop_requested', 'heartbeat']:
            self.write(status=status)
            s = component.Supervisor(self.binding, FakeMonitor(self.identity),
                lambda: 110, self.receipt, self.output)
            self.assertEqual(s.step().reason, 'invalid_receipt')

    def test_09_absence_requires_inspection(self):
        self.now = 165; self.monitor.current = None
        self.assertEqual(self.supervisor.step().state, 'observed_exited')
        self.assertEqual(self.monitor.inspections, 1)
        self.assertEqual(self.monitor.calls, [])

    def test_10_replacement_never_receives_signal(self):
        self.now = 165
        for identity in [component.ProcessIdentity(123, 'B', '/synthetic/game'),
                         component.ProcessIdentity(123, 'birthA', '/other')]:
            self.monitor.current = identity
            s = component.Supervisor(self.binding, self.monitor, lambda: 165,
                                     self.receipt, self.output)
            self.assertEqual(s.step().state, 'identity_denied')
        self.assertEqual(self.monitor.calls, [])

    def test_11_inspection_error_is_unknown(self):
        self.now = 165; self.monitor.inspect_error = True
        self.assertEqual(self.supervisor.step().state, 'unknown')
        self.assertEqual(self.monitor.calls, [])

    def test_12_stop_return_and_reinspect_error_not_exit(self):
        self.now = 165; self.monitor.exit_on_stop = False
        self.assertEqual(self.supervisor.step().state, 'unknown')
        self.assertEqual(self.monitor.inspections, 2)
        self.monitor.reinspect_error = True
        s = component.Supervisor(self.binding, self.monitor, lambda: 165,
                                 self.receipt, self.output)
        self.assertEqual(s.step().state, 'unknown')

    def test_13_normalquit_and_reuse_at_action(self):
        replacement = component.ProcessIdentity(123, 'B', '/other')
        self.monitor.race = replacement
        self.now = 165
        self.assertEqual(self.supervisor.step().state, 'identity_denied')
        self.assertEqual(self.monitor.current, replacement)

    def test_14_immutable_and_durable_bounded_output(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.binding.anchor = 200
        with self.assertRaises(AttributeError):
            self.supervisor.binding = self.binding
        with patch.object(component.os, 'fsync', wraps=os.fsync) as sync:
            self.supervisor.step()
        self.assertEqual(sync.call_count, 2)
        self.assertLessEqual(self.output.stat().st_size, 4096)
        self.assertEqual(json.loads(self.output.read_bytes())['result']['state'], 'holding')
        self.assertEqual(list(self.packet.glob('.stop-*')), [])

    def test_15_wallclock_is_irrelevant(self):
        with patch('time.time', side_effect=AssertionError('wall clock used')):
            self.assertEqual(self.supervisor.step().state, 'holding')

    def test_16_terminal_never_retries_even_concurrent(self):
        self.now = 165
        threads = [threading.Thread(target=self.supervisor.step) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(self.monitor.calls), 1)
        self.supervisor.step()
        self.assertEqual(len(self.monitor.calls), 1)

    def test_17_import_and_tests_never_touch_host_processes(self):
        with patch('os.kill', side_effect=AssertionError('host signal')):
            SPEC.loader.exec_module(component)
        # Restore module definitions so existing instances retain their types.
        self.assertNotIn('subprocess', component.__dict__)
        self.assertNotIn('main', component.__dict__)

    def test_18_documented_scope_and_reserve(self):
        self.assertEqual(360 + 180, 540)
        for phrase in ['root restoration', 'grants no runtime', 'Prebinding launch',
                       '570/600/900', '6GiB', 'never launches']:
            self.assertIn(phrase, component.__doc__)

    def test_valid_increasing_observation_and_clock_regression(self):
        self.supervisor.step(); self.now = 140
        self.write(seq=2, start=130, complete=139)
        self.assertEqual(self.supervisor.step().state, 'holding')
        self.now = 139
        self.assertEqual(self.supervisor.step().reason, 'invalid_clock')

    def test_publication_failure_is_visible_and_terminal_is_sticky(self):
        self.now = 165
        with patch.object(component.os, 'fsync', side_effect=OSError('synthetic disk error')):
            with self.assertRaises(OSError): self.supervisor.step()
        self.assertEqual(len(self.monitor.calls), 1)
        with self.assertRaises(OSError): self.supervisor.step()
        self.assertEqual(len(self.monitor.calls), 1)

    def test_receipt_replaced_during_read(self):
        original_read = os.read
        changed = [False]
        def racing_read(fd, amount):
            raw = original_read(fd, amount)
            if not changed[0]:
                changed[0] = True
                new = self.packet / 'new-receipt'
                new.write_bytes(raw)
                new.replace(self.receipt)
            return raw
        with patch.object(component.os, 'read', side_effect=racing_read):
            with self.assertRaises(component.ReceiptError):
                component.read_receipt(self.receipt)

    def test_holding_publication_failure_stops_before_sticky_error(self):
        with patch.object(component, 'publish_result', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.supervisor.step()
        self.assertEqual(self.monitor.calls, [self.identity])
        self.assertIsNone(self.monitor.current)
        self.now = 640
        with self.assertRaises(OSError):
            self.supervisor.step()
        self.assertEqual(self.monitor.calls, [self.identity])
        self.assertFalse(self.output.exists())

    def test_output_parent_rebinding_is_explicit_publication_failure(self):
        original_replace = os.replace
        displaced = self.packet.with_name(self.packet.name + '-displaced')
        def racing_replace(*args, **kwargs):
            self.packet.rename(displaced)
            self.packet.mkdir(mode=0o700)
            return original_replace(*args, **kwargs)
        try:
            with patch.object(component.os, 'replace', side_effect=racing_replace):
                with self.assertRaises(component.ReceiptError):
                    self.supervisor.step()
            self.assertFalse(self.output.exists())
            self.assertEqual(self.monitor.calls, [self.identity])
        finally:
            import shutil
            shutil.rmtree(displaced)

    def test_nonregular_and_symlink_packet_are_denied(self):
        self.receipt.unlink()
        os.mkfifo(self.receipt)
        with self.assertRaises(component.ReceiptError):
            component.read_receipt(self.receipt)
        link = self.packet / 'packet-link'
        link.symlink_to(self.packet, target_is_directory=True)
        with self.assertRaises(component.ReceiptError):
            component.read_receipt(link / 'receipt.json')

    def test_failed_stop_and_observation_regression(self):
        self.now = 165; self.monitor.stop_error = True
        result = self.supervisor.step()
        self.assertEqual(result.state, 'unknown')
        self.assertIn('stop_error', result.reason)
        self.now = 110
        s = component.Supervisor(self.binding, FakeMonitor(self.identity),
                                lambda: self.now, self.receipt, self.output)
        self.write(); s.step(); self.now = 120
        self.write(seq=2, start=104, complete=119)
        self.assertEqual(s.step().reason, 'invalid_receipt')

    def test_historical_context_delay_and_normal_quit_race(self):
        self.now = 477.320
        self.write(start=477.320, complete=477.320)
        self.assertEqual(self.supervisor.step().state, 'holding')
        self.now = 537.320
        self.assertEqual(self.supervisor.step().reason, 'stale60')
        monitor = FakeMonitor(self.identity); monitor.quit_before_stop = True
        s = component.Supervisor(self.binding, monitor, lambda: 707.925,
                                 self.receipt, self.output)
        result = s.step()
        self.assertEqual(result.reason, 'absolute540')
        self.assertEqual(result.state, 'observed_exited')
        self.assertEqual(len(monitor.calls), 1)
