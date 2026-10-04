"""Personal-fork release checks and private, publisher-checked update staging."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import quote, urlparse
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unicodedata
import uuid
import zipfile

from .activation import _assert_no_symlink_ancestor, _atomic_json, _fsync_dir


OWNER = "Craig-Franklin"
REPOSITORY = "Definitive-SMR-Launcher-Apple-Silicon"
SLUG = OWNER + "/" + REPOSITORY
BUNDLE_NAME = "Definitive SMR Launcher Apple Silicon.app"
BUNDLE_ID = "com.craigfranklin.smrlauncher.applesilicon"
LATEST_URL = f"https://api.github.com/repos/{SLUG}/releases/latest"
MAX_METADATA_BYTES = 512 * 1024
MAX_ZIP_BYTES = 250 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_ZIP_MEMBERS = 10000
VERSION = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TEAM_ID = re.compile(r"^[A-Z0-9]{10}$")


class UpdateError(RuntimeError):
    """An update is unavailable or failed publisher and integrity checks."""


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise UpdateError("Release version is not a stable version tag")
    return tuple(int(part) for part in value.lstrip("v").split("."))


@dataclass(frozen=True)
class Release:
    version: str
    asset_name: str
    asset_url: str
    sha256: str
    size: int
    page_url: str


def parse_release(payload: bytes, current_version: str) -> Optional[Release]:
    if len(payload) > MAX_METADATA_BYTES:
        raise UpdateError("Release metadata exceeds its size limit")
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise UpdateError("Release metadata is invalid") from exc
    if not isinstance(value, dict) or value.get("draft") is not False or value.get("prerelease") is not False:
        raise UpdateError("Latest release is not a published stable version")
    tag = value.get("tag_name")
    remote_version = version_tuple(tag)
    if remote_version <= version_tuple(current_version):
        return None
    version = tag.lstrip("v")
    asset_name = f"{REPOSITORY}-v{version}-arm64.zip"
    page_url = f"https://github.com/{SLUG}/releases/tag/v{version}"
    if value.get("html_url") != page_url:
        raise UpdateError("Release page is outside the personal fork")
    assets = value.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("Release has no asset list")
    matches = [item for item in assets if isinstance(item, dict) and item.get("name") == asset_name]
    if len(matches) != 1:
        raise UpdateError("Release has no unique Apple Silicon app archive")
    asset = matches[0]
    expected_url = f"https://github.com/{SLUG}/releases/download/v{version}/{quote(asset_name)}"
    if asset.get("browser_download_url") != expected_url or asset.get("state") != "uploaded":
        raise UpdateError("Release archive URL is not the expected personal-fork asset")
    digest = asset.get("digest")
    size = asset.get("size")
    if (not isinstance(digest, str) or not digest.startswith("sha256:")
            or not SHA256.fullmatch(digest[7:]) or type(size) is not int
            or not 0 < size <= MAX_ZIP_BYTES):
        raise UpdateError("Release archive has no bounded SHA-256 identity")
    return Release(version, asset_name, expected_url, digest[7:], size, page_url)


class _ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, newurl):
        parsed = urlparse(newurl)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or host not in (
            "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"
        ):
            raise UpdateError("Release download redirected outside GitHub HTTPS")
        return super().redirect_request(request, file_pointer, code, message, headers, newurl)


def _opener():
    return build_opener(_ReleaseRedirects())


def fetch_latest_release(current_version: str) -> Optional[Release]:
    request = Request(LATEST_URL, headers={"Accept": "application/vnd.github+json",
                                            "User-Agent": "Definitive-SMR-Launcher-Apple-Silicon"})
    try:
        with _opener().open(request, timeout=15) as response:
            payload = response.read(MAX_METADATA_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise UpdateError("Could not check the personal fork's latest release") from exc
    except OSError as exc:
        raise UpdateError("Could not check the personal fork's latest release") from exc
    return parse_release(payload, current_version)


class UpdatePreferences:
    def __init__(self, library: Path) -> None:
        self.path = Path(library) / "update-preferences.json"

    def automatic(self) -> bool:
        _assert_no_symlink_ancestor(self.path)
        if not self.path.exists():
            return False
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UpdateError("Update preferences need inspection") from exc
        if not isinstance(value, dict) or value.get("schema") != 1 or type(value.get("automatic")) is not bool:
            raise UpdateError("Update preferences have an unexpected format")
        return value["automatic"]

    def set_automatic(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise TypeError("Automatic update setting must be Boolean")
        _assert_no_symlink_ancestor(self.path)
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        _atomic_json(self.path, dict(schema=1, automatic=enabled))


def download_release(release: Release, private_directory: Path) -> Path:
    directory = Path(private_directory).expanduser()
    _assert_no_symlink_ancestor(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    target = directory / (release.sha256 + ".zip")
    _assert_no_symlink_ancestor(target)
    if target.exists():
        if target.is_file() and target.stat().st_size == release.size and _sha256(target) == release.sha256:
            return target
        raise UpdateError("Cached release archive differs from published identity")
    fd, temporary = tempfile.mkstemp(prefix=".release-download-", dir=directory)
    stage = Path(temporary)
    try:
        request = Request(release.asset_url, headers={"User-Agent": "Definitive-SMR-Launcher-Apple-Silicon"})
        count = 0
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as output, _opener().open(request, timeout=60) as response:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                count += len(chunk)
                if count > release.size:
                    raise UpdateError("Release archive exceeds its declared size")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if count != release.size or digest.hexdigest() != release.sha256:
            raise UpdateError("Release archive failed SHA-256 or size verification")
        stage.chmod(0o400)
        try:
            os.link(stage, target)
        except FileExistsError:
            if target.stat().st_size != release.size or _sha256(target) != release.sha256:
                raise UpdateError("Concurrent release download differs from published identity")
        _fsync_dir(directory)
        return target
    finally:
        stage.unlink(missing_ok=True)
        _fsync_dir(directory)


def _sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            result.update(chunk)
    return result.hexdigest()


def inspect_app_zip(path: Path) -> None:
    """Check paths and bounded expansion before passing a signed ZIP to ditto."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if not 0 < len(members) <= MAX_ZIP_MEMBERS:
                raise UpdateError("Release archive file count is outside the limit")
            total = 0
            seen = set()
            links: dict[str, str] = {}
            for member in members:
                name = member.filename
                raw = name[:-1] if name.endswith("/") else name
                pieces = tuple(raw.split("/"))
                if (not pieces or pieces[0] != BUNDLE_NAME or name.startswith("/")
                        or "\\" in name or any(part in ("", ".", "..") for part in pieces)
                        or len(name) > 1024 or any(len(part) > 255 for part in pieces)
                        or unicodedata.normalize("NFC", name) != name
                        or any(ord(char) < 32 or ord(char) == 127 for char in name)):
                    raise UpdateError("Release archive contains an unsafe path")
                key = name.rstrip("/").casefold()
                if key in seen:
                    raise UpdateError("Release archive contains duplicate paths")
                seen.add(key)
                total += member.file_size
                if total > MAX_EXPANDED_BYTES:
                    raise UpdateError("Release archive expansion exceeds its limit")
                mode = (member.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK):
                    raise UpdateError("Release archive contains a special file")
                if kind == stat.S_IFLNK:
                    if member.file_size > 4096:
                        raise UpdateError("Release archive contains an oversized link")
                    try:
                        target = archive.read(member).decode("utf-8")
                    except (UnicodeError, RuntimeError) as exc:
                        raise UpdateError("Release archive link is unreadable") from exc
                    if (not target or target.startswith("/") or "\\" in target
                            or unicodedata.normalize("NFC", target) != target
                            or any(ord(char) < 32 or ord(char) == 127 for char in target)):
                        raise UpdateError("Release archive link escapes the app")
                    links[key] = target
            for key in seen:
                parts = key.split("/")
                if any("/".join(parts[:end]) in links for end in range(1, len(parts))):
                    raise UpdateError("Release archive writes through a link")
            for key, target in links.items():
                stack = []
                remaining = list(key.split("/")[:-1]) + list(PurePosixPath(target).parts)
                hops = 0
                while remaining:
                    part = remaining.pop(0)
                    if part == "..":
                        if len(stack) <= 1:
                            raise UpdateError("Release archive link escapes the app")
                        stack.pop()
                    elif part != ".":
                        stack.append(part)
                        linked = "/".join(stack).casefold()
                        if linked in links:
                            hops += 1
                            if hops > 32:
                                raise UpdateError("Release archive link graph has a cycle")
                            stack.pop()
                            remaining = list(PurePosixPath(links[linked]).parts) + remaining
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError("Release archive cannot be inspected") from exc


