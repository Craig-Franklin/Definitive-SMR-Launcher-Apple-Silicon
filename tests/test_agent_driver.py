"""Finite agent-route tests: synthetic files and provider, never the game."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, MethodType
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
        owner.launch_deadline = owner.anchor + 120
        owner.frames = owner.launches = owner.seq = 0
        owner.first_stop_verified = owner.running = owner.cancelled = owner.uncertain_input = False
        owner.delivery_validation = None
        owner.transport_zoom_attempts = 0
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
                    ["type-text", "--text", self.owner.job.get("agent_save", {}).get("token")],
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
                "receiver": "filename_field" if stage in ("filename", "saved") else "owned_window",
                "observation": "Synthetic visual decision; no game or detector evidence.",
                "exact_token": self.owner.job.get("agent_save", {}).get("token"),
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

    def test_transport_partial_skips_cargo_and_uses_existing_confirmed_quit(self):
        self.owner.job["purpose"] = driver.TRANSPORT_PURPOSE
        self.quit_confirmation = True
        self.reply_transform = lambda value: (
            dict(value, choice="partial", action=[], observation="Second stop not established")
            if "action" in self.stage_indexes and "finished" not in self.stage_indexes
            else value
        )
        result = self.run_trial()
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(result["outcome"]["partial_reason"], "Second stop not established")
        self.assertEqual(result["outcome"]["agent_observed_stages"], ["world", "finished"])
        self.assertNotIn("continued", self.stage_indexes)
        self.assertFalse(result["outcome"]["cargo_credit"])
        self.assertEqual(
            self.actions[-2:],
            [["hotkey", "--key", "CmdOrCtrl+Q"], ["press-key", "--key", "Return"]],
        )

    def assert_transport_cutoff(self, final_ingress):
        self.owner.job["purpose"] = driver.TRANSPORT_PURPOSE
        self.quit_confirmation = True

        def expire_work():
            if "action" in self.stage_indexes and "finished" not in self.stage_indexes:
                self.clock[0] = self.owner.anchor + driver.TRANSPORT_WORK_SECONDS

        if final_ingress:
            self.before_poll = expire_work
        else:

            def reply(value):
                expire_work()
                return value

            self.reply_transform = reply
        result = self.run_trial()
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertIn("quit margin", result["outcome"]["partial_reason"])
        self.assertNotIn("continued", self.stage_indexes)
        self.assertFalse(self.owner.uncertain_input)
        self.assertEqual(len(self.actions), 3, "Only world input, Quit and confirmation")
        self.assertLess(self.clock[0], self.owner.anchor + driver.ACTION_SECONDS)

    def test_transport_cutoff_during_reply_preserves_quit_margin(self):
        self.assert_transport_cutoff(False)

    def test_transport_cutoff_during_final_ingress_preserves_quit_margin(self):
        self.assert_transport_cutoff(True)

    def test_fixed_scroll_grammar_refuses_other_stages_targets_and_units(self):
        frame = {"window": {"width": 800, "height": 600}}
        action = ["scroll", "--x", "200", "--y", "100", "--direction", "down", "--pages", "0.1"]
        self.assertEqual(
            driver.validate_action(
                "action", action, frame, "token", "owned_window", purpose=driver.TRANSPORT_PURPOSE
            ),
            action,
        )
        for stage, purpose, receiver, proposed in (
            ("continued", driver.TRANSPORT_PURPOSE, "owned_window", action),
            ("world", driver.TRANSPORT_PURPOSE, "owned_window", action),
            ("finished", driver.TRANSPORT_PURPOSE, "owned_window", action),
            ("action", driver.PURPOSE, "owned_window", action),
            ("action", driver.WINDOW_PURPOSE, "owned_window", action),
            ("action", driver.TRANSPORT_PURPOSE, "filename_field", action),
            ("action", driver.TRANSPORT_PURPOSE, "owned_window", action[:-1] + ["1"]),
            (
                "action",
                driver.TRANSPORT_PURPOSE,
                "owned_window",
                action[:-3] + ["up", "--pages", "0.1"],
            ),
            ("action", driver.TRANSPORT_PURPOSE, "owned_window", action + ["--restore-window"]),
            ("action", driver.TRANSPORT_PURPOSE, "owned_window", action + ["--app", "pid:999"]),
            (
                "action",
                driver.TRANSPORT_PURPOSE,
                "owned_window",
                ["scroll", "--element-index", "2", *action[-4:]],
            ),
            ("action", driver.TRANSPORT_PURPOSE, "owned_window", [*action[:2], "800", *action[3:]]),
            ("action", driver.TRANSPORT_PURPOSE, "owned_window", [*action[:4], "NaN", *action[5:]]),
        ):
            with self.subTest(stage=stage, proposed=proposed), self.assertRaises(ControlRefused):
                driver.validate_action(stage, proposed, frame, "token", receiver, purpose=purpose)

    def test_scroll_emits_owned_window_relative_points_without_adding_origin_or_scale(self):
        self.owner.job["purpose"] = driver.TRANSPORT_PURPOSE
        self.owner.launch()
        frame = {"window": {"x": 1000, "y": 2000, "width": 800, "height": 600}, "scale": 0.5}
        # A displayed pixel100,50 is window-relative point200,100. The pinned
        # provider coordinatePoint adds windowBounds.minX/minY, not the owner.
        action = ["scroll", "--x", "200", "--y", "100", "--direction", "down", "--pages", "0.1"]
        driver.validate_action(
            "action", action, frame, "token", "owned_window", purpose=driver.TRANSPORT_PURPOSE
        )
        self.owner.agent_input("Zoom", action, 7, frame, 100, 110, None)
        self.assertEqual(
            self.argv,
            [
                [
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
            ],
        )
        self.assertEqual(self.owner.transport_zoom_attempts, 1)

    def test_third_scroll_never_spawns_and_enters_existing_normal_quit(self):
        self.owner.job["purpose"] = driver.TRANSPORT_PURPOSE
        zoom = ["scroll", "--x", "200", "--y", "100", "--direction", "down", "--pages", "0.1"]
        self.reply_transform = lambda row: (
            dict(row, choice="action", action=zoom)
            if "action" in self.stage_indexes and "finished" not in self.stage_indexes
            else row
        )
        result = self.run_trial()
        self.assertEqual(self.owner.transport_zoom_attempts, 2)
        self.assertEqual(self.actions.count(zoom), 2)
        self.assertIn("Two zoom attempts exhausted", result["outcome"]["partial_reason"])
        self.assertNotIn("continued", self.stage_indexes)
        self.assertFalse(self.owner.uncertain_input)
        self.assertTrue(result["restored"] and result["terminal_finished"])
        self.assertEqual(self.actions[-1], ["hotkey", "--key", "CmdOrCtrl+Q"])

    def test_scroll_stale_or_cancelled_final_ingress_does_not_consume_attempt(self):
        self.owner.job["purpose"] = driver.TRANSPORT_PURPOSE
        self.owner.launch()
        action = ["scroll", "--x", "200", "--y", "100", "--direction", "down", "--pages", "0.1"]
        frame = {"window": {"width": 800, "height": 600}}
        self.before_poll = lambda: self.clock.__setitem__(0, 161)
        with self.assertRaises(driver.StaleFrame):
            self.owner.agent_input("StaleZoom", action, 7, frame, 100, 180, None)
        self.before_poll = lambda: setattr(self.owner.control, "cancelled", True)
        with self.assertRaises(UserCancelled):
            self.owner.agent_input("CancelledZoom", action, 7, frame, 161, 180, None)
        self.assertFalse(self.argv)
        self.assertEqual(self.owner.transport_zoom_attempts, 0)
        self.assertFalse(self.owner.uncertain_input)

    def test_unknown_scroll_delivery_keeps_existing_restoration_hold(self):
        self.owner.job["purpose"] = driver.TRANSPORT_PURPOSE
        zoom = ["scroll", "--x", "200", "--y", "100", "--direction", "down", "--pages", "0.1"]

        def fail_zoom(row):
            if "action" in self.stage_indexes:
                self.native_failure = TimeoutError("Unsettled synthetic scroll")
                return dict(row, choice="action", action=zoom)
            return row

        self.reply_transform = fail_zoom
        result = self.run_trial()
        self.assertEqual(self.owner.transport_zoom_attempts, 1)
        self.assertEqual(self.actions.count(zoom), 0)  # Fake fails after spawn, before response.
        self.assertTrue(self.owner.uncertain_input and result["stopped"])
        self.assertFalse(result["restored"] or result["terminal_finished"])

    def test_depot_shortcut_is_transport_only(self):
        frame = {"window": {"width": 800, "height": 600}}
        action = ["press-key", "--key", "B"]
        self.assertEqual(
            driver.validate_action(
                "action", action, frame, "token", "owned_window", purpose=driver.TRANSPORT_PURPOSE
            ),
            action,
        )
        for purpose, stage in (
            (driver.PURPOSE, "action"),
            (driver.WINDOW_PURPOSE, "menu"),
            (driver.TRANSPORT_PURPOSE, "world"),
        ):
            with self.assertRaises(ControlRefused):
                driver.validate_action(
                    stage, action, frame, "token", "owned_window", purpose=purpose
                )

    def assert_partial_refused(self, purpose, action):
        self.owner.launch()
        self.owner.job["purpose"] = purpose
        self.reply_transform = lambda value: dict(value, choice="partial", action=action)
        with self.assertRaisesRegex(ControlRefused, "Partial finish"):
            self.owner.decisions.stage("action", "Synthetic prerequisite")
        self.assertEqual(self.actions, [])

    def test_partial_cannot_carry_input(self):
        self.assert_partial_refused(driver.TRANSPORT_PURPOSE, ["press-key", "--key", "B"])

    def test_partial_cannot_finish_save_cycle_early(self):
        self.assert_partial_refused(driver.PURPOSE, [])

    def test_cached_load_partial_has_no_input_and_existing_normal_quit_remains_available(self):
        import catalogue_menu

        self.owner.launch()
        self.owner.job["purpose"] = driver.CACHED_LOAD_PURPOSE
        self.reply_transform = lambda value: dict(
            value, choice="partial", action=[], observation="Exact scenario title not visible"
        )
        with self.assertRaisesRegex(catalogue_menu.MenuIncomplete, "title not visible"):
            self.owner.decisions.stage("world", "Synthetic exact scenario")
        self.assertEqual(self.actions, [])
        self.reply_transform = lambda value: value
        self.owner.normal_quit("finished", None)
        self.assertEqual(self.actions, [["hotkey", "--key", "CmdOrCtrl+Q"]])
        self.assertFalse(self.owner.running)

    def test_load_without_save_intent_runs_actual_actions_and_confirmed_normal_quit(self):
        self.owner.launch()
        self.owner.job["purpose"] = driver.CACHED_LOAD_PURPOSE
        del self.owner.job["agent_save"]
        self.owner.decisions.stage("world", "Observe the actual loaded world")
        self.quit_confirmation = True
        self.owner.normal_quit("finished", None)
        self.assertEqual(
            self.actions,
            [
                ["click", "--x", "10", "--y", "20"],
                ["hotkey", "--key", "CmdOrCtrl+Q"],
                ["press-key", "--key", "Return"],
            ],
        )
        self.assertFalse(self.owner.running)

    def test_direct_catalogue_load_has_load_only_decisions_without_save_intent(self):
        self.owner.launch()
        self.owner.job["purpose"] = driver.CATALOGUE_LOAD_PURPOSE
        del self.owner.job["agent_save"]
        self.assertEqual(
            driver.admission_decisions(driver.CATALOGUE_LOAD_PURPOSE),
            ("ADMIT_ONE_AGENT_PACKAGED_CATALOGUE_LOAD", "GO_ONE_AGENT_PACKAGED_CATALOGUE_LOAD"),
        )
        for stage in ("menu", "filename", "saved", "reopened", "action", "continued"):
            with self.subTest(stage=stage), self.assertRaisesRegex(ControlRefused, "LOAD stage"):
                self.owner.decisions.stage(stage, "Forbidden for direct load")
        self.owner.decisions.stage("world", "Select exact reviewed Solo scenario")
        self.quit_confirmation = True
        self.owner.normal_quit("finished", None)
        self.assertFalse(self.owner.running)
        self.assertEqual(set(self.stage_indexes), {"world", "finished"})
        frame = dict(window=dict(width=200, height=100))
        for action in (
            ["hotkey", "--key", "CmdOrCtrl+S"],
            ["drag", "--from-x", "1", "--from-y", "2", "--to-x", "3", "--to-y", "4"],
        ):
            with self.assertRaises(ControlRefused):
                driver.validate_action(
                    "world",
                    action,
                    frame,
                    None,
                    "owned_window",
                    purpose=driver.CATALOGUE_LOAD_PURPOSE,
                )

    def test_load_without_save_intent_submits_semantic_play_once(self):
        self.owner.launch()
        self.owner.job["purpose"] = driver.CACHED_LOAD_PURPOSE
        del self.owner.job["agent_save"]
        original = self.owner.agent_frame
        titles = [True, True, False]

        def frame(label, deadline):
            receipt, value, window = original(label, deadline)
            value["play_index"] = 18
            value["window"]["title"] = (
                "Sid Meier's Railroads! Options" if titles.pop(0) else "Main Menu"
            )
            return receipt, value, window

        self.owner.agent_frame = frame
        self.reply_transform = lambda value: dict(value, choice="observed", action=[])
        self.owner.decisions.stage("launcher", "Observe main menu after unique Play")
        self.assertEqual(self.actions, [["click", "--element-index", "18"]])
        self.assertEqual(titles, [])

    def test_load_rejects_save_and_construction_stages_before_frame_or_input(self):
        self.owner.job["purpose"] = driver.CACHED_LOAD_PURPOSE
        del self.owner.job["agent_save"]
        for stage in ("filename", "saved", "reopened", "action", "continued"):
            with self.subTest(stage=stage), self.assertRaisesRegex(ControlRefused, "LOAD stage"):
                self.owner.decisions.stage(stage, "Not authorized by LOAD purpose")
        self.assertEqual(self.actions, [])
        self.assertEqual(list(self.directory.glob("*-request.json")), [])

    def test_cached_load_partial_with_input_refuses(self):
        self.owner.launch()
        self.owner.job["purpose"] = driver.CACHED_LOAD_PURPOSE
        self.reply_transform = lambda value: dict(value, choice="partial")
        with self.assertRaisesRegex(ControlRefused, "Partial load cannot carry input"):
            self.owner.decisions.stage("world", "Synthetic exact scenario")
        self.assertEqual(self.actions, [])

    def test_cached_load_decision_cap_hands_back_without_seventeenth_input(self):
        import catalogue_menu

        self.owner.launch()
        self.owner.job["purpose"] = driver.CACHED_LOAD_PURPOSE
        self.reply_transform = lambda value: dict(value, choice="wait", action=[])
        with self.assertRaisesRegex(catalogue_menu.MenuIncomplete, "decision budget exhausted"):
            self.owner.decisions.stage("world", "Synthetic exact scenario")
        self.assertEqual(self.stage_indexes["world"], 16)
        self.assertEqual(self.actions, [])
        self.reply_transform = lambda value: value
        self.owner.normal_quit("finished", None)
        self.assertFalse(self.owner.running)

    def test_cached_load_purpose_remains_separate_and_cannot_save_or_construct(self):
        self.assertEqual(
            driver.admission_decisions(driver.CACHED_LOAD_PURPOSE),
            ("ADMIT_ONE_AGENT_PACKAGED_CACHED_LOAD", "GO_ONE_AGENT_PACKAGED_CACHED_LOAD"),
        )
        frame = {"window": {"width": 200, "height": 100}}
        for action in (
            ["hotkey", "--key", "CmdOrCtrl+S"],
            ["type-text", "--text", "SyntheticToken12345"],
            ["drag", "--from-x", "10", "--from-y", "20", "--to-x", "30", "--to-y", "40"],
        ):
            with self.assertRaises(ControlRefused):
                driver.validate_action(
                    "world",
                    action,
                    frame,
                    "SyntheticToken12345",
                    "owned_window",
                    purpose=driver.CACHED_LOAD_PURPOSE,
                )

    def cached_save_owner(self):
        import catalogue_menu

        parent = type(self.owner)
        cls = catalogue_menu.pilot_class(self.common, parent)
        self.owner.__class__ = cls
        # Exercise the real catalogue save workflow and input/quit guards with
        # synthetic physical lifecycle hooks. Package-only launch composition
        # is independently covered by test_catalogue_menu.
        for name in ("prepare", "launch", "stop", "restore"):
            setattr(self.owner, name, MethodType(getattr(parent, name), self.owner))
        self.owner.job["purpose"] = driver.CACHED_SAVE_PURPOSE
        self.owner.config = dict(
            target=dict(profile_id=self.owner.diagnostic), scenario_title="Synthetic Canyon"
        )

    def test_catalogue_save_workflow_binds_exact_file_reopens_and_has_both_normal_exits(self):
        self.cached_save_owner()
        result = self.run_trial()
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.owner.launches, 2)
        self.assertEqual(
            set(self.stage_indexes), {"world", "filename", "quit", "reopened", "finished"}
        )
        self.assertFalse(
            result["outcome"]["gameplay_credit"] or result["outcome"]["save_reopen_credit"]
        )
        self.assertEqual(self.keep.read_bytes(), b"preserved original synthetic save")
        self.assertTrue((self.directory / "save-after-reopen-identity.json").is_file())
        self.assertEqual(self.actions.count(["hotkey", "--key", "CmdOrCtrl+Q"]), 2)
        self.assertFalse(any(action[0] == "drag" for action in self.actions))

    def test_save_lost_owned_window_enters_cleanup_without_quit_polling_or_save(self):
        self.cached_save_owner()
        original = self.owner.agent_frame

        def frame(label, deadline):
            result = original(label, deadline)
            self.window_responses = [[]]
            return result

        self.owner.agent_frame = frame
        result = self.run_trial()
        self.assertIn("Previously observed SAVE window unavailable", result["outcome"]["cause"])
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(self.stage_indexes, {"world": 1})
        self.assertNotIn(["hotkey", "--key", "CmdOrCtrl+Q"], self.actions)
        self.assertEqual(self.keep.read_bytes(), b"preserved original synthetic save")
        self.assertFalse((self.keep.parent / self.owner.job["agent_save"]["filename"]).exists())

    def test_filename_commit_reuses_observed_frame_after_capture_before_without_second_decision(
        self,
    ):
        self.cached_save_owner()
        original = self.owner.agent_input

        def delivery(label, *args, **kwargs):
            if label.endswith("FilenameCommit"):
                self.assertTrue((self.directory / "save-before.json").is_file())
                self.assertNotIn("saved", self.stage_indexes)
            return original(label, *args, **kwargs)

        self.owner.agent_input = delivery
        result = self.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.actions.count(["press-key", "--key", "Return"]), 1)
        receipt = json.loads((self.directory / "save-commit-input.json").read_text())
        self.assertEqual(receipt["evidence_kind"], "mechanical_commit_from_agent_observed_filename")
        self.assertFalse(receipt["machine_scene_predicate"] or receipt["human_confirmation"])
        self.assertIsNone(self.owner.decisions.filename_confirmation)
        with self.assertRaisesRegex(ControlRefused, "Repeated save commit"):
            self.owner.save_once(None, validate_delivery=lambda: None, deadline=580)

    def test_filename_commit_stale_final_ingress_reobserves_before_one_return(self):
        self.cached_save_owner()

        def poll():
            if (self.directory / "save-before.json").exists():
                self.before_poll = None
                self.clock[0] += driver.FRAME_SECONDS + 1

        self.before_poll = poll
        result = self.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertIn("saved", self.stage_indexes)
        self.assertEqual(self.actions.count(["press-key", "--key", "Return"]), 1)
        self.assertFalse(any("FilenameCommit" in str(argv) for argv in self.argv))
        receipt = json.loads((self.directory / "save-commit-input.json").read_text())
        self.assertEqual(receipt["evidence_kind"], "agent_input_decision")

    def test_filename_commit_unknown_input_never_retries_and_holds_restoration(self):
        self.cached_save_owner()
        original = self.owner.agent_input

        def delivery(label, *args, **kwargs):
            if label.endswith("FilenameCommit"):
                self.native_failure = TimeoutError("Synthetic unknown Return")
            return original(label, *args, **kwargs)

        self.owner.agent_input = delivery
        result = self.run_trial()
        self.assertIn("unknown Return", result["outcome"]["cause"])
        self.assertNotIn("saved", self.stage_indexes)
        self.assertTrue(self.owner.uncertain_input)
        self.assertFalse(result["restored"] or result["terminal_finished"])
        self.assertEqual(sum(a[2:5] == ["press-key", "--key", "Return"] for a in self.argv), 1)

    def test_filename_commit_cancellation_at_final_ingress_creates_no_return_child(self):
        self.cached_save_owner()

        def poll():
            if (self.directory / "save-before.json").exists():
                self.owner.control.cancelled = True

        self.before_poll = poll
        result = self.run_trial()
        self.assertEqual(result["outcome"]["cause"], "user_requested_game_handback")
        self.assertNotIn(["press-key", "--key", "Return"], self.actions)
        self.assertNotIn("saved", self.stage_indexes)
        self.assertTrue(result["restored"] and result["terminal_finished"])

    def test_filename_commit_rejects_changed_intent_and_receiver_before_return(self):
        self.owner.launch()
        for changed in ("mapping", "filename", "receiver", "frame"):
            with self.subTest(changed=changed):
                # Each context is produced by actual fresh AgentDecisions.
                self.owner.decisions = driver.AgentDecisions(self.owner)
                self.owner.save_commit_started = False
                self.stage_indexes = {}
                mapping = self.owner.decisions.stage("filename", "Observe exact filename")
                confirmation = self.owner.decisions.filename_confirmation
                intent = driver.SaveIntent(
                    str(self.keep.parent),
                    self.owner.job["agent_save"]["filename"],
                    driver.sha(driver.canonical(mapping)),
                )
                if changed == "mapping":
                    intent = driver.SaveIntent(intent.save_directory, intent.filename, "0" * 64)
                elif changed == "filename":
                    intent = driver.SaveIntent(
                        intent.save_directory,
                        "Other.SMRailroadsSavedGame",
                        intent.ui_mapping_observation_sha256,
                    )
                elif changed == "receiver":
                    confirmation["process"]["birth_us"] += 1
                else:
                    confirmation["frame"]["window"]["width"] += 1
                before = len(self.argv)
                with self.assertRaisesRegex(ControlRefused, "filename commit binding"):
                    self.owner.save_once(intent, validate_delivery=lambda: None, deadline=580)
                self.assertEqual(len(self.argv), before)
                # Immutable request files belong to this completed stage only.
                for path in self.directory.glob("Agent*-*.json"):
                    path.unlink()

    def test_filename_overwrite_never_arms_commit(self):
        self.owner.launch()
        self.reply_transform = lambda row: dict(row, overwrite_visible=True)
        with self.assertRaisesRegex(ControlRefused, "overwrite evidence refused"):
            self.owner.decisions.stage("filename", "Do not overwrite")
        self.assertFalse(hasattr(self.owner.decisions, "filename_confirmation"))
        self.assertEqual(self.actions, [])

    def test_save_window_history_is_bound_to_receiver_not_previous_launch(self):
        self.owner.job["purpose"] = driver.CACHED_SAVE_PURPOSE
        self.owner.menu_work_active = True
        self.owner.launch()
        self.owner.agent_frame("First", 120)
        first = self.owner.game
        self.window_responses = [[]]
        with self.assertRaisesRegex(ControlRefused, "Previously observed SAVE window unavailable"):
            self.owner.agent_frame("Lost", 120)
        self.owner.game = Process(first.pid + 1, first.birth_us + 1)
        self.window_responses = [[], [self.valid_window()]]
        self.owner.agent_frame("SecondStartup", 120)
        self.assertEqual(self.owner.last_agent_window_process, self.owner.game)
        self.assertEqual(self.actions, [])

    def test_save_startup_options_and_normal_quit_keep_existing_window_wait(self):
        self.owner.job["purpose"] = driver.CACHED_SAVE_PURPOSE
        self.owner.menu_work_active = True
        self.owner.launch()
        original = self.owner.retain_frame

        def startup(receipt, label):
            frame = original(receipt, label)
            frame["window"]["title"] = "Sid Meier's Railroads! Options"
            return frame

        self.owner.retain_frame = startup
        self.owner.agent_frame("Options", 120)
        self.owner.retain_frame = original
        self.window_responses = [[], [self.valid_window()]]
        self.owner.agent_frame("MainMenu", 120)
        self.assertFalse(self.owner.last_agent_window_startup)
        self.owner.menu_work_active = False
        self.window_responses = [[], [self.valid_window()]]
        self.owner.agent_frame("QuitConfirmation", 120)
        self.assertEqual(self.actions, [])

    def test_catalogue_save_unknown_first_normal_exit_never_relaunches(self):
        self.cached_save_owner()
        self.corrupt_exit = True
        result = self.run_trial()
        self.assertEqual(self.owner.launches, 1)
        self.assertTrue(result["errors"])
        self.assertNotIn("reopened", self.stage_indexes)
        self.assertFalse((self.directory / "save-after-reopen-identity.json").exists())

    def test_catalogue_reopened_partial_takes_final_normal_quit_without_cycle_credit(self):
        self.cached_save_owner()
        self.reply_transform = lambda value: (
            dict(value, choice="partial", action=[], observation="Reopened pause state unavailable")
            if self.stage_indexes.get("reopened", 0) >= 3 and "finished" not in self.stage_indexes
            else value
        )
        result = self.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(self.owner.launches, 2)
        self.assertEqual(self.actions.count(["hotkey", "--key", "CmdOrCtrl+Q"]), 2)
        self.assertEqual(result["outcome"]["partial_reason"], "Reopened pause state unavailable")
        self.assertFalse(result["outcome"]["save_reopen_credit"])
        self.assertFalse((self.directory / "save-after-reopen-identity.json").exists())
        self.assertEqual(self.keep.read_bytes(), b"preserved original synthetic save")

    def test_catalogue_reopened_partial_with_input_refuses_before_delivery(self):
        self.cached_save_owner()
        self.reply_transform = lambda value: (
            dict(value, choice="partial")
            if "reopened" in self.stage_indexes and "finished" not in self.stage_indexes
            else value
        )
        result = self.run_trial()
        self.assertTrue(result["errors"])
        self.assertEqual(self.actions.count(["hotkey", "--key", "CmdOrCtrl+Q"]), 1)
        self.assertNotIn("finished", self.stage_indexes)
        self.assertFalse((self.directory / "save-after-reopen-identity.json").exists())
        self.assertEqual(self.keep.read_bytes(), b"preserved original synthetic save")

    def test_catalogue_reopened_world_can_pause_with_current_frame_before_observation(self):
        self.cached_save_owner()
        self.reply_transform = lambda value: (
            dict(value, choice="action", action=["click", "--x", "30", "--y", "40"])
            if self.stage_indexes.get("reopened", 0) == 3
            else value
        )
        result = self.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertIn(["click", "--x", "30", "--y", "40"], self.actions)
        self.assertEqual(self.actions.count(["hotkey", "--key", "CmdOrCtrl+Q"]), 2)
        self.assertTrue((self.directory / "save-after-reopen-identity.json").exists())

    def test_catalogue_save_first_autosave_after_reopen_reaches_both_normal_exits(self):
        self.cached_save_owner()
        slot = self.owner.profiles.live / "Saves/Auto0.SMRailroadsSavedGame"
        created = [False]

        def reply(value):
            if self.stage_indexes.get("reopened", 0) >= 3 and not created[0]:
                slot.write_bytes(b"first scratch autosave after exact row reopened")
                created[0] = True
            return value

        self.reply_transform = reply
        result = self.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertTrue(created[0] and result["stopped"] and result["restored"])
        self.assertEqual(self.actions.count(["hotkey", "--key", "CmdOrCtrl+Q"]), 2)
        self.assertTrue((self.directory / "save-after-reopen-identity.json").is_file())
        self.assertEqual(self.keep.read_bytes(), b"preserved original synthetic save")
        self.assertFalse(result["outcome"]["save_reopen_credit"])

    def test_catalogue_save_reopens_when_new_scratch_slot_rotates_inside_owned_invocation(self):
        self.cached_save_owner()
        slot = self.owner.profiles.live / "Saves/Auto1.SMRailroadsSavedGame"
        rotated = [False]

        def reply(value):
            if "world" in self.stage_indexes and not slot.exists():
                slot.write_bytes(b"new scratch slot created inside this invocation")
            if "reopened" in self.stage_indexes and not rotated[0]:
                slot.write_bytes(b"new scratch slot rotated during reopening")
                rotated[0] = True
            return value

        self.reply_transform = reply
        result = self.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertTrue(rotated[0] and result["stopped"] and result["restored"])
        self.assertTrue((self.directory / "save-after-reopen-identity.json").is_file())
        self.assertEqual(self.keep.read_bytes(), b"preserved original synthetic save")
        self.assertFalse(result["outcome"]["save_reopen_credit"])

    def test_only_save_purpose_can_pair_observed_world_with_opening_save_as(self):
        self.owner.launch()
        action = ["hotkey", "--key", "CmdOrCtrl+S"]
        self.reply_transform = lambda value: dict(value, choice="observed", action=action)
        self.owner.job["purpose"] = driver.CACHED_LOAD_PURPOSE
        with self.assertRaisesRegex(ControlRefused, "Only observed track"):
            self.owner.decisions.stage("world", "Synthetic observed world")
        self.assertEqual(self.actions, [])
        sequence = self.owner.decisions.sequence
        self.owner.decisions = driver.AgentDecisions(self.owner)
        self.owner.decisions.sequence = sequence
        self.owner.job["purpose"] = driver.CACHED_SAVE_PURPOSE
        observed = self.owner.decisions.stage("world", "Synthetic observed world and Save As")
        self.assertEqual(self.actions, [action])
        self.assertFalse(observed["machine_scene_predicate"])
        self.assertEqual(list((self.owner.profiles.live / "Saves").iterdir()), [self.keep])

    def test_transport_stage_budget_ends_with_normal_quit_without_seventeenth_input(self):
        self.owner.job["purpose"] = driver.TRANSPORT_PURPOSE
        self.reply_transform = lambda value: (
            dict(value, choice="wait", action=[]) if "finished" not in self.stage_indexes else value
        )
        result = self.run_trial()
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(self.stage_indexes["world"], 16)
        self.assertEqual(self.actions, [["hotkey", "--key", "CmdOrCtrl+Q"]])
        self.assertIn("budget exhausted", result["outcome"]["partial_reason"])

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

    def test_direct_load_first_window_expiry_uses_owned_cleanup_without_actor_turn(self):
        import catalogue_menu

        parent = type(self.owner)
        self.owner.__class__ = catalogue_menu.pilot_class(self.common, parent)
        for name in ("prepare", "launch", "stop", "restore"):
            setattr(self.owner, name, MethodType(getattr(parent, name), self.owner))
        self.owner.job["purpose"] = driver.CATALOGUE_LOAD_PURPOSE
        self.owner.job.pop("agent_save")
        self.owner.config = dict(
            target=dict(profile_id=self.owner.diagnostic),
            scenario_title="Exact synthetic scenario",
            scenario_subtitle="Exact synthetic subtitle",
        )
        self.clock[0] = self.owner.launch_deadline - 0.6
        self.window_responses = [[]]
        result = self.run_trial()
        self.assertIn("Game window readiness deadline expired", result["outcome"]["cause"])
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertLess(self.clock[0], self.owner.launch_deadline)
        self.assertEqual(self.stage_indexes, {})
        self.assertEqual(self.actions, [])
        self.assertFalse(self.owner.uncertain_input)
        self.assertEqual(self.keep.read_bytes(), b"preserved original synthetic save")

    def test_cached_load_first_window_wait_cannot_reach_actor_work_cutoff(self):
        self.owner.launch()
        self.owner.job["purpose"] = driver.CACHED_LOAD_PURPOSE
        self.owner.job.pop("agent_save")
        self.clock[0] = self.owner.launch_deadline - 0.6
        self.window_responses = [[]]
        with self.assertRaisesRegex(ControlRefused, "Game window readiness deadline expired"):
            self.owner.decisions.stage("launcher", "Observe current first game window")
        self.assertLess(self.clock[0], self.owner.launch_deadline)
        self.assertEqual(self.stage_indexes, {})
        self.assertEqual(self.actions, [])

    def test_load_known_window_keeps_original_later_frame_and_normal_quit_path(self):
        self.owner.launch()
        self.owner.job["purpose"] = driver.CATALOGUE_LOAD_PURPOSE
        self.owner.job.pop("agent_save")
        self.clock[0] = self.owner.launch_deadline - 0.5
        self.window_responses = [[], [self.valid_window()]]
        self.owner.agent_frame("Initial", self.owner.anchor + 480)
        self.clock[0] = self.owner.launch_deadline + 1
        self.window_responses = [[], [self.valid_window()]]
        self.owner.agent_frame("LaterWorld", self.owner.anchor + 480)
        self.quit_confirmation = True
        self.owner.normal_quit("finished", None)
        self.assertFalse(self.owner.running)
        self.assertEqual(
            self.actions,
            [["hotkey", "--key", "CmdOrCtrl+Q"], ["press-key", "--key", "Return"]],
        )

    def test_expired_load_first_scene_never_calls_provider(self):
        self.owner.launch()
        self.owner.job["purpose"] = driver.CATALOGUE_LOAD_PURPOSE
        self.clock[0] = self.owner.launch_deadline
        with patch.object(self.owner, "native") as native:
            with self.assertRaisesRegex(ControlRefused, "Game window readiness deadline expired"):
                self.owner.agent_frame("Expired", self.owner.anchor + 480)
        native.assert_not_called()
        self.assertEqual(self.actions, [])

    def test_load_first_scene_never_extends_an_earlier_caller_deadline(self):
        self.owner.launch()
        self.owner.job["purpose"] = driver.CACHED_LOAD_PURPOSE
        caller_deadline = self.clock[0] + 0.1
        self.window_responses = [[]]
        with self.assertRaisesRegex(ControlRefused, "Game window readiness deadline expired"):
            self.owner.agent_frame("Earlier", caller_deadline)
        self.assertLess(self.clock[0], caller_deadline)
        self.assertEqual(self.actions, [])

    def test_nonload_first_window_keeps_existing_caller_deadline(self):
        self.owner.launch()
        self.owner.job["purpose"] = driver.WINDOW_PURPOSE
        self.clock[0] = self.owner.launch_deadline + 1
        self.window_responses = [[], [self.valid_window()]]
        self.owner.agent_frame("OriginalMenu", self.clock[0] + 2)
        self.assertGreater(self.clock[0], self.owner.launch_deadline)
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
        self.assertEqual(set(self.stage_indexes), set(driver.STAGES) - {"saved"})
        self.assertNotIn("saved", result["outcome"]["agent_observed_stages"])
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
        self.assertGreaterEqual(self.owner.frames, len(self.argv) + 2 * len(self.stage_indexes))

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

    def test_new_provider_contract_refuses_before_actual_native_spawn(self):
        self.owner.launch()
        self.owner.job["preinput_refusal_contract"] = dict(scope=driver.PREINPUT_229["scope"])
        with self.assertRaisesRegex(ControlRefused, "Exact prospective"):
            self.owner.native(
                "UnreviewedProvider", ["click", "--element-index", "18"], 7, 110, mutable=True
            )
        self.assertEqual(self.argv, [])
        self.assertFalse(self.owner.uncertain_input)

    def test_new_provider_completed_click_failure_does_not_reuse_old_wrapper_proof(self):
        self.owner.launch()
        self.owner.job["preinput_refusal_contract"] = dict(scope=driver.PREINPUT_229["scope"])
        self.native_failure = RuntimeError("completed click; effect unknown")
        with (
            patch.object(driver, "verify_preinput_229"),
            patch.object(driver, "native_source_digest", return_value=driver.NATIVE_SOURCE_SHA256),
            patch.object(driver, "completed_click_error", return_value=object()),
        ):
            with self.assertRaisesRegex(RuntimeError, "effect unknown"):
                self.owner.native(
                    "CurrentClick", ["click", "--element-index", "18"], 7, 110, mutable=True
                )
        self.assertTrue(self.owner.uncertain_input)
        self.assertEqual(len(self.argv), 1)
        self.assertIsNone(getattr(self.owner, "completed_input", None))
        self.assertFalse((self.directory / "CurrentClick-completed-error.json").exists())

    def test_cancellation_during_final_poll_prevents_native_spawn(self):
        self.before_poll = lambda: setattr(self.owner.control, "cancelled", True)
        result = self.run_trial()
        self.assertFalse(self.argv)
        self.assertEqual(result["outcome"]["cause"], "user_requested_game_handback")
        self.assertTrue(result["terminal_finished"])

    def test_ingress_prework_cannot_create_target_before_final_save_validation(self):
        def poll():
            if (self.directory / "save-before.json").exists():
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


class NativePreinput229Tests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.expected = dict(driver.PREINPUT_229)
        self.contract = dict(self.expected)
        self.job = dict(preinput_refusal_contract=self.contract)
        for path_key, hash_key in (
            ("native_helper_path", "native_helper_sha256"),
            ("native_source", "native_source_sha256"),
            ("proof_path", "proof_sha256"),
            ("release_match_path", "release_match_sha256"),
            ("request_loop_source_path", "request_loop_source_sha256"),
        ):
            path = self.directory / path_key
            path.write_bytes(("synthetic " + path_key).encode())
            digest = driver.sha(path.read_bytes())
            self.expected[hash_key] = self.contract[hash_key] = digest
            if path_key == "native_source":
                self.job[path_key] = str(path)
            else:
                self.contract[path_key] = str(path)
        self.expected["native_helper_path"] = self.contract["native_helper_path"]
        self.enterContext(patch.object(driver, "PREINPUT_229", self.expected))

    def test_actual_owner_selects_exact_new_contract_and_keeps_legacy_delegate(self):
        calls = []

        class Legacy:
            def verify_preinput_contract(self):
                calls.append("legacy")

        owner = object.__new__(driver.pilot_class(SimpleNamespace(UKCalibration=Legacy)))
        owner.job = self.job
        owner.verify_preinput_contract()
        self.assertEqual(
            driver.native_source_digest(self.job), self.expected["native_source_sha256"]
        )
        self.assertEqual(calls, [])
        owner.job = dict(preinput_refusal_contract=dict(scope="old frozen scope"))
        owner.verify_preinput_contract()
        self.assertEqual(calls, ["legacy"])
        self.assertEqual(driver.native_source_digest(owner.job), driver.NATIVE_SOURCE_SHA256)

    def test_each_runtime_input_and_evidence_file_remains_strict(self):
        driver.verify_preinput_229(self.job)
        for path in self.directory.iterdir():
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaisesRegex(ControlRefused, "build/proof changed"):
                    driver.verify_preinput_229(self.job)
                path.write_bytes(original)
        path = Path(self.contract["request_loop_source_path"])
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            driver.verify_preinput_229(self.job)

    def test_fields_and_types_cannot_replace_the_contract(self):
        for key, value in (
            ("scope", "different"),
            ("schema", True),
            ("binary_reproducible_build_proven", 0),
            ("native_helper_sha256", "a" * 64),
            ("native_source_blob", "b" * 40),
            ("extra", "not accepted"),
        ):
            with self.subTest(key=key):
                changed = {**self.contract, key: value}
                with self.assertRaisesRegex(ControlRefused, "Exact prospective"):
                    driver.verify_preinput_229({**self.job, "preinput_refusal_contract": changed})
        for key in self.contract:
            with self.subTest(missing=key):
                changed = dict(self.contract)
                del changed[key]
                with self.assertRaisesRegex(ControlRefused, "Exact prospective"):
                    driver.verify_preinput_229({**self.job, "preinput_refusal_contract": changed})


if __name__ == "__main__":
    unittest.main()
