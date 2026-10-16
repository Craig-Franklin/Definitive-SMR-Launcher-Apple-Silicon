"""Finite agent-operated Alternate UK experiment using the existing private owner.

This composition does not inherit the human-only owner or replay its job. Root
supplies a fresh reviewed job, exact admission and queue lease. The running parent
waits for bounded visual decisions; all native input runs in its bounded children.
Agent decisions and provider receipts never become human or detector evidence.
"""

from contextlib import contextmanager
from dataclasses import asdict
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from active_job_control import (
    ActiveJobControl,
    ControlRefused,
    UserCancelled,
    decode,
    read_plain,
    require,
    sha,
)
from finite_operation import run_calibration
from smr_launcher.scenario_evidence import canonical
from save_cycle import observe_save_cycle
from unique_save_file import SaveIntent, SaveScope, finite

PURPOSE = "ONE_AGENT_VISUAL_SAVE_REOPEN_PILOT_V1"
WINDOW_PURPOSE = "ONE_AGENT_WINDOW_MENU_PILOT_V1"
TRANSPORT_PURPOSE = "ONE_AGENT_UK_TRANSPORT_ACTION_V1"
CATALOGUE_MENU_PURPOSE = "ONE_AGENT_PACKAGED_CATALOGUE_MENU_V1"
CACHED_MENU_PURPOSE = "ONE_AGENT_PACKAGED_CACHED_MENU_V1"
CACHED_LOAD_PURPOSE = "ONE_AGENT_PACKAGED_CACHED_LOAD_V1"
CACHED_SAVE_PURPOSE = "ONE_AGENT_PACKAGED_CACHED_SAVE_REOPEN_V1"
TRANSPORT_WORK_SECONDS = 390
STAGES = ("world", "action", "filename", "saved", "quit", "reopened", "continued", "finished")
MENU_STAGES = ("launcher", "menu", "returned")
ACTION_SECONDS = 480
FRAME_SECONDS = 60
NATIVE_SOURCE_SHA256 = "1eabe4e19f52bc6684c0b58e6cbc2c92ccf8b8d95d160205e3164432e9951601"
LAUNCHER_CALIBRATION_SHA256 = "cfa6ec9095af74e7230e2f87a281988e958b1a97a4f6dc57750f2f3812df352c"
CLICK_SOURCE_SHA256 = "7beb128e95a8e4ed0f18e188a7a97231d6fd70a9e130ca1a4c6adff5eda395eb"


def admission_decisions(purpose):
    """Keep each finite operation bound to its own grant and review vocabulary."""
    names = {
        PURPOSE: "AGENT_SAVE_REOPEN_PILOT",
        WINDOW_PURPOSE: "AGENT_WINDOW_MENU_PILOT",
        TRANSPORT_PURPOSE: "AGENT_UK_TRANSPORT_ACTION",
        CATALOGUE_MENU_PURPOSE: "AGENT_PACKAGED_CATALOGUE_MENU",
        CACHED_MENU_PURPOSE: "AGENT_PACKAGED_CACHED_MENU",
        CACHED_LOAD_PURPOSE: "AGENT_PACKAGED_CACHED_LOAD",
        CACHED_SAVE_PURPOSE: "AGENT_PACKAGED_CACHED_SAVE_REOPEN",
    }
    require(purpose in names, "Fresh agent purpose required")
    return "ADMIT_ONE_" + names[purpose], "GO_ONE_" + names[purpose]


class StaleFrame(ControlRefused):
    """Final dispatch refused before native input child creation."""


class TransportIncomplete(ValueError):
    """End transport work and enter the existing normal-quit path."""


def transport_work_guard(owner):
    if (
        owner.job["purpose"] == TRANSPORT_PURPOSE
        and getattr(owner, "transport_work_active", False)
        and time.monotonic() >= owner.anchor + TRANSPORT_WORK_SECONDS
    ):
        raise TransportIncomplete("Transport work cutoff reached; quit margin reserved")


def completed_error(common, failure, argv, runtime, deadline):
    """Validate the actual typed, on-time response; child exit alone is insufficient."""
    if not isinstance(failure, common.ChildFailed):
        return None
    completion = failure.completion
    if not (
        type(completion) is common.ChildCompletion
        and completion.argv == tuple(argv)
        and completion.returncode == 1
        and completion.reaped is True
        and type(completion.pid) is int
        and completion.pid > 0
        and finite(completion.completed_at)
        and finite(completion.deadline)
        and completion.completed_at < min(completion.deadline, deadline)
        and time.monotonic() < deadline
        and type(completion.stdout) is bytes
        and 0 < len(completion.stdout) <= 65536
    ):
        return None
    try:
        receipt = decode(completion.stdout)
        if (
            set(receipt) == {"id", "ok", "error", "_meta"}
            and str(uuid.UUID(receipt["id"])) == receipt["id"]
            and receipt["ok"] is False
            and receipt["_meta"] == {"runtimeId": runtime}
            and type(receipt["error"]) is dict
        ):
            return receipt
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return None


def completed_quit_error(common, failure, argv, runtime, deadline):
    """A completed quit/confirmation error also requires exact normal owned exit."""
    receipt = completed_error(common, failure, argv, runtime, deadline)
    if receipt is not None and "--app" in argv:
        action = argv[2 : argv.index("--app")]
        if not (
            action in (["hotkey", "--key", "CmdOrCtrl+Q"], ["press-key", "--key", "Return"])
            or (len(action) == 5 and action[0:2] == ["click", "--x"] and action[3] == "--y")
        ):
            return None
        target = argv[argv.index("--app") + 1]
        if receipt["error"] == {"code": "app_not_found", "message": f"app '{target}' not found"}:
            return receipt
        error = receipt["error"]
        if (
            error.get("code") == "permission_denied"
            and str(error.get("message", "")).startswith(
                "app 'Sid Meier's Railroads!' has visible windows but no accessibility window ("
            )
            and "AX reads stayed blocked for 1500ms after retries" in error["message"]
        ):
            return receipt
    return None


