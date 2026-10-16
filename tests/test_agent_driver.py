"""Finite agent-route tests: synthetic files and provider, never the game."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/research")]
import agent_driver as driver
from active_job_control import ControlRefused, UserCancelled
from runner_journal import RunJournal, write_once


@dataclass
class Process:
    pid: int
    birth_us: int
    executable: str = "/synthetic/game"

    def same(self, other):
        return other is not None and self == other


@dataclass
class Completion:
    argv: tuple
    pid: int
    returncode: int
    stdout: bytes
    completed_at: float
    deadline: float
    reaped: bool


class ChildFailed(RuntimeError):
    def __init__(self, completion):
        super().__init__("Synthetic complete native error")
        self.completion = completion


class SyntheticLifecycle:
    def write(self, name, value):
        write_once(self.directory / name, value)

    def prepare(self):
        self.events.append("prepare")

    def launch(self, *, navigation_deadline, record_selection):
        if not self.clock[0] < navigation_deadline:
            raise ValueError("Original launch cutoff expired")
        self.events.append("launch" + str(self.launches))
        self.running = True
        self.game = Process(90 + self.launches, 1000 + self.launches)
        self.observer = SimpleNamespace(
            poll=lambda: None if self.running else 0, wait=lambda **kw: 0
        )
        self.nonce, self.code = "synthetic-nonce", "synthetic-code"

    def require_stopped(self):
        if self.running:
            raise ValueError("Exact process still running")

    def verify_available(self):
        if self.cancelled:
            raise UserCancelled("Synthetic native handback")

    def verify_code(self):
        pass

    def verify_provider(self):
        pass

    def verify_preinput_contract(self):
        pass

    def sample(self):
        pass

    def retain_frame(self, receipt, label):
        snapshot = receipt["result"]["snapshot"]
        return {
            "snapshot": snapshot["id"],
            "sha256": "d" * 64,
            "window": snapshot["window"],
            "label": label,
            "scale": 1,
        }

    def scene_frame(self, receipt, frame, window):
        assert receipt["result"]["snapshot"]["window"]["id"] == window

    def persist_outcome(self, outcome):
        self.events.append("raw")
        self.raw = dict(outcome)

    def stop_owned(self):
        self.events.append("exact_stop")
        self.running = False
        return True

    def stop(self):
        self.verify_cleanup_owner()
        self.events.append("unconditional_stop")
        return self.stop_owned()

    def restore(self):
        self.verify_cleanup_owner()
        self.require_settled_input()
        self.events.append("restore")
        return True

    def finish(self, *args):
        self.events.append("finish")
        return True


class AgentRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.clock = [100.0]
        self.enterContext(patch.object(driver.time, "monotonic", side_effect=lambda: self.clock[0]))
        self.enterContext(patch.object(driver.subprocess, "Popen", side_effect=self.spawn))
        common = SimpleNamespace(
            UKCalibration=SyntheticLifecycle,
            bounded_child=self.child,
            ChildFailed=ChildFailed,
            ChildCompletion=Completion,
            fresh_play_index=lambda receipt, pid, **kw: 18,
        )
        self.common = common
        self.owner = driver.pilot_class(common).__new__(driver.pilot_class(common))
        owner = self.owner
        owner.directory = owner.operation_directory = self.directory
        owner.anchor, owner.clock = 100.0, self.clock
        owner.frames = owner.launches = owner.seq = 0
        owner.first_stop_verified = owner.running = owner.cancelled = owner.uncertain_input = False
        owner.delivery_validation = None
        owner.events, self.argv, self.actions = [], [], []
        owner.game = owner.observer = None
        owner.diagnostic = "synthetic-diagnostic"
        live = self.directory / "profile"
        (live / "Saves").mkdir(parents=True)
        self.keep = live / "Saves/original.save"
        self.keep.write_bytes(b"preserved original synthetic save")
        self.original_stat = self.keep.stat()
        owner.profiles = SimpleNamespace(
            live=live, journal=live / "absent-journal", _state=lambda: {"active": owner.diagnostic}
        )
        owner.monitor = SimpleNamespace(
            games=lambda: [owner.game] if owner.running else [],
            inspect=lambda pid: owner.game if owner.running and owner.game.pid == pid else None,
        )
        owner.job = {
            "job": "synthetic-job",
            "purpose": driver.PURPOSE,
            "orca": "/synthetic/orca",
            "root": {"session": "synthetic-root"},
            "actor_binding": {"provider_runtime_id": "synthetic-runtime"},
            "agent_save": {
                "token": "SyntheticToken12345",
                "filename": "SyntheticToken12345.SMRailroadsSavedGame",
            },
            "source_original": {
                "archive_sha256": "a" * 64,
                "variant_id": "b" * 64,
                "scenario": "synthetic/scenario",
            },
            "game_sha256": "c" * 64,
        }
        native_source = self.directory / "native-source.synthetic"
        native_source.write_bytes(b"synthetic native contract")
        owner.job["native_source"] = str(native_source)
        self.enterContext(
            patch.object(driver, "NATIVE_SOURCE_SHA256", driver.sha(native_source.read_bytes()))
        )
        owner.admission = {"issued_epoch": 0, "expires_epoch": driver.time.time() + 1000}
        self.lease_live = True
        owner.lease_guard = self.lease_guard
        owner.control = SimpleNamespace(
            mutation_guard=self.mutation_guard,
            _poll_locked=self.poll_locked,
            cancelled=False,
            verify_owner=lambda: None,
        )
        owner.decisions = driver.AgentDecisions(owner)
        self.before_poll = None
        self.native_failure = None
        self.quit_error = False
        self.quit_ax_error = False
        self.quit_confirmation = self.quit_pending = False
        self.corrupt_exit = False
        self.reply_transform = lambda value: value
        self.stage_indexes = {}
        self.enterContext(patch.object(driver.time, "sleep", side_effect=self.advance))

    @contextmanager
    def mutation_guard(self):
        self.owner.verify_available()
        yield

    def lease_guard(self):
        if not self.lease_live:
            raise ValueError("Synthetic lease changed")

    def poll_locked(self):
        if self.before_poll:
            self.before_poll()

    def spawn(self, argv, **kwargs):
        self.argv.append(argv)
        return SimpleNamespace(pid=123)

    def child(self, argv, directory, label, tick, timeout, mutable=False, *, spawn=None):
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 30 if mutable else 10)
        if mutable:
            self.assertIsNotNone(spawn)
            spawn(argv)
            if self.native_failure:
                raise self.native_failure
        action = argv[2 : argv.index("--app")]
        self.assertEqual(argv[argv.index("--app") + 1], "pid:" + str(self.owner.game.pid))
        self.assertNotIn("--restore-window", argv)
        if action == ["list-windows"]:
            self.assertNotIn("--session", argv)
        else:
            self.assertEqual(argv[argv.index("--session") + 1], "synthetic-root")
        snapshot = {
            "id": label,
            "app": {"pid": self.owner.game.pid},
            "window": {"id": 7, "width": 800, "height": 600},
        }
        result = (
            {
                "app": {"pid": self.owner.game.pid},
                "windows": getattr(self, "window_responses", [[self.valid_window()]])[0],
            }
            if action == ["list-windows"]
            else {"snapshot": snapshot}
        )
        if action == ["list-windows"] and len(getattr(self, "window_responses", [])) > 1:
            self.window_responses.pop(0)
        if mutable:
            self.actions.append(action)
            if getattr(self, "transition_window", False):
                snapshot["window"]["id"] = 8
                result["action"] = {
                    "path": "accessibility",
                    "actionName": "AXPress",
                    "fallbackReason": None,
                }
                if getattr(self, "transition_foreign_pid", False):
                    snapshot["app"]["pid"] = 999
            if action == ["press-key", "--key", "Return"] and not self.quit_pending:
                (
                    self.owner.profiles.live / "Saves" / self.owner.job["agent_save"]["filename"]
                ).write_bytes(b"new synthetic save")
            quit_request = action == ["hotkey", "--key", "CmdOrCtrl+Q"]
            quit_confirmed = self.quit_pending and action == ["press-key", "--key", "Return"]
            if quit_request and self.quit_confirmation:
                self.quit_pending = True
            if (quit_request and not self.quit_confirmation) or quit_confirmed:
                self.quit_pending = False
                owner = self.owner
                write_once(
                    owner.helper_packet / "result.json",
                    {
                        "job": owner.job["job"],
                        "nonce": owner.nonce,
                        "anchor": owner.anchor,
                        "code": owner.code,
                        "process": {
                            "pid": owner.game.pid,
                            "birth": str(owner.game.birth_us),
                            "executable": owner.game.executable,
                        },
                        "result": {
                            "state": "observed_exited",
                            "terminal": True,
                            "stop_requested": False,
                        },
                    },
                )
                owner.running = False
                if self.corrupt_exit:
                    # The mismatched observer cannot authenticate normal exit.
                    path = owner.helper_packet / "result.json"
                    value = json.loads(path.read_text())
                    value["nonce"] = "wrong"
                    path.write_text(json.dumps(value))
                if self.quit_error:
                    error = {
                        "id": "00000000-0000-4000-8000-000000000001",
                        "ok": False,
                        "error": {
                            "code": "app_not_found",
                            "message": f"app 'pid:{owner.game.pid}' not found",
                        },
                        "_meta": {"runtimeId": "synthetic-runtime"},
                    }
                    if self.quit_ax_error:
                        error["error"] = {
                            "code": "permission_denied",
                            "message": "app 'Sid Meier's Railroads!' has visible windows but no accessibility window (AX reads stayed blocked for 1500ms after retries).",
                        }
                    raise ChildFailed(
                        Completion(
                            tuple(argv),
                            123,
                            1,
                            json.dumps(error).encode(),
                            self.clock[0],
                            self.clock[0] + timeout,
                            True,
                        )
                    )
        tick()
        return json.dumps(
            {"ok": True, "result": result, "_meta": {"runtimeId": "synthetic-runtime"}}
        ).encode()

    def advance(self, seconds):
        self.clock[0] += seconds
        requests = sorted(self.directory.glob("Agent*-request.json"))
        for request_path in requests:
            decision_path = request_path.with_name(
                request_path.name.replace("-request", "-decision")
            )
            if decision_path.exists():
                continue
            request = json.loads(request_path.read_text())
            stage = request["stage"]
            index = self.stage_indexes.get(stage, 0)
            self.stage_indexes[stage] = index + 1
            click = ["click", "--x", "10", "--y", "20"]
            drag = ["drag", "--from-x", "10", "--from-y", "20", "--to-x", "30", "--to-y", "40"]
            actions = {
                "launcher": [["click", "--element-index", "18"]],
                "menu": [click],
                "returned": [click],
                "world": [click],
                "action": [click, drag],
                "filename": [
                    ["hotkey", "--key", "CmdOrCtrl+S"],
                    click,
                    ["hotkey", "--key", "CmdOrCtrl+A"],
                    ["type-text", "--text", self.owner.job["agent_save"]["token"]],
                ],
                "saved": [["press-key", "--key", "Return"]],
                "quit": [["hotkey", "--key", "CmdOrCtrl+Q"]],
                "reopened": [click, click + ["--click-count", "2"]],
                "continued": [click, drag],
                "finished": [["hotkey", "--key", "CmdOrCtrl+Q"]],
            }[stage]
            if stage in ("quit", "finished") and self.quit_confirmation:
                actions = [actions[0], ["press-key", "--key", "Return"]]
            action = actions[index] if index < len(actions) else []
            response = {
                "request_sha256": driver.sha(request_path.read_bytes()),
                "evidence_kind": "agent_visual_decision",
                "choice": "action" if action else "observed",
                "action": action,
                "receiver": "filename_field" if stage == "filename" else "owned_window",
                "observation": "Synthetic visual decision; no game or detector evidence.",
                "exact_token": self.owner.job["agent_save"]["token"],
                "overwrite_visible": False,
            }
            write_once(decision_path, self.reply_transform(response))

    def run_trial(self):
        return driver.run_calibration(self.owner)

    def valid_window(self):
        return {
            "id": 7,
            "app": {"pid": self.owner.game.pid},
            "isOffscreen": False,
            "isMinimized": False,
        }

    def test_menu_flow_uses_two_inputs_and_real_journal_accepts_bounded_stop(self):
        self.owner.job["purpose"] = driver.WINDOW_PURPOSE
        result = self.run_trial()
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(
            self.actions,
            [["click", "--element-index", "18"]]
            + [["click", "--x", "10", "--y", "20"]] * 2
            + [["hotkey", "--key", "CmdOrCtrl+Q"]],
        )
        self.assertEqual(set(self.stage_indexes), {"launcher", "menu", "returned", "finished"})
        (self.directory / "real-journal").mkdir()
        journal = RunJournal(self.directory / "real-journal")
        journal.append("started", {"run_id": "synthetic-menu"})
        journal.outcome(result["outcome"])
        self.assertEqual(journal.records()["raw_outcome"]["payload"]["status"], "safety_stop")
        self.assertFalse(result["outcome"]["gameplay_credit"])
        self.assertFalse(result["outcome"]["save_reopen_credit"])

    def test_transport_action_uses_one_launch_no_save_and_exact_normal_exit(self):
        self.owner.job["purpose"] = driver.TRANSPORT_PURPOSE
        result = self.run_trial()
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(self.owner.launches, 1)
        self.assertEqual(set(self.stage_indexes), {"world", "action", "continued", "finished"})
        self.assertEqual(list((self.owner.profiles.live / "Saves").iterdir()), [self.keep])
        self.assertFalse(result["outcome"]["cargo_credit"])
        self.assertFalse(result["outcome"]["save_reopen_credit"])
        self.assertEqual(
            result["outcome"]["cause"], "agent_visual_transport_action_requires_review"
        )

    def test_transport_action_keeps_normal_exit_refusal_and_cleanup(self):
        self.owner.job["purpose"] = driver.TRANSPORT_PURPOSE
        self.corrupt_exit = True
        result = self.run_trial()
        self.assertIn("Exact normal exit receipt required", result["outcome"]["cause"])
        self.assertEqual(result["outcome"]["status"], "automation_failed")
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])

    def test_transport_purpose_has_distinct_admission_and_refuses_save_pair(self):
        self.assertEqual(
            driver.admission_decisions(driver.TRANSPORT_PURPOSE),
            ("ADMIT_ONE_AGENT_UK_TRANSPORT_ACTION", "GO_ONE_AGENT_UK_TRANSPORT_ACTION"),
        )
        with self.assertRaisesRegex(ControlRefused, "purpose"):
            driver.admission_decisions("unreviewed-purpose")
        self.owner.job["purpose"] = driver.TRANSPORT_PURPOSE
        self.owner.launch()
        self.reply_transform = lambda value: dict(
            value, choice="observed", action=["hotkey", "--key", "CmdOrCtrl+S"]
        )
        with self.assertRaisesRegex(ControlRefused, "Only observed track"):
            self.owner.decisions.stage("action", "Synthetic route observation")
        self.assertEqual(self.actions, [])

    def test_transport_entry_refuses_old_save_cycle_grant(self):
        actor = {"session": "synthetic", "terminal": "synthetic", "cwd": str(Path.cwd())}
        root = {"run": "run_9c111880b18c"}
        job = {
            "purpose": driver.TRANSPORT_PURPOSE,
            "common_root": "/synthetic/common",
            "actor_binding": {"driver_actor": actor},
            "root": root,
        }
        admission = {
            "decision": "ADMIT_ONE_AGENT_SAVE_REOPEN_PILOT",
            "job_sha256": "a" * 64,
            "runtime_owner": "synthetic",
            "root": root,
        }
        with (
            patch.object(driver, "verify_launch_backend"),
            patch.dict(
                driver.os.environ,
                {"CODEX_THREAD_ID": "synthetic", "ORCA_TERMINAL_HANDLE": "synthetic"},
            ),
            self.assertRaisesRegex(ControlRefused, "Exact root grant"),
        ):
            driver.verify_entry(job, admission, "a" * 64)

    def test_malformed_extra_or_hidden_windows_refuse_before_capture_or_input(self):
        self.owner.launch()
        for windows in (
            [self.valid_window(), {}],
            [dict(self.valid_window(), isOffscreen=True)],
            [{"id": True}],
        ):
            self.window_responses = [windows]
            with self.assertRaisesRegex(ControlRefused, "One exact game window required"):
                self.owner.agent_frame(
                    "Malformed" + str(len(list(self.directory.iterdir()))), 102.0
                )
        self.assertEqual(self.actions, [])

    def test_empty_startup_windows_wait_then_capture_without_input(self):
        self.owner.launch()
        self.window_responses = [[], [], [self.valid_window()]]
        self.owner.agent_frame("Startup", 102.0)
        self.assertEqual(self.clock[0], 100.5)
        self.assertEqual(self.actions, [])

    def test_semantic_play_waits_locally_and_submits_once_before_agent_request(self):
        self.owner.launch()
        original = self.owner.agent_frame
        titles = [(None, True), (63, True), (18, True), (18, True), (18, True), (None, False)]

        def frame(label, deadline):
            receipt, value, window = original(label, deadline)
            index, options = titles.pop(0)
            value["play_index"] = index
            value["window"]["title"] = (
                "Sid Meier's Railroads! Options" if options else "Sid Meier's Railroads!"
            )
            return receipt, value, window

        self.owner.agent_frame = frame
        self.reply_transform = lambda value: dict(value, choice="observed", action=[])
        result = self.owner.decisions.stage("world", "Synthetic menu observation")
        self.assertEqual(self.actions, [["click", "--element-index", "18"]])
        self.assertEqual(len(list(self.directory.glob("*-request.json"))), 1)
        self.assertEqual(len(list(self.directory.glob("*-semantic-play.json"))), 1)
        self.assertFalse(result["machine_scene_predicate"])
        self.assertEqual(titles, [])

    def test_missing_semantic_play_refuses_without_model_request_or_input(self):
        self.owner.launch()
        original = self.owner.agent_frame

        def frame(label, deadline):
            receipt, value, window = original(label, deadline)
            value["play_index"] = None
            value["window"]["title"] = "Sid Meier's Railroads! Options"
            return receipt, value, window

        self.owner.agent_frame = frame
        with self.assertRaisesRegex(ValueError, "action budget exhausted"):
            self.owner.decisions.stage("world", "Synthetic missing Play")
        self.assertEqual(self.actions, [])
        self.assertEqual(list(self.directory.glob("*-request.json")), [])

    def test_observed_track_can_open_save_as_without_another_model_request(self):
        self.owner.launch()
        self.reply_transform = lambda value: dict(
            value, choice="observed", action=["hotkey", "--key", "CmdOrCtrl+S"]
        )
        result = self.owner.decisions.stage("action", "Observe track then open Save As")
        self.assertEqual(self.actions, [["hotkey", "--key", "CmdOrCtrl+S"]])
        self.assertEqual(len(list(self.directory.glob("*-request.json"))), 1)
        self.assertEqual(result["stage"], "action")
        self.assertFalse(
            (self.owner.profiles.live / "Saves" / self.owner.job["agent_save"]["filename"]).exists()
        )

    def test_observation_pair_cannot_commit_save_or_start_quit(self):
        self.owner.launch()
        for index, action in enumerate(
            (
                ["press-key", "--key", "Return"],
                ["hotkey", "--key", "CmdOrCtrl+Q"],
            )
        ):
            self.owner.decisions = driver.AgentDecisions(self.owner)
            self.owner.decisions.sequence = index
            self.reply_transform = lambda value, action=action: dict(
                value, choice="observed", action=action
            )
            with self.assertRaisesRegex(ControlRefused, "Only observed track"):
                self.owner.decisions.stage("action", "Synthetic unsafe pairing")
        self.assertEqual(self.actions, [])

    def test_historical_launch_route_refuses_before_actor_or_admission_work(self):
        root = self.directory / "historical-owner"
        root.mkdir()
        (root / "agent_calibration.py").write_bytes(b"synthetic historical profiles.play route")
        with self.assertRaisesRegex(ControlRefused, "reviewed launcher backend composition"):
            driver.verify_entry(
                {"purpose": driver.WINDOW_PURPOSE, "common_root": str(root)}, {}, "unused"
            )
        self.assertEqual(self.actions, [])
        self.assertEqual(self.owner.events, [])
        self.assertFalse(self.owner.running)

    def test_backend_route_pin_and_maintained_module_must_both_match(self):
        root = self.directory / "reviewed-owner"
        root.mkdir()
        path = root / "agent_calibration.py"
        raw = b"synthetic reviewed app.play route"
        path.write_bytes(raw)
        with patch.object(driver, "LAUNCHER_CALIBRATION_SHA256", driver.sha(raw)):
            driver.verify_launch_backend(root)
            with patch.object(
                driver.importlib,
                "import_module",
                return_value=SimpleNamespace(__file__=str(root / "application.py")),
            ):
                with self.assertRaisesRegex(ControlRefused, "backend path drift"):
                    driver.verify_launch_backend(root)
            path.write_bytes(raw + b" changed")
            with self.assertRaisesRegex(ControlRefused, "reviewed launcher backend composition"):
                driver.verify_launch_backend(root)
            path.unlink()
            with self.assertRaises((ControlRefused, OSError)):
                driver.verify_launch_backend(root)
        self.assertEqual(self.actions, [])

    def test_empty_windows_expire_original_deadline_without_input(self):
        self.owner.launch()
        self.window_responses = [[]]
        with self.assertRaisesRegex(ControlRefused, "readiness deadline expired"):
            self.owner.agent_frame("Startup", 100.6)
        self.assertLess(self.clock[0], 100.6)
        self.assertEqual(self.actions, [])

    def test_menu_refuses_save_and_drag(self):
        frame = {"window": {"width": 800, "height": 600}}
        for action in (
            ["hotkey", "--key", "CmdOrCtrl+S"],
            ["drag", "--from-x", "1", "--from-y", "1", "--to-x", "2", "--to-y", "2"],
        ):
            with self.assertRaises(ControlRefused):
                driver.validate_action("menu", action, frame, "token", "owned_window")

    def test_startup_wait_has_no_input_and_reobserves_before_play(self):
        self.owner.launch()
        replies = []

        def waiting(value):
            replies.append(True)
            if len(replies) == 1:
                value.update(choice="wait", action=[])
                self.stage_indexes["launcher"] = 0
            return value

        self.reply_transform = waiting
        result = self.owner.decisions.stage("launcher", "Synthetic startup")
        self.assertEqual(len(self.actions), 1)
        self.assertEqual(len(list(self.directory.glob("*-wait.json"))), 1)
        self.assertEqual(result["stage"], "launcher")

    def test_late_action_reframes_without_dispatching_stale_input(self):
        self.owner.launch()
        replies = []

        def late(value):
            replies.append(True)
            if len(replies) == 1:
                # Session compaction may delay a reply beyond the former
                # 45-second limit. It must reframe without stale input.
                self.clock[0] += 65
            return value

        self.reply_transform = late
        self.owner.decisions.stage("world", "Synthetic late proposal")
        self.assertEqual(self.actions, [])
        self.assertEqual(len(list(self.directory.glob("*-stale-decision.json"))), 1)

    def test_reply_at_or_after_decision_deadline_stops_before_reframe_or_input(self):
        self.owner.launch()
        for choice, delay in (("wait", 0), ("action", 1), ("observed", 1)):
            self.owner.decisions.used_stages.clear()
            self.clock[0] = 100.0
            requests_before = len(list(self.directory.glob("*-request.json")))

            def expired(value):
                request = sorted(self.directory.glob("*-request.json"))[-1]
                self.clock[0] = json.loads(request.read_text())["reply_deadline_monotonic"] + delay
                value["choice"] = choice
                if choice != "action":
                    value["action"] = []
                return value

            self.reply_transform = expired
            with self.assertRaisesRegex(ControlRefused, "Agent decision deadline expired"):
                self.owner.decisions.stage("launcher", "Synthetic reply race")
            self.assertEqual(len(list(self.directory.glob("*-request.json"))), requests_before + 1)
        self.assertEqual(self.actions, [])

    def test_final_ingress_expiry_reframes_without_spawning_input(self):
        self.owner.launch()
        polls = []

        def slow_ingress():
            polls.append(True)
            if len(polls) == 1:
                self.clock[0] = self.owner.frame_started + driver.FRAME_SECONDS

        self.before_poll = slow_ingress
        self.owner.decisions.stage("world", "Synthetic final ingress delay")
        self.assertEqual(self.argv, [])
        self.assertFalse(self.owner.uncertain_input)
        self.assertEqual(len(list(self.directory.glob("*-stale-decision.json"))), 1)

    def test_track_secondary_click_and_final_quit_have_closed_grammar(self):
        frame = {"window": {"width": 800, "height": 600}}
        action = ["click", "--x", "10", "--y", "20", "--mouse-button", "right"]
        self.assertEqual(
            driver.validate_action("action", action, frame, "token", "owned_window"), action
        )
        with self.assertRaises(ControlRefused):
            driver.validate_action("menu", action, frame, "token", "owned_window")
        self.assertEqual(
            driver.validate_action(
                "finished", ["hotkey", "--key", "CmdOrCtrl+Q"], frame, "token", "owned_window"
            ),
            ["hotkey", "--key", "CmdOrCtrl+Q"],
        )

    def test_normal_quit_observes_confirmation_before_accepting_actual_exit(self):
        self.quit_confirmation = self.quit_error = True
        result = self.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["restored"] and result["terminal_finished"])
        quit_actions = [x for x in self.actions if x == ["hotkey", "--key", "CmdOrCtrl+Q"]]
        self.assertEqual(len(quit_actions), 2)
        self.assertEqual(len(list(self.directory.glob("*-completed-quit-error.json"))), 2)

    def test_quit_confirmation_cannot_precede_request_or_repeat_cmdq(self):
        self.owner.launch()
        self.reply_transform = lambda row: {**row, "action": ["press-key", "--key", "Return"]}
        with self.assertRaisesRegex(ControlRefused, "Quit starts once"):
            self.owner.decisions.stage("quit", "Synthetic premature confirmation")
        self.assertEqual(self.argv, [])
        self.owner.decisions.used_stages.clear()
        self.quit_confirmation = True
        self.stage_indexes.clear()
        self.reply_transform = lambda row: {**row, "action": ["hotkey", "--key", "CmdOrCtrl+Q"]}
        with self.assertRaisesRegex(ControlRefused, "Quit starts once"):
            self.owner.decisions.stage("quit", "Synthetic repeated request")
        self.assertEqual(len(self.argv), 1)

    def test_known_quit_ax_shutdown_error_requires_current_granted_permissions(self):
        self.quit_confirmation = self.quit_error = self.quit_ax_error = True
        receipt = {
            "ok": True,
            "_meta": {"runtimeId": "synthetic-runtime"},
            "result": {
                "permissions": [
                    {"id": "accessibility", "status": "granted"},
                    {"id": "screenshots", "status": "granted"},
                ]
            },
        }
        with patch.object(
            driver.subprocess, "check_output", return_value=json.dumps(receipt).encode()
        ):
            result = self.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["restored"] and result["terminal_finished"])

    def test_normal_quit_keeps_same_child_wait_during_transient_process_exit_inspection(self):
        original = self.owner.guard_running
        failures = []
        error = type("SafetyError", (RuntimeError,), {})

        def exiting(*, allow_exit=False):
            if allow_exit and not self.owner.running and self.owner.game.pid not in failures:
                failures.append(self.owner.game.pid)
                raise error("Cannot identify a live process")
            return original(allow_exit=allow_exit)

        self.owner.guard_running = exiting
        result = self.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(failures), 2)
        self.assertEqual(self.actions.count(["hotkey", "--key", "CmdOrCtrl+Q"]), 2)
        self.assertTrue(result["restored"] and result["terminal_finished"])

    def test_quit_poll_does_not_defer_foreign_or_unknown_game_refusal(self):
        original = self.owner.guard_running
        error = type("SafetyError", (RuntimeError,), {})

        def foreign(*, allow_exit=False):
            if allow_exit and not self.owner.running:
                raise error("Another or unidentified Railroads process is running")
            return original(allow_exit=allow_exit)

        self.owner.guard_running = foreign
        result = self.run_trial()
        self.assertIn("Another or unidentified", result["outcome"]["cause"])
        self.assertFalse(result["restored"])
        self.assertTrue(self.owner.uncertain_input)

    def test_actual_permission_loss_during_quit_keeps_uncertainty_hold(self):
        self.quit_error = self.quit_ax_error = True
        receipt = {
            "ok": True,
            "_meta": {"runtimeId": "synthetic-runtime"},
            "result": {
                "permissions": [
                    {"id": "accessibility", "status": "denied"},
                    {"id": "screenshots", "status": "granted"},
                ]
            },
        }
        with patch.object(
            driver.subprocess, "check_output", return_value=json.dumps(receipt).encode()
        ):
            result = self.run_trial()
        self.assertIn("Platform permissions unavailable", result["outcome"]["cause"])
        self.assertFalse(result["restored"])
        self.assertTrue(self.owner.uncertain_input)

    def test_complete_route_dispatches_supported_argv_for_all_seven_stages_and_preserves_save(self):
        result = self.run_trial()
        self.assertTrue(result["terminal_finished"], result)
        self.assertEqual(result["errors"], [])
        self.assertEqual(set(self.stage_indexes), set(driver.STAGES))
        for action in (
            ["hotkey", "--key", "CmdOrCtrl+S"],
            ["type-text", "--text", "SyntheticToken12345"],
            ["press-key", "--key", "Return"],
            ["click", "--x", "10", "--y", "20", "--click-count", "2"],
        ):
            self.assertIn(action, self.actions)
        self.assertEqual(self.actions.count(["hotkey", "--key", "CmdOrCtrl+Q"]), 2)
        self.assertEqual(sum(a[0] == "drag" for a in self.actions), 2)
        self.assertEqual(result["outcome"]["world_loaded"], "agent_observed")
        self.assertFalse(result["outcome"]["human_confirmation"])
        self.assertFalse(result["outcome"]["gameplay_credit"])
        self.assertFalse(result["outcome"]["save_reopen_credit"])
        self.assertEqual(
            self.owner.events[-4:], ["unconditional_stop", "exact_stop", "restore", "finish"]
        )
        self.assertEqual(self.keep.read_bytes(), b"preserved original synthetic save")
        self.assertEqual(
            (self.keep.stat().st_ino, self.keep.stat().st_mtime_ns),
            (self.original_stat.st_ino, self.original_stat.st_mtime_ns),
        )
        self.assertLessEqual(self.owner.frames, 96)
        self.assertGreaterEqual(self.owner.frames, len(self.argv) + 2 * len(driver.STAGES))

    def test_model_nonresponse_expires_and_uses_existing_cleanup(self):
        with patch.object(
            driver.time,
            "sleep",
            side_effect=lambda seconds: self.clock.__setitem__(0, self.clock[0] + 3),
        ):
            result = self.run_trial()
        self.assertIn("Agent decision deadline expired", result["outcome"]["cause"])
        self.assertTrue(result["terminal_finished"])
        self.assertFalse(self.argv)

    def test_complete_post_quit_errors_and_exact_normal_exits_settle_without_input_success_claim(
        self,
    ):
        self.quit_error = True
        result = self.run_trial()
        self.assertTrue(result["terminal_finished"], result)
        receipts = list(self.directory.glob("*-completed-quit-error.json"))
        self.assertEqual(len(receipts), 2)
        for path in receipts:
            receipt = json.loads(path.read_text())
            self.assertTrue(receipt["operation_failed"])
            self.assertFalse(receipt["input_effect_proven"])
        self.assertFalse(self.owner.uncertain_input)

    def test_completed_quit_error_without_exact_exit_proof_keeps_uncertainty_hold(self):
        self.quit_error = self.corrupt_exit = True
        result = self.run_trial()
        self.assertTrue(result["stopped"])
        self.assertFalse(result["restored"])
        self.assertTrue(self.owner.uncertain_input)
        self.assertFalse(list(self.directory.glob("*-completed-quit-error.json")))

    def test_quit_error_contract_refuses_unreaped_late_wrong_runtime_and_wrong_argv(self):
        argv = ["/synthetic/orca", "computer", "hotkey", "--key", "CmdOrCtrl+Q", "--app", "pid:91"]
        receipt = {
            "id": "00000000-0000-4000-8000-000000000001",
            "ok": False,
            "error": {"code": "app_not_found", "message": "app 'pid:91' not found"},
            "_meta": {"runtimeId": "synthetic-runtime"},
        }
        for changes, runtime, target_argv in (
            ({"reaped": False}, "synthetic-runtime", argv),
            ({"completed_at": 111}, "synthetic-runtime", argv),
            ({}, "wrong-runtime", argv),
            ({}, "synthetic-runtime", argv + ["--restore-window"]),
        ):
            values = dict(
                argv=tuple(argv),
                pid=123,
                returncode=1,
                stdout=json.dumps(receipt).encode(),
                completed_at=100,
                deadline=110,
                reaped=True,
            )
            values.update(changes)
            self.assertIsNone(
                driver.completed_quit_error(
                    self.common, ChildFailed(Completion(**values)), target_argv, runtime, 110
                )
            )

    def test_human_label_or_wrong_request_refuses_without_input(self):
        self.reply_transform = lambda row: {**row, "evidence_kind": "native_direct_human_report"}
        result = self.run_trial()
        self.assertIn("Exact agent reply required", result["outcome"]["cause"])
        self.assertFalse(self.argv)
        self.assertTrue(result["terminal_finished"])

    def test_overwrite_refuses_without_input_and_preserves_original(self):
        self.reply_transform = lambda row: {**row, "overwrite_visible": True}
        result = self.run_trial()
        self.assertFalse(self.argv)
        self.assertTrue(result["terminal_finished"])

    def test_uncertain_native_delivery_stops_but_blocks_restoration_and_finish(self):
        self.native_failure = TimeoutError("Synthetic queued operation remains unsettled")
        result = self.run_trial()
        self.assertTrue(result["stopped"])
        self.assertFalse(result["restored"])
        self.assertFalse(result["terminal_finished"])
        self.assertTrue(self.owner.uncertain_input)
        self.assertEqual(len(self.argv), 1)
        self.assertIn("unconditional_stop", self.owner.events)
        self.assertNotIn("restore", self.owner.events)

    def test_semantic_play_requires_fresh_exact_index_and_refuses_coordinate_replay(self):
        frame = {"play_index": 18, "window": {"width": 800, "height": 600}}
        action = ["click", "--element-index", "18"]
        self.assertEqual(
            driver.validate_action("launcher", action, frame, "token", "owned_window"), action
        )
        for wrong in (["click", "--element-index", "17"], ["click", "--x", "10", "--y", "20"]):
            with self.assertRaises(ControlRefused):
                driver.validate_action("launcher", wrong, frame, "token", "owned_window")
        with self.assertRaises(ControlRefused):
            driver.validate_action(
                "launcher", action, {"play_index": None}, "token", "owned_window"
            )

    def test_semantic_axpress_may_return_new_window_only_for_same_owned_pid(self):
        self.owner.launch()
        self.transition_window = True
        receipt = self.owner.native(
            "PlayTransition", ["click", "--element-index", "18"], 7, 110, mutable=True
        )
        self.assertEqual(receipt["result"]["snapshot"]["window"]["id"], 8)
        with self.assertRaisesRegex(ControlRefused, "Native receiver drift"):
            self.owner.native(
                "CoordinateDrift", ["click", "--x", "10", "--y", "20"], 7, 110, mutable=True
            )
        self.transition_foreign_pid = True
        with self.assertRaisesRegex(ControlRefused, "Native receiver drift"):
            self.owner.native(
                "ForeignTransition", ["click", "--element-index", "18"], 7, 110, mutable=True
            )

    def test_fresh_dispatch_can_finish_after_frame_expiry_within_operation_deadline(self):
        self.owner.launch()
        self.clock[0] = 100 + driver.FRAME_SECONDS - 1
        original = self.common.bounded_child

        def slow_completion(*args, **kwargs):
            result = original(*args, **kwargs)
            self.clock[0] = 100 + driver.FRAME_SECONDS + 3
            return result

        self.common.bounded_child = slow_completion
        self.owner.agent_input(
            "LoadingTransition", ["click", "--x", "10", "--y", "20"], 7, {}, 100, 200, None
        )
        self.assertEqual(len(self.argv), 1)
        self.assertIn("--no-screenshot", self.argv[0])
        self.assertFalse(self.owner.uncertain_input)

    def test_relaunch_uses_remaining_original_operation_clock_without_renewal(self):
        self.owner.launch()
        self.owner.first_stop_verified = True
        self.owner.running = False
        self.clock[0] = self.owner.anchor + 250
        self.owner.launch()
        self.assertEqual(self.owner.launches, 2)
        self.assertEqual(self.owner.anchor, 100)
        self.clock[0] = self.owner.anchor + driver.ACTION_SECONDS
        with self.assertRaisesRegex(ControlRefused, "Fixed agent operation deadline expired"):
            self.owner.guard_running()

    def test_frame_expiry_still_refuses_before_dispatch(self):
        self.owner.launch()
        self.clock[0] = 100 + driver.FRAME_SECONDS
        with self.assertRaisesRegex(ControlRefused, "Agent frame expired before delivery"):
            self.owner.agent_input(
                "StaleDispatch", ["click", "--x", "10", "--y", "20"], 7, {}, 100, 200, None
            )
        self.assertFalse(self.argv)

    def test_known_observer_shutdown_overlap_keeps_diagnostic_and_accepts_exact_absence(self):
        self.owner.launch()
        owner = self.owner
        owner.running = False
        owner.observer = SimpleNamespace(wait=lambda **kw: 2)
        result = {
            "job": owner.job["job"],
            "nonce": owner.nonce,
            "code": owner.code,
            "anchor": owner.anchor,
            "process": {
                "pid": owner.game.pid,
                "birth": str(owner.game.birth_us),
                "executable": owner.game.executable,
            },
            "result": {
                "state": "observed_exited",
                "terminal": True,
                "stop_requested": False,
                "reason": "invalid_clock",
            },
        }
        write_once(owner.helper_packet / "result.json", result)
        write_once(
            owner.helper_packet / "state.json",
            {k: result[k] for k in ("job", "nonce", "code", "anchor")}
            | {"state": {"terminal": True, "reason": "bound_identity_unknown_or_mismatch"}},
        )
        owner.normal_exit_receipt()
        self.assertEqual(json.loads((owner.helper_packet / "result.json").read_text()), result)
        (owner.helper_packet / "state.json").unlink()
        write_once(
            owner.helper_packet / "state.json",
            {k: result[k] for k in ("job", "nonce", "code", "anchor")}
            | {"state": {"terminal": True, "reason": "some_other_failure"}},
        )
        with self.assertRaisesRegex(ControlRefused, "Unknown observer failure"):
            owner.normal_exit_receipt()

    def test_completed_one_press_failure_allows_stopped_cleanup_keeps_effect_unknown(self):
        self.owner.launch()
        action = ["click", "--element-index", "18"]
        argv = [
            "/synthetic/orca",
            "computer",
            *action,
            "--app",
            "pid:91",
            "--json",
            "--session",
            "synthetic-root",
            "--window-id",
            "7",
            "--no-screenshot",
        ]
        response = {
            "id": "00000000-0000-4000-8000-000000000001",
            "ok": False,
            "error": {
                "code": "window_not_focused",
                "message": "coordinate click aborted because target pid 91 window 7 is no longer the focused topmost recipient (current: no focused window); 1 press(es) may already have been delivered; run get-app-state and verify state before retrying",
                "data": {
                    "nextSteps": [
                        "Run `orca computer get-app-state --app <app> --json` and verify whether the intended action already occurred before retrying.",
                        "Do not retry the click if it already took effect; otherwise bring the target window forward and use fresh state before trying again.",
                    ]
                },
            },
            "_meta": {"runtimeId": "synthetic-runtime"},
        }
        completion = Completion(tuple(argv), 123, 1, json.dumps(response).encode(), 100, 110, True)
        self.native_failure = ChildFailed(completion)
        source = self.directory / "click-source.synthetic"
        source.write_bytes(b"synthetic balanced post source")
        self.owner.job["native_click_source"] = str(source)
        with patch.object(driver, "CLICK_SOURCE_SHA256", driver.sha(source.read_bytes())):
            with self.assertRaises(ChildFailed):
                self.owner.native("CompletedClick", action, 7, 110, mutable=True)
        self.assertTrue(self.owner.uncertain_input)
        self.owner.stop_owned()
        self.assertTrue(self.owner.restore())
        self.assertTrue(self.owner.uncertain_input)
        receipt = json.loads((self.directory / "CompletedClick-completed-error.json").read_text())
        self.assertEqual(receipt["effect"], "unknown")
        self.assertTrue(receipt["provider_emission_completed"])
        self.assertEqual(receipt["completed_at"], 100)
        self.assertEqual(len(self.argv), 1)
        for count in ("0", "2"):
            changed = json.loads(json.dumps(response))
            changed["error"]["message"] = changed["error"]["message"].replace(
                "1 press(es)", count + " press(es)"
            )
            failed = ChildFailed(
                Completion(tuple(argv), 123, 1, json.dumps(changed).encode(), 100, 110, True)
            )
            self.assertIsNone(
                driver.completed_click_error(self.common, failed, argv, "synthetic-runtime", 110)
            )
        self.assertNotIn("finish", self.owner.events)

    def test_cancellation_during_final_poll_prevents_native_spawn(self):
        self.before_poll = lambda: setattr(self.owner.control, "cancelled", True)
        result = self.run_trial()
        self.assertFalse(self.argv)
        self.assertEqual(result["outcome"]["cause"], "user_requested_game_handback")
        self.assertTrue(result["terminal_finished"])

    def test_ingress_prework_cannot_create_target_before_final_save_validation(self):
        def poll():
            if "saved" in self.stage_indexes:
                target = (
                    self.owner.profiles.live / "Saves" / self.owner.job["agent_save"]["filename"]
                )
                target.write_bytes(b"racing writer must survive")

        self.before_poll = poll
        result = self.run_trial()
        self.assertFalse(any(action == ["press-key", "--key", "Return"] for action in self.actions))
        self.assertIn("Before-save inventory drift", result["outcome"]["cause"])
        self.assertTrue(result["terminal_finished"])

    def test_expired_frame_after_blocking_ingress_refuses_native_spawn(self):
        self.before_poll = lambda: self.clock.__setitem__(
            0, self.clock[0] + driver.FRAME_SECONDS + 1
        )
        result = self.run_trial()
        self.assertFalse(self.argv)
        self.assertIn("expired", result["outcome"]["cause"])
        self.assertTrue(result["terminal_finished"])

    def test_lease_change_during_prework_prevents_input(self):
        self.reply_transform = lambda row: setattr(self, "lease_live", False) or row
        result = self.run_trial()
        self.assertFalse(self.argv)
        self.assertFalse(result["restored"])

    def test_selector_clipboard_focus_restore_and_wrong_token_are_refused(self):
        frame = {"window": {"width": 800, "height": 600}}
        for action in (
            ["type-text", "--text", "wrong"],
            ["paste-text", "--text", "SyntheticToken12345"],
            ["click", "--x", "10", "--y", "20", "--restore-window"],
            ["hotkey", "--key", "CmdOrCtrl+A", "--app", "other"],
            ["click", "--x", "800", "--y", "1"],
        ):
            with self.subTest(action=action), self.assertRaises((ValueError, RuntimeError)):
                driver.validate_action(
                    "filename", action, frame, "SyntheticToken12345", "filename_field"
                )


if __name__ == "__main__":
    unittest.main()
