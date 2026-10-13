"""Static refresh must not label current stock with an old edition's build."""

import unittest
from unittest.mock import patch

import test_import_check_freshness as diagnostics_fixtures
from smr_launcher.application import ApplicationError
from smr_launcher import import_checks


class ImportCheckBuildBindingTests(unittest.TestCase):
    def setUp(self):
        self.case = diagnostics_fixtures.ImportCheckFreshnessTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.app, self.record = self.case.app, self.case.record

    def test_changed_build_refresh_refuses_before_scan_and_preserves_reports_and_saves(self):
        fixture = self.case.fixture
        profile = self.app.profiles._profile_path(self.record.profile_id)
        for name in ("current.sav", "future.sav"):
            (profile / "Saves" / name).write_bytes(("invented " + name).encode())
        roots = (
            profile,
            fixture.installation.profile_root,
            self.app.originals,
            self.app._prepared_root(self.record),
        )
        before = [fixture.snapshot_files(root) for root in roots]
        report = self.case.report_path.read_bytes()
        archive = fixture.archive.read_bytes()
        fixture.installation.executable_sha256 = "f" * 64
        self.assertIsNone(self.app.import_checks(self.record))
        with patch(
            "smr_launcher.application.inspect_map", wraps=import_checks.inspect_map
        ) as scanner:
            with self.assertRaisesRegex(ApplicationError, "current game build"):
                self.app.import_checks(self.record, refresh=True)
            scanner.assert_not_called()
        self.assertEqual(self.case.report_path.read_bytes(), report)
        self.assertEqual(fixture.archive.read_bytes(), archive)
        self.assertEqual([fixture.snapshot_files(root) for root in roots], before)

    def test_matching_build_refresh_keeps_exact_label_and_current_cached_report(self):
        report = self.app.import_checks(self.record, refresh=True)
        self.assertEqual(
            report["game_executable_sha256"],
            self.case.fixture.installation.executable_sha256,
        )
        self.assertEqual(report["game_executable_sha256"], self.record.game_executable_sha256)
        self.assertEqual(self.app.import_checks(self.record), report)
        self.assertIsNone(self.app.verification_for(self.record))
