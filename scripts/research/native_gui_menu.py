"""Closed original-edition GUI launch for the existing finite catalogue owner.

The frontend owns its Python service. Neither is a PackagedService, and process
absence is not a protocol reply. Unknown dispatch keeps the observer and profile.
"""

from dataclasses import asdict
import fcntl
import os
from pathlib import Path
import re
import subprocess
import time
from types import SimpleNamespace

from active_job_control import decode, read_plain, require, sha
import catalogue_menu as catalogue

PURPOSE = catalogue.GUI_PURPOSE
SUCCESS = "Railroads launched. Quit it before changing maps."
STAGES = ("gui_select", "gui_play", "gui_original", "gui_confirm", "gui_success", "gui_quit")


def verify_gui_config(job):
    config = job["catalogue_menu"]
    gui = config["native_gui"]
    require(job["purpose"] == PURPOSE, "Closed GUI menu purpose required")
    require(
        set(gui) == {"display_name", "initial_warning", "frontend_sha256", "launcher_files"}
        and type(gui["display_name"]) is str
        and 0 < len(gui["display_name"]) <= 256
        and "\n" not in gui["display_name"]
        and type(gui["initial_warning"]) is str
        and 0 < len(gui["initial_warning"].encode()) <= 16384
        and gui["initial_warning"] != config["warning"]
        and config["prepare_compatibility"] is False
        and not any(
            key in config for key in ("selection", "preparation", "owned_autosave", "window_probe")
        ),
        "One original target with two separately pinned reviews required",
    )
    front = Path(config["package"]["bundle"]) / "Contents/MacOS/SMRLauncher"
    require(
        sha(read_plain(front, maximum=128 * 1024**2)) == gui["frontend_sha256"], "Frontend drift"
    )
    require(type(gui["launcher_files"]) is dict, "Launcher preservation projection required")


def process_rows():
    """Read only; kernel identities are checked separately before using each PID."""
    data = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid=,comm="], text=True, timeout=2)
    rows = []
    for line in data.splitlines():
        fields = line.split(None, 2)
        require(
            len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit(), "Process table shape"
        )
        rows.append((int(fields[0]), int(fields[1]), fields[2]))
    return rows


def lock_free(path):
    """Probe an existing inode only. Never create, remove or replace a lock."""
    path = Path(path)
    if not path.exists():
        require(not path.is_symlink(), "Lock symlink refused")
        return True
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    finally:
        os.close(fd)


def button_index(frame, label):
    rows = re.findall(r"^\s*(\d+) button " + re.escape(label) + r"$", frame["treeText"], re.M)
    require(len(rows) == 1, "One fresh named GUI button required: " + label)
    return rows[0]


def review_body(owner, *, initial):
    target = owner.config["target"]
    warning = owner.gui["initial_warning"] if initial else owner.config["warning"]
    return (
        owner.gui["display_name"]
        + "\nEdition: "
        + target["variant_id"]
        + "\nGame build: "
        + owner.job["game_sha256"]
        + "\n\n"
        + warning
    )


def exact_review(owner, frame, *, initial):
    # treeText uses literal newlines within multiline Text labels. Require the
    # complete body, rather than matching fragments or the other review's hash.
    body = review_body(owner, initial=initial)
    require(body in frame["treeText"], "Exact current review body/identity required")
    other = review_body(owner, initial=not initial)
    require(other not in frame["treeText"], "Stale or overlapping GUI reviews")
    button_index(frame, "Play Original Edition" if initial else "Launch This Edition Anyway")


def search_focused(frame):
    return (
        re.search(
            r"^The focused UI element is \d+ search text field \(settable\) Search maps(?:, Value: [^\n]*)?\.$",
            frame["treeText"],
            re.M,
        )
        is not None
    )


def selected_identity(owner, frame):
    text = frame["treeText"]
    target = owner.config["target"]
    for label in (
        "Archive SHA-256: " + target["archive_sha256"],
        "Variant: " + target["variant_id"],
        "Executable SHA-256: " + owner.job["game_sha256"],
    ):
        require(label in text, "Exact selected UI identity unavailable")
    require(owner.gui["display_name"] in text, "Selected title unavailable")


