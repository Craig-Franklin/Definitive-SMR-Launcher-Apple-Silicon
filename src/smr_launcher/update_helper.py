"""Use the full signed, staged app as a helper for an atomic bundle exchange."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import ctypes
import fcntl
import json
import os
import re
import shutil
import stat
import time

from .activation import _assert_no_symlink_ancestor, _atomic_json, _fsync_dir, _walk_error
from .updates import (
    BUNDLE_NAME,
    UpdateError,
    current_app_bundle,
    trusted_team_from_bundle,
    verify_publisher,
    version_tuple,
    _sha256,
)


@contextmanager
def _install_lock(directory: Path) -> Iterator[None]:
    _assert_no_symlink_ancestor(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(
        directory / ".install.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _swap(left: Path, right: Path) -> None:
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    lib.renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    lib.renamex_np.restype = ctypes.c_int
    if lib.renamex_np(os.fsencode(left), os.fsencode(right), 0x00000002):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    _fsync_dir(left.parent)
    if right.parent != left.parent:
        _fsync_dir(right.parent)


def _read_record(path: Path) -> dict:
    _assert_no_symlink_ancestor(path)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("Pending update record cannot be read") from exc
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise UpdateError("Pending update record has an unexpected format")
    return record


def _paths(record: dict) -> tuple[Path, Path, Path, Path, str, str, str]:
    try:
        target = Path(record["target"])
        candidate = Path(record["candidate"])
        backup = Path(record["backup"])
        helper = Path(record["helper_bundle"])
        team = record["team"]
        old_version = record["old_version"]
        new_version = record["new_version"]
    except (KeyError, TypeError) as exc:
        raise UpdateError("Pending update record is incomplete") from exc
    if (
        not isinstance(team, str)
        or not re.fullmatch(r"[A-Z0-9]{10}", team)
        or version_tuple(new_version) <= version_tuple(old_version)
        or target.name != BUNDLE_NAME
        or candidate.name != BUNDLE_NAME
        or candidate.parent.parent != target.parent
        or backup.parent != target.parent
        or not candidate.parent.name.startswith(".smr-update-")
        or not backup.name.startswith(".smr-previous-")
        or target == candidate
        or target == backup
        or candidate == backup
    ):
        raise UpdateError("Pending update paths or publisher are unsafe")
    for path in (target, candidate, backup, helper):
        _assert_no_symlink_ancestor(path)
    return target, candidate, backup, helper, team, old_version, new_version


def _finalize(
    record_path: Path,
    record: dict,
    target: Path,
    candidate: Path,
    backup: Path,
    team: str,
    old_version: str,
    new_version: str,
) -> None:
    verify_publisher(target, team, new_version)
    if backup.exists():
        verify_publisher(backup, team, old_version, require_name=False)
    elif candidate.exists():
        verify_publisher(candidate, team, old_version)
        os.rename(candidate, backup)
        _fsync_dir(target.parent)
        _fsync_dir(candidate.parent)
        try:
            candidate.parent.rmdir()
        except OSError:
            pass
    else:
        raise UpdateError("Previous app bundle is missing after exchange")
    completed = dict(record, state="installed", backup=str(backup))
    token = candidate.parent.name[len(".smr-update-") :]
    if re.fullmatch(r"[0-9a-f]{32}", token):
        history = record_path.parent / "completed"
        _assert_no_symlink_ancestor(history)
        history.mkdir(mode=0o700, exist_ok=True)
        _atomic_json(history / (token + ".json"), completed)
    _atomic_json(record_path, completed)


def reconcile_after_exchange(record_path: Path, installed_version: str) -> bool:
    """Finish a prior atomic exchange after a helper crash or power interruption."""
    with _install_lock(record_path.parent):
        record = _read_record(record_path)
        if record.get("state") == "installed":
            return True
        if record.get("state") != "ready":
            raise UpdateError("Pending update record has an unexpected state")
        target, candidate, backup, helper, team, old_version, new_version = _paths(record)
        if new_version != installed_version:
            return False
        _finalize(record_path, record, target, candidate, backup, team, old_version, new_version)
        return True


def main(record_path: Path) -> None:
    record = _read_record(record_path)
    if record.get("state") != "ready":
        raise UpdateError("No ready update is available")
    target, candidate, backup, helper, team, old_version, new_version = _paths(record)
    if helper != current_app_bundle() or trusted_team_from_bundle() != team:
        raise UpdateError("Update helper is not the expected signed app")
    verify_publisher(helper, team, new_version)
    pid = record.get("pid")
    if type(pid) is not int or pid <= 1 or pid == os.getpid():
        raise UpdateError("Pending update has an invalid launcher process")
    for _ in range(240):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.5)
    else:
        raise UpdateError("Launcher did not quit; update remains staged")

    with _install_lock(record_path.parent):
        record = _read_record(record_path)
        if record.get("state") == "installed":
            return
        target, candidate, backup, helper, team, old_version, new_version = _paths(record)
        record["helper_pid"] = os.getpid()
        _atomic_json(record_path, record)
        if backup.exists():
            _finalize(
                record_path, record, target, candidate, backup, team, old_version, new_version
            )
            return
        try:
            verify_publisher(target, team, new_version)
        except UpdateError:
            verify_publisher(target, team, old_version)
            verify_publisher(candidate, team, new_version)
            _swap(target, candidate)
            try:
                verify_publisher(target, team, new_version)
            except Exception:
                _swap(target, candidate)
                raise
        _finalize(record_path, record, target, candidate, backup, team, old_version, new_version)


def _cleanup_manifest(root: Path) -> dict:
    """Bind every remaining entry without following signed bundle symlinks."""
    _assert_no_symlink_ancestor(root)
    if not root.exists():
        return {}
    paths = [root]
    if root.is_dir():
        for base, directories, files in os.walk(root, followlinks=False, onerror=_walk_error):
            paths.extend(Path(base) / name for name in directories + files)
    manifest = {}
    device = root.stat().st_dev
    for path in paths:
        before = path.lstat()
        entry = dict(
            device=before.st_dev,
            inode=before.st_ino,
            mode=before.st_mode,
            uid=before.st_uid,
            gid=before.st_gid,
        )
        if before.st_dev != device:
            raise UpdateError("Cleanup tree crosses a volume boundary")
        if stat.S_ISREG(before.st_mode):
            if before.st_nlink != 1:
                raise UpdateError("Cleanup tree contains a shared hardlink")
            entry.update(
                size=before.st_size,
                mtime_ns=before.st_mtime_ns,
                ctime_ns=before.st_ctime_ns,
                sha256=_sha256(path),
            )
        elif stat.S_ISLNK(before.st_mode):
            entry.update(target=os.readlink(path), ctime_ns=before.st_ctime_ns)
        elif not stat.S_ISDIR(before.st_mode):
            raise UpdateError("Cleanup tree contains an unsupported file")
        after = path.lstat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise UpdateError("Cleanup tree changed during inventory")
        manifest[path.relative_to(root).as_posix()] = entry
    return manifest


def _completed_projection(
    receipt: Path, record: dict, root: Path, target: Path, team: str, installed_version: str
):
    old_target, candidate, backup, helper, old_team, old, new = _paths(record)
    token = receipt.stem
    digest, size = record.get("archive_sha256"), record.get("archive_size")
    if (
        record.get("state") != "installed"
        or old_target != target
        or old_team != team
        or version_tuple(new) > version_tuple(installed_version)
        or candidate.parent.name != ".smr-update-" + token
        or backup.name != ".smr-previous-" + old + "-" + token + ".app"
        or helper.parent.parent != root
        or not helper.parent.name.startswith(".app-stage-")
        or candidate.exists()
        or any(
            not path.is_absolute() or ".." in path.parts
            for path in (old_target, candidate, backup, helper)
        )
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or type(size) is not int
        or size <= 0
    ):
        raise UpdateError("Completed update ownership or archive identity is ambiguous")
    return backup, helper, old, new, digest, size


def prune_completed_updates(
    record_path: Path, installed_app: Path, installed_version: str
) -> list[str]:
    """Prune only recorded, superseded exchanges after authenticated app startup.

    Legacy/untracked downloads and staging directories remain untouched. The current
    transaction retains its rollback, archive and helper. A live or unidentifiable
    helper protects the entire older transaction. Cleanup failures remain retryable.
    """
    errors = []
    with _install_lock(record_path.parent):
        current = _read_record(record_path)
        if (
            current.get("state") != "installed"
            or current.get("target") != str(installed_app)
            or current.get("new_version") != installed_version
        ):
            return errors
        target, _, current_backup, _, team, current_old, _ = _paths(current)
        verify_publisher(target, team, installed_version)
        verify_publisher(current_backup, team, current_old, require_name=False)
        history = record_path.parent / "completed"
        _assert_no_symlink_ancestor(history)
        if not history.exists():
            return errors
        records = []
        for receipt in history.iterdir():
            try:
                if not re.fullmatch(r"[0-9a-f]{32}\.json", receipt.name):
                    raise UpdateError("Unrecognized completed update receipt")
                record = _read_record(receipt)
                _completed_projection(
                    receipt, record, record_path.parent, target, team, installed_version
                )
                cleanup = record.get("cleanup")
                if cleanup is not None and (
                    not isinstance(cleanup, dict)
                    or set(cleanup) != {"backup", "helper", "archive"}
                    or any(not isinstance(value, dict) for value in cleanup.values())
                ):
                    raise UpdateError("Completed cleanup authorization is invalid")
                records.append((receipt, record))
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(str(exc))
        # Unknown receipts could reference shared artifacts: refuse all pruning.
        if errors:
            return errors
        for receipt, record in records:
            try:
                backup, helper, old, new, digest, size = _completed_projection(
                    receipt, record, record_path.parent, target, team, installed_version
                )
                if backup == current_backup or version_tuple(new) >= version_tuple(
                    installed_version
                ):
                    continue
                pid = record.get("helper_pid")
                if type(pid) is not int or pid <= 1:
                    continue
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    continue
                archive = record_path.parent / (digest + ".zip")
                # Shared archives/helpers/backups stay pinned by every other receipt.
                others = [other for path, other in records if path != receipt and path.exists()] + [
                    current
                ]
                protected = {other.get("backup") for other in others}
                protected.update(other.get("helper_bundle") for other in others)
                if str(backup) in protected or str(helper) in protected:
                    continue
                shared_archive = any(other.get("archive_sha256") == digest for other in others)
                cleanup = record.get("cleanup")
                roots = dict(backup=backup, helper=helper.parent, archive=archive)
                if cleanup is None:
                    _assert_no_symlink_ancestor(archive)
                    if archive.exists():
                        info = archive.stat()
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or info.st_nlink != 1
                            or info.st_size != size
                            or _sha256(archive) != digest
                        ):
                            raise UpdateError("Completed update archive differs from its identity")
                    if backup.exists():
                        verify_publisher(backup, team, old, require_name=False)
                    if helper.parent.exists():
                        if set(p.name for p in helper.parent.iterdir()) - {
                            BUNDLE_NAME,
                            ".archive.json",
                        }:
                            raise UpdateError("Completed helper directory has unknown contents")
                        marker = helper.parent / ".archive.json"
                        _assert_no_symlink_ancestor(marker)
                        provenance = json.loads(marker.read_text(encoding="utf-8"))
                        if provenance != dict(sha256=digest, size=size):
                            raise UpdateError("Completed helper archive provenance differs")
                        verify_publisher(helper, team, new)
                    cleanup = {key: _cleanup_manifest(path) for key, path in roots.items()}
                    record = dict(record, cleanup=cleanup)
                    _atomic_json(receipt, record)
                # A remaining tree must be a subset of the authenticated pre-deletion
                # manifest. Missing children are expected after interrupted removal;
                # changed/replaced/new contents refuse recovery before further deletion.
                for key, path in roots.items():
                    if key == "archive" and shared_archive:
                        continue
                    remaining = _cleanup_manifest(path)
                    if any(cleanup[key].get(name) != entry for name, entry in remaining.items()):
                        raise UpdateError(
                            "Authorized cleanup contents changed; retained for inspection"
                        )
                # Validate every artifact before deleting any part of this transaction.
                if backup.exists():
                    shutil.rmtree(backup)
                    _fsync_dir(backup.parent)
                if helper.parent.exists():
                    shutil.rmtree(helper.parent)
                    _fsync_dir(helper.parent.parent)
                if archive.exists() and not shared_archive:
                    archive.unlink()
                    _fsync_dir(archive.parent)
                receipt.unlink()
                _fsync_dir(history)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(str(exc))
    return errors
