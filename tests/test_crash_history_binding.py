"""Synthetic process/incident joins; no live reports, collector or game access."""

import copy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from smr_launcher.crash_monitor import CrashMonitor
from smr_launcher.play_history import HistoryError, PlayHistoryStore, digest, summarize
from test_crash_monitor import EXE, PROCESS, START, report


CONTEXT = dict(archive_sha256="b" * 64, variant_id="c" * 64, game_executable_sha256="a" * 64)


def session(process=None):
    process = copy.deepcopy(PROCESS if process is None else process)
    return dict(
        executable=EXE,
        process=process,
        started_at=START.isoformat(),
        context=copy.deepcopy(CONTEXT),
    )


def issue():
    return dict(
        **CONTEXT,
        incident_id=digest("invented crash"),
        kind="crash",
        occurred_at=(START + timedelta(minutes=1)).isoformat(),
    )


class CrashHistoryBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PlayHistoryStore(Path(self.temp.name).resolve())
        self.issue = issue()
        self.session = session()
        self.failure = self.store.issue_event(self.issue)
        self.store.append(self.failure)

    def test_link_preserves_historical_and_player_phase_facts_without_new_open_or_crash(self):
        process, link = self.store.crash_process_events(self.issue, self.session)
        self.store.append(process)
        ctx = dict(CONTEXT, scenario="")
        attributed = self.store.event(
            ctx,
            "crash_play",
            at=self.issue["occurred_at"],
            source="player_confirmed",
            incident_id=self.issue["incident_id"],
            attempt_id=process["attempt_id"],
        )
        self.store.append(attributed)
        before_bytes = self.store.path.read_bytes()
        before = summarize(self.store.records(), ctx)
        self.store.append(process, link)
        self.assertTrue(self.store.path.read_bytes().startswith(before_bytes))
        self.assertEqual(self.store.records()[:3], (self.failure, process, attributed))
        after = summarize(self.store.records(), ctx)
        self.assertEqual((after.opens, after.crashes), (1, 1))
        self.assertEqual(after.events[:-1], before.events)
        self.assertEqual(after.events[-1], link)
        self.assertEqual(replace(after, events=before.events), before)
        self.assertEqual(link["kind"], "crash_process_link")
        self.assertEqual(
            (link["source"], link["scope"], link["scenario"]), ("observed", "related", "")
        )
        self.assertEqual(link["attempt_id"], process["attempt_id"])
        self.assertEqual(link["incident_id"], self.failure["incident_id"])

    def test_duplicate_link_is_idempotent_and_has_no_raw_process_data(self):
        events = self.store.crash_process_events(self.issue, self.session)
        self.store.append(*events)
        before = self.store.path.read_bytes()
        self.assertEqual(self.store.append(*events), ())
        self.assertEqual(self.store.path.read_bytes(), before)
        encoded = json.dumps(events)
        for forbidden in (EXE, '"pid"', '"birth_us"', '"executable"', "PRIVATE_CANARY"):
            self.assertNotIn(forbidden, encoded)

    def test_factory_refuses_missing_foreign_or_late_session(self):
        cases = []
        for key in CONTEXT:
            other = copy.deepcopy(self.session)
            other["context"][key] = "f" * 64
            cases.append(other)
        missing = copy.deepcopy(self.session)
        missing["process"] = None
        cases.append(missing)
        missing_context = copy.deepcopy(self.session)
        missing_context["context"]["archive_sha256"] = None
        cases.append(missing_context)
        for other in cases:
            with self.subTest(context=other["context"]), self.assertRaises(HistoryError):
                self.store.crash_process_events(self.issue, other)
        for changed in (
            dict(self.issue, kind="resource_stop"),
            dict(self.issue, occurred_at=(START - timedelta(seconds=1)).isoformat()),
        ):
            with self.assertRaises(HistoryError):
                self.store.crash_process_events(changed, self.session)

    def test_relation_requires_same_incident_map_process_capture_source_and_scope(self):
        process, link = self.store.crash_process_events(self.issue, self.session)
        before = self.store.path.read_bytes()
        with self.assertRaises(HistoryError):
            self.store.append(link)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.store.append(process)
        before = self.store.path.read_bytes()
        mutations = dict(
            incident_id=digest("different crash"),
            attempt_id=digest("different process"),
            archive_sha256="f" * 64,
            at=(START + timedelta(seconds=61)).isoformat(),
            source="player_confirmed",
            scope="exact",
            scenario="synthetic.xml",
        )
        for key, value in mutations.items():
            with self.subTest(field=key), self.assertRaises(HistoryError):
                self.store.append({**link, key: value})
            self.assertEqual(self.store.path.read_bytes(), before)
        empty = PlayHistoryStore(Path(self.temp.name).resolve() / "empty")
        with self.assertRaisesRegex(HistoryError, "matching retained crash"):
            empty.append(process, link)
        self.assertEqual(empty.records(), ())

    def test_conflicting_process_binding_refuses_without_rewriting_original(self):
        process, link = self.store.crash_process_events(self.issue, self.session)
        self.store.append(process, link)
        other = dict(
            process, event_id=digest("second confirmation"), attempt_id=digest("second process")
        )
        conflicting = dict(
            link, event_id=digest("conflicting link"), attempt_id=other["attempt_id"]
        )
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(HistoryError, "different process"):
            self.store.append(other, conflicting)
        self.assertEqual(self.store.path.read_bytes(), before)


class CollectorHistoryBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.reports = self.root / "diagnostics"
        self.reports.mkdir()
        self.record = SimpleNamespace(
            profile_id="synthetic-map", archive_sha256="b" * 64, variant_id="c" * 64
        )
        self.app = SimpleNamespace(
            library=self.root,
            installation=SimpleNamespace(
                executable=Path(EXE), executable_sha256="a" * 64, bundle_version="1.0"
            ),
            profiles=SimpleNamespace(active_profile=lambda: self.record.profile_id),
            catalogue=lambda: [self.record],
        )
        self.probe = Mock(return_value=PROCESS)
        self.monitor = CrashMonitor(
            self.app,
            "1.2.3",
            reports_directory=self.reports,
            process_probe=self.probe,
            publisher=Mock(),
            image_uuid_reader=lambda _: None,
            now=lambda: START + timedelta(minutes=3),
        )
        self.addCleanup(self.monitor.close)

    def test_delayed_report_uses_exact_accepted_older_session_not_newest_process(self):
        self.monitor.arm(self.record.profile_id, process=PROCESS)
        older = copy.deepcopy(self.monitor.session)
        newer = dict(PROCESS, pid=124, birth_us=PROCESS["birth_us"] + 120000000)
        self.monitor.arm(self.record.profile_id, process=newer)
        self.probe.return_value = newer
        (self.reports / "Sid Meiers Railroads_delayed.ips").write_bytes(report())
        self.monitor.poll_once()
        rows = self.monitor.play_history.records()
        links = [r for r in rows if r["kind"] == "crash_process_link"]
        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0]["attempt_id"], PlayHistoryStore.session_event(older)["attempt_id"]
        )
        self.assertNotEqual(
            links[0]["attempt_id"],
            PlayHistoryStore.session_event(self.monitor.session)["attempt_id"],
        )
        summary = summarize(rows, dict(CONTEXT, scenario=""))
        self.assertEqual((summary.opens, summary.crashes), (2, 1))
        self.assertEqual(links[0]["scenario"], "")
        self.assertEqual(
            [r["kind"] for r in rows if r["incident_id"] and r["kind"] != "crash_process_link"],
            ["crash_unknown"],
        )
        self.monitor.publisher.publish.assert_not_called()

    def test_invalid_optional_link_keeps_baseline_crash_history(self):
        self.monitor.arm(self.record.profile_id, process=PROCESS)
        foreign = copy.deepcopy(self.monitor.session)
        foreign["context"]["variant_id"] = "f" * 64
        self.monitor._queue_history_issue(issue(), session=foreign)
        self.monitor._flush_history()
        rows = self.monitor.play_history.records()
        self.assertEqual(len([r for r in rows if r["kind"] == "crash_unknown"]), 1)
        self.assertFalse(any(r["kind"] == "crash_process_link" for r in rows))
        self.assertTrue(self.monitor.history_error)

    def test_baseline_fits_when_optional_link_exceeds_capacity(self):
        self.monitor.arm(self.record.profile_id, process=PROCESS)
        before = self.monitor.play_history.records()
        self.monitor._queue_history_issue(issue(), session=self.monitor.session)
        with patch("smr_launcher.play_history.MAX_EVENTS", len(before) + 1):
            self.monitor._flush_history()
        rows = self.monitor.play_history.records()
        self.assertEqual(rows[: len(before)], before)
        self.assertEqual(len([r for r in rows if r["kind"] == "crash_unknown"]), 1)
        self.assertFalse(any(r["kind"] == "crash_process_link" for r in rows))
        self.assertIn("limit", self.monitor.history_error)

    def test_optional_conflict_does_not_block_another_crash_or_link(self):
        self.monitor.arm(self.record.profile_id, process=PROCESS)
        original = copy.deepcopy(self.monitor.session)
        self.monitor._queue_history_issue(issue(), session=original)
        self.monitor._flush_history()
        before = self.monitor.play_history.path.read_bytes()
        foreign = copy.deepcopy(original)
        foreign["process"]["pid"] += 1
        self.monitor._queue_history_issue(issue(), session=foreign)
        second = dict(issue(), incident_id=digest("second crash"))
        self.monitor._queue_history_issue(second, session=original)
        self.monitor._flush_history()
        rows = self.monitor.play_history.records()
        self.assertTrue(self.monitor.play_history.path.read_bytes().startswith(before))
        self.assertEqual(len([r for r in rows if r["kind"] == "crash_unknown"]), 2)
        self.assertEqual(len([r for r in rows if r["kind"] == "crash_process_link"]), 2)
        self.assertEqual(summarize(rows, dict(CONTEXT, scenario="")).opens, 1)
        self.assertIn("collision", self.monitor.history_error)

    def test_baseline_append_failure_does_not_attempt_optional_append_or_retry(self):
        self.monitor.arm(self.record.profile_id, process=PROCESS)
        self.monitor._queue_history_issue(issue(), session=self.monitor.session)
        with patch.object(
            self.monitor.play_history,
            "append",
            side_effect=HistoryError("synthetic mandatory failure"),
        ) as append:
            self.monitor._flush_history()
        self.assertEqual(append.call_count, 1)
        self.assertTrue(self.monitor.history_error)