def require_provenance_scroll_area(owner, frame, index):
    """Only the current detail subtree advertising the exact AX down action."""
    lines = frame["treeText"].splitlines()
    rows = [
        i
        for i, line in enumerate(lines)
        if re.match(
            r"^\s*" + re.escape(index) + r" scroll area(?: [^,]*)?, Secondary Actions: ", line
        )
    ]
    require(len(rows) == 1, "Fresh advertised provenance scroll area required")
    start = rows[0]
    require(
        "scroll down" in lines[start].split("Secondary Actions: ", 1)[1].split(", "),
        "Scroll area does not advertise scroll down",
    )
    depth = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if re.match(r"^\s*\d+ ", line) and len(line) - len(line.lstrip()) <= depth:
            break
        end += 1
    body = "\n".join(lines[start + 1 : end])
    title = r"^\s*\d+ text " + re.escape(owner.gui["display_name"]) + r"$"
    section = re.search(r"^\s*\d+ pop up button, Value: Issues & Evidence$", body, re.M)
    require(
        re.search(title, body, re.M) and section is not None,
        "Current exact title and Issues & Evidence detail required",
    )
    evidence = body[section.end() :]
    known = re.search(r"^\s*\d+ text Known issue$", evidence, re.M)
    require(
        known is not None and re.search(r"^\s*\d+ text \S.+$", evidence[known.end() :], re.M),
        "Current Issues & Evidence body required",
    )


