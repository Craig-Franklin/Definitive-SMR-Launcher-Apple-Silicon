"""Version and publish one notarized Mac release for an exact main-branch run."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

from release_policy import Commit, ReleasePoint, plan_release, release_notes

REPO = "Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon"
REPO_ID = 1404818508
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def version_tuple(value: str) -> tuple[int, int, int]:
    if not VERSION.fullmatch(value):
        raise ValueError("Expected a stable three-part version")
    return tuple(map(int, value.split(".")))


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def validate_origin(url: str) -> None:
    if url not in (f"https://github.com/{REPO}", f"https://github.com/{REPO}.git"):
        raise RuntimeError("Unexpected CI checkout origin")


def api(path: str, *, optional: bool = False):
    result = subprocess.run(["gh", "api", f"repos/{REPO}/{path}".rstrip("/")],
                            capture_output=True, text=True)
    if result.returncode:
        if optional and "HTTP 404" in result.stderr:
            return None
        raise RuntimeError("GitHub read failed: " + path)
    return json.loads(result.stdout)


def guard() -> str:
    """Recheck identity, source and remotes before each GitHub mutation."""
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_EVENT_NAME") != "push"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise RuntimeError("Releases require a main push in the personal fork")
    if output("git", "remote") != "origin":
        raise RuntimeError("Unexpected CI checkout remotes")
    for flags in ((), ("--push",)):
        validate_origin(output("git", "remote", "get-url", *flags, "origin"))
    repo = api("")
    if (repo["id"], repo["full_name"], repo["html_url"],
            repo["owner"]["login"], repo["owner"]["type"], repo["parent"]["id"]) != (
            REPO_ID, REPO, f"https://github.com/{REPO}", "Craig-Franklin", "User", 950948787):
        raise RuntimeError("Repository identity differs from the personal fork")
    sha = os.environ["GITHUB_SHA"]
    if output("git", "rev-parse", "HEAD") != sha:
        raise RuntimeError("Checkout differs from the triggering commit")
    subprocess.run(["git", "fetch", "origin", "main"], check=True)
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=True)
    commit = api("commits/" + sha)
    if commit["sha"] != sha or commit["commit"]["verification"]["verified"] is not True:
        raise RuntimeError("Source commit is not GitHub Verified")
    return sha


def tag_target(tag: str):
    ref = api("git/ref/tags/" + tag, optional=True)
    if ref is None:
        return None
    obj = ref["object"]
    for _ in range(5):
        if obj["type"] == "commit":
            return obj["sha"]
        if obj["type"] != "tag":
            break
        obj = api("git/tags/" + obj["sha"])["object"]
    raise RuntimeError("Release tag does not resolve to a commit")


def asset_name(version: str) -> str:
    version_tuple(version)
    return f"Definitive-SMR-Launcher-Apple-Silicon-v{version}-arm64.zip"


def release_asset_names(version: str, *, include_dmg: bool = True) -> list[str]:
    zip_name = asset_name(version)
    archives = [zip_name, zip_name.removesuffix(".zip") + ".dmg"] if include_dmg else [zip_name]
    return [name for archive in archives for name in (archive, archive + ".sha256")]


def validate_assets(release: dict, version: str, digests: dict[str, str] | None = None,
                    *, require_dmg: bool = False) -> None:
    assets = release["assets"]
    names = sorted(a["name"] for a in assets)
    has_dmg = any(name.endswith(".dmg") for name in names)
    if require_dmg and not has_dmg:
        raise RuntimeError("Release disk image is missing")
    if names != sorted(release_asset_names(version, include_dmg=has_dmg)):
        raise RuntimeError("Release assets are incomplete or unexpected")
    for item in assets:
        if item["state"] != "uploaded" or item["size"] <= 0:
            raise RuntimeError("Release upload is incomplete")
        if item["name"].endswith((".zip", ".dmg")):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("digest") or ""):
                raise RuntimeError("Release archive has no GitHub SHA-256 identity")
            if digests is not None and item["digest"] != "sha256:" + digests[item["name"]]:
                raise RuntimeError("Uploaded archive differs from notarized local archive")


def validate_checksum(version: str, release: dict) -> None:
    for asset in release["assets"]:
        name = asset["name"]
        if not name.endswith((".zip", ".dmg")):
            continue
        checksum = output("gh", "release", "download", "v" + version, "--repo", REPO,
                          "--pattern", name + ".sha256", "--output", "-")
        if checksum != f"{asset['digest'][7:]}  {name}":
            raise RuntimeError("Published checksum differs from GitHub archive identity")


def stamp_version(root: Path, version: str) -> None:
    version_tuple(version)
    for filename, pattern, replacement in (
        ("pyproject.toml", r'^version = "[^"]+"$', f'version = "{version}"'),
        ("src/smr_launcher/__init__.py", r'^APP_VERSION = "[^"]+"$', f'APP_VERSION = "{version}"'),
    ):
        path = root / filename
        text, count = re.subn(pattern, replacement, path.read_text(), flags=re.MULTILINE)
        if count != 1:
            raise RuntimeError("Cannot uniquely stamp " + filename)
        path.write_text(text)


def _stable_version(tag: str) -> str | None:
    if isinstance(tag, str) and tag.startswith("v") and VERSION.fullmatch(tag[1:]):
        return tag[1:]
    return None


def _api_pages(path: str) -> list:
    result = []
    for page in range(1, 101):
        values = api(f"{path}?per_page=100&page={page}")
        if not isinstance(values, list):
            raise RuntimeError("Expected a paginated GitHub list")
        result.extend(values)
        if len(values) < 100:
            return result
    raise RuntimeError("Release history exceeds the bounded lookup limit")


def release_for_tag(tag: str, *, optional: bool = False) -> dict | None:
    """Find drafts as well as published releases without treating draft 404 as absence.

    GitHub's tag endpoint can omit drafts even with write authorization. The
    authenticated release list includes them; fetch the exact matching ID for
    current asset details. Required post-write reads tolerate brief propagation.
    """
    if _stable_version(tag) is None:
        raise RuntimeError("Release lookup requires a stable version tag")
    for attempt in range(1 if optional else 3):
        release = api("releases/tags/" + tag, optional=True)
        if release is None:
            matches = [item for item in _api_pages("releases") if item.get("tag_name") == tag]
            if len(matches) > 1:
                raise RuntimeError("Multiple releases claim the same tag")
            if matches:
                release_id = matches[0].get("id")
                if type(release_id) is not int or release_id <= 0:
                    raise RuntimeError("Release lookup returned an invalid ID")
                release = api("releases/" + str(release_id), optional=True)
        if release is not None:
            if release.get("tag_name") != tag:
                raise RuntimeError("Release ID lookup returned another tag")
            return release
        if not optional and attempt < 2:
            time.sleep(attempt + 1)
    if optional:
        return None
    raise RuntimeError("Expected release is not readable after bounded retries")


def release_history() -> tuple[list[ReleasePoint], dict[str, str | None]]:
    """Published releases establish history; every stable tag/draft reserves a number."""
    reservations: dict[str, str | None] = {}
    for item in _api_pages("tags"):
        version = _stable_version(item.get("name"))
        if version is not None:
            reservations[version] = tag_target(item["name"])
    published = []
    for item in _api_pages("releases"):
        version = _stable_version(item.get("tag_name"))
        if version is None:
            continue
        if version not in reservations:
            reservations[version] = tag_target(item["tag_name"])
        if item.get("draft") is False and item.get("prerelease") is False:
            target = reservations[version]
            if target is None:
                raise RuntimeError("Published release has no commit tag")
            published.append(ReleasePoint(version, target))
    # During migration, older workflows may be notarizing before creating tags.
    # Explicit reservations prevent the semantic workflow from reusing their numbers.
    legacy = os.environ.get("SMR_RESERVED_VERSIONS", "")
    if legacy:
        for version in legacy.split(","):
            version_tuple(version)
            reservations.setdefault(version, None)
    return published, reservations


def is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant],
                            capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError("Could not establish release commit ancestry")
    return result.returncode == 0


def commits_since(base: str, head: str) -> tuple[Commit, ...]:
    revision = base + ".." + head if base else head
    shas = output("git", "rev-list", "--reverse", revision, "--").splitlines()
    if len(shas) > 10000 or any(not re.fullmatch(r"[0-9a-f]{40}", sha) for sha in shas):
        raise RuntimeError("Release commit range exceeds its bound or contains invalid identities")
    return tuple(Commit(sha, output("git", "show", "-s", "--format=%B", sha, "--")) for sha in shas)


def _notes_path(sha: str) -> Path:
    return Path(os.environ["RUNNER_TEMP"]) / ("smr-release-notes-" + sha + ".md")


def prepare() -> None:
    sha = guard()
    subprocess.run(["git", "fetch", "origin", "--tags"], check=True)
    published, reservations = release_history()
    base = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    plan = plan_release(sha, published, reservations, commits_since, is_ancestor, initial_version=base)
    version = plan.version
    tag = "v" + version if version else ""
    notes_file = ""
    if plan.published:
        release = release_for_tag(tag)
        if tag_target(tag) != sha or release["draft"] or release["prerelease"]:
            raise RuntimeError("Existing release identity differs")
        validate_assets(release, version)
        validate_checksum(version, release)
    elif plan.required:
        target = tag_target(tag)
        if target is not None and target != sha:
            raise RuntimeError("Version tag already belongs to another source commit")
        latest = api("releases/latest", optional=True)
        if latest is not None and version_tuple(version) <= version_tuple(latest["tag_name"].removeprefix("v")):
            latest_sha = tag_target(latest["tag_name"])
            if latest_sha is None or not is_ancestor(sha, latest_sha):
                raise RuntimeError("New source would regress the published release version")
        notes = _notes_path(sha)
        notes.write_text(release_notes(plan, REPO, sha, os.environ["GITHUB_RUN_ID"]), encoding="utf-8")
        notes_file = str(notes)
        stamp_version(Path.cwd(), version)
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(f"release_required={str(plan.required).lower()}\n"
                     f"skip_reason={'' if plan.required else plan.reason}\n"
                     f"version={version}\ntag={tag}\npublished={str(plan.published).lower()}\n"
                     f"notes_file={notes_file}\n"
                     f"previous_tag={'v' + plan.previous_version if plan.previous_sha else ''}\n")
    print((tag + ": " if tag else "Release skipped: ") + plan.reason)


def publish() -> None:
    sha = guard()
    tag = os.environ["SMR_TAG"]
    version = tag.removeprefix("v")
    if tag != "v" + version:
        raise RuntimeError("Invalid release tag")
    names = release_asset_names(version)
    notes = _notes_path(sha)
    if not notes.is_file():
        raise RuntimeError("Prepared release notes are missing")
    digests = {}
    for name in names:
        if name.endswith(".sha256"):
            continue
        digest = hashlib.sha256(Path(name).read_bytes()).hexdigest()
        if Path(name + ".sha256").read_text().strip() != f"{digest}  {name}":
            raise RuntimeError("Release checksum differs from the local archive")
        digests[name] = digest
    target = tag_target(tag)
    if target is None:
        guard()
        subprocess.run(["gh", "api", f"repos/{REPO}/git/refs", "--method", "POST",
                        "-f", "ref=refs/tags/" + tag, "-f", "sha=" + sha], check=True,
                       stdout=subprocess.DEVNULL)
    elif target != sha:
        raise RuntimeError("Existing tag belongs to another source commit")
    release = release_for_tag(tag, optional=True)
    if release is not None and not release["draft"]:
        raise RuntimeError("Refusing to replace an already published release")
    guard()
    if release is None:
        subprocess.run(["gh", "release", "create", tag, *names,
                        "--repo", REPO, "--verify-tag", "--draft", "--title", tag,
                        "--notes-file", str(notes)], check=True)
    else:
        # Only retry this run's private draft; published artifacts are immutable.
        subprocess.run(["gh", "release", "upload", tag, *names,
                        "--repo", REPO, "--clobber"], check=True)
    draft = release_for_tag(tag)
    validate_assets(draft, version, digests, require_dmg=True)
    validate_checksum(version, draft)
    latest = api("releases/latest", optional=True)
    make_latest = latest is None or version_tuple(version) > version_tuple(latest["tag_name"].removeprefix("v"))
    guard()
    subprocess.run(["gh", "release", "edit", tag, "--repo", REPO, "--draft=false",
                    "--prerelease=false", "--latest=" + str(make_latest).lower()], check=True)
    published = release_for_tag(tag)
    if published["draft"] or published["prerelease"] or tag_target(tag) != sha:
        raise RuntimeError("Published release identity failed readback")
    validate_assets(published, version, digests, require_dmg=True)
    print(f"Published {tag} from {sha}; latest={make_latest}")


if __name__ == "__main__":
    {"prepare": prepare, "publish": publish}[sys.argv[1]]()
