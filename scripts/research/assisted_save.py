"""One finite human-assisted save cycle; human reports stay human reports.

The concrete admitted owner supplies the existing launch/stop/restoration methods.
No GUI input is dispatched here. A lost model turn or absent human response expires
the fixed wait in the running controller and returns through finite_operation.
This module does not grant runtime authority or validate a machine scene predicate.
"""

from dataclasses import asdict
from datetime import datetime, timezone
import time
import uuid

from active_job_control import (
    UserCancelled,
    decode,
    direct_user_command,
    read_plain,
    require,
    sha,
)
from runner_journal import write_once
from unique_save_file import (
    SaveIntent,
    bind_stable,
    capture_before,
    finite,
    inspect_new,
    public_reference,
    verify_reopen,
)


class HumanConfirmations:
    """Consume one fresh, exact native user acknowledgement per requested stage.

    The UI action is deliberately performed by Craig through the game, outside
    automatic input hooks. Checks before/after a report are not an atomic guard
    around his physical input. No automatic focus/delivery proof is manufactured.
    """

    def __init__(self, control, directory, deadline):
        require(finite(deadline) and time.monotonic() < deadline, "Finite human deadline required")
        self.control = control
        self.directory = directory
        self.deadline = deadline
        self.used = set()

    def request(self, stage, instruction, process, guard):
        require(
            stage
            in {"world", "action", "filename", "saved", "quit", "reopened", "continued", "finished"}
            and stage not in self.used,
            "Unknown or repeated human stage",
        )
        self.used.add(stage)
        guard()
        now = time.monotonic()
        deadline = min(now + 60, self.deadline)
        require(now < deadline, "Original assisted deadline expired")
        binding = self.control.binding
        raw = read_plain(binding["rollout"], identity=binding["stream_identity"])
        offset = raw.rfind(b"\n") + 1
        require(offset > 0, "Complete native boundary required")
        prefix = sha(raw[:offset])
        token = uuid.uuid4().hex[:12]
        phrase = f"pilot {token} {stage}"
        stop = f"pilot {token} stop"
        requested = datetime.now(timezone.utc)
        prompt = {
            "stage": stage,
            "instruction": instruction,
            "reply": phrase,
            "stop_reply": stop,
            "root": binding["root"],
            "job": binding["job"],
            "process": process,
            "requested_at": requested.isoformat(),
            "deadline_monotonic": deadline,
            "evidence_kind": "human_assistance_request",
            "automatic_input_authority": False,
        }
        write_once(self.directory / f"human-{stage}-prompt.json", prompt)
        while True:
            guard()
            require(time.monotonic() < deadline, "Human response deadline expired")
            data = read_plain(binding["rollout"], identity=binding["stream_identity"])
            require(len(data) >= offset and sha(data[:offset]) == prefix, "Human stream replaced")
            end = data.rfind(b"\n") + 1
            reports = []
            for line in data[offset:end].splitlines():
                require(0 < len(line) <= 1024 * 1024, "Native human record size bound")
                parsed = direct_user_command(decode(line), {phrase: stage, stop: "stop"})
                if parsed is None:
                    continue
                value, when, message_id = parsed
                if when <= requested:
                    continue
                require(when <= datetime.now(timezone.utc), "Human report from future")
                if value == "stop":
                    raise UserCancelled("Human stopped assisted pilot")
                reports.append((when, message_id))
            require(len(reports) <= 1, "Ambiguous human acknowledgement")
            if reports:
                guard()
                require(time.monotonic() < deadline, "Human acknowledgement expired during guard")
                when, message_id = reports[0]
                report = {
                    "stage": stage,
                    "root": binding["root"],
                    "job": binding["job"],
                    "process": process,
                    "message_id": message_id,
                    "native_created_at": when.isoformat(),
                    "observed_monotonic": time.monotonic(),
                    "prompt_sha256": sha(read_plain(self.directory / f"human-{stage}-prompt.json")),
                    "evidence_kind": "native_direct_human_report",
                    "automatic_scene_focus_or_delivery_proof": False,
                }
                write_once(self.directory / f"human-{stage}-report.json", report)
                return report
            offset, prefix = end, sha(data[:end])
            time.sleep(0.1)


def observe_assisted_save(owner, scope, token, filename, *, deadline, scenario_label):
    """Execute the concrete owner's manual route and exact filesystem checks.

    Unlike save_cycle's automatic delivery hooks, each human step is an explicit
    request followed by a native human report. Human input is never replayed by a
    provider. Failure returns to the existing unconditional owned cleanup path.
    """
    owner.human(
        "world",
        f"Start the selected {scenario_label} scenario. Confirm its playable world is visible. "
        "Stop if the selected scenario or scene is uncertain.",
    )
    owner.human(
        "action",
        "Build one short stretch of track. Confirm the new track is visible in the world.",
    )
    mapping = owner.human(
        "filename",
        f"Open Save As and put {token} in the File name box. "
        "Check the title and exact text. Do not confirm the save yet.",
    )
    intent = SaveIntent(str(owner.profiles.live / "Saves"), filename, sha(owner.canonical(mapping)))
    before = capture_before(scope, intent)
    owner.write("save-before.json", asdict(before))
    owner.guard_save(before)
    owner.human(
        "saved",
        "Confirm this save once. Stop on any overwrite prompt or unexpected scene. "
        "Acknowledge only after the dialog closes and the world returns.",
    )
    owner.guard_running()
    first = inspect_new(before)
    owner.write("save-first-sample.json", asdict(first))
    owner.wait_stability(first.inventory.complete + 1, deadline)
    bound = bind_stable(before, first, minimum_stable_seconds=1, deadline=deadline)
    owner.write("save-bound-file.json", asdict(bound))
    verify = lambda: verify_reopen(bound, scope, deadline=deadline)
    owner.write("save-before-quit-identity.json", verify())
    owner.human(
        "quit",
        "Quit the game normally. Do not save again or replace a save. "
        "Acknowledge only after its window has closed.",
        allow_exit=True,
    )
    require(owner.stop_for_relaunch() is True, "Exact first stop refused")
    owner.write("save-after-stop-identity.json", verify())
    owner.relaunch(verify)
    owner.write("save-before-reopen-identity.json", verify())
    owner.human(
        "reopened",
        f"Use Load Game to open exactly {token}. Stop if its identity is ambiguous. "
        "Confirm the saved world and earlier action are visible.",
        validate=verify,
    )
    owner.write("save-after-reopen-identity.json", verify())
    owner.human(
        "continued",
        "Build a second short stretch of track. Confirm its visible result.",
        validate=verify,
    )
    owner.write("save-after-continued-identity.json", verify())
    owner.human(
        "finished",
        "Quit normally without saving again. Acknowledge once the game window closes.",
        allow_exit=True,
    )
    owner.require_normal_exit()
    owner.write("save-after-final-quit-identity.json", verify())
    return {
        "status": "safety_stop",
        "cause": "assisted_save_cycle_human_reported_and_exact_file_bound",
        "save_reference": public_reference(bound),
        "human_reported_stages": [
            "world",
            "action",
            "filename",
            "saved",
            "quit",
            "reopened",
            "continued",
            "finished",
        ],
        "world_loaded": "human_reported",
        "gameplay_credit": False,
        "save_reopen_credit": False,
        "automatic_scene_focus_or_delivery_proof": False,
    }
