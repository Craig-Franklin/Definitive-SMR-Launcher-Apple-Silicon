"""Authoring inspection uses the real read-only service, without library setup."""

import hashlib
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

import test_map_inspection as fixtures
import test_native_backend as native_fixtures
from smr_launcher.application import ApplicationError, LauncherApplication
from smr_launcher.map_inspection import inspect_map, report_text


class AuthoringInspectionTests(TestCase):
    def setUp(self):
        self.fixture = fixtures.MapInspectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = LauncherApplication.__new__(LauncherApplication)
        executable = self.fixture.game / "Contents/MacOS/Sid Meiers Railroads"
        self.app.installation = SimpleNamespace(
            executable=executable,
            executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        )
        # No profiles, catalogue, import folders or mutation services are supplied.

    def test_real_service_preserves_directory_and_reports_unknown_build(self):
        before = self.fixture.facts(self.fixture.root)
        report = self.app.inspect_map_directory(self.fixture.map)
        direct = inspect_map(self.fixture.map, self.fixture.game)
        self.assertEqual(report_text(report), report_text(direct))
        self.assertEqual(report["runtime_acceptance"], "not_tested")
        self.assertEqual(report["game"]["build_status"], "unsupported")
        self.assertEqual(self.fixture.facts(self.fixture.root), before)
        self.assertTrue(
            all(row["category"] != "confirmed_invalid_condition" for row in report["findings"])
        )

    def test_fresh_inspection_observes_edits_and_refuses_stale_game_identity(self):
        first = self.app.inspect_map_directory(self.fixture.map)
        self.fixture.scenario.write_text("<RRTScenario><szMapName>Edited</szMapName></RRTScenario>")
        second = self.app.inspect_map_directory(self.fixture.map)
        self.assertNotEqual(first["input"]["content_identity"], second["input"]["content_identity"])
        self.app.installation.executable.write_bytes(b"changed synthetic executable")
        with self.assertRaisesRegex(ApplicationError, "Game executable changed"):
            self.app.inspect_map_directory(self.fixture.map)

    def test_file_and_link_are_not_authoring_directories(self):
        link = self.fixture.root / "linked-map"
        link.symlink_to(self.fixture.map, target_is_directory=True)
        for source in (link, self.fixture.scenario, self.fixture.root / "missing"):
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(ApplicationError, "nonlinked map directory"),
            ):
                self.app.inspect_map_directory(source)

    def test_native_request_uses_same_service_without_a_catalogue_record(self):
        window = native_fixtures.window()
        window.app = self.app
        window._native_reply = Mock()
        window.native_library.snapshot = Mock(side_effect=AssertionError("No library refresh"))
        window._background = lambda operation, completed, **_: completed(True, operation())
        request = dict(
            id="authoring", method="inspection.directory", params=dict(path=str(self.fixture.map))
        )
        before = self.fixture.facts(self.fixture.root)
        window._native_dispatch(request)
        reply = window._native_reply.call_args.args
        self.assertEqual(reply[:2], (request, True))
        self.assertEqual(
            reply[2]["report_text"], report_text(inspect_map(self.fixture.map, self.fixture.game))
        )
        self.assertEqual(self.fixture.facts(self.fixture.root), before)
        window.native_library.snapshot.assert_not_called()
        self.assertIn("Observed: ", reply[2]["provenance_text"])
        self.assertIn("Analyzer: ", reply[2]["provenance_text"])
        self.assertIn("Stock identity: ", reply[2]["provenance_text"])

    def test_native_request_refuses_missing_relative_or_nontext_paths_before_scanning(self):
        window = native_fixtures.window()
        window.app = self.app
        window._native_reply = Mock()
        window._background = Mock()
        for params in ({}, {"path": ""}, {"path": "."}, {"path": 12}):
            with self.subTest(params=params):
                request = dict(id="bad-authoring", method="inspection.directory", params=params)
                window._native_dispatch(request)
                self.assertFalse(window._native_reply.call_args.args[1])
        window._background.assert_not_called()
