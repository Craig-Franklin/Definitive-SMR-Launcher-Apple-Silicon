#!/usr/bin/env python3
"""Validate REA on original synthetic code. Never accepts a game target."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True, type=Path)
    args = parser.parse_args()
    requested_root = args.private_root.expanduser()
    root = requested_root.resolve()
    if not requested_root.is_absolute() or any(p.startswith(".") for p in root.parts):
        parser.error("Use an absolute directory without dot-prefixed components")
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    check = subprocess.run(
        ["git", "-C", str(ancestor), "rev-parse", "--show-toplevel"], capture_output=True
    )
    if check.returncode == 0:
        parser.error("Private output must be outside every Git worktree")
    os.umask(0o077)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    run = Path(tempfile.mkdtemp(prefix="synthetic-", dir=root))
    here = Path(__file__).resolve().parent
    rea = here / "node_modules/.bin/rea"
    if not rea.exists():
        parser.error("Install the pinned dependencies with npm ci first")
    env = dict(os.environ)
    for key in ("GHIDRA_INSTALL_DIR", "JAVA_HOME"):
        if not env.get(key):
            parser.error(key + " must identify an existing installation")
    env["REA_ANALYSIS_PROVIDER"] = "ghidra"
    env["GHIDRA_HEADLESS_MAXMEM"] = "512M"
    env["REA_GHIDRA_STARTUP_TIMEOUT_MS"] = "120000"
    temp = run / "tmp"
    temp.mkdir(mode=0o700)
    env["TMPDIR"] = str(temp)
    records = []

    def command(label, argv):
        started = time.monotonic()
        with (
            (run / (label + ".stdout")).open("wb") as out,
            (run / (label + ".stderr")).open("wb") as err,
        ):
            # REA owns provider timeouts/cleanup. No unsafe blanket process kill.
            result = subprocess.run(argv, stdout=out, stderr=err, env=env)
        records.append(
            {
                "step": label,
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        (run / "receipt.json").write_text(json.dumps(records, indent=2) + "\n")
        if result.returncode:
            raise SystemExit("Step failed: " + label + "; inspect private logs; do not auto-retry")
        return (run / (label + ".stdout")).read_text()

    doctor = json.loads(command("doctor", [str(rea), "doctor", "--provider", "ghidra", "--json"]))
    if doctor.get("healthy") is not True:
        raise SystemExit("Scoped Ghidra readiness did not pass")
    binary = run / "synthetic-x86_64"
    command(
        "compile",
        [
            "xcrun",
            "clang",
            "-arch",
            "x86_64",
            "-O0",
            "-g",
            str(here / "fixture.c"),
            "-o",
            str(binary),
        ],
    )
    before = hashlib.sha256(binary.read_bytes()).hexdigest()
    argv = [
        str(rea),
        "decompile",
        str(binary),
        "_bounded_index",
        "--provider",
        "ghidra",
        "--snapshot",
        str(run / "snapshot.json"),
        "--json",
    ]
    first = command("decompile", argv)
    second = command("replay", argv)
    # These are content-presence checks, not a proof of semantic equivalence.
    for label, value in (("decompile", first), ("replay", second)):
        evidence = json.loads(value)
        body = evidence.get("normalized_result")
        if (
            evidence.get("provider", {}).get("id") != "ghidra"
            or not isinstance(body, str)
            or "bounded_index" not in body
            or "return" not in body
        ):
            raise SystemExit(label + " lacks expected synthetic function content")
    if before != hashlib.sha256(binary.read_bytes()).hexdigest():
        raise SystemExit("Synthetic binary bytes changed")
    if not (run / "snapshot.json").is_file():
        raise SystemExit("Expected snapshot is missing")
    snapshot = json.loads((run / "snapshot.json").read_text())
    cache_entries = len(snapshot.get("entries", [])) + len(snapshot.get("workflow_entries", []))
    print(
        json.dumps(
            {
                "status": "synthetic_smoke_pass",
                "game_analyzed": False,
                "binary_unchanged": True,
                "snapshot_cache_entries": cache_entries,
                "cache_reuse_proven": False,
                "steps": records,
                "private_receipt": str(run / "receipt.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