def _signature_details(bundle: Path) -> tuple[str, str]:
    checked = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
                             capture_output=True, text=True, timeout=60)
    if checked.returncode:
        raise UpdateError("App code signature is invalid")
    details = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=4", str(bundle)],
                             capture_output=True, text=True, timeout=30)
    output = details.stdout + details.stderr
    team = re.search(r"^TeamIdentifier=(.+)$", output, re.MULTILINE)
    identifier = re.search(r"^Identifier=(.+)$", output, re.MULTILINE)
    if details.returncode or not team or not identifier:
        raise UpdateError("App has no readable Developer ID identity")
    return team.group(1), identifier.group(1)


def verify_publisher(bundle: Path, expected_team: str, expected_version: str,
                     *, require_name: bool = True) -> None:
    if not TEAM_ID.fullmatch(expected_team):
        raise UpdateError("Trusted Developer ID team is not configured")
    bundle = Path(bundle)
    _assert_no_symlink_ancestor(bundle)
    if not bundle.is_dir() or (require_name and bundle.name != BUNDLE_NAME):
        raise UpdateError("Update is not the expected app bundle")
    team, identifier = _signature_details(bundle)
    if team != expected_team or identifier != BUNDLE_ID:
        raise UpdateError("Update is signed by another publisher or app")
    requirement = f'anchor apple generic and certificate leaf[subject.OU] = "{expected_team}" and identifier "{BUNDLE_ID}"'
    result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict",
                             "-R=" + requirement, str(bundle)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise UpdateError("Update does not satisfy the trusted Apple signing requirement")
    result = subprocess.run(["/usr/sbin/spctl", "--assess", "--type", "execute",
                             "--verbose=4", str(bundle)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode or "source=Notarized Developer ID" not in result.stdout + result.stderr:
        raise UpdateError("macOS did not accept the signed and notarized app")
    with (bundle / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if (info.get("CFBundleIdentifier") != BUNDLE_ID
            or info.get("CFBundleShortVersionString") != expected_version):
        raise UpdateError("Signed app identity or version differs from the release")
    executable = bundle / "Contents/MacOS/Definitive SMR Launcher Apple Silicon"
    with executable.open("rb") as stream:
        header = stream.read(8)
    if len(header) != 8 or struct.unpack("<II", header) != (0xFEEDFACF, 0x0100000C):
        raise UpdateError("Release app has no Apple Silicon executable")


def stage_release(zip_path: Path, release: Release, library: Path,
                  trusted_team: str) -> Path:
    """Extract and authenticate an app without touching the installed app or maps."""
    if not TEAM_ID.fullmatch(trusted_team):
        raise UpdateError("Automatic installation requires a trusted Developer ID build")
    if zip_path.stat().st_size != release.size or _sha256(zip_path) != release.sha256:
        raise UpdateError("Release archive changed before extraction")
    inspect_app_zip(zip_path)
    directory = Path(library) / "updates"
    _assert_no_symlink_ancestor(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".app-stage-", dir=directory))
    try:
        result = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zip_path), str(stage)],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise UpdateError("macOS could not unpack the release app")
        bundle = stage / BUNDLE_NAME
        verify_publisher(bundle, trusted_team, release.version)
        return bundle
    except Exception:
        shutil.rmtree(stage)
        raise