def pilot_class(common, catalogue_class):
    class NativeGUIMenuPilot(catalogue_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.gui = self.config["native_gui"]
            self.frontend = self.backend = self.open_child = None
            self.gui_phase = True
            self.gui_bound = self.gui_closed = self.gui_quit_sent = False
            self.gui_play_sent = self.gui_original_sent = self.gui_confirm_sent = False
            self.gui_selection_bound = False
            self.gui_launch_started = False
            self.gui_frames = None
            self.gui_play_action = None
            self.gui_scroll_dispatches = 0
            self.gui_scroll_permit = None

        @property
        def frontend_path(self):
            return str(Path(self.config["package"]["bundle"]) / "Contents/MacOS/SMRLauncher")

        def native_receiver(self):
            return self.frontend if self.gui_phase else super().native_receiver()

        def verify_launcher_files(self):
            expected = self.gui["launcher_files"]
            # The two update files are a closed projection, not arbitrary reads.
            paths = [
                self.app.library / "update-preferences.json",
                self.app.library / "updates/pending.json",
            ]
            require(
                set(expected) == {str(p) for p in paths},
                "Exact launcher preservation paths required",
            )
            for path in paths:
                catalogue._assert_no_symlink_ancestor(path)
                actual = catalogue.file_facts(path) if path.exists() else None
                require(actual == expected[str(path)], "Launcher/update state drift")
            if paths[1].exists():
                require(
                    decode(read_plain(paths[1])).get("state") == "installed",
                    "Pending update work refused",
                )
            require(
                lock_free(self.app.library / "updates/.install.lock"), "Installer owns launcher"
            )

        def launcher_rows(self):
            rows = process_rows()
            owned = {p.pid for p in (self.frontend, self.backend) if p is not None}
            for pid, _, exe in rows:
                if exe.endswith(("/SMRLauncher", "/Definitive SMR Launcher Apple Silicon")):
                    require(pid in owned, "Foreign frontend/service/helper; GUI ownership refused")
            return rows

        def require_gui_identity(self, *, allow_exit=False):
            require(
                self.frontend is not None and self.backend is not None,
                "Both GUI identities required",
            )
            rows = self.launcher_rows()
            current_front = self.monitor.inspect(self.frontend.pid)
            current_backend = self.monitor.inspect(self.backend.pid)
            if allow_exit:
                require(
                    current_front is None or self.frontend.same(current_front),
                    "Frontend PID reused",
                )
                require(
                    current_backend is None or self.backend.same(current_backend),
                    "Backend PID reused",
                )
            else:
                require(
                    self.frontend.same(current_front) and self.backend.same(current_backend),
                    "GUI owner identity changed",
                )
                require(
                    (self.backend.pid, self.frontend.pid, self.backend.executable) in rows,
                    "Swift-owned backend relationship changed",
                )
                lease = decode(read_plain(self.app.library / ".native-instance.lock"))
                require(
                    lease.get("pid") == self.frontend.pid
                    and lease.get("executable") == self.frontend.executable
                    and not lock_free(self.app.library / ".native-instance.lock"),
                    "Native frontend lease drift",
                )
            self.verify_launcher_files()

        def require_gui_closed(self):
            require(self.gui_closed and self.gui_quit_sent, "Normal GUI close not settled")
            self.require_gui_identity(allow_exit=True)
            require(
                self.monitor.inspect(self.frontend.pid) is None
                and self.monitor.inspect(self.backend.pid) is None
                and lock_free(self.app.library / ".native-instance.lock"),
                "Frontend/backend absence and native lease release required",
            )
            require(
                self.open_child is not None and self.open_child.poll() is not None,
                "Owned open child not reaped",
            )

        def prepare(self):
            super().prepare()
            self.verify_launcher_files()
            require(
                lock_free(self.app.library / ".native-instance.lock"),
                "Frontend already owns library",
            )
            self.launcher_rows()
            records = [
                r
                for r in self.app.catalogue()
                if r.profile_id == self.config["target"]["profile_id"]
            ]
            require(
                len(records) == 1
                and records[0].name == self.gui["display_name"]
                and self.app._is_original_variant(records[0]),
                "Exact original GUI catalogue target drift",
            )

        def guard_running(self, *, allow_exit=False):
            if not self.gui_phase:
                self.require_gui_closed()
                return super().guard_running(allow_exit=allow_exit)
            self.verify_available()
            self.admitted()
            self.verify_code()
            require(
                time.monotonic() < self.anchor + (480 if self.gui_quit_sent else 390),
                "Original GUI work deadline expired",
            )
            self.require_gui_identity(allow_exit=allow_exit)
            if hasattr(self, "request") and not self.gui_bound:
                self.require_binding_window()
            if self.observer is not None:
                require(self.observer.poll() is None, "Independent observer exited")
            active = self.profiles._state()["active"]
            require(
                not self.profiles.journal.exists(), "Activation in flight; GUI observation held"
            )
            allowed = (self.job["return_profile"], self.config["target"]["profile_id"])
            require(active in allowed, "Foreign active profile")
            if self.gui_bound:
                require(active == self.config["target"]["profile_id"], "Bound game profile drift")
            games = self.monitor.games()
            if not self.gui_confirm_sent:
                require(
                    not games and active == self.job["return_profile"],
                    "Unexpected pre-confirmation dispatch",
                )
            elif games:
                require(len(games) == 1, "Ambiguous new game")
                game = games[0]
                require(
                    game.executable == str(self.app.installation.executable)
                    and self.request_wall_us <= game.birth_us <= self.request_wall_deadline_us
                    and game.birth_us <= time.time_ns() // 1000
                    and (self.game is None or self.game.same(game)),
                    "Game executable/birth/identity mismatch",
                )
                self.sample(force=True)

        def retain_frame(self, receipt, label):
            if not self.gui_phase:
                return super().retain_frame(receipt, label)
            # Frozen frame validation names its receiver `game`. Supply a frame
            # context only; the lifecycle's game remains None until a real birth.
            if self.gui_frames is None:
                self.gui_frames = SimpleNamespace(
                    game=self.frontend,
                    job=self.job,
                    directory=self.directory,
                    write=self.write,
                    phases=self.phases,
                )
            return common.UKCalibration.retain_frame(self.gui_frames, receipt, label)

        def scene_frame(self, receipt, frame, window, *, fresh=True):
            if not self.gui_phase:
                return super().scene_frame(receipt, frame, window, fresh=fresh)
            return common.UKCalibration.scene_frame(
                self.gui_frames, receipt, frame, window, fresh=fresh
            )

        def validate_gui_observation(self, stage, frame, decision):
            require(self.job["purpose"] == PURPOSE and stage in STAGES, "Closed GUI stage required")
            require(decision["action"] == [], "GUI observation cannot carry input")
            if stage == "gui_select":
                selected_identity(self, frame)
                require(SUCCESS not in frame["treeText"], "Stale success before original request")
                self.gui_selection_bound = True
            elif stage == "gui_success":
                require(
                    self.gui_confirm_sent and SUCCESS in frame["treeText"],
                    "Fresh launch success unavailable",
                )
                # CurrentPlay and target.matches gate this status in Swift.
                # Combine it with independently bound selection, profile, assets,
                # game birth and observer; never manufacture a protocol response.
                require(self.gui_selection_bound, "GUI selection unbound")
                selected_identity(self, frame)
                self.gui_success = dict(
                    snapshot=frame["snapshot"], image_sha256=frame["sha256"], status=SUCCESS
                )
            else:
                require(False, "This GUI stage requires one input")

        def validate_gui_action(self, stage, decision, frame):
            from agent_driver import validate_action

            require(self.job["purpose"] == PURPOSE and stage in STAGES, "Closed GUI stage required")
            action = decision["action"]
            receiver = decision["receiver"]
            require(
                receiver
                in ("owned_window", "search_field", "exact_target_card", "provenance_control"),
                "GUI receiver refused",
            )
            if action and action[0] == "perform-secondary-action":
                require(
                    stage == "gui_select"
                    and receiver == "provenance_control"
                    and self.gui_phase
                    and not self.gui_play_sent
                    and not hasattr(self, "request")
                    and len(action) == 5
                    and action[1] == "--element-index"
                    and action[2].isdigit()
                    and action[3:] == ["--action", "scroll down"],
                    "Only pre-Play GUIselect provenance semantic scroll down is admitted",
                )
                require(
                    self.gui_scroll_dispatches < 2, "Two provenance scroll dispatches exhausted"
                )
                require_provenance_scroll_area(self, frame, action[2])
                self.gui_scroll_permit = dict(
                    action=tuple(action),
                    captured=self.frame_started,
                    window=frame["window"]["id"],
                )
                return action
            if stage == "gui_select":
                # Actor reads the fresh image to identify the original card among
                # substring matches. Exact name is not a unique-search claim.
                if action[:2] == ["type-text", "--text"]:
                    require(
                        action == ["type-text", "--text", self.gui["display_name"]]
                        and receiver == "search_field",
                        "Exact map search only",
                    )
                    require(
                        search_focused(frame),
                        "Current search focus required",
                    )
                    return action
                if action in (["hotkey", "--key", "CmdOrCtrl+A"], ["press-key", "--key", "Return"]):
                    require(
                        receiver == "search_field" and search_focused(frame),
                        "Current search focus required",
                    )
                    return action
                require(
                    receiver in ("search_field", "exact_target_card", "provenance_control"),
                    "Selection input requires its current visual receiver",
                )
                require(
                    action[0:2] == ["click", "--x"],
                    "Selection uses current visual coordinates only",
                )
                return validate_action(
                    "menu", action, frame, self.job["agent_save"]["token"], "owned_window"
                )
            require(receiver == "owned_window", "GUI action receiver mismatch")
            if stage == "gui_quit":
                require(
                    self.gui_bound
                    and not self.gui_quit_sent
                    and action == ["hotkey", "--key", "CmdOrCtrl+Q"],
                    "One normal launcher Quit only",
                )
                require(
                    not any(
                        word in frame["treeText"]
                        for word in ("Discard Drafts", "Install & Quit", "Continue Working")
                    ),
                    "Draft/update review refused",
                )
                self.verify_launcher_files()
                self.gui_quit_sent = True
                return action
            if stage == "gui_play":
                require(
                    self.gui_selection_bound and not self.gui_play_sent,
                    "One Play after exact selection only",
                )
                selected_identity(self, frame)
                require(SUCCESS not in frame["treeText"], "Stale success before Play")
                require(
                    len(action) == 3
                    and action[:2] == ["click", "--element-index"]
                    and re.search(
                        r"^\s*" + re.escape(action[2]) + r" button(?: Play selected map)?$",
                        frame["treeText"],
                        re.M,
                    ),
                    "Fresh enabled Play required; actor must identify the visible control",
                )
                self.gui_play_sent = True
                self.gui_play_action = tuple(action)
            elif stage == "gui_original":
                require(
                    self.gui_play_sent and not self.gui_original_sent,
                    "One original review input only",
                )
                exact_review(self, frame, initial=True)
                index = button_index(frame, "Play Original Edition")
                require(
                    action == ["click", "--element-index", index],
                    "Preparation refused; original only",
                )
                self.gui_original_sent = True
            else:
                require(
                    stage == "gui_confirm" and self.gui_original_sent and not self.gui_confirm_sent,
                    "One final confirmation only",
                )
                exact_review(self, frame, initial=False)
                index = button_index(frame, "Launch This Edition Anyway")
                require(
                    action == ["click", "--element-index", index],
                    "Exact final review confirmation required",
                )
                self.gui_confirm_sent = True
            return action

        def require_binding_window(self):
            require(
                time.monotonic() < min(self.request + 20, self.launch_deadline),
                "Original observer request+20 binding deadline expired",
            )

        def arm_gui_play_observer(self):
            # The actor's validated Play decision precedes the one original arm.
            # Record before calling the maintained arm; failures cannot re-arm.
            require(
                self.job["purpose"] == PURPOSE
                and self.gui_phase
                and self.gui_selection_bound
                and self.gui_play_sent
                and not hasattr(self, "request")
                and self.observer is None
                and not hasattr(self, "gui_arm_started")
                and not getattr(self, "inside_mutation", False),
                "One observer arm after validated GUI Play, outside the input guard, required",
            )
            self.guard_running()
            require(
                self.launch_deadline == self.anchor + 390
                and time.monotonic() < self.launch_deadline,
                "Original GUI observer arm cutoff expired",
            )
            self.gui_arm_started = time.monotonic()
            self.observer_admission = catalogue.arm_observer(self, common)

        def publish_play_request(self):
            # Called inside the existing final input guard, after the actor's
            # fresh Play decision and delivery validation. No nested guard.
            require(not hasattr(self, "request"), "One original GUI request only")
            require(
                time.monotonic() < min(self.gui_arm_started + 20, self.launch_deadline)
                and self.observer.poll() is None,
                "Original observer ready+20 request window expired before Play",
            )
            self.request = time.monotonic()
            self.request_wall_us = time.time_ns() // 1000
            self.request_wall_deadline_us = self.request_wall_us + int(
                (self.launch_deadline - self.request) * 1_000_000
            )
            self.phases["launch_request"] = self.request
            self.publish(
                "request.json",
                dict(
                    version=1,
                    job=self.job["job"],
                    nonce=self.nonce,
                    code=self.code,
                    anchor=self.anchor,
                    request=self.request,
                ),
            )
            self.write(
                "native-gui-request-interval.json",
                dict(
                    start=self.request,
                    wall_us=self.request_wall_us,
                    wall_deadline_us=self.request_wall_deadline_us,
                    deadline=self.launch_deadline,
                    binding_deadline=self.request + 20,
                    ready_request_cutoff=self.gui_arm_started + 20,
                    frontend=asdict(self.frontend),
                    backend=asdict(self.backend),
                    target=self.config["target"],
                    one_final_confirmation=True,
                ),
            )
            # The wrapper's actual one-second tick must consume the request
            # before Play. A publication near ready+20 is not an acknowledgment.
            from wrapper import bounded

            limit = min(self.gui_arm_started + 20, self.request + 20, self.launch_deadline)
            while True:
                self.control._poll_locked()  # Existing input guard already owns this lock.
                require(not self.control.cancelled, "Cancelled while awaiting request consumption")
                require(
                    time.monotonic() < limit and self.observer.poll() is None,
                    "Original observer request window expired before acknowledgment; no Play input",
                )
                state = decode(bounded(self.helper_packet / "state.json"))
                require(
                    all(state.get(k) == v for k, v in self.observer_admission.items())
                    and state.get("kind") == "state"
                    and state.get("version") == 1
                    and type(state.get("state_seq")) is int
                    and state["state_seq"] > 0
                    and self.gui_arm_started <= state.get("observed", -1) <= time.monotonic()
                    and state.get("state", {}).get("terminal") is False,
                    "Actual observer request acknowledgment drift",
                )
                if state["state"].get("phase") == "awaiting_binding":
                    require(
                        state["observed"] >= self.request
                        and state["state"].get("reason") == "descriptor_pending"
                        and state["state"].get("pending_launch") is True
                        and state["state"].get("bound") is False,
                        "Actual observer did not consume the original request",
                    )
                    break
                require(state["state"].get("phase") == "ready", "Unexpected observer request phase")
                time.sleep(0.05)
            self.write(
                "native-gui-request-ack.json",
                dict(
                    evidence_kind="actual_observer_request_consumed",
                    state=state,
                    request=self.request,
                    ready_request_cutoff=self.gui_arm_started + 20,
                ),
            )
            self.guard_running()
            self.verify_provider()
            self.verify_preinput_contract()
            self.control._poll_locked()
            require(not self.control.cancelled, "Cancelled before acknowledged Play dispatch")
            if self.delivery_validation:
                self.delivery_validation()
            require(
                time.monotonic() < limit and self.observer.poll() is None,
                "Original observer request window expired after acknowledgment; no Play input",
            )
            self.open_attempted = True

        def spawn_input(self, argv, *, deadline, before_spawn=None, **kwargs):
            from agent_driver import FRAME_SECONDS

            def final_gui_dispatch():
                if argv[:3] == [self.job["orca"], "computer", "perform-secondary-action"]:
                    permit = self.gui_scroll_permit
                    require(
                        self.job["purpose"] == PURPOSE
                        and self.gui_phase
                        and not self.gui_play_sent
                        and not hasattr(self, "request")
                        and permit is not None
                        and "--app" in argv
                        and tuple(argv[2 : argv.index("--app")]) == permit["action"]
                        and argv[argv.index("--app") + 1] == f"pid:{self.frontend.pid}"
                        and "--window-id" in argv
                        and argv[argv.index("--window-id") + 1] == str(permit["window"])
                        and self.frame_started == permit["captured"]
                        and 0 <= time.monotonic() - permit["captured"] < FRAME_SECONDS,
                        "Fresh pre-Play provenance scroll permit required",
                    )
                    require(
                        self.gui_scroll_dispatches < 2, "Two provenance scroll dispatches exhausted"
                    )
                    self.gui_scroll_dispatches += 1
                    self.gui_scroll_permit = None
                if self.gui_phase and self.gui_play_sent and not hasattr(self, "request"):
                    require(
                        argv[:2] == [self.job["orca"], "computer"]
                        and "--app" in argv
                        and tuple(argv[2 : argv.index("--app")]) == self.gui_play_action
                        and argv[argv.index("--app") + 1] == f"pid:{self.frontend.pid}",
                        "Original request requires the validated actual GUI Play dispatch",
                    )
                    self.publish_play_request()
                if before_spawn:
                    before_spawn()

            return super().spawn_input(
                argv, deadline=deadline, before_spawn=final_gui_dispatch, **kwargs
            )

        def complete_pinned_reviews(self):
            # No model round trips between Play and actual observer binding.
            # Each transition needs its own fresh exact body/control, never an
            # assumed dialogue advance or a fabricated visual actor decision.
            deadline = min(self.request + 20, self.launch_deadline)
            for stage, label, button in (
                ("gui_original", "MachineOriginalReview", "Play Original Edition"),
                ("gui_confirm", "MachineFinalReview", "Launch This Edition Anyway"),
            ):
                self.require_binding_window()
                receipt, frame, window = self.agent_frame(label, deadline)
                captured = self.frame_started
                action = ["click", "--element-index", button_index(frame, button)]
                action = self.validate_gui_action(
                    stage, dict(action=action, receiver="owned_window"), frame
                )
                self.write(
                    label + "-mechanical.json",
                    dict(
                        evidence_kind="machine_exact_gui_review",
                        stage=stage,
                        snapshot=frame["snapshot"],
                        image_sha256=frame["sha256"],
                        provider_receipt_sha256=sha(catalogue.canonical(receipt)),
                        action=action,
                        request=self.request,
                        deadline=deadline,
                    ),
                )
                self.agent_input(
                    label, action, window, frame, captured, deadline, self.require_binding_window
                )
            self.require_binding_window()
            receipt, frame, _ = self.agent_frame("MachineLaunchSuccess", deadline)
            self.validate_gui_observation("gui_success", frame, dict(action=[]))
            self.gui_success["evidence_kind"] = "machine_exact_gui_success"
            self.write(
                "MachineLaunchSuccess-mechanical.json",
                dict(
                    evidence_kind="machine_exact_gui_success",
                    snapshot=frame["snapshot"],
                    image_sha256=frame["sha256"],
                    provider_receipt_sha256=sha(catalogue.canonical(receipt)),
                    request=self.request,
                    deadline=deadline,
                ),
            )
            self.require_binding_window()

        def launch(self):
            require(self.launches == 0, "One native GUI launch maximum")
            self.require_stopped()
            self.verify_available()
            self.verify_code()
            catalogue.verify_config(self.job)
            require(
                self.profiles._state()["active"] == self.job["return_profile"]
                and not self.profiles.journal.exists(),
                "Prelaunch return profile/journal drift",
            )
            self.verify_launcher_files()
            self.launcher_rows()
            require(
                lock_free(self.app.library / ".native-instance.lock"), "Native frontend occupied"
            )
            self.launches = 1
            self.launch_deadline = self.anchor + 390
            self.gui_launch_started = True
            wall = time.time_ns() // 1000
            with self.mutation_guard():
                self.require_stopped()
                self.verify_launcher_files()
                self.launcher_rows()
                require(
                    lock_free(self.app.library / ".native-instance.lock"),
                    "Native frontend occupied",
                )
                require(
                    time.monotonic() < self.launch_deadline, "Original GUI launch cutoff expired"
                )
                self.open_child = subprocess.Popen(
                    [
                        "/usr/bin/open",
                        "-W",
                        self.config["package"]["bundle"],
                        "--args",
                        "--play-deadline",
                        str(self.launch_deadline),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            limit = min(time.monotonic() + 15, self.launch_deadline)
            while self.backend is None:
                self.verify_available()
                self.admitted()
                require(time.monotonic() < limit, "Native frontend/service binding deadline")
                lease_path = self.app.library / ".native-instance.lock"
                if lease_path.exists():
                    lease = decode(read_plain(lease_path))
                    pid = lease.get("pid")
                    front = self.monitor.inspect(pid) if type(pid) is int else None
                    if front is not None:
                        require(
                            front.executable == self.frontend_path and front.birth_us >= wall,
                            "Foreign native lease",
                        )
                        self.frontend = front
                        children = [
                            r
                            for r in process_rows()
                            if r[1] == front.pid and r[2] == self.config["package"]["executable"]
                        ]
                        require(len(children) <= 1, "Ambiguous Swift service")
                        if children:
                            self.backend = self.monitor.inspect(children[0][0])
                            require(
                                self.backend is not None
                                and self.backend.executable == self.config["package"]["executable"]
                                and self.backend.birth_us >= front.birth_us,
                                "Backend birth/executable unavailable",
                            )
                time.sleep(0.05)
            self.require_gui_identity()
            self.write(
                "native-gui-binding.json",
                dict(
                    frontend=asdict(self.frontend),
                    backend=asdict(self.backend),
                    fixed_launch_deadline=self.launch_deadline,
                ),
            )
            self.decisions.stage(
                "gui_select",
                "Select the exact original card by current image. Search may return multiple cards. Click the current search field once, require fresh focused-field evidence, type the exact name and press Return to commit. Identify the fresh exact original card visually. Open Issues & Evidence / Exact Identities & Provenance. Observe the exact title/archive/variant/build. If provenance is below the current Issues & Evidence viewport, use only its fresh scroll area advertised perform-secondary-action scroll down, receiver provenance_control, at most twice. Inspect a fresh viewport after each action. Stop if it did not move down; a second action is allowed only if the first visibly advanced and metadata remains below. This is actor visual judgment, not machine scene proof. Never click Play here.",
            )
            self.decisions.stage(
                "gui_play",
                "Click the current enabled Play once. The two exact pinned original-only reviews are mechanically checked next.",
                return_after_input=True,
            )
            self.complete_pinned_reviews()
            target = self.config["target"]
            require(
                self.profiles._state()["active"] == target["profile_id"], "Success/profile mismatch"
            )
            require(
                self.profiles._asset_hash(self.profiles.live) == target["assets_sha256"],
                "Success/assets mismatch",
            )
            catalogue.bind_observer(self, self.observer_admission)
            games = self.monitor.games()
            require(
                self.game is not None and len(games) == 1 and self.game.same(games[0]),
                "One bound expected game required",
            )
            self.guard_running()
            require(
                self.profiles._state()["active"] == target["profile_id"]
                and self.profiles._asset_hash(self.profiles.live) == target["assets_sha256"],
                "Bound game profile/assets mismatch",
            )
            self.gui_bound = True
            self.write(
                "native-gui-correspondence.json",
                dict(
                    status=self.gui_success,
                    target=target,
                    frontend=asdict(self.frontend),
                    backend=asdict(self.backend),
                    game=asdict(self.game),
                    observer_bound=True,
                    raw_protocol_response_available=False,
                ),
            )
            self.decisions.stage(
                "gui_quit",
                "Normal launcher Quit once. Any draft/update confirmation is HOLD; never consent.",
                return_after_input=True,
            )
            while (
                self.monitor.inspect(self.frontend.pid) is not None
                or self.monitor.inspect(self.backend.pid) is not None
            ):
                self.guard_running(allow_exit=True)
                time.sleep(0.05)
            self.open_child.wait(timeout=max(0.01, min(2, self.launch_deadline - time.monotonic())))
            self.gui_closed = True
            self.require_gui_closed()
            self.write(
                "native-gui-close.json",
                dict(
                    frontend_absent=True,
                    backend_absent=True,
                    native_lease_free=True,
                    open_child_reaped=True,
                    open_child_returncode=self.open_child.returncode,
                    service_returncode_available=False,
                    service_eof_available=False,
                ),
            )
            require(time.monotonic() < self.anchor + 480, "Original GUI shutdown cutoff expired")
            self.dispatch_settled = True
            self.gui_phase = False
            engine = dict(
                game_sha256=self.job["game_sha256"],
                catalogue_target=target,
                selected_catalogue_edition=target,
                prepare_compatibility=False,
            )
            self.journal.append(
                "selected",
                dict(
                    process={k: asdict(self.game)[k] for k in ("pid", "birth_us", "executable")},
                    selected_at_us=time.time_ns() // 1000,
                    observed_inputs=self.job["source_original"],
                    observed_engine_inputs=engine,
                    observed_input_identity=sha(catalogue.canonical(engine)),
                ),
            )

        def normal_exit_receipt(self):
            if not self.gui_phase:
                return super().normal_exit_receipt()
            # A completed provider error during launcher Quit may be settled
            # only by both exact absences; no game receipt can stand in for it.
            require(self.gui_quit_sent, "Launcher Quit was not requested")
            self.require_gui_identity(allow_exit=True)
            require(
                self.monitor.inspect(self.frontend.pid) is None
                and self.monitor.inspect(self.backend.pid) is None,
                "Launcher/backend close remains unresolved",
            )

        def stop(self):
            self.verify_cleanup_owner()
            # Explicitly bypass CatalogueMenuPilot's service=None shortcut.
            # GUI death cannot cancel an asynchronous unbound LaunchServices request.
            require(
                not self.gui_launch_started or self.dispatch_settled,
                "GUI dispatch unbound/inflight; observer retained, restoration held",
            )
            if self.gui_launch_started:
                self.require_gui_closed()
            return super(catalogue_class, self).stop()

    return NativeGUIMenuPilot
