"""Persistent edition warnings must not become fabricated scenario failures."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import test_application as application_fixtures
import test_crash_monitor as monitor_fixtures
from test_crash_monitor import PROCESS
from smr_launcher.game_health import ResourceGuard, GIB
from smr_launcher.map_issues import MapIssueStore
from smr_launcher.application import LauncherApplication
from smr_launcher.gui import LauncherWindow


class MapWarningControls(unittest.TestCase):
    setUp = application_fixtures.ApplicationTests.setUp

    def test_warning_persists_and_is_visible_to_all_map_views(self):
        app = self.application
        app.setup()
        record = app.import_archive(self.archive)
        context = dict(archive_sha256=record.archive_sha256, variant_id=record.variant_id,
                       game_executable_sha256=app.installation.executable_sha256)
        store = MapIssueStore(app.library)
        def retain():
            return store.record(context, incident_id="b"*64,
                occurred_at="2026-10-11T14:00:00+00:00", kind="resource_limit",
                detail="Map loading failed with excessive memory use.")
        retain(); retain()
        self.assertEqual(len(store.for_map(record, app.installation.executable_sha256)), 1)
        result = app.verification_for(record)
        self.assertEqual(result.status, "Known issue")
        self.assertIn("excessive memory", result.issue)
        self.assertIn("excessive memory", LauncherWindow._verification_details(result))
        self.assertTrue(all(s.status == "Not verified" for s in result.scenarios))
        self.assertEqual(app.catalogue_verification()[record.variant_id].status, "Known issue")
        restarted = LauncherApplication(app.installation, app.library)
        self.assertEqual(restarted.verification_for(record).status, "Known issue")
        self.assertEqual(store.for_map(replace(record, variant_id="c"*64), context["game_executable_sha256"]), ())
        self.assertEqual(store.for_map(record, "d"*64), ())
        self.assertEqual(store.for_map(replace(record, archive_sha256="e"*64), context["game_executable_sha256"]), ())

    def test_unbound_context_does_not_assign_map_issue(self):
        self.application.setup()
        store = MapIssueStore(self.application.library)
        self.assertIsNone(store.record({}, incident_id="b"*64,
            occurred_at="2026-10-11T14:00:00+00:00", kind="crash", detail="Crash"))
        self.assertFalse(store.root.exists())


class MonitorWarningControls(unittest.TestCase):
    setUp = monitor_fixtures.MonitorControls.setUp
    monitor = monitor_fixtures.MonitorControls.monitor
    write_report = monitor_fixtures.MonitorControls.write_report

    def bind_map(self):
        record = SimpleNamespace(profile_id="map-test", archive_sha256="b"*64,
                                 variant_id="c"*64)
        self.app.catalogue = lambda: [record]
        self.app.profiles.active_profile = lambda: record.profile_id
        return record

    def test_confirmed_crash_warns_locally_with_reporting_disabled_and_backfills(self):
        record = self.bind_map()
        self.write_report()
        monitor = self.monitor()
        monitor.poll_once()
        store = MapIssueStore(self.app.library)
        self.assertEqual(len(store.for_map(record, "a"*64)), 1)
        self.publisher.publish.assert_not_called()
        # Simulate a pre-upgrade crash incident with no issue receipt.
        for path in store.root.glob("*.json"): path.unlink()
        restarted = self.monitor()
        self.assertEqual(len(store.for_map(record, "a"*64)), 1)
        restarted.poll_once()
        self.assertEqual(len(store.for_map(record, "a"*64)), 1)

    def test_guard_retains_evidence_and_warning_before_stop_even_when_crash_monitor_off(self):
        record = self.bind_map()
        monitor = self.monitor()
        monitor.configure(enabled=False, automatic_reporting=False)
        def stop(process):
            self.assertEqual(process, PROCESS)
            self.assertEqual(len(list(monitor.root.glob("resource-*.json"))), 1)
            self.assertEqual(len(monitor.map_issues.for_map(record, "a"*64)), 1)
        stopper = Mock(side_effect=stop)
        monitor.resource_guard = ResourceGuard(probe=lambda p: 200*GIB, stopper=stopper)
        monitor.poll_health_once()
        stopper.assert_called_once()
        self.assertEqual(len(monitor.take_events()), 1)
        self.assertEqual(monitor.take_events(), [])
        self.assertEqual(monitor.store.pending(), [])
        self.publisher.publish.assert_not_called()
        monitor.poll_health_once()
        stopper.assert_called_once()
        for path in monitor.map_issues.root.glob("*.json"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_guard_preference_persists_and_disabled_never_observes_resources(self):
        monitor = self.monitor()
        monitor.configure(enabled=True, automatic_reporting=False, protect_resources=False)
        restarted = self.monitor()
        self.assertFalse(restarted.protect_resources)
        restarted.resource_guard.probe = Mock()
        restarted.poll_health_once()
        restarted.resource_guard.probe.assert_not_called()

    def test_normal_exit_and_wrong_pid_diagnostics_never_mark_map(self):
        record = self.bind_map()
        monitor = self.monitor()
        monitor.poll_once()
        self.probe.return_value = None
        self.write_report(pid=999)
        monitor.poll_once()
        self.assertEqual(monitor.map_issues.for_map(record, "a"*64), ())

    def test_blocked_publisher_does_not_block_resource_protection(self):
        from threading import Event, Thread
        self.bind_map()
        monitor = self.monitor()
        self.write_report()
        monitor.configure(enabled=True, automatic_reporting=True)
        entered, release = Event(), Event()
        def publish(*args, **kwargs):
            entered.set()
            release.wait(2)
            return dict(status="pending")
        self.publisher.publish.side_effect = publish
        stopper = Mock()
        monitor.resource_guard = ResourceGuard(probe=lambda p: 200*GIB, stopper=stopper)
        polling = Thread(target=monitor.poll_once)
        polling.start()
        try:
            self.assertTrue(entered.wait(1))
            monitor.poll_health_once()
            stopper.assert_called_once_with(PROCESS)
        finally:
            release.set()
            polling.join(2)
        self.assertFalse(polling.is_alive())

    def test_original_game_resource_failure_does_not_implicate_custom_map(self):
        monitor = self.monitor()
        stopper = Mock()
        monitor.resource_guard = ResourceGuard(probe=lambda p: 200*GIB, stopper=stopper)
        monitor.poll_health_once()
        stopper.assert_called_once()
        self.assertFalse(monitor.map_issues.root.exists())
        self.assertIn("No custom map was identified", monitor.take_events()[0])

    def test_full_disk_during_session_binding_still_stops_runaway_game(self):
        monitor = self.monitor()
        monitor.arm = Mock(side_effect=OSError("disk full"))
        monitor._retain_resource_failure = Mock(side_effect=OSError("disk full"))
        stopper = Mock()
        monitor.resource_guard = ResourceGuard(probe=lambda p: 200*GIB, stopper=stopper)
        monitor.poll_health_once()
        stopper.assert_called_once_with(PROCESS)
        self.assertIn("could not be saved", monitor.take_events()[0])
