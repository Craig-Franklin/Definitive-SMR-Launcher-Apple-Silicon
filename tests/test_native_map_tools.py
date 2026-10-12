"""Native contextual tools against synthetic ledgers and isolated fake installations."""

from dataclasses import replace
from pathlib import Path
import os
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import test_application
from test_native_backend import app, record, window
from smr_launcher.application import ApplicationError
from smr_launcher import editions, removal
from smr_launcher.activation import ORIGINAL
from smr_launcher.native_backend import NativeLibrary
from smr_launcher.native_map_tools import dispatch
from smr_launcher.play_history import HistoryError, PlayHistoryStore, context, digest
from smr_launcher.play_history import HistoryWriteOutcomeUnknown
from smr_launcher.verification import CHECKS

URL = "https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/220"


class MapToolsTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.app = app(Path(folder.name).resolve())
        self.app.compatibility_recipes = Mock(return_value=())
        self.app._legacy_verification_for = Mock(return_value=None)
        self.library = NativeLibrary()
        self.record = record()
        self.ctx = context(self.record, "c" * 64)
        self.params = {**self.ctx, "profile_id": self.record.profile_id, "confirmed": True}
        self.store = PlayHistoryStore(Path(folder.name).resolve())

    def call(self, action, **fields):
        return dispatch(self.library, self.app, "maptools." + action, {**self.params, **fields})

    def test_every_map_operation_rejects_changed_missing_edition_and_build(self):
        actions = (
            "inspect",
            "legacy_record",
            "link_issue",
            "record_fix",
            "create_recipe",
            "create_experimental",
            "remove",
            "refresh_checks",
            "refresh_issues",
            "issue_url",
        )
        for action in actions:
            for fields in (
                {"variant_id": "d" * 64},
                {"archive_sha256": ""},
                {"game_executable_sha256": "d" * 64},
                {"profile_id": "missing"},
            ):
                with self.subTest(action=action, fields=fields), self.assertRaises(ValueError):
                    self.call(action, **fields)
        self.assertEqual(self.store.records(), ())

    def test_legacy_report_does_not_invent_exact_history(self):
        self.app.record_gameplay = Mock(return_value=Mock(status="Not verified"))
        checks = dict.fromkeys(CHECKS, True)
        result = self.call(
            "legacy_record", checks=checks, scope="20 minutes", issue="Crash observed"
        )
        self.assertIn("Not verified", result["message"])
        args = self.app.record_gameplay.call_args.args
        self.assertEqual(args[0], self.record)
        self.assertEqual(args[3:], (checks, "20 minutes", "Crash observed"))
        self.assertEqual(self.store.records(), ())
        for fields in (
            {"scope": ""},
            {"issue": "x" * 501},
            {"checks": {}},
            {"checks": dict.fromkeys(CHECKS, 1)},
            {"confirmed": False},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.call("legacy_record", **({"checks": checks, "scope": "scope"} | fields))
        self.assertEqual(self.app.record_gameplay.call_count, 1)

    def test_link_scope_repository_validation_and_idempotent_link(self):
        for fields in (
            {"scope": "exact", "scenario": ""},
            {"scope": "exact", "scenario": "other.xml"},
            {"scope": "related", "issue_url": "https://github.com/other/repo/issues/1"},
        ):
            with self.subTest(fields=fields), self.assertRaises(HistoryError):
                self.call("link_issue", **({"issue_url": URL} | fields))
        self.call("link_issue", scope="related", issue_url=URL)
        self.call("link_issue", scope="related", issue_url=URL)
        self.assertEqual(len(self.store.records()), 1)
        self.assertEqual(self.call("issue_url", issue_url=URL), {"url": URL})
        with self.assertRaises(HistoryError):
            self.call("issue_url", issue_url=URL.replace("220", "221"))
        self.app.catalogue.return_value = (replace(self.record, variant_id="d" * 64),)
        with self.assertRaises(ValueError):
            self.call("issue_url", issue_url=URL)

    def test_fix_invalid_issue_scenario_and_unknown_attribution(self):
        fields = dict(
            operation_id="retained-draft", kind="fix_implemented", scenario="Scenario.xml"
        )
        with self.assertRaises(HistoryError):
            self.call("record_fix", **fields, issue_url=URL)
        self.call("link_issue", scope="exact", scenario="Scenario.xml", issue_url=URL)
        result = self.call("record_fix", **fields, issue_url=URL)
        again = self.call("record_fix", **fields, issue_url=URL)
        self.assertEqual(result, again)
        self.assertEqual(len(self.store.records()), 2)
        with self.assertRaises(HistoryError):
            self.call(
                "record_fix",
                operation_id="bad-test",
                scenario="Scenario.xml",
                kind="fix_test_passed",
                fix_id=result["fix_id"],
                target_id=digest("missing"),
            )
        with self.assertRaises(HistoryError):
            self.call("record_fix", **(fields | {"kind": "goals_met"}))

    def test_fix_test_requires_new_open_and_explicit_success(self):
        ctx = {**self.ctx, "scenario": "Scenario.xml"}
        old, new, fix = digest("old"), digest("new"), digest("fix")

        def event(kind, minute, **fields):
            return self.store.event(ctx, kind, at=f"2026-01-01T00:{minute:02d}:00+00:00", **fields)

        failed = event(
            "crash_play", 2, attempt_id=old, incident_id=digest("incident"), checkpoint="playable"
        )
        self.store.append(
            event("process_confirmed", 1, attempt_id=old, source="observed"),
            failed,
            event("fix_implemented", 3, fix_id=fix),
            event("process_confirmed", 4, attempt_id=new, source="observed"),
        )
        fields = dict(
            operation_id="test-fact",
            scenario="Scenario.xml",
            kind="fix_test_passed",
            fix_id=fix,
            target_id=failed["event_id"],
            attempt_id=old,
        )
        with self.assertRaisesRegex(HistoryError, "newly confirmed"):
            self.call("record_fix", **fields)
        fields["attempt_id"] = new
        with self.assertRaisesRegex(HistoryError, "Record the tested checkpoint"):
            self.call("record_fix", **fields)
        self.store.append(event("playable", 5, attempt_id=new))
        self.call("record_fix", **fields)
        self.call("record_fix", **fields)
        self.assertEqual(
            len([e for e in self.store.records() if e["kind"] == "fix_test_passed"]), 1
        )
        self.assertIn(failed, self.store.records(), "Past failures must be retained")

    def test_inspector_keeps_other_builds_out_of_current_facts_and_read_errors_visible(self):
        self.store.link({**self.ctx, "scenario": "Scenario.xml"}, URL)
        other = {**self.ctx, "game_executable_sha256": "d" * 64, "scenario": "Scenario.xml"}
        self.store.link(other, URL.replace("220", "221"))
        self.app.import_checks.side_effect = OSError("synthetic check error")
        self.app.compatibility_recipes.side_effect = ValueError("synthetic recipe error")
        result = self.call("inspect")
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("1 retained events", result["scope_text"])
        self.assertIn("synthetic check error", result["checks_error"])
        self.assertIn("synthetic recipe error", result["recipes_error"])
        self.assertIn("legacy_checks", result)
        self.assertNotIn("prepared_directory", result)

    def test_provider_and_store_errors_are_not_success_and_no_retry_occurs(self):
        self.app.record_gameplay = Mock(side_effect=OSError("synthetic full disk"))
        with self.assertRaisesRegex(OSError, "full disk"):
            self.call("legacy_record", checks=dict.fromkeys(CHECKS, False), scope="scope")
        self.app.record_gameplay.assert_called_once()
        with patch.object(
            PlayHistoryStore, "records", side_effect=HistoryError("synthetic corrupt ledger")
        ):
            with self.assertRaisesRegex(HistoryError, "corrupt ledger"):
                self.call("inspect")
        self.app.import_checks.side_effect = OSError("synthetic scan failure")
        with self.assertRaisesRegex(OSError, "scan failure"):
            self.call("refresh_checks")
        self.app.import_checks.assert_called_once_with(self.record, refresh=True)

    def test_partial_history_append_is_unknown_but_prewrite_validation_is_retryable(self):
        with self.assertRaises(HistoryError) as refused:
            self.call("link_issue", scope="exact", scenario="", issue_url=URL)
        self.assertNotIn("outcome needs inspection", str(refused.exception))
        self.assertFalse(self.store.path.exists())
        real_write, calls = os.write, []

        def short_then_zero(fd, data):
            calls.append(len(data))
            return real_write(fd, data[:12]) if len(calls) == 1 else 0

        with patch("smr_launcher.play_history.os.write", side_effect=short_then_zero):
            with self.assertRaisesRegex(
                RuntimeError, "Map tool outcome needs inspection"
            ) as failed:
                self.call("link_issue", scope="related", issue_url=URL)
        self.assertIsInstance(failed.exception.__cause__, HistoryWriteOutcomeUnknown)
        self.assertEqual(self.store.path.stat().st_size, 12)
        self.assertEqual(len(calls), 2, "The bridge must not retry the interrupted append")
        with self.assertRaisesRegex(HistoryError, "Interrupted history append"):
            self.store.records()

    def test_issue_refresh_reuses_bounded_existing_provider(self):
        self.call("link_issue", scenario="Scenario.xml", scope="exact", issue_url=URL)
        with patch.object(PlayHistoryStore, "refresh_issues") as refresh:
            self.call("refresh_issues")
        self.assertEqual(refresh.call_args.args[0], self.store.records())

    def test_recipe_calls_existing_service_and_refuses_changed_inputs_before_creation(self):
        edition = replace(self.record, variant_id="d" * 64)
        self.app.create_recipe_edition = Mock(return_value=edition)
        result = self.call("create_recipe", recipe_id="synthetic-reviewed-recipe")
        self.app.create_recipe_edition.assert_called_once_with(
            self.record, "synthetic-reviewed-recipe"
        )
        self.assertEqual(result["profile_id"], edition.profile_id)
        self.assertIn("do not prove gameplay", result["message"])
        with self.assertRaises(ValueError):
            self.call("create_recipe", recipe_id="synthetic-reviewed-recipe", variant_id="e" * 64)
        self.app.create_recipe_edition.assert_called_once()
        self.app.create_recipe_edition.side_effect = ValueError("Synthetic unsupported recipe")
        with self.assertRaisesRegex(ValueError, "unsupported recipe"):
            self.call("create_recipe", recipe_id="unknown-recipe")

    def test_original_context_is_read_only_and_activation_rejects_changed_build(self):
        self.app.activate = Mock()
        self.assertEqual(self.call("original_context"), {"game_executable_sha256": "c" * 64})
        with self.assertRaisesRegex(ValueError, "build changed"):
            self.call("activate_original", game_executable_sha256="d" * 64)
        self.app.activate.assert_not_called()

    def test_dispatch_uses_existing_envelope_and_receipt_deduplication(self):
        owner = window()
        owner.app = self.app
        owner._native_completed = __import__("collections").OrderedDict()
        owner._background = lambda operation, done, **_: done(True, operation())
        request = dict(id="native-map-request", method="maptools.inspect", params=self.params)
        owner._native_dispatch(request)
        response = owner._emit_native.call_args.args[0]
        self.assertTrue(response["ok"])
        self.assertEqual(response["id"], request["id"])
        self.assertIn(request["id"], owner._native_completed)
        with patch.object(owner, "_native_dispatch") as repeated:
            owner._native_requests.put(request)
            owner._native_tick()
        repeated.assert_not_called()
        self.assertEqual(owner._emit_native.call_args.args[0], response)


class MapToolsApplicationGuards(TestCase):
    setUp = test_application.ApplicationTests.setUp

    def prepare(self):
        self.application.setup()
        self.record = self.application.import_archive(self.archive)
        self.params = {
            **context(self.record, self.installation.executable_sha256),
            "profile_id": self.record.profile_id,
            "confirmed": True,
        }
        self.library = NativeLibrary()

    def call(self, action, **fields):
        return dispatch(
            self.library, self.application, "maptools." + action, {**self.params, **fields}
        )

    def test_running_recovery_and_active_map_refuse_removal_and_legacy_write(self):
        self.prepare()
        self.installation.running = True
        for action in ("remove", "activate_original", "create_experimental", "legacy_record"):
            with self.subTest(action=action), self.assertRaises(ApplicationError):
                self.call(
                    action, difficulty=True, checks=dict.fromkeys(CHECKS, False), scope="scope"
                )
        self.installation.running = False
        self.application.activate(self.record.profile_id)
        with self.assertRaises(removal.RemovalError):
            self.call("remove")
        self.application.profiles.journal.write_text("{}")
        with self.assertRaisesRegex(ValueError, "needs recovery"):
            self.call("activate_original")
        with self.assertRaises(Exception):
            self.call("legacy_record", checks=dict.fromkeys(CHECKS, False), scope="scope")
        self.assertEqual(len(self.application.catalogue()), 1)
        self.assertFalse((self.application.library / "retained-saves").exists())

    def test_activation_and_removal_keep_synthetic_saves_and_reject_missing_consent(self):
        self.prepare()
        for action in ("remove", "activate_original", "create_experimental", "create_recipe"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "confirmation"):
                self.call(action, confirmed=False)
        self.application.activate(self.record.profile_id)
        (self.installation.profile_root / "Saves/synthetic.sav").write_bytes(b"keep synthetic save")
        self.call("activate_original")
        self.assertEqual(self.application.active(), ORIGINAL)
        result = self.call("remove")
        self.assertEqual(result["removed"], self.record.profile_id)
        self.assertNotIn("path", result)
        imported = self.application.import_archive(self.archive)
        self.application.activate(imported.profile_id)
        self.assertEqual(
            (self.installation.profile_root / "Saves/synthetic.sav").read_bytes(),
            b"keep synthetic save",
        )

    def test_new_experimental_edition_uses_existing_validation_and_separate_saves(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "Only the existing"):
            self.call("create_experimental", difficulty=True, editor=True)
        with patch.object(editions, "SUPPORTED_GAME", self.installation.executable_sha256):
            result = self.call("create_experimental", difficulty=True)
        edition = next(
            r for r in self.application.catalogue() if r.profile_id == result["profile_id"]
        )
        self.assertNotEqual(edition.variant_id, self.record.variant_id)
        self.assertIn("do not prove gameplay", result["message"])
        self.assertEqual(
            list((self.application.profiles._profile_path(edition.profile_id) / "Saves").iterdir()),
            [],
        )
        self.assertEqual(self.application.verification_store.records(), ())

    def test_custom_removal_failure_after_catalogue_commit_holds_unknown_with_saves_retained(self):
        self.prepare()
        saves = self.application.profiles._profile_path(self.record.profile_id) / "Saves"
        (saves / "synthetic-retain.sav").write_bytes(b"retained synthetic save")
        with patch.object(
            removal, "_discard", side_effect=removal.RemovalError("Synthetic cleanup refusal")
        ):
            with self.assertRaisesRegex(
                RuntimeError, "Map tool outcome needs inspection"
            ) as failed:
                self.call("remove")
        self.assertIsInstance(failed.exception.__cause__, removal.RemovalError)
        self.assertTrue((self.application.library / "removal.json").exists())
        self.assertEqual(self.application.catalogue(), ())
        retained = removal.retained_saves(self.application, self.record.variant_id)
        self.assertEqual(
            (retained / "synthetic-retain.sav").read_bytes(), b"retained synthetic save"
        )
