"""Fast repository checks; no app build and no automatic fixes."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def baseline_errors(base, baseline, root=ROOT):
    """Allow the first baseline only for files already present in valid history."""

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE).decode()

    try:
        commit = git("rev-parse", "--verify", f"{base}^{{commit}}").strip()
        tree = set(git("ls-tree", "-r", "--name-only", commit).splitlines())
        if ".ruff-format-baseline" in tree:
            old = {
                line
                for line in git("show", f"{commit}:.ruff-format-baseline").splitlines()
                if line and not line.startswith("#")
            }
        else:
            # Bootstrap is restricted to existing legacy files, never new code.
            old = {
                name
                for name in tree
                if name.endswith(".py") and name.split("/")[0] in {"src", "tests", "scripts"}
            }
    except subprocess.CalledProcessError:
        return [f"Cannot verify formatting baseline against {base}; fetch valid history first."]
    return (
        ["New formatting exemptions are forbidden: " + ", ".join(sorted(baseline - old))]
        if baseline - old
        else []
    )


def documentation_errors():
    errors = []
    for name in ("AGENTS.md", "CONTRIBUTING.md", "docs/ARCHITECTURE.md"):
        path = ROOT / name
        if not path.is_file():
            errors.append(f"Missing {name}")
            continue
        text = path.read_text()
        for target in re.findall(r"\]\(([^\s)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            if not (path.parent / target).exists():
                errors.append(f"{name}: missing link target {target}")
    if (ROOT / "AGENTS.md").is_file() and (ROOT / "AGENTS.md").stat().st_size > 32 * 1024:
        errors.append("AGENTS.md exceeds the default Codex instruction discovery budget.")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docs", action="store_true", help="Only lightweight documentation checks."
    )
    parser.add_argument(
        "--base",
        default="HEAD",
        help="Compare formatting exemptions with this valid revision (default: HEAD).",
    )
    args = parser.parse_args()
    errors = documentation_errors()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if args.docs:
        print("Documentation checks passed; no app build needed.")
        return 0
    expected = next(
        line.split("==")[1]
        for line in (ROOT / "requirements-dev.txt").read_text().splitlines()
        if line.startswith("ruff==")
    )
    try:
        installed = version("ruff")
    except PackageNotFoundError:
        print(
            "Install requirements-dev.txt in your development environment first.", file=sys.stderr
        )
        return 1
    if installed != expected:
        print(f"Expected Ruff {expected}; found {installed}.", file=sys.stderr)
        return 1
    files = (
        subprocess.check_output(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                "src",
                "tests",
                "scripts",
            ],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    files = sorted({name for name in files if name.endswith(".py") and (ROOT / name).is_file()})
    baseline_path = ".ruff-format-baseline"
    baseline = {
        line
        for line in (ROOT / baseline_path).read_text().splitlines()
        if line and not line.startswith("#")
    }
    errors = baseline_errors(args.base, baseline)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    lint = subprocess.run([sys.executable, "-m", "ruff", "check", "--no-fix", *files], cwd=ROOT)
    adopted = [name for name in files if name not in baseline]
    formatted = (
        subprocess.run([sys.executable, "-m", "ruff", "format", "--check", *adopted], cwd=ROOT)
        if adopted
        else None
    )
    return int(lint.returncode != 0 or (formatted is not None and formatted.returncode != 0))


if __name__ == "__main__":
    raise SystemExit(main())
