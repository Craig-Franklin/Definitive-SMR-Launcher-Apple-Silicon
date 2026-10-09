"""Passive controller integration controls with local synthetic crash reports."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from smr_launcher.crash_monitor import CrashMonitor

START = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
EXE = '/Applications/Synthetic.app/Contents/MacOS/Game'
PROCESS = dict(pid=123, birth_us=int(START.timestamp() * 1000000), executable=EXE)


def report(*, pid=123, birth=None, private='SECRET_CANARY'):
    return json.dumps(dict(procPath=EXE, pid=pid, procLaunch=birth or START.isoformat(),
        captureTime='2026-01-02T12:01:00+00:00', exception=dict(type='EXC_BAD_ACCESS', signal='SIGSEGV'),
        termination=dict(namespace='SIGNAL', code=11), private=private)).encode()


class MonitorControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name).resolve()
        self.reports = self.library / 'diagnostics'
        self.reports.mkdir()
        self.app = SimpleNamespace(library=self.library,
            installation=SimpleNamespace(executable=Path(EXE), executable_sha256='a'*64, bundle_version='1.0'),
            profiles=SimpleNamespace(active_profile=lambda: 'original'), catalogue=lambda: [])
        self.probe = Mock(return_value=PROCESS)
        self.publisher = Mock()
        self.publisher.publish.return_value = dict(status='published', issue_url='https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/999')

    def monitor(self):
        value = CrashMonitor(self.app, '1.2.3', reports_directory=self.reports,
            process_probe=self.probe, publisher=self.publisher, now=lambda: START)
        self.addCleanup(value.close)
        return value

    def write_report(self, name='Sid Meiers Railroads_1.ips', **kwargs):
        path = self.reports / name
        path.write_bytes(report(**kwargs))
        return path

    def test_local_collection_default_and_private_files(self):
        self.write_report()
        monitor = self.monitor()
        monitor.poll_once()
        entries = monitor.store.pending()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['payload']['exception_type'], 'EXC_BAD_ACCESS')
        self.assertNotIn('SECRET_CANARY', json.dumps(entries[0]['payload']))
        self.publisher.publish.assert_not_called()
        self.assertEqual((monitor.root.stat().st_mode & 0o777), 0o700)
        for path in monitor.root.rglob('*'):
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_no_report_no_crash_and_wrong_pid_private_only(self):
        monitor = self.monitor()
        monitor.poll_once()
        self.assertEqual(monitor.store.pending(), [])
        self.write_report(pid=124)
        monitor.poll_once()
        self.assertEqual(monitor.store.pending(), [])
        self.assertEqual(len(list((monitor.root/'unconfirmed').glob('*.raw'))), 1)
        self.publisher.publish.assert_not_called()

    def test_durable_old_session_delayed_report_after_new_game(self):
        first = self.monitor()
        first.poll_once()
        first.close()
        self.probe.return_value = dict(PROCESS, pid=456, birth_us=PROCESS['birth_us']+120000000)
        second = self.monitor()
        second.poll_once()
        self.write_report()
        second.poll_once()
        self.assertEqual(len(second.store.pending()), 1)
        self.assertEqual(len(second.sessions), 2)

    def test_probe_failure_still_collects_retained_session(self):
        monitor = self.monitor()
        monitor.poll_once()
        self.probe.side_effect = OSError('PRIVATE_ERROR_CANARY')
        self.write_report()
        monitor.poll_once()
        self.assertEqual(len(monitor.store.pending()), 1)
        self.assertNotIn('PRIVATE_ERROR_CANARY', monitor.status)

    def test_sending_is_durable_before_post_and_ambiguity_never_reposts(self):
        monitor = self.monitor()
        self.write_report()
        def ambiguous(payload, **kwargs):
            self.assertEqual(monitor.store.pending()[0]['publication']['status'], 'unknown')
            raise OSError('PRIVATE_ERROR_CANARY')
        self.publisher.publish.side_effect = ambiguous
        monitor.configure(enabled=True, automatic_reporting=True)
        monitor.poll_once()
        self.assertEqual(self.publisher.publish.call_count, 1)
        self.assertEqual(monitor.store.pending()[0]['publication']['status'], 'unknown')
        monitor.next_publish = 0
        monitor.poll_once()
        self.assertEqual(self.publisher.publish.call_count, 1)
        self.publisher.publish.side_effect = None
        self.publisher.publish.return_value = dict(status='unknown')
        monitor.poll_once(reconcile=True)
        self.assertTrue(self.publisher.publish.call_args.kwargs['reconcile_only'])
        self.assertEqual(monitor.store.pending()[0]['publication']['status'], 'unknown')

    def test_pre_mutation_unavailable_retry_and_success_dedup(self):
        monitor = self.monitor()
        self.write_report()
        monitor.configure(enabled=True, automatic_reporting=True)
        self.publisher.publish.return_value = dict(status='pending')
        monitor.poll_once()
        self.assertEqual(monitor.store.pending()[0]['publication']['status'], 'pending')
        self.publisher.publish.return_value = dict(status='published', issue_url=None)
        monitor.next_publish = 0
        monitor.poll_once()
        self.assertEqual(monitor.store.pending(), [])
        monitor.next_publish = 0
        monitor.poll_once()
        self.assertEqual(self.publisher.publish.call_count, 2)

    def test_settings_persist_disable_and_no_game_control(self):
        first = self.monitor()
        first.configure(enabled=False, automatic_reporting=True)
        second = self.monitor()
        second.poll_once()
        self.assertFalse(second.enabled)
        self.assertTrue(second.automatic_reporting)
        self.probe.assert_not_called()
        self.assertFalse(hasattr(second, 'launch'))
        self.assertFalse(hasattr(second, 'signal'))

    def test_incomplete_report_does_not_starve_valid_report(self):
        monitor = self.monitor()
        self.write_report()
        (self.reports/'Sid Meiers Railroads_partial.ips').write_bytes(b'{')
        monitor.poll_once()
        self.assertEqual(len(monitor.store.pending()), 1)

    def test_report_symlink_never_read(self):
        self.write_report('unrelated.ips')
        (self.reports/'Sid Meiers Railroads_link.ips').symlink_to(self.reports/'unrelated.ips')
        monitor = self.monitor()
        monitor.poll_once()
        self.assertEqual(monitor.store.pending(), [])

class RedactionCorrelationControls(unittest.TestCase):
    def parse(self, *, report_change=None, context_change=None):
        from smr_launcher.crash_reports import parse_crash_report
        alias = '/Users/USER/Library/Application Support/Steam/*/Synthetic.app/Contents/MacOS/Game'
        context = dict(game_executable_sha256='a'*64, launcher_version='1.2.3',
            expected_bundle_id='example.game', expected_redacted_path=alias,
            expected_image_uuid='12345678-1234-5678-1234-567812345678',
            process_birth_tolerance_us=1000)
        context.update(context_change or {})
        value = json.loads(report())
        value.update(procPath=alias, procLaunch='2026-01-02T12:00:00.000500Z',
            bundleInfo=dict(CFBundleIdentifier='example.game'),
            usedImages=[dict(path=alias, uuid=context['expected_image_uuid'])],
            threads=[dict(triggered=True, frames=[dict(imageIndex=0, imageOffset=256)])])
        value.update(report_change or {})
        return parse_crash_report(json.dumps(value).encode(), expected_executable=EXE,
            expected_pid=123, session_started_at=START, context=context)

    def test_known_alias_uuid_bundle_pid_and_bounded_birth(self):
        payload = self.parse()
        self.assertEqual(payload['exception_type'], 'EXC_BAD_ACCESS')
        self.assertEqual(payload['frames'][0]['image_role'], 'game')
        self.assertNotIn('/Users/', json.dumps(payload))

    def test_wrong_or_missing_qualifier_cannot_publish(self):
        for change in (dict(pid=124), dict(procPath='/Users/USER/other/Game'),
                dict(procLaunch='2026-01-02T12:00:00.002Z'), dict(procLaunch=None),
                dict(bundleInfo=dict(CFBundleIdentifier='other.game')), dict(usedImages=[]),
                dict(usedImages=[dict(path='/Users/USER/Library/Application Support/Steam/*/Synthetic.app/Contents/MacOS/Game',
                     uuid='87654321-4321-8765-4321-876543218765')])):
            with self.subTest(change=change):
                self.assertIsNone(self.parse(report_change=change))

    def test_null_termination_with_actual_exception_is_crash(self):
        self.assertEqual(self.parse(report_change=dict(termination=None))['exception_type'], 'EXC_BAD_ACCESS')

    def test_expected_uuid_enforced_for_exact_path_too(self):
        good = dict(procPath=EXE, usedImages=[dict(path=EXE, uuid='12345678-1234-5678-1234-567812345678')])
        self.assertIsNotNone(self.parse(report_change=good))
        for images in ([], [dict(path=EXE, uuid='87654321-4321-8765-4321-876543218765')],
                       good['usedImages'] * 2):
            self.assertIsNone(self.parse(report_change=dict(procPath=EXE, usedImages=images)))

    def test_precision_is_opt_in_and_bounded(self):
        from smr_launcher.crash_reports import CrashReportError
        self.assertIsNone(self.parse(context_change=dict(process_birth_tolerance_us=0)))
        for tolerance in (-1, 1001, True, 1.5):
            with self.assertRaises(CrashReportError):
                self.parse(context_change=dict(process_birth_tolerance_us=tolerance))

    def test_image_uuid_reader_bounded_commands(self):
        import struct
        import uuid
        from smr_launcher.crash_monitor import executable_image_uuid
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'image'
            header = struct.pack('<IIIIIIII', 0xfeedfacf, 0x1000007, 3, 2, 1, 24, 0, 0)
            command = struct.pack('<II', 0x1b, 24) + uuid.UUID('12345678-1234-5678-1234-567812345678').bytes
            path.write_bytes(header + command)
            self.assertEqual(executable_image_uuid(path), '12345678-1234-5678-1234-567812345678')
            path.write_bytes(header + struct.pack('<II', 0x1b, 200000) + command[8:])
            self.assertIsNone(executable_image_uuid(path))
            path.write_bytes(b'x'*32)
            self.assertIsNone(executable_image_uuid(path))

class ReviewFindingControls(MonitorControls):
    def test_blocked_publisher_cannot_block_play_arm(self):
        from threading import Event, Thread
        monitor = self.monitor()
        self.write_report()
        monitor.configure(enabled=True, automatic_reporting=True)
        entered, release, armed = Event(), Event(), Event()
        def publish(payload, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError()
            return dict(status='pending')
        self.publisher.publish.side_effect = publish
        polling = Thread(target=monitor.poll_once)
        polling.start()
        try:
            self.assertTrue(entered.wait(1))
            arming = Thread(target=lambda: (monitor.arm(), armed.set()))
            arming.start()
            self.assertTrue(armed.wait(0.5), 'Play arm blocked behind network')
            arming.join(1)
        finally:
            release.set()
            polling.join(2)
        self.assertFalse(polling.is_alive())

    def test_immediate_exact_binding_and_no_game_control(self):
        monitor = self.monitor()
        monitor.arm()
        self.assertTrue(monitor.bind_after_launch('original'))
        self.assertEqual(monitor.session['process'], PROCESS)
        self.assertEqual(len(monitor.sessions), 2)

    def test_forced_termination_retained_private_never_published(self):
        monitor = self.monitor()
        value = json.loads(report())
        value['exception'] = dict(type='EXC_CRASH', signal='SIGKILL')
        (self.reports/'Sid Meiers Railroads_forced.ips').write_text(json.dumps(value))
        monitor.configure(enabled=True, automatic_reporting=True)
        monitor.poll_once()
        self.assertEqual(monitor.store.pending(), [])
        self.assertEqual(len(list((monitor.root/'unconfirmed').glob('*.raw'))), 1)
        self.publisher.publish.assert_not_called()
