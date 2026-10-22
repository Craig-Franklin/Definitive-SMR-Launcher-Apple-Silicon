"""Bounded read-only acquisition and exclusive private inspection outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import stat
import tempfile

from . import resource_identity as resources
from .extraction import extract_package
from .packages import MAX_ARCHIVE_BYTES

PRIVATE_ROOT = Path.home() / "Library/Application Support/SMR Research/inspections"


def absolute(path):
    return Path(os.path.abspath(Path(path).expanduser()))


def content_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class InspectionError(ValueError):
    """Inspection cannot finish safely or within its supported bounds."""

    def __init__(self, message, *, report=None, archive_snapshot=None):
        super().__init__(message)
        self.report = report
        self.archive_snapshot = archive_snapshot


class _InspectionWalker(resources._Walker):
    """Reuse the descriptor walker, retaining preservation facts separately."""

    def _hash_file(self, parent_fd, name, label, relative, before):
        row = super()._hash_file(parent_fd, name, label, relative, before)
        row["stat"] = resources._stat_identity(before)
        return row

    def _walk_dir(self, directory_fd, label, relative, depth, opened_stat=None):
        before = opened_stat if opened_stat is not None else os.fstat(directory_fd)
        rows = super()._walk_dir(directory_fd, label, relative, depth, before)
        rows[0]["stat"] = resources._stat_identity(before)
        return rows


@dataclass
class Snapshot:
    path: Path
    entries: list
    limits: dict

    def __post_init__(self):
        self.by_path = {row["path"]: row for row in self.entries}
        self._files = [
            {key: row[key] for key in ("path", "size", "sha256")}
            for row in self.entries
            if row["kind"] == "file"
        ]

    @property
    def files(self):
        return self._files

    @property
    def identity(self):
        return content_digest(self.files)

    def read(self, relative, limit, *, keep=None):
        expected = self.by_path[relative]
        data, _ = read_file(self.path / relative, limit, expected=expected, keep=keep)
        return data

    def verify(self):
        if inventory(self.path, **self.limits).entries != self.entries:
            raise InspectionError("Source tree changed during inspection: " + str(self.path))


def inventory(path, *, max_entries, max_total_bytes, max_file_bytes, max_depth, scopes=None):
    path = absolute(path)
    limits = dict(
        max_entries=max_entries,
        max_total_bytes=max_total_bytes,
        max_file_bytes=max_file_bytes,
        max_depth=max_depth,
    )
    fd, identity = resources._open_directory_path(path, "inspection")
    try:
        walker = _InspectionWalker(**limits)
        if scopes is None:
            rows = walker._walk_dir(fd, "inspection", ".", 0, os.fstat(fd))
        else:
            if (
                not isinstance(scopes, (tuple, list))
                or not 0 < len(scopes) <= 64
                or len(set(scopes)) != len(scopes)
                or any(
                    not isinstance(s, str) or not s or s in (".", "..") or "/" in s or "\\" in s
                    for s in scopes
                )
            ):
                raise InspectionError("Inventory scopes must be distinct safe directory names")
            rows = []
            for name in sorted(scopes):
                try:
                    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat.S_ISDIR(before.st_mode):
                    raise InspectionError("Inventory scope is not a nonlinked directory: " + name)
                child = os.open(name, resources._open_flags(directory=True), dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    if resources._stat_identity(opened) != resources._stat_identity(before):
                        raise InspectionError("Inventory scope changed before reading: " + name)
                    rows.extend(walker._walk_dir(child, "inspection", name, 0, opened))
                    after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if resources._stat_identity(after) != resources._stat_identity(before):
                        raise InspectionError("Inventory scope changed while reading: " + name)
                finally:
                    os.close(child)
            limits["scopes"] = tuple(scopes)
        resources._verify_directory_path(path, "inspection", identity)
        return Snapshot(path, rows, limits)
    finally:
        os.close(fd)


def read_file(path, limit, *, expected=None, keep=None, sink=None):
    """Hash one regular descriptor; optionally retain a prefix or copy its bytes."""
    path = absolute(path)
    parent, identity = resources._open_directory_path(path.parent, "inspection file")
    fd = None
    try:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
            raise InspectionError("Not a bounded regular file: " + str(path))
        if expected is not None and (resources._stat_identity(before) != tuple(expected["stat"])):
            raise InspectionError("File changed before analysis: " + str(path))
        fd = os.open(path.name, resources._open_flags(), dir_fd=parent)
        if resources._stat_identity(os.fstat(fd)) != resources._stat_identity(before):
            raise InspectionError("File replaced before analysis: " + str(path))
        digest = hashlib.sha256()
        chunks = []
        count = retained = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            if count > limit:
                raise InspectionError("File exceeds read budget: " + str(path))
            digest.update(chunk)
            if sink is not None:
                sink.write(chunk)
            if keep is None or retained < keep:
                piece = chunk if keep is None else chunk[: keep - retained]
                chunks.append(piece)
                retained += len(piece)
        after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if count != before.st_size or any(
            resources._stat_identity(value) != resources._stat_identity(before)
            for value in (after, os.fstat(fd))
        ):
            raise InspectionError("File changed while reading: " + str(path))
        facts = dict(size=count, sha256=digest.hexdigest(), stat=resources._stat_identity(before))
        if expected is not None and facts["sha256"] != expected["sha256"]:
            raise InspectionError("Analyzed bytes differ from inventory: " + str(path))
        resources._verify_directory_path(path.parent, "inspection file", identity)
        return b"".join(chunks), facts
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def check_destination(path, protected):
    path = absolute(path)
    for root in protected:
        root = absolute(root)
        if path == root or root in path.parents:
            raise InspectionError("Output overlaps a protected source: " + str(path))
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            raise InspectionError("Private inspection output must stay outside Git: " + str(path))
    return path


def write_report(path, report, protected):
    path = check_destination(path, protected)
    parent, identity = resources._open_directory_path(path.parent, "report output")
    fd = None
    try:
        fd = os.open(
            path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(report, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        resources._verify_directory_path(path.parent, "report output", identity)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def archive_snapshot(source, protected, workspace_parent=None):
    """Retain a new private source copy and frozen extraction; never edit source."""
    parent = absolute(workspace_parent or PRIVATE_ROOT)
    check_destination(parent / "new-inspection", protected)
    if workspace_parent is None:
        # Check existing ancestors before creating only our private cache path.
        existing = parent
        while not existing.exists():
            existing = existing.parent
        fd, identity = resources._open_directory_path(existing, "snapshot parent")
        os.close(fd)
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        resources._verify_directory_path(existing, "snapshot parent", identity)
    fd, identity = resources._open_directory_path(parent, "snapshot parent")
    try:
        workspace = Path(tempfile.mkdtemp(prefix="inspection-", dir=parent))
        resources._verify_directory_path(parent, "snapshot parent", identity)
    finally:
        os.close(fd)
    try:
        copied = workspace / "source.archive"
        with copied.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            _, facts = read_file(source, MAX_ARCHIVE_BYTES, keep=0, sink=stream)
            stream.flush()
            os.fsync(stream.fileno())
        copied.chmod(0o400)
        extracted = workspace / "extracted"
        result = extract_package(copied, extracted, facts["sha256"])
        read_file(source, MAX_ARCHIVE_BYTES, expected=facts, keep=0)
        archive = dict(
            wrapper=result.inspection.wrapper,
            entries=[
                dict(
                    archive_path=e.archive_path,
                    install_path=e.install_path,
                    is_dir=e.is_dir,
                    size=e.size,
                )
                for e in result.inspection.entries
            ],
        )
        return extracted, facts, workspace, archive
    except Exception as exc:
        raise InspectionError(
            f"Archive inspection refused; private snapshot retained at {workspace}: {exc}",
            archive_snapshot=str(workspace),
        ) from exc