def completed_click_error(common, failure, argv, runtime, deadline):
    """Known one-press failure follows buttonUp; effect stays unknown, no retry."""
    receipt = completed_error(common, failure, argv, runtime, deadline)
    if receipt is None or argv[1:3] != ["computer", "click"]:
        return None
    try:
        target = argv[argv.index("--app") + 1]
        window = argv[argv.index("--window-id") + 1]
    except (ValueError, IndexError):
        return None
    error = receipt["error"]
    if (
        set(error) == {"code", "message", "data"}
        and error["code"] == "window_not_focused"
        and error["message"]
        == (
            f"coordinate click aborted because target {target.replace('pid:', 'pid ')} window {window} "
            "is no longer the focused topmost recipient (current: no focused window); "
            "1 press(es) may already have been delivered; run get-app-state and verify state before retrying"
        )
        and error["data"]
        == {
            "nextSteps": [
                "Run `orca computer get-app-state --app <app> --json` and verify whether the intended action already occurred before retrying.",
                "Do not retry the click if it already took effect; otherwise bring the target window forward and use fresh state before trying again.",
            ]
        }
    ):
        return receipt
    return None


class AgentJobControl(ActiveJobControl):
    """Keep root cancellation and Run identity; bind the actual delegated owner."""

    def validate_owner(self, state):
        expected = self.job["agent_owner_state"]
        require(
            expected["run"] == self.job["root"]["run"]
            and expected["consumer_generation"] == self.job["root"]["consumer_generation"]
            and expected["session"] == self.job["root"]["session"]
            and expected["coordinator"] == self.job["root"]["coordinator"]
            and expected["exclusive_runtime_owner"]
            == self.job["actor_binding"]["driver_actor"]["terminal"]
            and expected["admitted_job"] == self.job["job"]
            and expected["runtime_admitted"] is True
            and expected["runtime_automation_allowed"] is True
            and expected["closed_circuits_unchanged"] is True,
            "Exact delegated owner state required",
        )
        owner = state.get("effective_continuation")
        require(type(owner) is dict and owner == expected, "Delegated admission changed")
        active = state.get("active_job_control")
        require(
            type(active) is dict
            and active.get("binding") == self.binding
            and active.get("phase") == "active"
            and type(active.get("cancelled")) is bool
            and (not self.cancelled or active["cancelled"])
            and type(active.get("cursor")) is int
            and active["cursor"] >= self.last_cursor
            and type(active.get("prefix_sha256")) is str,
            "Active delegated job changed",
        )
        return active


def validate_action(stage, action, frame, token, receiver, *, purpose=None):
    """Closed native input grammar; target selectors and focus recovery refuse."""
    require(type(action) is list and all(type(x) is str for x in action), "Action grammar")
    require(stage in STAGES + MENU_STAGES and action, "Unknown action stage")
    if stage in ("quit", "finished"):
        require(
            action == ["hotkey", "--key", "CmdOrCtrl+Q"]
            or action == ["press-key", "--key", "Return"]
            or action[0] == "click",
            "Only normal Quit or observed confirmation admitted",
        )
    command = action[0]
    if stage == "launcher":
        require(action[:2] == ["click", "--element-index"], "Launcher requires fresh semantic Play")
    if action[:2] == ["click", "--element-index"]:
        require(
            stage in ("launcher", "world", "reopened")
            and len(action) == 3
            and type(frame.get("play_index")) is int
            and action[2] == str(frame["play_index"]),
            "Only the fresh unique AX Play index is admitted",
        )
        return action
    if stage in ("menu", "returned"):
        require(
            command == "click" or action == ["press-key", "--key", "Escape"],
            "Menu pilot accepts only click or Escape",
        )
    if command == "type-text":
        require(
            stage == "filename"
            and action == [command, "--text", token]
            and receiver == "filename_field",
            "Exact token and agent-observed filename receiver required",
        )
    elif command in ("hotkey", "press-key"):
        allowed = {
            "filename": {"CmdOrCtrl+A", "CmdOrCtrl+S"},
            "saved": {"Return"},
            "quit": {"CmdOrCtrl+Q", "Return"},
            "finished": {"CmdOrCtrl+Q", "Return"},
            "world": {"Return", "Escape"},
            "reopened": {"Return", "Escape"},
            "menu": {"Escape"},
            "returned": {"Escape"},
        }
        if (
            purpose == TRANSPORT_PURPOSE
            and stage in ("action", "continued")
            and command == "press-key"
        ):
            allowed[stage] = {"B"}
        require(
            len(action) == 3 and action[1] == "--key" and action[2] in allowed.get(stage, set()),
            "Unnamed keyboard action refused",
        )
        if stage == "filename" and action[2] == "CmdOrCtrl+A":
            require(receiver == "filename_field", "Agent-observed filename receiver required")
    elif command in ("click", "drag", "scroll"):
        geometry = action
        if command == "scroll":
            require(
                purpose == TRANSPORT_PURPOSE
                and stage == "action"
                and receiver == "owned_window"
                and action[-4:] == ["--direction", "down", "--pages", "0.1"],
                "Only fixed transport action-stage zoom-out is admitted",
            )
            geometry = action[:-4]
        if command == "click" and len(action) == 7:
            require(
                (stage == "reopened" and action[-2:] == ["--click-count", "2"])
                or (
                    stage in ("action", "continued") and action[-2:] == ["--mouse-button", "right"]
                ),
                "Extended click stage refused",
            )
            geometry = action[:-2]
        flags = (
            ["--from-x", "--from-y", "--to-x", "--to-y"] if command == "drag" else ["--x", "--y"]
        )
        require(command != "drag" or stage in ("action", "continued"), "Drag stage refused")
        require(
            len(geometry) == 1 + 2 * len(flags) and geometry[1::2] == flags, "Coordinate grammar"
        )
        for index, value in enumerate(geometry[2::2]):
            require(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value), "Finite coordinate required")
            axis = "width" if index % 2 == 0 else "height"
            require(0 <= float(value) < frame["window"][axis], "Point outside observed window")
    else:
        raise ValueError("Unsupported agent native action")
    return action


