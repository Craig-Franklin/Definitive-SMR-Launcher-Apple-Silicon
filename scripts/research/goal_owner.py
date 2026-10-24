"""Bind a sole primary driver's fresh runtime namespace to its existing goal."""

import hashlib
import re

from active_job_control import decode, read_plain, require, sha


LEGACY_RUN = "run_9c111880b18c"
GOAL_PURPOSES = frozenset(
    (
        "ONE_AGENT_PACKAGED_CATALOGUE_LOAD_V1",
        "ONE_AGENT_PACKAGED_CACHED_LOAD_V1",
        "ONE_AGENT_PACKAGED_CACHED_SAVE_REOPEN_V1",
        "ONE_AGENT_PACKAGED_CACHED_GAMEPLAY_V1",
    )
)


def verify_root_scope(job, actor):
    """Select explicit goal ownership without broadening legacy admission.

    The frozen host readback is evidence bound by the fresh job and independent
    admission review. It does not grant GUI or launch authority. The existing
    caller, live Run/generation, source, observer, cancellation, lease, deadline
    and final-input checks remain mandatory in the driver and owner.
    """
    root = job["root"]
    if "goal_scope" not in job:
        require(root["run"] == LEGACY_RUN, "Exact root grant required")
        return 9

    scope = job["goal_scope"]
    require(
        type(scope) is dict
        and set(scope)
        == {"kind", "thread_id", "created_at", "objective_sha256", "subject", "path", "sha256"}
        and scope["kind"] == "current-goal-owner-v1"
        and job["purpose"] in GOAL_PURPOSES,
        "Exact current-goal owner scope required",
    )
    require(
        scope["thread_id"] == actor["session"] == root["session"]
        and actor["terminal"] == root["coordinator"]
        and type(scope["created_at"]) is int
        and scope["created_at"] > 0
        and type(scope["objective_sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", scope["objective_sha256"]) is not None
        and type(root["run"]) is str
        and re.fullmatch(r"run_[0-9a-f]{12}", root["run"]) is not None
        and root["run"] != LEGACY_RUN,
        "Current goal requires its own sole-primary runtime owner",
    )
    subject = scope["subject"]
    require(
        type(subject) is dict
        and set(subject) == {"archive_sha256", "variant_id", "scenario"}
        and subject == job["source_original"],
        "Current-goal map subject changed",
    )
    raw = read_plain(scope["path"])
    require(sha(raw) == scope["sha256"], "Current-goal host readback changed")
    envelope = decode(raw)
    require(type(envelope) is dict, "Current-goal host envelope required")
    goal = envelope.get("goal")
    require(
        type(goal) is dict
        and goal.get("threadId") == scope["thread_id"]
        and type(goal.get("createdAt")) is int
        and goal["createdAt"] == scope["created_at"]
        and goal.get("status") == "active"
        and type(goal.get("objective")) is str
        and hashlib.sha256(goal["objective"].encode()).hexdigest() == scope["objective_sha256"],
        "Existing active goal identity and objective required",
    )
    return 1
