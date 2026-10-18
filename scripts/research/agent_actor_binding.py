"""Read genuine delegated-driver provenance without transferring the Orca Run."""

import os
from pathlib import Path
import subprocess

from active_job_control import MAX_NATIVE_HISTORY_BYTES, decode, read_plain, require


def collect_actor(binding):
    actor = binding["driver_actor"]
    require(
        os.environ.get("CODEX_THREAD_ID") == actor["session"]
        and os.environ.get("ORCA_TERMINAL_HANDLE") == actor["terminal"]
        and str(Path.cwd()) == actor["cwd"],
        "Exact delegated driver caller required",
    )
    raw = subprocess.check_output(
        [binding["orca"], "terminal", "show", "--terminal", actor["terminal"], "--json"],
        timeout=3,
    )
    require(len(raw) <= 65536, "Actor metadata bound")
    receipt = decode(raw)
    require(
        receipt.get("ok") is True
        and receipt.get("_meta", {}).get("runtimeId") == binding["provider_runtime_id"],
        "Actor runtime changed",
    )
    info = receipt["result"]["terminal"]
    for key, value in {
        "handle": actor["terminal"],
        "incarnationId": actor["incarnation_id"],
        "worktreePath": actor["cwd"],
        "connected": True,
        "writable": True,
        "orphaned": False,
        "executionHostId": "local",
        "agentIdentity": "codex",
    }.items():
        require(type(info.get(key)) is type(value) and info[key] == value, "Actor terminal drift")
    path = Path(actor["rollout"])
    require(
        path.is_absolute()
        and path.name.endswith("-" + actor["session"] + ".jsonl")
        and path.is_relative_to(Path.home() / ".codex/sessions"),
        "Exact native driver rollout required",
    )
    raw = read_plain(path, maximum=MAX_NATIVE_HISTORY_BYTES)
    end = raw.rfind(b"\n") + 1
    require(end > 0, "Complete native actor boundary required")
    rows = raw[:end].splitlines()
    require(len(rows[0]) <= 1024 * 1024, "Native actor identity row bound")
    first = decode(rows[0])
    require(
        first.get("type") == "session_meta"
        and first["payload"]["id"] == actor["session"]
        and first["payload"]["cwd"] == actor["cwd"],
        "Driver session metadata drift",
    )
    contexts = []
    for line in rows[1:]:
        record = decode(line)
        # Screenshots can exceed the identity-record limit. The complete
        # stream remains bounded by read_plain; identity records retain theirs.
        if record.get("type") == "turn_context":
            require(len(line) <= 1024 * 1024, "Native actor identity row bound")
            contexts.append(record["payload"])
    require(bool(contexts), "Native actor context absent")
    context = contexts[-1]
    result = {
        **actor,
        "model": context.get("model"),
        "effort": context.get("effort"),
        "approval_policy": context.get("approval_policy"),
        "sandbox": context.get("sandbox_policy", {}).get("type"),
    }
    require(context.get("cwd") == actor["cwd"], "Latest actor cwd drift")
    return result