class AgentDecisions:
    """One immutable request/reply per fresh provider frame, with fixed clocks."""

    def __init__(self, owner):
        self.owner = owner
        self.sequence = 0
        self.used_stages = set()

    def stage(self, stage, instruction, *, validate=None, return_after_input=False):
        owner = self.owner
        require(
            stage in STAGES + MENU_STAGES and stage not in self.used_stages,
            "Unknown or repeated agent stage",
        )
        self.used_stages.add(stage)
        # Navigation can include startup animations and several observed menus.
        # All stages remain inside the original operation anchor; no renewal.
        deadline = owner.anchor + ACTION_SECONDS
        menu_inputs = 0
        quit_inputs = 0
        play_sent = False
        previous_play = None
        quitting = stage in ("quit", "finished")
        for _ in range(16):
            owner.guard_running(allow_exit=quitting and quit_inputs > 0)
            transport_work_guard(owner)
            if quitting and quit_inputs and owner.monitor.inspect(owner.game.pid) is None:
                return {
                    "stage": stage,
                    "normal_quit_inputs": quit_inputs,
                    "owned_process_absent": True,
                }
            if validate:
                validate()
            require(time.monotonic() < deadline, "Fixed agent stage deadline expired")
            self.sequence += 1
            label = f"Agent{self.sequence:03d}"
            receipt, frame, window = owner.agent_frame(label, deadline)
            transport_work_guard(owner)
            captured = owner.frame_started
            if stage in ("launcher", "world", "reopened") and frame["window"].get("title") == (
                "Sid Meier's Railroads! Options"
            ):
                # Semantic startup only. A submitted Play does not prove menu
                # arrival; leave rendered scene recognition to the next agent frame.
                current_play = (window, frame.get("play_index"))
                if (
                    frame.get("play_index") is not None
                    and current_play == previous_play
                    and not play_sent
                ):
                    action = validate_action(
                        stage,
                        ["click", "--element-index", str(frame["play_index"])],
                        frame,
                        owner.job["agent_save"]["token"],
                        "owned_window",
                    )
                    try:
                        delivered = owner.agent_input(
                            label, action, window, frame, captured, deadline, validate
                        )
                    except StaleFrame:
                        owner.write(
                            label + "-stale-decision.json", {"no_input": True, "reobserve": True}
                        )
                        continue
                    play_sent = True
                    if stage == "launcher":
                        menu_inputs += 1
                    owner.write(
                        label + "-semantic-play.json",
                        {
                            "evidence_kind": "fresh_unique_ax_play_submission",
                            "snapshot": frame["snapshot"],
                            "provider_receipt_sha256": sha(canonical(delivered)),
                            "menu_arrival_proven": False,
                        },
                    )
                else:
                    previous_play = current_play
                    time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                continue
            reply_deadline = min(deadline, time.monotonic() + 120)
            request = {
                "evidence_kind": "agent_visual_decision_request",
                "stage": stage,
                "instruction": instruction,
                "job": owner.job["job"],
                "process": asdict(owner.game),
                "snapshot": frame["snapshot"],
                "image_sha256": frame["sha256"],
                "frame": frame,
                "provider_receipt_sha256": sha(canonical(receipt)),
                "reply_deadline_monotonic": reply_deadline,
                "frame_expires_monotonic": min(deadline, captured + FRAME_SECONDS),
                "human_confirmation": False,
                "machine_scene_predicate": False,
            }
            owner.write(label + "-request.json", request)
            request_sha = sha(read_plain(owner.directory / (label + "-request.json")))
            reply_path = owner.directory / (label + "-decision.json")
            while not reply_path.exists():
                owner.guard_running()
                transport_work_guard(owner)
                require(
                    time.monotonic() < reply_deadline,
                    "Agent decision deadline expired",
                )
                time.sleep(0.1)
            transport_work_guard(owner)
            require(time.monotonic() < reply_deadline, "Agent decision deadline expired")
            decision = decode(read_plain(reply_path, maximum=65536))
            require(
                set(decision)
                == {
                    "request_sha256",
                    "evidence_kind",
                    "choice",
                    "action",
                    "receiver",
                    "observation",
                    "exact_token",
                    "overwrite_visible",
                }
                and decision["request_sha256"] == request_sha
                and decision["evidence_kind"] == "agent_visual_decision"
                and decision["choice"] in ("action", "observed", "wait", "stop", "partial")
                and type(decision["action"]) is list
                and type(decision["observation"]) is str
                and 0 < len(decision["observation"]) <= 2048
                and decision["overwrite_visible"] is False,
                "Exact agent reply required; human or overwrite evidence refused",
            )
            require(time.monotonic() < reply_deadline, "Agent decision deadline expired")
            if decision["choice"] == "stop":
                require(decision["action"] == [], "Stop cannot carry input")
                raise ValueError("Agent stopped uncertain scene")
            owner.guard_running()
            transport_work_guard(owner)
            require(time.monotonic() < reply_deadline, "Agent decision deadline expired")
            require(time.monotonic() < deadline, "Fixed agent stage deadline expired")
            if decision["choice"] == "partial":
                if (owner.job["purpose"] == CACHED_LOAD_PURPOSE and stage == "world") or (
                    owner.job["purpose"] == CACHED_SAVE_PURPOSE
                    and stage in ("world", "filename", "reopened")
                ):
                    require(decision["action"] == [], "Partial load cannot carry input")
                    from catalogue_menu import MenuIncomplete

                    raise MenuIncomplete(decision["observation"])
                require(
                    owner.job["purpose"] == TRANSPORT_PURPOSE
                    and stage in ("world", "action", "continued")
                    and decision["action"] == [],
                    "Partial finish is transport-only and cannot carry input",
                )
                raise TransportIncomplete(decision["observation"])
            if decision["choice"] == "wait":
                require(decision["action"] == [], "Wait cannot carry input")
                owner.write(
                    label + "-wait.json", {"no_input": True, "observation": decision["observation"]}
                )
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
                continue
            if time.monotonic() >= captured + FRAME_SECONDS:
                owner.write(label + "-stale-decision.json", {"no_input": True, "reobserve": True})
                continue
            if decision["choice"] == "observed":
                require(not quitting, "Normal Quit requires actual process exit")
                require(
                    stage not in MENU_STAGES or menu_inputs == 1,
                    "Menu effect requires one preceding input",
                )
                require(not return_after_input, "One save input required")
                if decision["action"]:
                    require(
                        (
                            (owner.job["purpose"] == PURPOSE and stage == "action")
                            or (owner.job["purpose"] == CACHED_SAVE_PURPOSE and stage == "world")
                        )
                        and decision["action"] == ["hotkey", "--key", "CmdOrCtrl+S"]
                        and decision["receiver"] == "owned_window",
                        "Only observed track may pair with opening Save As",
                    )
                    # The same fresh frame supports the track/world observation
                    # and opening Save As. Filename recognition and actual saving
                    # still happen later under their original guards.
                    action = validate_action(
                        "filename",
                        decision["action"],
                        frame,
                        owner.job["agent_save"]["token"],
                        decision["receiver"],
                    )
                    try:
                        owner.agent_input(
                            label, action, window, frame, captured, deadline, validate
                        )
                    except StaleFrame:
                        owner.write(
                            label + "-stale-decision.json", {"no_input": True, "reobserve": True}
                        )
                        continue
                if stage in ("filename", "reopened"):
                    require(
                        decision["exact_token"] == owner.job["agent_save"]["token"],
                        "Exact visual save token required",
                    )
                if stage == "filename":
                    require(
                        decision["receiver"] == "filename_field",
                        "Agent-observed filename field required",
                    )
                return {
                    **decision,
                    "stage": stage,
                    "snapshot": frame["snapshot"],
                    "image_sha256": frame["sha256"],
                    "machine_scene_predicate": False,
                    "human_confirmation": False,
                }
            require(decision["choice"] == "action", "Unknown agent decision")
            if stage in MENU_STAGES:
                require(menu_inputs == 0, "One menu input per stage maximum")
                menu_inputs += 1
            action = validate_action(
                stage,
                decision["action"],
                frame,
                owner.job["agent_save"]["token"],
                decision["receiver"],
                purpose=owner.job["purpose"],
            )
            if quitting:
                require(
                    action == ["hotkey", "--key", "CmdOrCtrl+Q"]
                    if quit_inputs == 0
                    else action[0] == "click" or action == ["press-key", "--key", "Return"],
                    "Quit starts once with CmdQ; later input requires observed confirmation",
                )
            # Native keyboard/click focus checks remain enabled. The visual field
            # observation is agent evidence, never an AX text-receiver predicate.
            try:
                delivered = owner.agent_input(
                    label, action, window, frame, captured, deadline, validate, allow_exit=quitting
                )
            except StaleFrame:
                owner.write(label + "-stale-decision.json", {"no_input": True, "reobserve": True})
                if stage in MENU_STAGES:
                    menu_inputs -= 1
                continue
            if quitting:
                quit_inputs += 1
            if return_after_input:
                return {
                    "evidence_kind": "agent_input_decision",
                    "decision": decision,
                    "provider_receipt_sha256": sha(canonical(delivered)),
                    "machine_scene_predicate": False,
                    "human_confirmation": False,
                }
        if owner.job["purpose"] == TRANSPORT_PURPOSE and getattr(
            owner, "transport_work_active", False
        ):
            raise TransportIncomplete("Finite transport stage action budget exhausted")
        if (owner.job["purpose"] == CACHED_LOAD_PURPOSE and stage == "world") or (
            owner.job["purpose"] == CACHED_SAVE_PURPOSE and stage in ("world", "filename")
        ):
            from catalogue_menu import MenuIncomplete

            raise MenuIncomplete("Finite scenario-load decision budget exhausted")
        raise ValueError("Finite agent stage action budget exhausted")


