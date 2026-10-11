"""Fast repository checks; no app build and no automatic fixes."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def repository_files(*patterns, root=ROOT):
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *patterns],
        cwd=root,
    ).decode()
    return sorted({name for name in output.split("\0") if name and (root / name).is_file()})


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


def documentation_errors(root=ROOT):
    errors = []
    required = {
        "README.md",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "docs/README.md",
        "docs/ARCHITECTURE.md",
    }
    for name in sorted(required):
        if not (root / name).is_file():
            errors.append(f"Missing {name}")
    for name in repository_files("*.md", root=root):
        path = root / name
        text = path.read_text()
        for target in re.findall(r"\]\(([^\s)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            if not (path.parent / target).exists():
                errors.append(f"{name}: missing link target {target}")
    if (root / "AGENTS.md").is_file() and (root / "AGENTS.md").stat().st_size > 32 * 1024:
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
    files = [name for name in repository_files("src", "tests", "scripts") if name.endswith(".py")]
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
