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
import time

from .activation import _assert_no_symlink_ancestor, _atomic_json, _fsync_dir
from .updates import (BUNDLE_NAME, UpdateError, current_app_bundle,
                      trusted_team_from_bundle, verify_publisher, version_tuple)


@contextmanager
def _install_lock(directory: Path) -> Iterator[None]:
    _assert_no_symlink_ancestor(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(directory / ".install.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
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
    if (not isinstance(team, str) or not re.fullmatch(r"[A-Z0-9]{10}", team)
            or version_tuple(new_version) <= version_tuple(old_version)
            or target.name != BUNDLE_NAME or candidate.name != BUNDLE_NAME
            or candidate.parent.parent != target.parent
            or backup.parent != target.parent or not candidate.parent.name.startswith(".smr-update-")
            or not backup.name.startswith(".smr-previous-")
            or target == candidate or target == backup or candidate == backup):
        raise UpdateError("Pending update paths or publisher are unsafe")
    for path in (target, candidate, backup, helper):
        _assert_no_symlink_ancestor(path)
    return target, candidate, backup, helper, team, old_version, new_version


def _finalize(record_path: Path, record: dict, target: Path, candidate: Path,
              backup: Path, team: str, old_version: str, new_version: str) -> None:
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
    _atomic_json(record_path, dict(record, state="installed", backup=str(backup)))


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
        if backup.exists():
            _finalize(record_path, record, target, candidate, backup, team, old_version, new_version)
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
