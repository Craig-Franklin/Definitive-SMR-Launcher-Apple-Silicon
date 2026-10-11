"""Read-only session preflight; never launch or mutate an Orca workspace."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


class SessionError(RuntimeError):
    """The advertised Orca caller cannot be verified."""


def check_session(env=None, cwd=None, run=subprocess.run):
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else Path(cwd)
    signals = ("ORCA_TERMINAL_HANDLE", "ORCA_WORKTREE_ID", "ORCA_WORKSPACE_ID", "ORCA_AGENT_LAUNCH")
    if not any(env.get(key) for key in signals):
        return {"host": "standard", "verified": True, "detail": "No Orca session advertised."}
    caller = env.get("ORCA_TERMINAL_HANDLE")
    if not caller:
        raise SessionError("Orca session advertised without a caller terminal handle.")
    cli = env.get("ORCA_CLI_COMMAND") or ("orca-dev" if env.get("ORCA_DEV_REPO_ROOT") else "orca")

    def read(*args):
        try:
            result = run(
                [cli, *args, "--json"], cwd=cwd, capture_output=True, text=True, timeout=15
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SessionError(f"Cannot query the selected Orca CLI: {exc}") from exc
        if result.returncode:
            raise SessionError(
                result.stderr.strip() or result.stdout.strip() or "Orca query failed."
            )
        try:
            payload = json.loads(result.stdout)
            if payload.get("ok") is not True:
                raise SessionError(
                    f"Orca refused the query: {payload.get('error', 'unknown error')}"
                )
            return payload["result"]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SessionError("Orca returned an invalid identity response.") from exc

    try:
        worktree = read("worktree", "current")["worktree"]
        terminal = read("terminal", "show", "--terminal", caller)["terminal"]
        worktree_id = worktree["id"]
        path = worktree["path"]
        identities = (worktree_id, terminal["handle"], terminal["worktreeId"])
        if not all(isinstance(value, str) and value.strip() for value in identities):
            raise SessionError("Orca returned an empty or invalid workspace identity.")
        if not isinstance(path, str) or not path.strip() or not Path(path).is_absolute():
            raise SessionError("Orca returned an empty or non-absolute workspace path.")
        if not cwd.resolve().is_relative_to(Path(path).resolve()):
            raise SessionError("Orca workspace does not contain the current directory.")
        # Current managed terminals advertise both addresses as the live worktree ID.
        # Refuse conflicting or unsupported address namespaces instead of guessing.
        for key in ("ORCA_WORKTREE_ID", "ORCA_WORKSPACE_ID"):
            if env.get(key) and env[key] != worktree_id:
                raise SessionError(f"Advertised {key} differs from the live workspace.")
        if terminal["handle"] != caller or terminal["worktreeId"] != worktree_id:
            raise SessionError("Orca caller terminal belongs to a different workspace.")
        if terminal.get("orphaned") is not False or terminal.get("connected") is not True:
            raise SessionError("Orca caller is orphaned, disconnected or unverifiable.")
        if terminal.get("writable") is not True:
            raise SessionError("Orca caller is not writable; hold Orca-dependent operations.")
    except (KeyError, TypeError, AttributeError) as exc:
        raise SessionError("Orca response lacks the required caller/workspace identity.") from exc
    return {
        "host": "orca",
        "verified": True,
        "cli": cli,
        "detail": "Caller and workspace verified.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print the machine-readable result.")
    args = parser.parse_args()
    try:
        result = check_session()
    except SessionError as exc:
        print(json.dumps({"host": "orca", "verified": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result) if args.json else f"{result['host']}: {result['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