def trusted_team_from_bundle() -> str:
    """Read the policy sealed inside this signed app; development builds have none."""
    if not getattr(sys, "frozen", False) or not hasattr(sys, "_MEIPASS"):
        return ""
    resource = Path(sys._MEIPASS) / "release-policy.json"
    try:
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("Installed app has no readable release policy") from exc
    if (not isinstance(value, dict) or value.get("schema") != 1
            or value.get("repository") != SLUG or value.get("bundle_id") != BUNDLE_ID):
        raise UpdateError("Installed app release policy is unexpected")
    team = value.get("team_id")
    return team if isinstance(team, str) and TEAM_ID.fullmatch(team) else ""


def current_app_bundle() -> Path:
    if not getattr(sys, "frozen", False):
        raise UpdateError("Automatic installation requires the signed Mac app")
    executable = Path(sys.executable)
    bundle = executable.parents[2]
    if (bundle.name != BUNDLE_NAME or executable.parent.name != "MacOS"
            or executable.parent.parent.name != "Contents"):
        raise UpdateError("Running app bundle has an unexpected location")
    _assert_no_symlink_ancestor(bundle)
    return bundle


def pending_install(library: Path, installed_app: Path, current_version: str) -> Optional[Path]:
    pending = Path(library) / "updates/pending.json"
    _assert_no_symlink_ancestor(pending)
    if not pending.exists():
        return None
    try:
        record = json.loads(pending.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("Pending update record needs inspection") from exc
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise UpdateError("Pending update record has an unexpected format")
    if record.get("state") != "ready":
        return None
    if record.get("target") == str(installed_app) and record.get("new_version") == current_version:
        from .update_helper import reconcile_after_exchange
        reconcile_after_exchange(pending, current_version)
        return None
    candidate = record.get("candidate")
    if (record.get("target") != str(installed_app)
            or record.get("old_version") != current_version
            or not isinstance(candidate, str) or not Path(candidate).is_dir()
            or Path(candidate).is_symlink()):
        raise UpdateError("Pending update cannot be resumed automatically")
    return pending


def pending_install_manual(pending: Path) -> bool:
    """Retain a user's explicit install request across a launcher restart."""
    _assert_no_symlink_ancestor(pending)
    try:
        record = json.loads(Path(pending).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("Pending update record needs inspection") from exc
    if not isinstance(record, dict) or record.get("schema") != 1 or record.get("state") != "ready":
        raise UpdateError("Pending update record has an unexpected format")
    value = record.get("manual_install", False)
    if type(value) is not bool:
        raise UpdateError("Pending update intent has an unexpected format")
    return value


def prepare_install(staged_app: Path, installed_app: Path, release: Release,
                    library: Path, trusted_team: str, current_version: str,
                    manual_install: bool = False) -> Path:
    """Copy a verified app beside its install; leave current app and maps intact."""
    if version_tuple(release.version) <= version_tuple(current_version):
        raise UpdateError("Update is not newer than the installed version")
    if type(manual_install) is not bool:
        raise TypeError("Manual install intent must be Boolean")
    installed_app = Path(installed_app)
    _assert_no_symlink_ancestor(installed_app)
    if (installed_app.name != BUNDLE_NAME or not installed_app.is_dir()
            or not os.access(installed_app.parent, os.W_OK | os.X_OK)):
        raise UpdateError("Move the app to a user-writable Applications folder before automatic updates")
    verify_publisher(installed_app, trusted_team, current_version)
    verify_publisher(staged_app, trusted_team, release.version)
    update_root = Path(library) / "updates"
    _assert_no_symlink_ancestor(update_root)
    update_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    pending = update_root / "pending.json"
    if pending.exists():
        try:
            previous = json.loads(pending.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UpdateError("An earlier pending update needs inspection") from exc
        if previous.get("state") == "ready":
            raise UpdateError("An earlier update is already ready for installation")
    token = uuid.uuid4().hex
    candidate_parent = installed_app.parent / (".smr-update-" + token)
    candidate = candidate_parent / BUNDLE_NAME
    backup = installed_app.parent / (".smr-previous-" + current_version + "-" + token + ".app")
    if candidate_parent.exists() or backup.exists():
        raise UpdateError("Update staging path already exists")
    candidate_parent.mkdir(mode=0o700)
    try:
        result = subprocess.run(["/usr/bin/ditto", str(staged_app), str(candidate)],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise UpdateError("Could not copy the release beside the installed app")
        verify_publisher(candidate, trusted_team, release.version)
        _atomic_json(pending, dict(schema=1, state="ready", target=str(installed_app),
                                   candidate=str(candidate), backup=str(backup),
                                   helper_bundle=str(staged_app),
                                   team=trusted_team, old_version=current_version,
                                   new_version=release.version, pid=os.getpid(),
                                   manual_install=manual_install))
        return pending
    except Exception:
        shutil.rmtree(candidate_parent)
        raise


def start_install_helper(pending: Path, library: Path) -> None:
    """Schedule an atomic swap once this launcher's process has exited."""
    if not getattr(sys, "frozen", False):
        raise UpdateError("Update installation requires the signed Mac app")
    from . import APP_VERSION
    verify_publisher(current_app_bundle(), trusted_team_from_bundle(), APP_VERSION)
    update_root = Path(library) / "updates"
    _assert_no_symlink_ancestor(update_root)
    try:
        record = json.loads(Path(pending).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("Pending update record needs inspection") from exc
    if record.get("schema") != 1 or record.get("state") != "ready":
        raise UpdateError("No ready update is available for installation")
    helper_bundle = Path(record["helper_bundle"])
    verify_publisher(helper_bundle, trusted_team_from_bundle(), record["new_version"])
    executable = helper_bundle / "Contents/MacOS/Definitive SMR Launcher Apple Silicon"
    record["pid"] = os.getpid()
    _atomic_json(Path(pending), record)
    log = update_root / "helper.log"
    with log.open("ab") as output:
        subprocess.Popen([str(executable), "--apply-update", str(pending)],
                         stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                         start_new_session=True, close_fds=True)
