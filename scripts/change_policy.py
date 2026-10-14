"""Shared CI/release impact policy. Requires Python 3.12; never builds an app."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib


PACKAGING_INPUTS = {
    "requirements-build.txt",
    "APPLE_SILICON_LICENSE.md",
    "docs/UPSTREAM_ASSETS.md",
    "scripts/build_macos.sh",
    "scripts/build_dmg.sh",
    "scripts/create_macos_icon.sh",
    "scripts/dmg_settings.py",
    "scripts/verify_dmg.py",
    "scripts/launcher_entry.py",
    "scripts/backend_entry.py",
    "scripts/translate_macos.swift",
    "Definitive-SMR-Launcher/icon/icon.ico",
    "Definitive-SMR-Launcher/DSMRL_data/RRT_Difficulty.xml",
}


@dataclass(frozen=True)
class Impact:
    quality: bool = False
    tests: bool = False
    package: bool = False
    app: bool = False


def git(*args):
    return subprocess.check_output(["git", *args], text=True)


def build_metadata(revision, read=git):
    """Ignore only known development-tool sections; unknown metadata is conservative."""
    if not revision or not read("ls-tree", "--name-only", revision, "--", "pyproject.toml").strip():
        return None
    data = tomllib.loads(read("show", f"{revision}:pyproject.toml"))
    tools = data.get("tool", {})
    for name in ("ruff", "pytest"):
        tools.pop(name, None)
    if not tools:
        data.pop("tool", None)
    return data


def path_impact(path, *, metadata_changed=True):
    if (
        path in PACKAGING_INPUTS
        or (
            path.startswith("requirements")
            and path.endswith(".txt")
            and path != "requirements-dev.txt"
        )
        or (path == "pyproject.toml" and metadata_changed)
    ):
        return Impact(True, True, True, True)
    if path.startswith("src/"):
        return Impact(True, True, not path.endswith(".py"), True)
    if path == "pyproject.toml" or path in {"requirements-dev.txt", ".ruff-format-baseline"}:
        return Impact(quality=True)
    if Path(path).suffix.lower() in {".md", ".rst", ".txt"} or path in {
        ".gitignore",
        ".gitattributes",
        "LICENSE",
    }:
        return Impact()
    if path.startswith("Definitive-SMR-Launcher/"):
        # Only the explicitly listed icon/difficulty resource enters the Mac package.
        return Impact()
    if path.startswith(("tests/", "scripts/", ".github/")):
        return Impact(True, True)
    if path.startswith("docs/"):
        return Impact()
    # New build inputs must be classified explicitly before skipping verification.
    return Impact(True, True, True, True)


def changed_impact(base, head, read=git):
    """Include both sides of renames and deletions; missing history is an error."""
    read("rev-parse", "--verify", f"{head}^{{commit}}")
    if base:
        read("rev-parse", "--verify", f"{base}^{{commit}}")
        paths = read("diff", "--name-only", "--no-renames", "-z", base, head, "--")
    else:
        paths = read(
            "diff-tree",
            "--root",
            "--no-commit-id",
            "-r",
            "--name-only",
            "--no-renames",
            "-z",
            head,
            "--",
        )
    paths = [path for path in paths.split("\0") if path]
    metadata_changed = "pyproject.toml" in paths and build_metadata(base, read) != build_metadata(
        head, read
    )
    impacts = [path_impact(path, metadata_changed=metadata_changed) for path in paths]
    return Impact(
        *(any(getattr(item, field) for item in impacts) for field in Impact.__dataclass_fields__)
    )


def verification_plan(impact, release_required, build_requested=False):
    # A release at a docs-only HEAD still needs checks for that exact source revision.
    return {
        "quality": impact.quality or release_required or build_requested,
        "tests": impact.tests or impact.package or release_required or build_requested,
        "build": (impact.package or build_requested) and not release_required,
        "release": release_required,
    }


def gate_errors(plan, results):
    errors = []
    for name in ("quality", "tests", "build", "release"):
        if plan.get(name) not in {"true", "false"}:
            errors.append(f"Missing or invalid plan for {name}")
    if plan.get("tests") == "true" and plan.get("quality") != "true":
        errors.append("Code verification requires Python quality checks")
    if plan.get("build") == "true" and plan.get("release") == "true":
        errors.append("Release builds replace development builds")
    if (plan.get("build") == "true" or plan.get("release") == "true") and plan.get(
        "tests"
    ) != "true":
        errors.append("App builds and releases require the full test suite")
    for name in ("plan", "quality", "tests", "build"):
        required = name in {"plan", "quality"} or plan.get(name) == "true"
        expected = "success" if required else "skipped"
        if results.get(name) != expected:
            errors.append(f"{name}: expected {expected}, observed {results.get(name, 'missing')}")
    return errors


def event_range(event, event_name, head, read=git):
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("Invalid triggering commit")
    if read("rev-parse", "HEAD").strip() != head:
        raise ValueError("Checkout differs from the triggering commit")
    if event_name == "workflow_dispatch":
        # Compare against actual history so quality cannot self-approve new exemptions.
        base = read("rev-parse", "--verify", f"{head}^1").strip()
    elif event_name == "push":
        base = event["before"]
    elif event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
    else:
        raise ValueError("Unsupported verification event")
    if not re.fullmatch(r"[0-9a-f]{40}", base) or base == "0" * 40:
        raise ValueError("A valid existing comparison base is required")
    read("rev-parse", "--verify", f"{base}^{{commit}}")
    ancestor = read("merge-base", base, head).strip()
    if event_name == "push" and ancestor != base:
        raise ValueError("Push rewrote history; reconcile before verification")
    return ancestor, head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "gate"))
    args = parser.parse_args()
    if args.command == "gate":
        needs = json.loads(os.environ["SMR_NEEDS"])
        errors = gate_errors(
            needs["plan"].get("outputs", {}), {name: job["result"] for name, job in needs.items()}
        )
        if errors:
            raise RuntimeError("; ".join(errors))
        print("All required checks passed; only unneeded jobs were skipped.")
        return
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    base, head = event_range(event, os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_SHA"])
    release = (
        "false"
        if os.environ["GITHUB_EVENT_NAME"] == "pull_request"
        else os.environ.get("SMR_RELEASE_REQUIRED", "")
    )
    if release not in {"true", "false"}:
        raise ValueError("Release planning did not produce a valid decision")
    impact = changed_impact(base, head)
    messages = git("log", "--format=%B", base + ".." + head, "--")
    build_requested = bool(re.search(r"^Build-Verification: required$", messages, re.MULTILINE))
    plan = verification_plan(impact, release == "true", build_requested)
    values = {key: str(value).lower() for key, value in plan.items()}
    values["base"] = base
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
    summary = "\n".join(f"- {key}: {value}" for key, value in values.items())
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
        stream.write(
            "## Verification plan\n\n"
            + summary
            + "\n\nRelease builds replace duplicate development builds.\n"
        )
    print(summary)


if __name__ == "__main__":
    main()
