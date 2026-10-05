"""Pure Conventional Commit release policy; no network or repository mutations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence
import re


_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_HEADER = re.compile(r"(?P<type>[A-Za-z][A-Za-z0-9-]*)(?:\((?P<scope>[^()\r\n]+)\))?(?P<breaking>!)?: (?P<summary>\S.*)")
_BREAKING = re.compile(r"^BREAKING(?: CHANGE|-CHANGE):\s+\S", re.MULTILINE)
_ORDER = {"": 0, "patch": 1, "minor": 2, "major": 3}


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError("Expected a stable three-part version")
    return tuple(map(int, value.split(".")))


def bump_version(version: str, bump: str) -> str:
    major, minor, patch = version_tuple(version)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError("Unknown release bump")


@dataclass(frozen=True)
class Commit:
    sha: str
    message: str

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message.splitlines() else ""

    @property
    def kind(self) -> str:
        header = _HEADER.fullmatch(self.subject)
        return header["type"].lower() if header else ""

    @property
    def bump(self) -> str:
        header = _HEADER.fullmatch(self.subject)
        if (header and header["breaking"]) or _BREAKING.search(self.message):
            return "major"
        return {"feat": "minor", "fix": "patch", "perf": "patch"}.get(self.kind, "")


@dataclass(frozen=True)
class ReleasePoint:
    version: str
    sha: str


@dataclass(frozen=True)
class ReleasePlan:
    required: bool
    version: str
    reason: str
    previous_version: str = ""
    previous_sha: str = ""
    bump: str = ""
    published: bool = False
    commits: tuple[Commit, ...] = ()


def plan_release(head: str, published: Sequence[ReleasePoint],
                 reservations: Mapping[str, Optional[str]],
                 commits_since: Callable[[str, str], Sequence[Commit]],
                 is_ancestor: Callable[[str, str], bool], *,
                 initial_version: str = "0.0.0") -> ReleasePlan:
    """Select the highest bump since the newest published ancestral release.

    Stable tags and in-flight migration versions reserve numbers even before
    publication. They are a version floor, never a commit-history baseline.
    Breaking changes bump major even below 1.0. A published source is immutable.
    """
    version_tuple(initial_version)
    for point in published:
        version_tuple(point.version)
    for version in reservations:
        version_tuple(version)
    exact = [point for point in published if point.sha == head]
    if exact:
        point = max(exact, key=lambda item: version_tuple(item.version))
        return ReleasePlan(False, point.version, "Source commit already has a published release", published=True)
    baseline = max(published, key=lambda item: version_tuple(item.version)) if published else None
    if baseline and not is_ancestor(baseline.sha, head):
        if is_ancestor(head, baseline.sha):
            return ReleasePlan(False, "", "A newer source commit is already released",
                               baseline.version, baseline.sha)
        raise RuntimeError("Newest published release is not in this source commit's ancestry")
    base_version = baseline.version if baseline else initial_version
    base_sha = baseline.sha if baseline else ""
    commits = tuple(commits_since(base_sha, head))
    bump = max((commit.bump for commit in commits), key=lambda value: _ORDER[value], default="")
    if not bump:
        return ReleasePlan(False, "", "No feat, fix, perf, or breaking change since the published baseline",
                           base_version, base_sha, commits=commits)
    candidate = bump_version(base_version, bump)
    # Resume a tag already assigned to this exact source after an interrupted publish.
    floor = max(reservations, key=version_tuple) if reservations else ""
    assigned = [version for version, sha in reservations.items()
                if sha == head and version_tuple(version) >= version_tuple(candidate)
                and (not floor or version_tuple(version) >= version_tuple(floor))]
    if assigned:
        candidate = max(assigned, key=version_tuple)
    elif reservations:
        if version_tuple(candidate) <= version_tuple(floor):
            candidate = bump_version(floor, "patch")
    return ReleasePlan(True, candidate, "Conventional Commit " + bump + " release",
                       base_version, base_sha, bump, commits=commits)


def release_notes(plan: ReleasePlan, repository: str, head: str, run_id: str) -> str:
    """Generate readable notes from the exact released commit range."""
    if not plan.required:
        raise ValueError("Skipped releases have no release notes")
    def escape(text: str) -> str:
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", text)

    sections: dict[str, list[Commit]] = {
        "Breaking changes": [], "Features": [], "Fixes": [],
        "Performance": [], "Other changes": [],
    }
    for commit in plan.commits:
        section = ("Breaking changes" if commit.bump == "major" else
                   {"feat": "Features", "fix": "Fixes", "perf": "Performance"}.get(commit.kind, "Other changes"))
        sections[section].append(commit)
    lines = [f"Signed and notarized Apple Silicon release **v{plan.version}**.", ""]
    lines.extend([
        f"[**Download the Mac installer (.dmg)**](https://github.com/{repository}/releases/download/v{plan.version}/Definitive-SMR-Launcher-Apple-Silicon-v{plan.version}-arm64.dmg)", "",
        "Open the disk image, drag the launcher to Applications, then open it from Applications.",
        "The ZIP asset is used by the built-in updater.", "",
    ])
    for title, commits in sections.items():
        if not commits:
            continue
        lines.extend(["## " + title, ""])
        for commit in commits:
            lines.append(f"- {escape(commit.subject)} ([{commit.sha[:7]}](https://github.com/{repository}/commit/{commit.sha}))")
            if commit.bump == "major":
                for line in commit.message.splitlines()[1:]:
                    if _BREAKING.match(line):
                        lines.append("  - " + escape(line))
        lines.append("")
    if plan.previous_sha:
        lines.extend([f"[Full changes](https://github.com/{repository}/compare/{plan.previous_sha}...{head})", ""])
    lines.extend([
        f"Source commit: `{head}`.", "",
        f"[Build and verification](https://github.com/{repository}/actions/runs/{run_id}) · "
        f"[Installation instructions](https://github.com/{repository}/blob/{head}/MACOS_README.md).", "",
        "The release version is stamped into the CI build; the tag preserves the exact source commit.", "",
    ])
    return "\n".join(lines)
