"""Stream verified canonical package members without installing or transforming them."""

from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import re
import stat

from .activation import _assert_no_symlink_ancestor
from .packages import _check, _digest, _library, inspect_package, PackageError
from .cold_storage import ColdStorageError, _checkpoint


def stream_members(path, consume, finish, *, wanted=None, cancelled=None):
    """Read exact inspected members once; callbacks receive bounded payload chunks."""
    path = Path(path)
    _assert_no_symlink_ancestor(path)
    if not re.fullmatch(r"[0-9a-f]{64}\.7z", path.name) or path.stat().st_nlink != 1:
        raise ColdStorageError("Archive reference requires a canonical independent original")
    expected = path.stem
    try:
        inspection = inspect_package(path)
        if inspection.source_sha256 != expected:
            raise ColdStorageError("Canonical archive bytes differ from their identity")
        entries = {entry.archive_path: entry for entry in inspection.entries}
        seen = set()
        lib = _library()
        archive = lib.archive_read_new()
        if not archive:
            raise ColdStorageError("Cannot initialize canonical archive reader")
        try:
            _check(
                lib.archive_read_support_filter_all(archive), lib, archive, "Enable archive filters"
            )
            _check(
                lib.archive_read_support_format_all(archive), lib, archive, "Enable archive formats"
            )
            _check(
                lib.archive_read_open_filename(archive, os.fsencode(path), 10240),
                lib,
                archive,
                "Open canonical archive",
            )
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _checkpoint(cancelled)
                header = ctypes.c_void_p()
                result = lib.archive_read_next_header(archive, ctypes.byref(header))
                if result == 1:
                    break
                _check(result, lib, archive, "Read canonical member")
                raw = lib.archive_entry_pathname(header)
                if raw is None:
                    raise ColdStorageError("Canonical member lost its path")
                name = raw.decode("utf-8", "strict")
                kind = lib.archive_entry_filetype(header)
                if (
                    inspection.wrapper
                    and name.rstrip("/") == inspection.wrapper
                    and kind == stat.S_IFDIR
                ):
                    _check(lib.archive_read_data_skip(archive), lib, archive, "Skip wrapper")
                    continue
                entry = entries.get(name)
                if (
                    entry is None
                    or name in seen
                    or kind != (stat.S_IFDIR if entry.is_dir else stat.S_IFREG)
                    or lib.archive_entry_size(header) != entry.size
                    or lib.archive_entry_symlink(header)
                    or lib.archive_entry_hardlink(header)
                ):
                    raise ColdStorageError("Canonical archive membership changed")
                seen.add(name)
                if entry.is_dir or (wanted is not None and name not in wanted):
                    _check(
                        lib.archive_read_data_skip(archive), lib, archive, "Skip canonical member"
                    )
                    continue
                digest, count = hashlib.sha256(), 0
                while True:
                    _checkpoint(cancelled)
                    amount = lib.archive_read_data(archive, buffer, len(buffer))
                    _check(amount, lib, archive, "Read canonical payload")
                    if amount == 0:
                        break
                    count += amount
                    if count > entry.size:
                        raise ColdStorageError("Canonical member exceeds its declared size")
                    chunk = buffer.raw[:amount]
                    digest.update(chunk)
                    consume(name, chunk)
                if count != entry.size:
                    raise ColdStorageError("Canonical member is truncated")
                finish(name, digest.hexdigest(), count)
            if seen != set(entries) or _digest(path) != expected:
                raise ColdStorageError("Canonical archive changed during streaming")
        finally:
            lib.archive_read_free(archive)
    except (OSError, UnicodeError, PackageError) as exc:
        raise ColdStorageError("Cannot verify canonical archive reference") from exc


def archive_references(path, *, cancelled=None):
    """Build eligibility from actual payload hashes, never names or inferred recipes."""
    references = {}
    path = Path(path)

    def finish(name, digest, size):
        references.setdefault(digest, dict(archive=path.stem, member=name, size=size))

    stream_members(path, lambda name, chunk: None, finish, cancelled=cancelled)
    return references