def pilot_class(common):
    """Use the unchanged common lifecycle without inheriting the human-only class."""

    class AgentPilot(common.UKCalibration):
        def __init__(self, job, root, admission, lease_guard):
            super().__init__(job, root, journal_name="Agent visual new-save exact-reopen pilot")
            self.control = AgentJobControl(job)
            self.admission, self.lease_guard = admission, lease_guard
            self.operation_directory = self.directory
            self.launches = 0
            self.first_stop_verified = False
            self.delivery_validation = None
            self.completed_input = None
            self.transport_zoom_attempts = 0
            self.decisions = AgentDecisions(self)

        def write(self, name, value):
            return super().write(name, decode(canonical(value)))

        def admitted(self):
            require(
                finite(self.admission["expires_epoch"])
                and self.admission["issued_epoch"] <= time.time() < self.admission["expires_epoch"],
                "Exact admission expired",
            )
            self.lease_guard()

        def verify_cleanup_owner(self):
            self.lease_guard()
            self.control.verify_owner()

        def finish(self, outcome, stopped, restored):
            self.lease_guard()
            return super().finish(outcome, stopped, restored)

        def require_settled_input(self):
            if not self.uncertain_input:
                return
            settled = getattr(self, "completed_input", None)
            require(settled is not None, "Uncompleted provider work; restoration refused")
            self.require_stopped()
            self.verify_code()
            self.verify_provider()
            require(self.monitor.inspect(settled["pid"]) is None, "Input child still present")
            require(
                sha(read_plain(self.directory / settled["record"])) == settled["sha256"],
                "Completed provider evidence changed",
            )

        @contextmanager
        def mutation_guard(self):
            with self.control.mutation_guard():
                self.admitted()
                require(
                    time.monotonic() < self.anchor + ACTION_SECONDS,
                    "Fixed agent operation deadline expired",
                )
                self.inside_mutation = True
                try:
                    yield
                finally:
                    self.inside_mutation = False

        def verify_available(self):
            if getattr(self, "inside_mutation", False):
                # The existing STATE fence is held. Public poll would acquire
                # it again; final ingress is drained explicitly by spawn_input.
                if self.control.cancelled:
                    raise UserCancelled("Cancelled under agent mutation fence")
            else:
                return super().verify_available()

        def spawn_input(self, argv, *, deadline, before_spawn=None, **kwargs):
            def final_guard():
                self.admitted()
                self.control._poll_locked()
                if self.control.cancelled:
                    raise UserCancelled("Cancelled before agent input")
                # All blocking admission/ingress work precedes the final file
                # validator. The cooperating STATE writers remain fenced.
                if self.delivery_validation:
                    self.delivery_validation()
                if self.control.cancelled:
                    raise UserCancelled("Cancelled during agent delivery validation")
                require(
                    time.monotonic() < min(deadline, self.anchor + ACTION_SECONDS),
                    "Agent dispatch deadline expired",
                )
                transport_work_guard(self)
                if before_spawn:
                    before_spawn()

            with self.mutation_guard():
                final_guard()
                return subprocess.Popen(argv, **kwargs)

        @contextmanager
        def launch_files(self, number):
            previous = self.directory
            self.directory = self.operation_directory / f"launch-{number}"
            self.directory.mkdir(mode=0o700, exist_ok=number == self.launches)
            try:
                yield
            finally:
                self.directory = previous

        def tick(self):
            self.verify_available()
            self.admitted()
            require(
                time.monotonic() < self.anchor + ACTION_SECONDS,
                "Fixed agent action deadline expired",
            )
            if self.observer is not None:
                require(self.observer.poll() is None, "Independent observer exited")
            self.sample()

        def next_launch(self):
            number = self.launches + 1
            require(number in (1, 2), "Two launches maximum")
            if number == 2:
                require(self.first_stop_verified, "Exact first stop required")
                self.require_stopped()
                self.game = self.observer = None
                self.open_attempted = False
                self.seq = 0
                self.next_sample = time.monotonic()
            return number

        def launch(self):
            number = self.next_launch()
            with self.launch_files(number):
                self.helper_packet = self.directory / "observer"
                self.helper_packet.mkdir(mode=0o700)
                self.launches = number
                super().launch(
                    navigation_deadline=self.anchor + (120 if number == 1 else ACTION_SECONDS),
                    record_selection=number == 1,
                )

        def guard_running(self, *, allow_exit=False):
            self.verify_available()
            self.admitted()
            self.verify_code()
            require(
                time.monotonic() < self.anchor + ACTION_SECONDS,
                "Fixed agent operation deadline expired",
            )
            require(
                self.profiles._state()["active"] == self.diagnostic
                and not self.profiles.journal.exists(),
                "Diagnostic profile drift",
            )
            games = self.monitor.games()
            require(len(games) <= 1, "Ambiguous game process")
            if allow_exit and not games:
                require(self.monitor.inspect(self.game.pid) is None, "Unresolved quit identity")
                return
            require(len(games) == 1 and self.game.same(games[0]), "Owned game identity drift")
            require(self.observer.poll() is None, "Independent observer exited")
            self.sample()

        def native(
            self, label, action, window, deadline, *, mutable=False, validate=None, allow_exit=False
        ):
            self.guard_running()
            self.verify_provider()
            if mutable:
                self.verify_preinput_contract()
                require(
                    sha(read_plain(self.job["native_source"])) == NATIVE_SOURCE_SHA256,
                    "Native source contract drift",
                )
            require(self.frames < 192, "Finite provider call budget exhausted")
            self.frames += 1
            argv = [
                self.job["orca"],
                "computer",
                *action,
                "--app",
                f"pid:{self.game.pid}",
                "--json",
            ]
            if action[0] != "list-windows":
                argv += ["--session", self.job["root"]["session"]]
            if window is not None:
                argv += ["--window-id", str(window)]
            if mutable:
                # A fresh explicit read follows each action. Do not block the
                # input response on a screenshot during a map-loading transition.
                argv += ["--no-screenshot"]
            limit = min(
                deadline, self.anchor + ACTION_SECONDS, time.monotonic() + (30 if mutable else 10)
            )
            self.write(
                label + "-attempt.json",
                {
                    "mutable": mutable,
                    "anchor": self.anchor,
                    "elapsed": time.monotonic() - self.anchor,
                },
            )
            require(time.monotonic() < limit, "Original native deadline expired")
            attempted = False

            def mark():
                nonlocal attempted
                if action[0] == "scroll":
                    if self.transport_zoom_attempts >= 2:
                        raise TransportIncomplete(
                            "Two zoom attempts exhausted; quit without search"
                        )
                    self.transport_zoom_attempts += 1
                attempted = True

            def poll_child():
                try:
                    self.guard_running(allow_exit=allow_exit)
                except RuntimeError as failure:
                    # libproc can temporarily lose identity while an owned game
                    # exits. During an already-dispatched normal quit, keep
                    # waiting for this same bounded child. This grants neither
                    # another input nor absence; exact normal exit is still
                    # required before settlement or relaunch.
                    if not (
                        allow_exit
                        and type(failure).__name__ == "SafetyError"
                        and str(failure)
                        in (
                            "Cannot identify a live process",
                            "Cannot read process birth identity",
                            "Cannot read process physical footprint",
                        )
                    ):
                        raise

            self.delivery_validation = validate
            try:
                data = common.bounded_child(
                    argv,
                    self.directory,
                    label,
                    poll_child,
                    limit - time.monotonic(),
                    mutable,
                    spawn=(
                        lambda argv, **kwargs: self.spawn_input(
                            argv, deadline=limit, before_spawn=mark, **kwargs
                        )
                    )
                    if mutable
                    else None,
                )
                receipt = decode(data)
                require(
                    receipt.get("ok") is True
                    and receipt.get("_meta", {}).get("runtimeId")
                    == self.job["actor_binding"]["provider_runtime_id"],
                    "Native operation unsettled",
                )
                snapshot = receipt.get("result", {}).get("snapshot", {})
                require(not mutable or bool(snapshot), "Mutable native receiver receipt absent")
                if snapshot:
                    metadata = receipt.get("result", {}).get("action", {})
                    transition = (
                        mutable
                        and action[:2] == ["click", "--element-index"]
                        and metadata.get("path") == "accessibility"
                        and metadata.get("actionName") == "AXPress"
                        and metadata.get("fallbackReason") is None
                        and type(snapshot.get("window", {}).get("id")) is int
                        and snapshot["window"]["id"] > 0
                    )
                    require(
                        snapshot.get("app", {}).get("pid") == self.game.pid
                        and (
                            window is None
                            or snapshot.get("window", {}).get("id") == window
                            or transition
                        ),
                        "Native receiver drift",
                    )
                self.verify_provider()
                require(time.monotonic() < limit, "Native receipt validation deadline expired")
                return receipt
            except BaseException as failure:
                if attempted and allow_exit:
                    settled = completed_quit_error(
                        common,
                        failure,
                        argv,
                        self.job["actor_binding"]["provider_runtime_id"],
                        limit,
                    )
                    if settled is not None:
                        try:
                            self.normal_exit_receipt()
                            if settled["error"]["code"] == "permission_denied":
                                self.verify_exit_permissions()
                            self.verify_available()
                            self.admitted()
                            self.verify_provider()
                            self.verify_preinput_contract()
                            require(time.monotonic() < limit, "Normal exit settlement expired")
                            self.write(
                                label + "-completed-quit-error.json",
                                {
                                    "evidence_kind": "completed_native_error_and_exact_normal_exit",
                                    "operation_failed": True,
                                    "input_effect_proven": False,
                                    "receipt_sha256": sha(failure.completion.stdout),
                                    "no_retry": True,
                                },
                            )
                            require(
                                time.monotonic() < limit, "Normal exit receipt persistence expired"
                            )
                            return settled
                        except BaseException:
                            self.uncertain_input = True
                            raise
                if attempted:
                    self.uncertain_input = True
                    completed = completed_click_error(
                        common,
                        failure,
                        argv,
                        self.job["actor_binding"]["provider_runtime_id"],
                        limit,
                    )
                    if completed is not None:
                        require(
                            sha(read_plain(self.job["native_click_source"])) == CLICK_SOURCE_SHA256,
                            "Single-click emission source changed",
                        )
                        value = failure.completion
                        name = label + "-completed-error.json"
                        self.write(
                            name,
                            {
                                "argv": list(value.argv),
                                "pid": value.pid,
                                "returncode": value.returncode,
                                "completed_at": value.completed_at,
                                "deadline": value.deadline,
                                "reaped": value.reaped,
                                "response_sha256": sha(value.stdout),
                                "provider_emission_completed": True,
                                "effect": "unknown",
                                "no_retry": True,
                            },
                        )
                        self.completed_input = {
                            "pid": value.pid,
                            "record": name,
                            "sha256": sha(read_plain(self.directory / name)),
                        }
                raise
            finally:
                self.delivery_validation = None

        def agent_frame(self, label, deadline):
            attempt = 0
            while True:
                require(time.monotonic() < deadline, "Game window readiness deadline expired")
                attempt += 1
                result = self.native(
                    label + f"Windows{attempt:02d}", ["list-windows"], None, deadline
                )["result"]
                require(
                    type(result.get("app", {}).get("pid")) is int
                    and result["app"]["pid"] == self.game.pid,
                    "Window enumeration app drift",
                )
                windows = result.get("windows")
                require(
                    type(windows) is list and all(type(w) is dict for w in windows),
                    "Window enumeration shape",
                )
                if windows:
                    break
                self.guard_running()
                require(
                    time.monotonic() + 0.25 < deadline, "Game window readiness deadline expired"
                )
                time.sleep(0.25)
            visible = windows
            require(
                len(visible) == 1
                and type(visible[0].get("id")) is int
                and visible[0]["id"] > 0
                and type(visible[0].get("app", {}).get("pid")) is int
                and visible[0]["app"]["pid"] == self.game.pid
                and visible[0].get("isOffscreen") is False
                and visible[0].get("isMinimized") is False,
                "One exact game window required",
            )
            window = visible[0]["id"]
            self.frame_started = time.monotonic()
            receipt = self.native(label + "Read", ["get-app-state"], window, deadline)
            frame = self.retain_frame(receipt, label)
            frame["play_index"] = common.fresh_play_index(
                receipt, self.game.pid, allow_missing=True
            )
            self.scene_frame(receipt, frame, window)
            return receipt, frame, window

        def agent_input(
            self, label, action, window, frame, captured, deadline, validate, *, allow_exit=False
        ):
            def delivery():
                actual = self.monitor.inspect(self.game.pid)
                require(
                    actual is not None and self.game.same(actual), "Agent receiver identity changed"
                )
                if validate:
                    validate()
                transport_work_guard(self)
                require(time.monotonic() < deadline, "Fixed agent operation deadline expired")
                if not 0 <= time.monotonic() - captured < FRAME_SECONDS:
                    raise StaleFrame("Agent frame expired before delivery")

            return self.native(
                label + "Input",
                action,
                window,
                deadline,
                mutable=True,
                validate=delivery,
                allow_exit=allow_exit or action == ["hotkey", "--key", "CmdOrCtrl+Q"],
            )

        def guard(self, phase, scope, binding):
            if phase == "relaunch" and (self.launches == 1 or self.game is None):
                self.verify_available()
                self.admitted()
                self.verify_code()
                self.require_stopped()
            else:
                self.guard_running()

        def save_once(self, intent, *, validate_delivery, deadline):
            require(deadline == self.anchor + ACTION_SECONDS, "Original cycle deadline required")
            result = self.decisions.stage(
                "saved",
                "Confirm the exact new token once using Save or Return; refuse any overwrite prompt.",
                validate=validate_delivery,
                return_after_input=True,
            )
            until = min(time.monotonic() + 10, deadline)
            while not (Path(intent.save_directory) / intent.filename).exists():
                self.guard_running()
                require(time.monotonic() < until, "New save did not appear within fixed wait")
                time.sleep(0.1)
            self.wait_stability(time.monotonic() + 1, deadline)
            return result

        def owned_reopen_location(self, bound):
            return self.scope, None

        def reopen_once(self, checked, *, validate_delivery, deadline):
            require(deadline == self.anchor + ACTION_SECONDS, "Original cycle deadline required")
            observation = (
                "; observe the matching saved world and HUD. If running, pause once using the current visible control, then observe the paused banner. Do not assume the pause state persisted. If this cannot be established promptly, return partial with no input; normal quit follows. No construction, purchases, transport or saving."
                if self.job["purpose"] == CACHED_SAVE_PURPOSE
                else "; observe the saved world and earlier track action."
            )
            return self.decisions.stage(
                "reopened",
                "Load exactly " + self.job["agent_save"]["token"] + observation,
                validate=validate_delivery,
            )

        def observe_reopened_world_and_continued_action(self, *, validate_delivery, deadline):
            require(deadline == self.anchor + ACTION_SECONDS, "Original cycle deadline required")
            continued = self.decisions.stage(
                "continued",
                "Build a second short stretch of track and observe its result.",
                validate=validate_delivery,
            )
            self.normal_quit("finished", validate_delivery)
            return continued

        def wait_stability(self, until, deadline):
            require(
                deadline == self.anchor + ACTION_SECONDS and until < deadline,
                "Fixed stability deadline required",
            )
            while time.monotonic() < until:
                self.guard_running()
                time.sleep(0.1)
            self.guard_running()

        def stop_for_relaunch(self):
            require(self.launches == 1 and not self.first_stop_verified, "Repeated first stop")
            self.normal_quit("quit", None)
            with self.launch_files(1):
                stopped = super().stop_owned()
            self.first_stop_verified = stopped is True
            return stopped

        def relaunch_once(self, *, validate_delivery, deadline):
            require(deadline == self.anchor + ACTION_SECONDS, "Original cycle deadline required")
            self.delivery_validation = validate_delivery
            try:
                validate_delivery()
                self.launch()
                self.guard_running()
                validate_delivery()
            finally:
                self.delivery_validation = None

        def normal_quit(self, stage, validate):
            self.decisions.stage(
                stage,
                "Request Quit once with CmdQ. If still open, observe and confirm the actual quit dialog without saving again. Do not repeat CmdQ or claim exit while a game process remains.",
                validate=validate,
            )
            self.normal_exit_receipt()

        def normal_exit_receipt(self):
            self.require_stopped()
            observer_code = self.observer.wait(timeout=5)
            require(observer_code in (0, 2), "Normal exit observer unavailable")
            receipt = decode(read_plain(self.helper_packet / "result.json"))
            require(
                receipt["job"] == self.job["job"]
                and receipt["nonce"] == self.nonce
                and receipt["anchor"] == self.anchor
                and receipt["code"] == self.code
                and receipt["process"]
                == {
                    "pid": self.game.pid,
                    "birth": str(self.game.birth_us),
                    "executable": self.game.executable,
                }
                and receipt["result"]["state"] == "observed_exited"
                and receipt["result"]["terminal"] is True
                and receipt["result"]["stop_requested"] is False,
                "Exact normal exit receipt required",
            )
            if observer_code == 2:
                # The existing observer deliberately injects NaN when its final
                # identity check sees the just-exited game. Keep that diagnostic;
                # exact absence and no observer stop remain separate predicates.
                state = decode(read_plain(self.helper_packet / "state.json"))
                require(
                    all(state.get(k) == receipt[k] for k in ("job", "nonce", "code", "anchor"))
                    and state.get("state", {}).get("terminal") is True
                    and state["state"].get("reason") == "bound_identity_unknown_or_mismatch"
                    and receipt["result"].get("reason") == "invalid_clock",
                    "Unknown observer failure during normal quit",
                )

        def verify_exit_permissions(self):
            # This known post-action AX failure can occur while the owned game
            # exits. A genuine permission loss must still refuse settlement.
            raw = subprocess.check_output(
                [self.job["orca"], "computer", "permissions", "--json"], timeout=3
            )
            require(len(raw) <= 65536, "Permission status response bound")
            receipt = decode(raw)
            require(
                receipt.get("ok") is True
                and receipt.get("_meta", {}).get("runtimeId")
                == self.job["actor_binding"]["provider_runtime_id"]
                and {x["id"]: x["status"] for x in receipt["result"]["permissions"]}
                == {"accessibility": "granted", "screenshots": "granted"},
                "Platform permissions unavailable after normal exit",
            )

        def observe(self):
            if self.job["purpose"] == TRANSPORT_PURPOSE:
                world = route = cargo = None
                partial_reason = None
                self.transport_work_active = True
                try:
                    world = self.decisions.stage(
                        "world",
                        "Start the selected original Alternate UK scenario. Observe its playable world and actual starting city; pause while planning construction.",
                    )
                    route = self.decisions.stage(
                        "action",
                        "Use the current world to establish a short connected route between actual terminals. Build the required station or resource annex, buy an available engine and attach cars matching visibly offered goods. Observe the selected endpoints, engine, cars and route; purchases or a moving train alone do not prove delivery.",
                    )
                    cargo = self.decisions.stage(
                        "continued",
                        "Run the established route and observe three distinct facts: goods offered at its source, matching cars attached/loaded, and actual arrival and unloading or an explicit delivery result at the destination. Do not infer delivery from movement, time passing or unrelated income. Stop honestly if these facts cannot be observed within this attempt.",
                    )
                except TransportIncomplete as failure:
                    partial_reason = str(failure)
                    self.write(
                        "transport-partial.json", {"reason": partial_reason, "cargo_credit": False}
                    )
                finally:
                    self.transport_work_active = False
                self.normal_quit("finished", None)
                return {
                    "status": "safety_stop",
                    "cause": "agent_visual_transport_action_requires_review",
                    "world_loaded": "agent_observed" if world else "unknown",
                    "gameplay_credit": False,
                    "save_reopen_credit": False,
                    "cargo_credit": False,
                    "evidence_kind": "agent_visual_observations",
                    "human_confirmation": False,
                    "agent_observed_stages": [
                        name
                        for name, value in (
                            ("world", world),
                            ("action", route),
                            ("continued", cargo),
                        )
                        if value is not None
                    ]
                    + ["finished"],
                    "world": world,
                    "route": route,
                    "cargo": cargo,
                    "partial_reason": partial_reason,
                }
            if self.job["purpose"] == WINDOW_PURPOSE:
                launcher = self.decisions.stage(
                    "launcher",
                    "Wait while the Feral splash animates. Once Play is visible, activate the fresh AX Play index once. Then observe the game main menu.",
                )
                opened = self.decisions.stage(
                    "menu",
                    "From the visible game main menu, open exactly one reversible submenu. Observe the changed menu. Stop if this is not the game main menu or ownership is uncertain.",
                )
                returned = self.decisions.stage(
                    "returned",
                    "Return once to the same main menu using its Back control or Escape. Observe the main menu again. No gameplay, settings changes or saves.",
                )
                self.normal_quit("finished", None)
                return {
                    "status": "safety_stop",
                    "cause": "agent_observed_reversible_menu",
                    "world_loaded": "unknown",
                    "gameplay_credit": False,
                    "save_reopen_credit": False,
                    "evidence_kind": "agent_visual_observations",
                    "launcher": launcher,
                    "opened": opened,
                    "returned": returned,
                    "gameplay_accepted": False,
                    "save_reopen_accepted": False,
                }
            source = self.job["source_original"]
            self.scope = SaveScope(
                self.job["job"],
                source["archive_sha256"],
                source["variant_id"],
                source["scenario"],
                self.job["game_sha256"],
                self.diagnostic,
                str(self.profiles.live),
            )
            config = self.job["agent_save"]
            self.decisions.stage(
                "world", "Start the selected Alternate UK scenario and observe a playable world."
            )
            self.decisions.stage(
                "action", "Build one short stretch of track and observe its result."
            )
            mapping = self.decisions.stage(
                "filename",
                "Open Save As and enter exactly "
                + config["token"]
                + "; observe its field and caret, and do not save yet.",
            )
            intent = SaveIntent(
                str(self.profiles.live / "Saves"), config["filename"], sha(canonical(mapping))
            )
            outcome = observe_save_cycle(
                self,
                self.scope,
                intent,
                minimum_stable_seconds=1,
                deadline=self.anchor + ACTION_SECONDS,
                scratch_autosaves=tuple(f"Auto{i}.SMRailroadsSavedGame" for i in range(5)),
            )
            outcome.update(
                cause="agent_visual_save_cycle_and_exact_file_bound_requires_review",
                agent_observed_stages=list(STAGES),
                world_loaded="agent_observed",
                evidence_kind="agent_visual_observations",
                human_confirmation=False,
            )
            return outcome

    return AgentPilot


