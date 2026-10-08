"""Crash-recoverable switching of complete, caller-supplied game profiles.

This module deliberately has no Steam or Feral path discovery. A clean-install
binding must establish one suitable support tree and a reliable process probe
before this service is allowed to touch real game data.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import uuid


class ActivationError(RuntimeError):
    """Activation cannot proceed without risking a complete profile."""


class RecoveryError(ActivationError):
    """The on-disk state requires inspection before another switch."""


ORIGINAL = "original-game"
MARKER = ".smr-launcher-profile.json"
SCHEMA = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryError("Cannot read " + str(path)) from exc
    if not isinstance(value, dict):
        raise RecoveryError("Invalid JSON object at " + str(path))
    return value


def _assert_no_symlink_ancestor(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ActivationError("Symlink in managed path: " + str(part))


def _canonical_directory_path(value: Path) -> Path:
    """Resolve case aliases through the filesystem, including an absent leaf."""
    source = Path(value).expanduser()
    for part in (source, *source.parents):
        if part.is_symlink() and part != Path("/var"):
            raise ActivationError("Linked managed path: " + str(part))
    resolved = source.resolve()
    missing = []
    current = resolved
    while not current.exists():
        missing.append(current.name)
        current = current.parent
    if not current.is_dir():
        raise ActivationError("Managed path has a nondirectory parent")
    fd = os.open(current, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        # Darwin F_GETPATH returns the on-disk spelling of an open directory.
        raw = fcntl.fcntl(fd, 50, b"\0" * 1024).split(b"\0", 1)[0]
    finally:
        os.close(fd)
    if not raw:
        raise ActivationError("Could not canonicalize managed directory")
    return Path(os.fsdecode(raw)).joinpath(*reversed(missing))


def _macos_exchange(first: Path, second: Path, *, observed_io=None) -> None:
    """Atomically exchange two existing directory names on macOS/APFS."""
    if sys.platform != "darwin":
        raise ActivationError("Atomic directory exchange requires macOS")
    function = ctypes.CDLL(None, use_errno=True).renamex_np
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    args = (os.fsencode(first), os.fsencode(second), 0x00000002)
    result = (function(*args) if observed_io is None else
              observed_io.call("activation.exchange_syscall", function, *args))
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(first), str(second))
    sync = _fsync_dir if observed_io is None else observed_io.fsync_dir
    sync(first.parent)
    if first.parent != second.parent:
        sync(second.parent)


def _walk_error(error: OSError) -> None:
    raise error


def _assert_regular_tree(root: Path) -> None:
    """A profile must contain only local directories and independent regular files."""
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ActivationError("Profile root is not a real directory: " + str(root))
    device = root.stat().st_dev
    for base, directories, files in os.walk(root, followlinks=False, onerror=_walk_error):
        here = Path(base)
        for name in directories + files:
            path = here / name
            item = path.lstat()
            if item.st_dev != device:
                raise ActivationError("Profile crosses a volume boundary: " + str(path))
            if stat.S_ISLNK(item.st_mode):
                raise ActivationError("Linked profile path: " + str(path))
            if stat.S_ISDIR(item.st_mode):
                continue
            if not stat.S_ISREG(item.st_mode):
                raise ActivationError("Unsupported profile file: " + str(path))
            if item.st_nlink != 1:
                raise ActivationError("Shared hardlink in profile: " + str(path))


def _make_writable_tree(root: Path) -> None:
    """Prepared packages may be read-only; game generations need writable files."""
    for base, directories, files in os.walk(root, followlinks=False, onerror=_walk_error):
        here = Path(base)
        here.chmod(here.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
        for name in files:
            path = here / name
            path.chmod(path.stat().st_mode | stat.S_IWUSR)


def _fsync_tree(root: Path) -> None:
    """Flush regular files and directory entries without following links."""
    _assert_regular_tree(root)
    directories_seen = []
    for base, directories, files in os.walk(root, followlinks=False, onerror=_walk_error):
        here = Path(base)
        directories_seen.append(here)
        for name in files:
            path = here / name
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    for directory in reversed(directories_seen):
        _fsync_dir(directory)


class FilesystemProfiles:
    """Manage one live tree and one inactive tree per exact variant identity.

    ``asset_roots`` identifies the map-specific directories inside each complete
    tree. Other files, including saves, travel with their profile unchanged.
    ``game_running`` must return literal False to authorize any switch/recovery.
    """

    def __init__(
        self,
        live_root: Path,
        store_root: Path,
        asset_roots: Sequence[str],
        game_running: Callable[[], Optional[bool]],
        fault_hook: Optional[Callable[[str], None]] = None,
        exchange: Callable[[Path, Path], None] = _macos_exchange,
        observed_io=None,
    ) -> None:
        # macOS exposes /var as a system symlink to /private/var. Resolve that
        # alias once, then reject any later link substitution in managed paths.
        self.live = _canonical_directory_path(Path(live_root))
        self.store = _canonical_directory_path(Path(store_root))
        self.profiles = self.store / "profiles"
        self.staging = self.store / "staging"
        self.state_file = self.store / "state.json"
        self.journal = self.store / "journal.json"
        # This lock and binding live outside the exchanged directory. A lock
        # inside a chosen store would not exclude another store for this game.
        binding_name = ".smr-launcher-" + hashlib.sha256(os.fsencode(self.live)).hexdigest()
        self.lock_file = self.live.parent / (binding_name + ".lock")
        self.binding_file = self.live.parent / (binding_name + ".binding.json")
        self.asset_roots = tuple(Path(name) for name in asset_roots)
        if not self.asset_roots:
            raise ValueError("At least one asset root is required")
        for rel in self.asset_roots:
            if rel.is_absolute() or not rel.parts or any(part in (".", "..") for part in rel.parts):
                raise ValueError("Asset roots must be safe relative paths")
        if self.live == self.store or self.live in self.store.parents or self.store in self.live.parents:
            raise ValueError("Live and store roots must be separate")
        self.game_running = game_running
        self.fault_hook = fault_hook or (lambda event: None)
        self.exchange = exchange
        self.observed_io = observed_io


    def _call(self, name, function, *args, **kwargs):
        if self.observed_io is None:
            return function(*args, **kwargs)
        return self.observed_io.call(name, function, *args, **kwargs)

    def _atomic_json(self, path, value):
        if self.observed_io is None:
            return _atomic_json(path, value)
        return self.observed_io.atomic_json(path, value)

    def _fsync_dir(self, path):
        if self.observed_io is None:
            return _fsync_dir(path)
        return self.observed_io.fsync_dir(path)

    def _fsync_tree(self, path):
        if self.observed_io is None:
            return _fsync_tree(path)
        return self.observed_io.fsync_tree(path)

    def _copytree(self, source, destination):
        if self.observed_io is None:
            return shutil.copytree(source, destination, copy_function=shutil.copy2)
        return self.observed_io.copytree(source, destination)

    def _make_writable_tree(self, root):
        if self.observed_io is None:
            return _make_writable_tree(root)
        for base, directories, files in os.walk(root, followlinks=False, onerror=_walk_error):
            here = Path(base)
            self._call("activation.directory_chmod", here.chmod,
                       here.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
            for name in files:
                path = here / name
                self._call("activation.file_chmod", path.chmod, path.stat().st_mode | stat.S_IWUSR)

    def _exchange(self, first, second):
        if self.observed_io is None:
            return self.exchange(first, second)
        if self.exchange is _macos_exchange:
            return _macos_exchange(first, second, observed_io=self.observed_io)
        # An alternate exchange is an explicit trusted synthetic provider. Its
        # successful return alone is never evidence of macOS kernel behavior.
        return self._call("activation.synthetic_exchange", self.exchange, first, second)

    def _ensure_paths(self) -> None:
        _assert_no_symlink_ancestor(self.live)
        _assert_no_symlink_ancestor(self.store)
        if not self.live.is_dir():
            raise ActivationError("Live profile root is missing")
        _assert_no_symlink_ancestor(self.profiles)
        _assert_no_symlink_ancestor(self.staging)
        self.profiles.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        if self.live.stat().st_dev != self.store.stat().st_dev:
            raise ActivationError("Live and profile storage are on different volumes")

    def _verify_binding(self, enrolling: bool) -> None:
        _assert_no_symlink_ancestor(self.binding_file)
        if not self.binding_file.exists():
            if not enrolling:
                raise RecoveryError("Game profile binding is missing")
            self._atomic_json(self.binding_file, dict(schema=SCHEMA, live=str(self.live), store=str(self.store)))
        binding = _read_json(self.binding_file)
        if binding != dict(schema=SCHEMA, live=str(self.live), store=str(self.store)):
            raise RecoveryError("Game profile is bound to another launcher store")

    @contextmanager
    def _locked(self, enrolling: bool = False) -> Iterator[None]:
        _assert_no_symlink_ancestor(self.live.parent)
        _assert_no_symlink_ancestor(self.store)
        self.store.mkdir(parents=True, mode=0o700, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = self._call("activation.lock_open", os.open, self.lock_file, flags, 0o600)
        try:
            self._call("activation.lock_acquire", fcntl.flock, fd, fcntl.LOCK_EX)
            if enrolling:
                self._ensure_paths()
                self._require_stopped()
                if self.state_file.exists() and not self.binding_file.exists():
                    raise RecoveryError("Existing state has no game profile binding")
                self._verify_binding(enrolling=True)
            else:
                self._verify_binding(enrolling=False)
                self._ensure_paths()
            yield
        finally:
            try:
                self._call("activation.lock_release", fcntl.flock, fd, fcntl.LOCK_UN)
            finally:
                self._call("activation.lock_close", os.close, fd)
        self._call("activation.lock_exit", lambda: None)

    def _require_stopped(self) -> None:
        try:
            status = self.game_running()
        except Exception as exc:
            raise ActivationError("Could not determine whether Railroads is running") from exc
        if status is not False:
            raise ActivationError("Railroads is running or its process state is unknown")

    def _asset_hash(self, root: Path) -> str:
        result = hashlib.sha256()
        for rel in self.asset_roots:
            subtree = root / rel
            if not subtree.is_dir() or subtree.is_symlink():
                raise ActivationError("Missing or linked asset root: " + str(subtree))
            for base, directories, files in os.walk(subtree, followlinks=False, onerror=_walk_error):
                directories.sort()
                files.sort()
                for name in directories:
                    p = Path(base) / name
                    if p.is_symlink():
                        raise ActivationError("Linked asset path: " + str(p))
                    key = p.relative_to(root).as_posix().encode("utf-8")
                    result.update(b"D\0" + key + b"\0")
                for name in files:
                    p = Path(base) / name
                    if p.is_symlink() or not p.is_file():
                        raise ActivationError("Unsupported asset path: " + str(p))
                    key = p.relative_to(root).as_posix().encode("utf-8")
                    result.update(b"F\0" + key + b"\0")
                    result.update(str(p.stat().st_size).encode("ascii") + b"\0")
                    if self.observed_io is None:
                        with p.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1 << 20), b""):
                                result.update(chunk)
                    else:
                        result.update(self.observed_io.readbytes(p))
                    result.update(b"\0")
        return result.hexdigest()

    def _write_marker(self, root: Path, profile_id: str, variant_id: Optional[str]) -> None:
        self._atomic_json(root / MARKER, dict(schema=SCHEMA, profile_id=profile_id, variant_id=variant_id, assets_sha256=self._asset_hash(root)))

    def _marker(self, root: Path, expected: Optional[str] = None) -> dict:
        _assert_no_symlink_ancestor(root)
        _assert_regular_tree(root)
        marker = _read_json(root / MARKER)
        if marker.get("schema") != SCHEMA or not isinstance(marker.get("profile_id"), str):
            raise RecoveryError("Invalid profile identity at " + str(root))
        if expected is not None and marker["profile_id"] != expected:
            raise RecoveryError("Unexpected profile at " + str(root))
        if marker.get("assets_sha256") != self._asset_hash(root):
            raise RecoveryError("Map assets changed in " + str(root) + "; preserved for inspection")
        return marker

    def _profile_path(self, profile_id: str) -> Path:
        if profile_id != ORIGINAL and not (profile_id.startswith("map-") and _SHA256.fullmatch(profile_id[4:])):
            raise ValueError("Profile ID must be original-game or map-<sha256>")
        path = self.profiles / profile_id
        _assert_no_symlink_ancestor(path)
        return path

    def enroll_original(self) -> None:
        """Bind a known clean live tree as Original Game (after external baseline checks)."""
        with self._locked(enrolling=True):
            self._require_stopped()
            if self.state_file.exists():
                raise ActivationError("A live profile is already enrolled")
            if self.journal.exists():
                raise RecoveryError("An incomplete activation journal exists")
            if (self.live / MARKER).exists():
                self._marker(self.live, ORIGINAL)
            else:
                _assert_regular_tree(self.live)
                self._write_marker(self.live, ORIGINAL, None)
            self._atomic_json(self.state_file, dict(schema=SCHEMA, active=ORIGINAL))

    def register_variant(self, variant_id: str, prepared_root: Path, *, saved_games: Optional[Path] = None) -> str:
        """Make an independent writable generation from an immutable prepared tree."""
        if not _SHA256.fullmatch(variant_id):
            raise ValueError("Variant identity must be a lowercase SHA-256 digest")
        profile_id = "map-" + variant_id
        source = _canonical_directory_path(Path(prepared_root))
        with self._locked():
            self._require_stopped()
            self._recover_locked()
            _assert_no_symlink_ancestor(source)
            if not source.is_dir():
                raise ActivationError("Prepared source is missing")
            _assert_regular_tree(source)
            if source == self.live or source in self.live.parents or self.live in source.parents or source == self.store or source in self.store.parents or self.store in source.parents:
                raise ActivationError("Prepared source overlaps managed storage")
            expected_hash = self._asset_hash(source)
            destination = self._profile_path(profile_id)
            if self._state()["active"] == profile_id:
                marker = self._marker(self.live, profile_id)
                if marker.get("variant_id") != variant_id or marker.get("assets_sha256") != expected_hash:
                    raise RecoveryError("Active variant differs from the prepared source")
                return profile_id
            if destination.exists():
                marker = self._marker(destination, profile_id)
                if marker.get("variant_id") != variant_id or marker.get("assets_sha256") != expected_hash:
                    raise RecoveryError("Variant ID collision or modified stored profile")
                return profile_id
            temporary = self.staging / (profile_id + "-" + uuid.uuid4().hex)
            try:
                self._copytree(source, temporary)
                self._make_writable_tree(temporary)
                if self._asset_hash(temporary) != expected_hash or self._asset_hash(source) != expected_hash:
                    raise ActivationError("Prepared source changed during registration")
                if saved_games is not None:
                    from .variants import _tree_manifest
                    _assert_no_symlink_ancestor(saved_games)
                    _assert_regular_tree(saved_games)
                    saved_manifest = _tree_manifest(saved_games)
                    # Never merge retained saves into a generation with existing saves.
                    self._call("activation.empty_saves_rmdir", (temporary / "Saves").rmdir)
                    self._copytree(saved_games, temporary / "Saves")
                    if (_tree_manifest(temporary / "Saves") != saved_manifest
                            or _tree_manifest(saved_games) != saved_manifest):
                        raise ActivationError("Retained saves changed during restoration")
                    self._make_writable_tree(temporary / "Saves")
                self._write_marker(temporary, profile_id, variant_id)
                self._fsync_tree(temporary)
                self._call("activation.publish_rename", os.rename, temporary, destination)
                self._fsync_dir(self.profiles)
            finally:
                if temporary.exists() and self.observed_io is None:
                    shutil.rmtree(temporary)
            return profile_id

    def active_profile(self) -> str:
        with self._locked():
            self._require_stopped()
            self._recover_locked()
            return self._state()["active"]

    def _state(self) -> dict:
        state = _read_json(self.state_file)
        if state.get("schema") != SCHEMA or not isinstance(state.get("active"), str):
            raise RecoveryError("Invalid launcher state")
        self._profile_path(state["active"])
        return state

    def _clear_journal(self) -> None:
        self._call("activation.journal_unlink", self.journal.unlink)
        self._fsync_dir(self.store)

    def _recover_locked(self) -> None:
        if self.observed_io is not None and (self.journal.exists() or self.journal.is_symlink()):
            def refuse_history():
                raise RecoveryError("Observed runner refuses pending historical activation recovery")
            self._call("activation.pending_recovery_denied", refuse_history)
        state = self._state()
        if not self.journal.exists():
            self._marker(self.live, state["active"])
            return
        journal = _read_json(self.journal)
        if journal.get("schema") != SCHEMA or state["active"] not in (journal.get("source"), journal.get("target")):
            raise RecoveryError("Activation journal conflicts with current state")
        source_id = journal.get("source")
        target_id = journal.get("target")
        if not isinstance(source_id, str) or not isinstance(target_id, str) or source_id == target_id:
            raise RecoveryError("Invalid activation journal identities")
        source_path = self._profile_path(source_id)
        target_path = self._profile_path(target_id)
        source_exists = source_path.exists()
        target_exists = target_path.exists()
        live_id = self._marker(self.live)["profile_id"]
        if live_id == source_id and target_exists and not source_exists and state["active"] == source_id:
            self._marker(target_path, target_id)
            self._clear_journal()  # Exchange never occurred.
            return
        if live_id == target_id and target_exists and not source_exists:
            self._marker(target_path, source_id)
            self._call("activation.outgoing_rename", os.rename, target_path, source_path)
            self._fsync_dir(self.profiles)
            source_exists, target_exists = True, False
        if live_id == target_id and source_exists and not target_exists:
            self._marker(source_path, source_id)
            self._atomic_json(self.state_file, dict(schema=SCHEMA, active=target_id))
            self._clear_journal()
            return
        raise RecoveryError("Unexpected profile layout; all trees retained for inspection")

    def recover(self) -> str:
        with self._locked():
            self._require_stopped()
            self._recover_locked()
            return self._state()["active"]

    def _switch_locked(self, target_id: str) -> str:
        self._require_stopped()
        self._recover_locked()
        source_id = self._state()["active"]
        target_path = self._profile_path(target_id)
        if source_id == target_id:
            return target_id
        source_path = self._profile_path(source_id)
        if source_path.exists() or not target_path.is_dir() or target_path.is_symlink():
            raise RecoveryError("Current or target profile storage is unexpected")
        self._marker(self.live, source_id)
        self._marker(target_path, target_id)
        if self.live.stat().st_dev != target_path.stat().st_dev:
            raise ActivationError("Target generation is on another volume")
        self._fsync_tree(self.live)
        self._atomic_json(self.journal, dict(schema=SCHEMA, source=source_id, target=target_id))
        self.fault_hook("after_journal")
        self._require_stopped()
        self._exchange(self.live, target_path)
        self.fault_hook("after_exchange")
        self._require_stopped()
        self._marker(self.live, target_id)
        self._marker(target_path, source_id)
        self._call("activation.outgoing_rename", os.rename, target_path, source_path)
        self._fsync_dir(self.profiles)
        self.fault_hook("after_outgoing_saved")
        self._atomic_json(self.state_file, dict(schema=SCHEMA, active=target_id))
        self.fault_hook("after_state")
        self._clear_journal()
        return target_id

    def switch(self, target_id: str) -> str:
        with self._locked():
            return self._switch_locked(target_id)

    def play(self, target_id: str, launch: Callable[[], None], startup_seconds: float = 30.0) -> str:
        """Serialize this launcher's switch and game start under one live lock.

        Direct Steam/Finder launches do not participate in this lock. The
        process checks fail closed if one is observed, but cannot exclude a
        launch that races the atomic exchange from another application.
        """
        with self._locked():
            target = self._switch_locked(target_id)
            self._require_stopped()
            launch()
            deadline = time.monotonic() + startup_seconds
            while time.monotonic() < deadline:
                try:
                    running = self.game_running()
                except Exception as exc:
                    raise ActivationError("Could not verify game startup") from exc
                if running is True:
                    return target
                if running is None:
                    raise ActivationError("Game process state became unknown after launch")
                time.sleep(0.2)
            raise ActivationError("Railroads did not start before the launch timeout")
