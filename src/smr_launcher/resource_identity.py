"""Metadata-aware, bounded fingerprints for game resource trees.

Callers select resource roots explicitly (Assets, CustomAssets, or UserMaps).
The fingerprint includes relative paths, directory paths and mtimes, plus file
bytes, size, and filesystem ``mtime_ns``. Absolute locations are provenance and
do not affect the identity, so an exact metadata-preserving copy has the same
identity. FPK contents are hashed as ordinary files, including their serialized
member timestamps.

The walker does not follow symlinks. It fails closed on incomplete roots,
unsupported file types, resource limits, I/O errors, or metadata changes while
enumerating or reading. It does not infer which duplicate resource wins.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Mapping, Sequence


SCHEMA_VERSION = 1
ALGORITHM = "sha256-resource-content-mtime-ns-v1"
CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_ENTRIES = 250_000
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024**3
DEFAULT_MAX_FILE_BYTES = 16 * 1024**3
DEFAULT_MAX_DEPTH = 64
DEFAULT_HASH_CACHE_ENTRIES = 16_384

_ROOT_NAMES = {
    "installed_assets": "assets",
    "custom_assets": "customassets",
    "user_maps": "usermaps",
}


class ResourceFingerprintError(RuntimeError):
    """Raised when a resource tree cannot be fingerprinted completely/stably."""


class ContentHashCache:
    """Bounded, opt-in LRU of file stat identities to SHA-256 digests only."""

    def __init__(self, max_entries: int = DEFAULT_HASH_CACHE_ENTRIES):
        if not isinstance(max_entries, int) or isinstance(max_entries, bool) or max_entries <= 0:
            raise ValueError("max_entries must be a positive integer")
        self.max_entries = max_entries
        self._digests: OrderedDict[tuple[int, ...], str] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple[int, ...]) -> str | None:
        with self._lock:
            value = self._digests.get(key)
            if value is None:
                self.misses += 1
                return None
            self._digests.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: tuple[int, ...], digest: str) -> None:
        with self._lock:
            self._digests[key] = digest
            self._digests.move_to_end(key)
            while len(self._digests) > self.max_entries:
                self._digests.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._digests.clear()
            self.hits = 0
            self.misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._digests)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _open_flags(*, directory: bool = False) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ResourceFingerprintError("Platform does not support no-follow opens")
    flags = os.O_RDONLY | nofollow
    if directory:
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if directory_flag is None:
            raise ResourceFingerprintError("Platform does not support directory-only opens")
        flags |= directory_flag
    else:
        # A regular file can be replaced with a FIFO after lstat. O_NONBLOCK
        # makes that race fail at the post-open type check instead of hanging.
        flags |= getattr(os, "O_NONBLOCK", 0)
    return flags


class _Walker:
    def __init__(self, *, max_entries: int, max_total_bytes: int,
                 max_file_bytes: int, max_depth: int,
                 hash_cache: ContentHashCache | None = None):
        for name, value in (("max_entries", max_entries),
                            ("max_total_bytes", max_total_bytes),
                            ("max_file_bytes", max_file_bytes),
                            ("max_depth", max_depth)):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self.max_entries = max_entries
        self.max_total_bytes = max_total_bytes
        self.max_file_bytes = max_file_bytes
        self.max_depth = max_depth
        if hash_cache is not None and not isinstance(hash_cache, ContentHashCache):
            raise TypeError("hash_cache must be a ContentHashCache or None")
        self.hash_cache = hash_cache
        self.entry_count = 0
        self.total_bytes = 0
        self.file_count = 0

    def _count_entry(self, label: str, relative: str) -> None:
        self.entry_count += 1
        if self.entry_count > self.max_entries:
            raise ResourceFingerprintError(
                f"Resource entry limit exceeded at {label}:{relative}")

    @staticmethod
    def _stable(before: os.stat_result, after: os.stat_result) -> bool:
        return _stat_identity(before) == _stat_identity(after)

    def _hash_file(self, parent_fd: int, name: str, label: str,
                   relative: str, before: os.stat_result) -> dict:
        if not stat.S_ISREG(before.st_mode):
            raise ResourceFingerprintError(
                f"Unsupported resource entry type at {label}:{relative}")
        if before.st_size < 0 or before.st_size > self.max_file_bytes:
            raise ResourceFingerprintError(
                f"Per-file byte limit exceeded at {label}:{relative}")
        if self.total_bytes + before.st_size > self.max_total_bytes:
            raise ResourceFingerprintError(
                f"Total resource byte limit exceeded at {label}:{relative}")

        try:
            fd = os.open(name, _open_flags(), dir_fd=parent_fd)
        except OSError as exc:
            raise ResourceFingerprintError(
                f"Cannot open resource file at {label}:{relative}: {exc}") from exc

        cache_key = _stat_identity(before)
        cached_digest = self.hash_cache.get(cache_key) if self.hash_cache is not None else None
        digest = hashlib.sha256() if cached_digest is None else None
        bytes_read = 0
        try:
            opened = os.fstat(fd)
            if not self._stable(before, opened):
                raise ResourceFingerprintError(
                    f"Resource file changed before reading at {label}:{relative}")
            if cached_digest is None:
                while True:
                    try:
                        block = os.read(fd, min(CHUNK_SIZE, self.max_file_bytes + 1 - bytes_read))
                    except OSError as exc:
                        raise ResourceFingerprintError(
                            f"Cannot read resource file at {label}:{relative}: {exc}") from exc
                    if not block:
                        break
                    bytes_read += len(block)
                    if bytes_read > self.max_file_bytes or self.total_bytes + bytes_read > self.max_total_bytes:
                        raise ResourceFingerprintError(
                            f"Resource byte limit exceeded while reading {label}:{relative}")
                    digest.update(block)
            else:
                # Cached content still counts toward the caller's logical byte
                # budget even though its bytes are not reread.
                bytes_read = before.st_size

            after_fd = os.fstat(fd)
            try:
                after_path = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as exc:
                raise ResourceFingerprintError(
                    f"Resource file disappeared while reading {label}:{relative}") from exc
            if not self._stable(before, after_fd) or not self._stable(before, after_path):
                raise ResourceFingerprintError(
                    f"Resource file changed while reading at {label}:{relative}")
            if bytes_read != before.st_size:
                raise ResourceFingerprintError(
                    f"Resource file size changed while reading at {label}:{relative}")
        finally:
            os.close(fd)

        self.total_bytes += bytes_read
        self.file_count += 1
        file_digest = cached_digest if cached_digest is not None else digest.hexdigest()
        if self.hash_cache is not None and cached_digest is None:
            self.hash_cache.put(cache_key, file_digest)
        return {"path": relative, "kind": "file", "size": bytes_read,
                "mtime_ns": before.st_mtime_ns, "sha256": file_digest}

    def _walk_dir(self, directory_fd: int, label: str, relative: str,
                  depth: int, opened_stat: os.stat_result | None = None) -> list[dict]:
        if depth > self.max_depth:
            raise ResourceFingerprintError(f"Resource depth limit exceeded at {label}:{relative}")
        try:
            before = opened_stat if opened_stat is not None else os.fstat(directory_fd)
            if not stat.S_ISDIR(before.st_mode):
                raise ResourceFingerprintError(f"Expected directory at {label}:{relative}")
            names = []
            with os.scandir(directory_fd) as scan:
                for entry in scan:
                    names.append(entry.name)
                    if len(names) + self.entry_count > self.max_entries:
                        raise ResourceFingerprintError(
                            f"Resource entry limit exceeded while listing {label}:{relative}")
        except ResourceFingerprintError:
            raise
        except OSError as exc:
            raise ResourceFingerprintError(
                f"Cannot enumerate resource directory at {label}:{relative}: {exc}") from exc

        results = [{"path": relative, "kind": "directory", "mtime_ns": before.st_mtime_ns}]
        self._count_entry(label, relative)
        for name in sorted(names):
            child = f"{relative}/{name}" if relative != "." else name
            try:
                child_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise ResourceFingerprintError(
                    f"Cannot stat resource entry at {label}:{child}: {exc}") from exc
            self._count_entry(label, child)
            if stat.S_ISLNK(child_stat.st_mode):
                raise ResourceFingerprintError(f"Symlink resource entry at {label}:{child}")
            if stat.S_ISDIR(child_stat.st_mode):
                try:
                    child_fd = os.open(name, _open_flags(directory=True), dir_fd=directory_fd)
                except OSError as exc:
                    raise ResourceFingerprintError(
                        f"Cannot open resource directory at {label}:{child}: {exc}") from exc
                try:
                    opened = os.fstat(child_fd)
                    if not self._stable(child_stat, opened):
                        raise ResourceFingerprintError(
                            f"Resource directory changed before reading at {label}:{child}")
                    # The child directory's own record is counted by _walk_dir.
                    self.entry_count -= 1
                    results.extend(self._walk_dir(child_fd, label, child, depth + 1, opened))
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(child_stat.st_mode):
                results.append(self._hash_file(directory_fd, name, label, child, child_stat))
            else:
                raise ResourceFingerprintError(
                    f"Unsupported resource entry type at {label}:{child}")

        try:
            after = os.fstat(directory_fd)
        except OSError as exc:
            raise ResourceFingerprintError(
                f"Cannot recheck resource directory at {label}:{relative}: {exc}") from exc
        if not self._stable(before, after):
            raise ResourceFingerprintError(
                f"Resource directory changed while enumerating at {label}:{relative}")
        return results

    def walk_root(self, role: str, value: os.PathLike | str) -> tuple[str, list[dict]]:
        try:
            path = Path(os.path.abspath(os.fspath(value)))
        except (TypeError, ValueError) as exc:
            raise ResourceFingerprintError(f"Invalid resource root for {role}: {exc}") from exc
        expected_name = _ROOT_NAMES[role]
        if path.name.casefold() != expected_name:
            raise ResourceFingerprintError(
                f"Root {role} must name a {expected_name} directory, got {path.name!r}")
        root_fd, path_identity = _open_directory_path(path, role)
        try:
            opened = os.fstat(root_fd)
            if not stat.S_ISDIR(opened.st_mode):
                raise ResourceFingerprintError(f"Resource root is not a directory: {role}")
            entries = self._walk_dir(root_fd, role, ".", 0, opened)
            _verify_directory_path(path, role, path_identity)
        finally:
            os.close(root_fd)
        return str(path), entries


def _directory_identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)


def _open_directory_path(path: Path, role: str) -> tuple[int, list[tuple[int, int, int]]]:
    """Open every absolute path component relative to a no-follow directory fd."""
    identities = []
    current_fd = None
    try:
        current_fd = os.open("/", _open_flags(directory=True))
        identities.append(_directory_identity(os.fstat(current_fd)))
        for component in path.parts[1:]:
            try:
                before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except OSError as exc:
                raise ResourceFingerprintError(
                    f"Missing/unreadable root ancestor for {role} at {component!r}: {exc}") from exc
            if stat.S_ISLNK(before.st_mode):
                if component == path.parts[-1]:
                    raise ResourceFingerprintError(f"Symlink resource root for {role}: {path}")
                raise ResourceFingerprintError(
                    f"Symlink root ancestor for {role}: {component!r}")
            if not stat.S_ISDIR(before.st_mode):
                raise ResourceFingerprintError(
                    f"Non-directory root ancestor for {role}: {component!r}")
            next_fd = None
            try:
                next_fd = os.open(component, _open_flags(directory=True), dir_fd=current_fd)
                opened = os.fstat(next_fd)
                identity = _directory_identity(opened)
                if identity != _directory_identity(before):
                    raise ResourceFingerprintError(
                        f"Root ancestor changed while opening {role} at {component!r}")
            except OSError as exc:
                if next_fd is not None:
                    os.close(next_fd)
                raise ResourceFingerprintError(
                    f"Cannot open root ancestor for {role} at {component!r}: {exc}") from exc
            except Exception:
                if next_fd is not None:
                    os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
            identities.append(identity)
        return current_fd, identities
    except Exception:
        if current_fd is not None:
            os.close(current_fd)
        raise


def _verify_directory_path(path: Path, role: str,
                           expected: list[tuple[int, int, int]]) -> None:
    try:
        check_fd, observed = _open_directory_path(path, role)
    except ResourceFingerprintError as exc:
        raise ResourceFingerprintError(
            f"Resource root path changed during fingerprint for {role}: {exc}") from exc
    try:
        if observed != expected:
            raise ResourceFingerprintError(
                f"Resource root path was replaced during fingerprint for {role}")
    finally:
        os.close(check_fd)


def fingerprint_resources(roots: Mapping[str, os.PathLike | str], *,
                          required_roles: Sequence[str] = (),
                          max_entries: int = DEFAULT_MAX_ENTRIES,
                          max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
                          max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
                          max_depth: int = DEFAULT_MAX_DEPTH,
                          hash_cache: ContentHashCache | None = None) -> dict:
    """Return a versioned identity for caller-selected resource directories.

    ``roots`` maps roles to existing directory roots. Supported roles are
    ``installed_assets``, ``custom_assets`` and ``user_maps``. Use the same
    role labels for prepared and live roots so byte/metadata-identical copies
    can be compared directly; absolute locations remain provenance. Passing a complete profile
    root is rejected by the role/basename check, which keeps settings, saves,
    and logs outside the fingerprint. Supplied missing roots always raise. Use
    ``required_roles`` to fail if a caller omitted a role required by its own
    verification/binding scope. A caller can fingerprint installed Assets once
    separately, then combine it with the same ``custom_assets``/``user_maps``
    labels for either prepared or live roots.

    Identity is computed from role labels and sorted relative entries, not
    absolute locations. By default file hashes are freshly computed on every
    call. An explicitly supplied ``ContentHashCache`` may reuse only file
    digests keyed by device, inode, mode, size, mtime_ns, and ctime_ns; each
    call still re-enumerates roots and opens/revalidates every file without
    following symlinks. Cache hits still count against logical byte limits.
    """
    if not isinstance(roots, Mapping) or not roots:
        raise ValueError("At least one resource root must be supplied")
    if any(not isinstance(role, str) for role in roots):
        raise ResourceFingerprintError("Resource root roles must be strings")
    if isinstance(required_roles, (str, bytes)) or any(
            not isinstance(role, str) for role in required_roles):
        raise ResourceFingerprintError("required_roles must be a sequence of role names")
    roles = set(roots)
    unknown = sorted(roles - _ROOT_NAMES.keys())
    if unknown:
        raise ResourceFingerprintError(f"Unsupported resource root roles: {', '.join(unknown)}")
    missing = sorted(set(required_roles) - roles)
    if missing:
        raise ResourceFingerprintError(f"Required resource roots were not supplied: {', '.join(missing)}")
    if set(required_roles) - _ROOT_NAMES.keys():
        raise ResourceFingerprintError("required_roles contains an unsupported root role")

    walker = _Walker(max_entries=max_entries, max_total_bytes=max_total_bytes,
                     max_file_bytes=max_file_bytes, max_depth=max_depth,
                     hash_cache=hash_cache)
    root_records = []
    identity_roots = []
    for role in sorted(roles):
        location, entries = walker.walk_root(role, roots[role])
        identity_roots.append({"role": role, "entries": entries})
        root_records.append({"role": role, "location": location, "entries": entries})

    identity_payload = {"schema": SCHEMA_VERSION, "algorithm": ALGORITHM,
                        "roots": identity_roots}
    identity = hashlib.sha256(_canonical_json(identity_payload)).hexdigest()
    return {
        "schema": SCHEMA_VERSION,
        "algorithm": ALGORITHM,
        "identity": identity,
        "roots": root_records,
        "totals": {"entries": walker.entry_count, "files": walker.file_count,
                   "bytes": walker.total_bytes},
        "limits": {"max_entries": max_entries, "max_total_bytes": max_total_bytes,
                   "max_file_bytes": max_file_bytes, "max_depth": max_depth},
    }