def verify_launch_backend(common_root):
    """Bind the reviewed app-launch route before queue or owner construction."""
    require(
        sha(read_plain(Path(common_root) / "agent_calibration.py")) == LAUNCHER_CALIBRATION_SHA256,
        "Exact reviewed launcher backend composition required",
    )
    # Load the maintained service before private frozen support paths can win.
    application = importlib.import_module("smr_launcher.application")
    require(
        Path(application.__file__).resolve()
        == Path(__file__).resolve().parents[2] / "src/smr_launcher/application.py",
        "Maintained launcher backend path drift",
    )


def verify_entry(job, admission, job_sha):
    admission_decision, review_decision = admission_decisions(job["purpose"])
    verify_launch_backend(job["common_root"])
    actor = job["actor_binding"]["driver_actor"]
    require(
        os.environ.get("CODEX_THREAD_ID") == actor["session"]
        and os.environ.get("ORCA_TERMINAL_HANDLE") == actor["terminal"]
        and str(Path.cwd()) == actor["cwd"],
        "Actual delegated caller required",
    )
    require(
        job["root"]["run"] == "run_9c111880b18c"
        and admission["decision"] == admission_decision
        and admission["job_sha256"] == job_sha
        and admission["runtime_owner"] == actor["terminal"]
        and admission["root"] == job["root"],
        "Exact root grant required",
    )
    require(
        finite(admission["issued_epoch"])
        and finite(admission["expires_epoch"])
        and 0 < admission["expires_epoch"] - admission["issued_epoch"] <= 900
        and admission["issued_epoch"] <= time.time() < admission["expires_epoch"],
        "Fresh bounded agent admission required",
    )
    require(sha(read_plain(job["authority_path"])) == job["authority_sha256"], "Authority drift")
    config = job["agent_save"]
    require(
        set(config) == {"token", "filename"}
        and re.fullmatch(r"[A-Za-z0-9]{19}", config["token"])
        and config["filename"] == config["token"] + ".SMRailroadsSavedGame",
        "Exact new agent save contract required",
    )
    if job["purpose"] in (
        CATALOGUE_MENU_PURPOSE,
        CACHED_MENU_PURPOSE,
        CACHED_LOAD_PURPOSE,
        CACHED_SAVE_PURPOSE,
    ):
        from catalogue_menu import verify_config

        verify_config(job)
    else:
        require(
            job["source_original"]
            == {
                "archive_sha256": "732ad2ee0c7e1a120876e2792e028d8ac27bf243559e5776e8d7d3aebccb90e9",
                "variant_id": "e09bd4eef4862c86953410a65ce6c39c66cfe411036bfcfa6cd464bf5841c162",
                "scenario": "UserMaps/Alternate_UK_SAM/RRT_Scenario_Alternate_UK.xml",
            },
            "Exact Alternate UK source required",
        )
    paths = set()
    for row in job["closure"]:
        raw = read_plain(row["path"])
        require(
            row["path"] not in paths and len(raw) == row["bytes"] and sha(raw) == row["sha256"],
            "Exact source closure drift",
        )
        paths.add(row["path"])
    require(
        {
            str(Path(__file__).resolve()),
            str(Path(__file__).with_name("agent_actor_binding.py").resolve()),
            job["queue_module"],
            str(Path(job["common_root"]) / "agent_calibration.py"),
            str(Path(job["common_root"]) / "stop-helper/agent_host.py"),
        }.issubset(paths),
        "Driver/common/queue closure required",
    )
    manifest_raw = read_plain(Path(job["common_root"]) / "source-manifest.json")
    require(sha(manifest_raw) == job["source_manifest_sha256"], "Common source manifest drift")
    common_rows = decode(manifest_raw)["files"]
    require(all(row in job["closure"] for row in common_rows), "Complete common closure required")
    # Python has already imported the maintained package before loading the
    # private composition. Pin those actual paths, not only identical frozen copies.
    repository = Path(__file__).resolve().parents[2]
    needed = {str(p) for p in (repository / "src").rglob("*.py")}
    needed.update(
        str(repository / "scripts/research" / name)
        for name in (
            "active_job_control.py",
            "individual_stop_support/host.py",
            "finite_operation.py",
            "save_cycle.py",
            "unique_save_file.py",
            "runner_journal.py",
        )
    )
    needed.add(job["native_source"])
    if job["purpose"] in (
        CATALOGUE_MENU_PURPOSE,
        CACHED_MENU_PURPOSE,
        CACHED_LOAD_PURPOSE,
        CACHED_SAVE_PURPOSE,
    ):
        needed.add(str(Path(__file__).with_name("catalogue_menu.py").resolve()))
    require(needed.issubset(paths), "Actual imported source paths missing from closure")
    raw_review = read_plain(admission["review"]["path"])
    review = decode(raw_review)
    require(
        sha(raw_review) == admission["review"]["sha256"]
        and review["decision"] == review_decision
        and review["job_sha256"] == job_sha
        and review["closure"] == job["closure"]
        and not review["findings"],
        "Exact agent composition review required",
    )


