"""Offline history contracts; all processes, diagnostics, saves and GitHub are fake."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from smr_launcher.play_history import (
    PlayHistoryStore,
    HistoryError,
    context,
    digest,
    summarize,
    issue_readback,
    ISSUE_PREFIX,
    LABELS,
)
from smr_launcher.history_ui import confirm_checkpoint, save_digest, gallery_columns, timeline_text
from smr_launcher.crash_monitor import CrashMonitor


def time(n):
    return f"2026-10-11T12:{n // 60:02}:{n % 60:02}+00:00"


class HistoryContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.library = Path(temporary.name).resolve()
        self.store = PlayHistoryStore(self.library)
        self.record = SimpleNamespace(
            archive_sha256="a" * 64, variant_id="b" * 64, scenarios=("scenario.xml", "other.xml")
        )
        self.ctx = context(self.record, "c" * 64, "scenario.xml")
        self.open = digest("open1")

    def event(self, kind, n=0, **fields):
        return self.store.event(self.ctx, kind, at=time(n), **fields)

    def opened(self, identity=None, n=0):
        identity = identity or self.open
        self.store.append(
            self.event("process_confirmed", n, attempt_id=identity, source="observed")
        )
        return identity

    def summary(self, ctx=None):
        return summarize(
            self.store.records(),
            ctx or self.ctx,
            now=datetime(2026, 10, 11, 13, tzinfo=timezone.utc),
        )

    def failure(self, kind="crash_load", n=1):
        e = self.event(
            kind, n, attempt_id=self.open, incident_id=digest("incident"), checkpoint="playable"
        )
        self.store.append(e)
        return e

    def test_first_exact_save_reopen_and_continuation_append_as_one_batch(self):
        self.opened()
        confirm_checkpoint(self.store, self.ctx, "playable", self.open, game="new", at=time(1))
        game = next(e["game_id"] for e in self.store.records() if e["kind"] == "game_started")
        save = digest("exact synthetic save")
        confirm_checkpoint(
            self.store, self.ctx, "save_made", self.open, game=game, save_id=save, at=time(2)
        )
        second = self.opened(digest("resumed open"), 3)
        with patch.object(self.store, "append", wraps=self.store.append) as append:
            added = confirm_checkpoint(
                self.store,
                self.ctx,
                "save_reopened",
                second,
                game=game,
                save_id=save,
                continued=True,
                at=time(4),
            )
        append.assert_called_once()
        self.assertEqual(
            [e["kind"] for e in added], ["game_resumed", "save_reopened", "continued_play"]
        )
        self.assertTrue(all(e["attempt_id"] == second and e["game_id"] == game for e in added))
        self.assertTrue(all(e["save_id"] == save for e in added[1:]))

    def test_invalid_continuation_batch_leaves_reopen_and_binding_unwritten(self):
        self.opened()
        confirm_checkpoint(self.store, self.ctx, "playable", self.open, game="new", at=time(1))
        game = next(e["game_id"] for e in self.store.records() if e["kind"] == "game_started")
        save = digest("exact synthetic save")
        confirm_checkpoint(self.store, self.ctx, "save_made", self.open, save_id=save, at=time(2))
        second = self.opened(digest("resumed open"), 3)
        before, event = self.store.path.read_bytes(), self.store.event

        def invalid_second(ctx, kind, **fields):
            if kind == "continued_play":
                fields["save_id"] = digest("different save without reopen")
            return event(ctx, kind, **fields)

        with patch.object(self.store, "event", side_effect=invalid_second):
            with self.assertRaises(HistoryError):
                confirm_checkpoint(
                    self.store,
                    self.ctx,
                    "save_reopened",
                    second,
                    game=game,
                    save_id=save,
                    continued=True,
                    at=time(4),
                )
        self.assertEqual(self.store.path.read_bytes(), before)
        for kind, continued in (("playable", True), ("save_reopened", "yes")):
            with self.assertRaises(HistoryError):
                confirm_checkpoint(
                    self.store, self.ctx, kind, second, continued=continued, at=time(4)
                )
        self.assertEqual(self.store.path.read_bytes(), before)

    def _tested(self, *, completed=False):
        self.opened()
        failure = self.failure()
        fix = digest("fix")
        self.store.append(self.event("fix_implemented", 2, fix_id=fix))
        second = self.opened(digest("open2"), 3)
        confirm_checkpoint(self.store, self.ctx, "playable", second, game="new", at=time(4))
        self.store.append(
            self.event(
                "fix_test_passed",
                5,
                fix_id=fix,
                attempt_id=second,
                target_id=failure["event_id"],
                checkpoint="playable",
            )
        )
        if completed:
            game = next(e["game_id"] for e in self.store.records() if e["kind"] == "game_started")
            confirm_checkpoint(self.store, self.ctx, "retired", second, game=game, at=time(6))
        return failure, fix, second

    def test_three_opens_one_game_one_completion_survives_restart(self):
        self.opened()
        confirm_checkpoint(self.store, self.ctx, "playable", self.open, game="new", at=time(1))
        game = next(e["game_id"] for e in self.store.records() if e["kind"] == "game_started")
        save = digest("private save contents")
        confirm_checkpoint(self.store, self.ctx, "save_made", self.open, save_id=save, at=time(2))
        for index, n in ((2, 3), (3, 6)):
            opened = self.opened(digest(index), n)
            confirm_checkpoint(
                self.store,
                self.ctx,
                "save_reopened",
                opened,
                game=game,
                save_id=save,
                at=time(n + 1),
            )
            confirm_checkpoint(
                self.store,
                self.ctx,
                "continued_play",
                opened,
                game=game,
                save_id=save,
                at=time(n + 2),
            )
        confirm_checkpoint(self.store, self.ctx, "retired", opened, game=game, at=time(9))
        confirm_checkpoint(self.store, self.ctx, "goals_met", opened, game=game, at=time(10))
        self.store = PlayHistoryStore(self.library)
        summary = self.summary()
        self.assertEqual(
            (summary.opens, summary.started, summary.completed, summary.crashes), (3, 1, 1, 0)
        )
        self.assertEqual((summary.light, summary.label), ("green", "Completed"))
        self.assertIn("Retired", timeline_text(self.store.records()))
        self.assertIn("All scenario goals met", timeline_text(self.store.records()))

    def test_neutral_attempt_unknown_and_resource_labels(self):
        self.assertEqual(self.summary().label, "Never opened")
        self.store.append(self.event("launch_attempted", attempt_id=self.open, source="observed"))
        self.assertEqual((self.summary().opens, self.summary().label), (0, "Start unconfirmed"))
        self.store.append(self.event("cancelled", 1, attempt_id=self.open))
        self.assertEqual(self.summary().opens, 0)
        self.opened(n=2)
        self.store.append(self.event("unknown_exit", 3, attempt_id=self.open, source="observed"))
        self.assertEqual(
            (self.summary().light, self.summary().label, self.summary().crashes),
            ("grey", "Outcome unknown", 0),
        )
        self.failure("resource_stop", 4)
        self.assertEqual(
            (self.summary().light, self.summary().label, self.summary().crashes),
            ("amber", "Resource stop", 0),
        )

    def test_crash_stays_amber_after_success_until_explicit_test(self):
        self.opened()
        self.failure()
        self.assertEqual(self.summary().light, "red")
        confirm_checkpoint(self.store, self.ctx, "playable", self.open, at=time(2))
        self.assertEqual((self.summary().light, self.summary().label), ("amber", "Past crash"))
        self.store.append(self.event("fix_implemented", 3, fix_id=digest("fix")))
        self.assertEqual(self.summary().light, "amber")

    def test_tested_fix_without_completion_is_neutral_and_issue_closure_is_separate(self):
        self._tested()
        self.assertEqual(
            (self.summary().light, self.summary().test, self.summary().completed),
            ("grey", "Passed", 0),
        )
        self.store.link(self.ctx, ISSUE_PREFIX + "9001")
        self.store.append(
            self.event(
                "issue_state",
                7,
                issue_url=ISSUE_PREFIX + "9001",
                issue_state="closed",
                source="observed",
            )
        )
        self.assertEqual((self.summary().light, self.summary().completed), ("grey", 0))
        self.assertIn("Closed", self.summary().issue)

    def test_completion_with_tested_fix_green_and_failed_retest_restores_warning(self):
        failure, fix, opened = self._tested(completed=True)
        self.assertEqual(self.summary().light, "green")
        self.store.append(
            self.event(
                "fix_test_failed",
                7,
                fix_id=fix,
                attempt_id=opened,
                target_id=failure["event_id"],
                checkpoint="playable",
            )
        )
        self.assertEqual((self.summary().light, self.summary().test), ("amber", "Failed"))

    def test_new_incident_cannot_be_cleared_by_old_test(self):
        self._tested(completed=True)
        self.store.append(
            self.event("crash_play", 8, attempt_id=self.open, incident_id=digest("new incident"))
        )
        self.assertEqual((self.summary().light, self.summary().crashes), ("red", 2))

    def test_resource_stop_does_not_hide_unrecovered_red_crash(self):
        self.opened()
        self.failure()
        self.store.append(
            self.event("resource_stop", 2, attempt_id=self.open, incident_id=digest("resource"))
        )
        self.assertEqual(self.summary().light, "red")

    def test_build_and_variant_do_not_inherit_green(self):
        self._tested(completed=True)
        for changed in (
            {**self.ctx, "variant_id": "d" * 64},
            {**self.ctx, "game_executable_sha256": "e" * 64},
        ):
            self.assertEqual(
                (self.summary(changed).light, self.summary(changed).completed), ("grey", 0)
            )
        self.assertGreater(len(self.store.records()), 0)

    def test_test_requires_attributed_failure_same_scenario_and_new_open(self):
        self.opened()
        failure = self.failure()
        fix = digest("fix")
        self.store.append(self.event("fix_implemented", 2, fix_id=fix))
        confirm_checkpoint(self.store, self.ctx, "playable", self.open, at=time(3))
        fields = dict(
            fix_id=fix, attempt_id=self.open, target_id=failure["event_id"], checkpoint="playable"
        )
        with self.assertRaises(HistoryError):
            self.store.append(self.event("fix_test_passed", 4, **fields))
        opened = self.opened(digest("newopen"), 4)
        fields["attempt_id"] = opened
        with self.assertRaises(HistoryError):
            self.store.append(
                self.event("fix_test_passed", 5, **fields)
            )  # No checkpoint on this open.
        confirm_checkpoint(self.store, self.ctx, "playable", opened, at=time(5))
        fields["checkpoint"] = "map_loaded"
        with self.assertRaises(HistoryError):
            self.store.append(self.event("fix_test_passed", 6, **fields))
        fields["checkpoint"] = "playable"
        e = self.store.event(
            {**self.ctx, "scenario": "other.xml"}, "fix_test_passed", at=time(6), **fields
        )
        with self.assertRaises(HistoryError):
            self.store.append(e)

    def test_exact_save_and_game_bindings_reject_invention(self):
        self.opened()
        for kind, fields in (
            ("retired", dict(game_id=digest("invented"))),
            ("game_resumed", dict(game_id=digest("invented"))),
            ("save_reopened", dict(save_id=digest("invented"))),
            ("continued_play", dict(save_id=digest("invented"))),
        ):
            with self.assertRaises(HistoryError):
                self.store.append(self.event(kind, attempt_id=self.open, **fields))
        self.assertEqual(self.summary().completed, 0)

    def test_observer_imports_deduplicate_and_attribution_counts_incident_once(self):
        session = dict(
            executable="/Synthetic/Game",
            started_at=time(0),
            context=self.ctx,
            process=dict(
                pid=99,
                birth_us=int(datetime.fromisoformat(time(0)).timestamp() * 1e6),
                executable="/Synthetic/Game",
            ),
        )
        observed = self.store.observe_session(session)
        self.store.observe_session(session)
        issue = dict(
            **{k: self.ctx[k] for k in ("archive_sha256", "variant_id", "game_executable_sha256")},
            incident_id=digest("import"),
            kind="crash",
            occurred_at=time(1),
        )
        imported = self.store.import_issue(issue)
        self.store.import_issue(issue)
        confirm_checkpoint(
            self.store,
            self.ctx,
            "crash_load",
            observed["attempt_id"],
            failure=imported,
            checkpoint="playable",
            at=time(2),
        )
        self.assertEqual((self.summary().opens, self.summary().crashes), (1, 1))
        self.assertEqual(self.summary().label, "Crashed during load")
        self.assertNotIn("/Synthetic/Game", json.dumps(self.store.records()))

    def test_append_dedup_collision_and_invalid_batch_leave_bytes_unchanged(self):
        e = self.event("process_confirmed", attempt_id=self.open, source="observed")
        self.store.append(e)
        before = self.store.path.read_bytes()
        self.assertEqual(self.store.append(e), ())
        with self.assertRaises(HistoryError):
            self.store.append({**e, "at": time(1)})
        with self.assertRaises(HistoryError):
            self.store.append(
                self.event("playable", 2, attempt_id=self.open),
                self.event("retired", 3, attempt_id=self.open, game_id=digest("invented")),
            )
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_concurrent_once_is_one_durable_event(self):
        def record(_):
            return PlayHistoryStore(self.library).once(
                self.ctx,
                "launch_attempted",
                event_id=digest("once"),
                attempt_id=self.open,
                source="observed",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(record, range(16)))
        self.assertEqual(len(self.store.records()), 1)
        self.assertTrue(all(e == values[0] for e in values))

    def test_private_modes_partial_append_symlink_and_bounds_fail_closed(self):
        self.opened()
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        before = self.store.path.read_bytes()
        self.store.path.write_bytes(before + b'{"schema":')
        with self.assertRaises(HistoryError):
            self.store.records()
        self.assertEqual(self.store.path.read_bytes(), before + b'{"schema":')
        self.store.path.write_bytes(before)
        with patch("smr_launcher.play_history.MAX_BYTES", len(before) - 1):
            with self.assertRaises(HistoryError):
                self.store.records()
        self.store.path.chmod(0o644)
        with self.assertRaises(HistoryError):
            self.store.records()
        self.store.path.chmod(0o600)
        self.store.path.rename(self.store.root / "real")
        self.store.path.symlink_to("real")
        with self.assertRaises(ValueError):
            self.store.records()

    def test_refresh_is_bounded_get_only_and_offline_readback_keeps_last_state(self):
        url = ISSUE_PREFIX + "9001"
        self.store.link(self.ctx, url)
        runner = Mock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout=json.dumps(dict(html_url=url, number=9001, state="open")).encode(),
            )
        )
        self.store.refresh_issues(self.store.records(), runner=runner, at=time(1))
        argv = runner.call_args.args[0]
        self.assertEqual(
            argv,
            (
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                "repos/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/9001",
            ),
        )
        self.assertIsNone(runner.call_args.kwargs["stdin"])
        self.assertNotIn(self.ctx["archive_sha256"], repr(runner.call_args))
        runner.side_effect = subprocess.TimeoutExpired("gh", 10)
        self.store.refresh_issues(self.store.records(), runner=runner, at=time(2))
        result = issue_readback(self.store.records())
        self.assertEqual(result[0]["label"], "Open · unavailable")
        self.assertEqual(result[0]["checked_at"], time(1))
        runner.side_effect = None
        self.store.refresh_issues(self.store.records(), runner=runner, at=time(3))
        stale = issue_readback(self.store.records(), datetime(2026, 10, 13, tzinfo=timezone.utc))
        self.assertEqual(stale[0]["label"], "Open · stale")

    def test_wrong_repo_pull_request_and_unknown_payload_fields_are_rejected(self):
        with self.assertRaises(HistoryError):
            self.store.link(
                self.ctx, "https://github.com/ageekhere/Definitive-SMR-Launcher/issues/1"
            )
        with self.assertRaises(HistoryError):
            self.store.event(self.ctx, "launch_attempted", raw_diagnostic="SECRET_CANARY")
        url = ISSUE_PREFIX + "9001"
        self.store.link(self.ctx, url, scope="related")
        runner = Mock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    dict(html_url=url, number=9001, state="closed", pull_request={})
                ).encode(),
            )
        )
        self.store.refresh_issues(self.store.records(), runner=runner, at=time(1))
        self.assertIn("Related", self.summary().issue)
        self.assertIn("unavailable", self.summary().issue)

    def test_user_chosen_save_hash_keeps_bytes_and_path_out_of_ledger(self):
        save = self.library / "PRIVATE_SAVE_CANARY.smr"
        contents = b"PRIVATE_GAME_CONTENTS_CANARY"
        save.write_bytes(contents)
        identity = save_digest(save)
        self.opened()
        confirm_checkpoint(
            self.store, self.ctx, "save_made", self.open, save_id=identity, at=time(1)
        )
        ledger = self.store.path.read_bytes()
        self.assertNotIn(contents, ledger)
        self.assertNotIn(save.name.encode(), ledger)
        self.assertEqual(save.read_bytes(), contents)
        self.assertEqual(identity, __import__("hashlib").sha256(contents).hexdigest())

    def test_gallery_columns_and_text_alternatives(self):
        self.assertEqual([gallery_columns(w) for w in (260, 525, 772, 1020)], [1, 1, 3, 4])
        self.assertIn("crash_load", LABELS)
        self.assertIn("resource_stop", LABELS)

    def test_fix_identity_and_linkage_cannot_cross_scenarios_or_related_scope(self):
        fix = digest("fix")
        self.store.append(self.event("fix_proposed", 1, fix_id=fix))
        with self.assertRaises(HistoryError):
            self.store.append(
                self.store.event(
                    {**self.ctx, "scenario": "other.xml"}, "fix_implemented", fix_id=fix
                )
            )
        url = ISSUE_PREFIX + "9001"
        self.store.link(self.ctx, url, scope="related")
        with self.assertRaises(HistoryError):
            self.store.append(self.event("fix_implemented", 2, fix_id=fix, issue_url=url))
        self.store.link(self.ctx, url, scope="exact")
        self.store.append(self.event("fix_implemented", 2, fix_id=fix, issue_url=url))
        with self.assertRaises(HistoryError):
            self.store.append(self.event("fix_implemented", 3, fix_id=fix))

    def test_checkpoint_cannot_predate_its_selected_open(self):
        self.opened(n=10)
        with self.assertRaises(HistoryError):
            self.store.append(self.event("playable", 1, attempt_id=self.open))

    def test_process_birth_must_match_retained_session_before_counting(self):
        session = dict(
            executable="/Synthetic/Game",
            started_at=time(10),
            context=self.ctx,
            process=dict(
                pid=99,
                birth_us=int(datetime.fromisoformat(time(0)).timestamp() * 1e6),
                executable="/Synthetic/Game",
            ),
        )
        with self.assertRaises(HistoryError):
            self.store.observe_session(session)
        self.assertEqual(self.summary().opens, 0)


class HistoryMonitorContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.library = Path(temporary.name).resolve()
        self.executable = Path("/Synthetic/Game")
        self.record = SimpleNamespace(
            archive_sha256="a" * 64, variant_id="b" * 64, profile_id="map"
        )
        self.app = SimpleNamespace(
            library=self.library,
            installation=SimpleNamespace(
                executable=self.executable, executable_sha256="c" * 64, bundle_version="1"
            ),
            catalogue=lambda: (self.record,),
            profiles=SimpleNamespace(active_profile=lambda: "map"),
        )
        self.process = dict(
            pid=55,
            birth_us=int(datetime.fromisoformat(time(0)).timestamp() * 1e6),
            executable=str(self.executable),
        )
        self.probe = Mock(return_value=self.process)
        self.publisher = Mock()
        self.monitor = CrashMonitor(
            self.app,
            "synthetic",
            reports_directory=self.library / "empty-diagnostics",
            process_probe=self.probe,
            publisher=self.publisher,
            image_uuid_reader=lambda _: None,
            resource_guard=Mock(),
            now=lambda: datetime.fromisoformat(time(10)),
        )
        self.addCleanup(self.monitor.close)

    def test_observed_exit_is_unknown_prompt_once_and_restart_does_not_add_opens(self):
        self.monitor.poll_once()
        self.monitor.poll_once()
        self.probe.return_value = None
        self.monitor.poll_once()
        self.monitor.poll_once()
        exits = self.monitor.take_history_exits()
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]["kind"], "unknown_exit")
        store = PlayHistoryStore(self.library)
        summary = summarize(store.records(), context(self.record, "c" * 64))
        self.assertEqual((summary.opens, summary.crashes, summary.completed), (1, 0, 0))
        second = CrashMonitor(
            self.app,
            "synthetic",
            reports_directory=self.library / "empty-diagnostics",
            process_probe=self.probe,
            publisher=self.publisher,
            image_uuid_reader=lambda _: None,
        )
        self.addCleanup(second.close)
        second.poll_once()
        self.assertEqual(second.take_history_exits(), [])
        self.assertEqual(summarize(store.records(), context(self.record, "c" * 64)).opens, 1)
        self.publisher.publish.assert_not_called()

    def test_probe_error_does_not_infer_exit(self):
        self.monitor.poll_once()
        self.probe.side_effect = OSError("probe unavailable")
        self.monitor.poll_once()
        self.assertEqual(self.monitor.take_history_exits(), [])

    def test_history_write_failure_cannot_prevent_resource_retention(self):
        self.monitor.arm("map", process=self.process)
        with patch.object(self.monitor.play_history, "append", side_effect=OSError("disk")):
            self.monitor._retain_resource_failure(self.process, 9 * 1024**3)
            self.assertEqual(self.monitor.history_error, "")
            self.monitor.poll_once()
        self.assertEqual(self.monitor.history_error, "disk")
        self.assertEqual(len(self.monitor.map_issues.for_map(self.record, "c" * 64)), 1)
        self.publisher.publish.assert_not_called()
