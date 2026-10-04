"""Version and publish one notarized Mac release for an exact main-branch run."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

REPO = "Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon"
REPO_ID = 1404818508
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def version_tuple(value: str) -> tuple[int, int, int]:
    if not VERSION.fullmatch(value):
        raise ValueError("Expected a stable three-part version")
    return tuple(map(int, value.split(".")))


def release_version(base: str, run_number: str) -> str:
    major, minor, patch = version_tuple(base)
    if patch != 0:
        raise ValueError("Source release series must have patch zero")
    if not re.fullmatch(r"[1-9]\d*", run_number):
        raise ValueError("Expected a positive GitHub workflow run number")
    return f"{major}.{minor}.{int(run_number)}"


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


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
    expected_url = f"https://github.com/{REPO}.git"
    if output("git", "remote") != "origin":
        raise RuntimeError("Unexpected CI checkout remotes")
    for flags in ((), ("--push",)):
        if output("git", "remote", "get-url", *flags, "origin") != expected_url:
            raise RuntimeError("Unexpected CI checkout origin")
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


def validate_assets(release: dict, version: str, digest: str | None = None) -> None:
    name = asset_name(version)
    assets = release["assets"]
    if sorted(a["name"] for a in assets) != sorted([name, name + ".sha256"]):
        raise RuntimeError("Release assets are incomplete or unexpected")
    for item in assets:
        if item["state"] != "uploaded" or item["size"] <= 0:
            raise RuntimeError("Release upload is incomplete")
    archive = next(a for a in assets if a["name"] == name)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", archive.get("digest") or ""):
        raise RuntimeError("Release archive has no GitHub SHA-256 identity")
    if digest is not None and archive["digest"] != "sha256:" + digest:
        raise RuntimeError("Uploaded archive differs from notarized local archive")


def validate_checksum(version: str, release: dict) -> None:
    name = asset_name(version)
    checksum = output("gh", "release", "download", "v" + version, "--repo", REPO,
                      "--pattern", name + ".sha256", "--output", "-")
    digest = next(a["digest"][7:] for a in release["assets"] if a["name"] == name)
    if checksum != f"{digest}  {name}":
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


def prepare() -> None:
    sha = guard()
    base = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    version = release_version(base, os.environ["GITHUB_RUN_NUMBER"])
    tag = "v" + version
    target = tag_target(tag)
    if target is not None and target != sha:
        raise RuntimeError("Version tag already belongs to another source commit")
    release = api("releases/tags/" + tag, optional=True)
    published = release is not None and release["draft"] is False
    if published:
        if target != sha or release["prerelease"]:
            raise RuntimeError("Existing release identity differs")
        validate_assets(release, version)
        validate_checksum(version, release)
    else:
        latest = api("releases/latest", optional=True)
        if latest is not None and version_tuple(version) <= version_tuple(latest["tag_name"].removeprefix("v")):
            latest_sha = tag_target(latest["tag_name"])
            # A delayed older run is valid; a newer source must advance the series.
            if latest_sha is None or subprocess.run(
                    ["git", "merge-base", "--is-ancestor", sha, latest_sha],
                    capture_output=True).returncode:
                raise RuntimeError("New source would regress the release version; raise its major/minor series")
        stamp_version(Path.cwd(), version)
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(f"version={version}\ntag={tag}\npublished={str(published).lower()}\n")
    print(f"{tag}: " + ("already published; preserving existing artifacts" if published else "ready to build"))


def publish() -> None:
    sha = guard()
    tag = os.environ["SMR_TAG"]
    version = tag.removeprefix("v")
    if tag != "v" + version:
        raise RuntimeError("Invalid release tag")
    name = asset_name(version)
    digest = hashlib.sha256(Path(name).read_bytes()).hexdigest()
    if Path(name + ".sha256").read_text().strip() != f"{digest}  {name}":
        raise RuntimeError("Release checksum differs from the local archive")
    target = tag_target(tag)
    if target is None:
        guard()
        subprocess.run(["gh", "api", f"repos/{REPO}/git/refs", "--method", "POST",
                        "-f", "ref=refs/tags/" + tag, "-f", "sha=" + sha], check=True,
                       stdout=subprocess.DEVNULL)
    elif target != sha:
        raise RuntimeError("Existing tag belongs to another source commit")
    release = api("releases/tags/" + tag, optional=True)
    if release is not None and not release["draft"]:
        raise RuntimeError("Refusing to replace an already published release")
    guard()
    if release is None:
        notes = Path(os.environ["RUNNER_TEMP"]) / "smr-release-notes.md"
        notes.write_text(f"Signed and notarized Apple Silicon build from source commit `{sha}`.\n\n"
                         f"[Build and verification](https://github.com/{REPO}/actions/runs/"
                         f"{os.environ['GITHUB_RUN_ID']}) · "
                         f"[Installation instructions](https://github.com/{REPO}/blob/{sha}/MACOS_README.md).\n\n"
                         f"Release version `{version}` is stamped into the CI build; the tag preserves the exact source commit.\n")
        subprocess.run(["gh", "release", "create", tag, name, name + ".sha256",
                        "--repo", REPO, "--verify-tag", "--draft", "--title", tag,
                        "--notes-file", str(notes), "--generate-notes"], check=True)
    else:
        # Only retry this run's private draft; published artifacts are immutable.
        subprocess.run(["gh", "release", "upload", tag, name, name + ".sha256",
                        "--repo", REPO, "--clobber"], check=True)
    draft = api("releases/tags/" + tag)
    validate_assets(draft, version, digest)
    validate_checksum(version, draft)
    latest = api("releases/latest", optional=True)
    make_latest = latest is None or version_tuple(version) > version_tuple(latest["tag_name"].removeprefix("v"))
    guard()
    subprocess.run(["gh", "release", "edit", tag, "--repo", REPO, "--draft=false",
                    "--prerelease=false", "--latest=" + str(make_latest).lower()], check=True)
    published = api("releases/tags/" + tag)
    if published["draft"] or published["prerelease"] or tag_target(tag) != sha:
        raise RuntimeError("Published release identity failed readback")
    validate_assets(published, version, digest)
    print(f"Published {tag} from {sha}; latest={make_latest}")


if __name__ == "__main__":
    {"prepare": prepare, "publish": publish}[sys.argv[1]]()