def main():
    require(sys.version_info[:2] == (3, 12) and not sys.flags.optimize, "Python 3.12 required")
    require(len(sys.argv) == 4, "job admission admission-sha256 required")
    raw_job, raw_admission = read_plain(sys.argv[1]), read_plain(sys.argv[2])
    require(sha(raw_admission) == sys.argv[3], "Explicit admission pin required")
    job, admission = decode(raw_job), decode(raw_admission)
    verify_entry(job, admission, sha(raw_job))
    common_root = Path(job["common_root"])
    # The new composition explicitly selects the delegated actor dependency.
    # The frozen V4 tree and its root-only collector remain intact.
    sys.modules["actor_binding"] = importlib.import_module("agent_actor_binding")
    sys.path[:0] = [
        str(common_root),
        str(common_root / "frozen-code/src"),
        str(common_root / "frozen-code/scripts/research"),
        str(common_root / "stop-helper"),
        str(Path(job["queue_module"]).parent),
    ]
    common = importlib.import_module("agent_calibration")
    from game_queue import GameQueue

    queue = GameQueue(admission["queue_path"])
    lease = admission["lease"]
    require(
        lease["owner"] == job["actor_binding"]["driver_actor"]["terminal"],
        "Exact delegated queue lease required",
    )
    queue.guard(lease)
    owner_class = pilot_class(common)
    if job["purpose"] in (
        CATALOGUE_MENU_PURPOSE,
        CACHED_MENU_PURPOSE,
        CACHED_LOAD_PURPOSE,
        CACHED_SAVE_PURPOSE,
    ):
        from catalogue_menu import pilot_class as catalogue_pilot_class

        owner_class = catalogue_pilot_class(common, owner_class)
    owner = owner_class(job, common_root, admission, lambda: queue.guard(lease))
    result = run_calibration(owner)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["terminal_finished"] and not result["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
