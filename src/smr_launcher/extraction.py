"""Independent, bounded package extraction into a caller-owned private location."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
import ctypes
import errno
import hashlib
import os
import shutil
import stat
import sys
import tempfile

from .packages import PackageError, PackageInspection, _check, _digest, _library, inspect_package


@dataclass(frozen=True)
class ExtractionResult:
    inspection: PackageInspection
    file_sha256: Dict[str, str]


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _walk_error(error: OSError) -> None:
    raise error


def _freeze_tree(root: Path) -> None:
    for base, directories, files in os.walk(root, topdown=False, onerror=_walk_error):
        here = Path(base)
        for name in files:
            path = here / name
            path.chmod(stat.S_IRUSR)
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        here.chmod(stat.S_IRUSR | stat.S_IXUSR)
        _fsync_directory(here)


def _unfreeze_directories(root: Path) -> None:
    for base, directories, files in os.walk(root, topdown=False, onerror=_walk_error):
        Path(base).chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def _publish_exclusive(source: Path, destination: Path) -> None:
    if sys.platform != "darwin":
        raise PackageError("Exclusive prepared-directory publication requires macOS")
    function = ctypes.CDLL(None, use_errno=True).renamex_np
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    # The macOS 15 CI runner returned EACCES for a frozen mode-0500 directory.
    # Keep its contents frozen and give only the source root temporary owner
    # write permission. The open descriptor restores the exact same inode even
    # after its name changes; the destination is never reopened to chmod it.
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    original_mode = stat.S_IMODE(os.fstat(fd).st_mode)
    try:
        os.fchmod(fd, original_mode | stat.S_IWUSR)
        if function(os.fsencode(source), os.fsencode(destination), 0x00000004):
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise PackageError("Prepared destination already exists")
            raise OSError(error, os.strerror(error), str(source), str(destination))
    finally:
        try:
            os.fchmod(fd, original_mode)
            os.fsync(fd)
        finally:
            os.close(fd)
    _fsync_directory(destination.parent)


def _private_parent(destination: Path) -> Path:
    parent = destination.parent
    if not parent.is_dir() or destination.exists() or destination.is_symlink():
        raise PackageError("Prepared destination needs an existing parent and a new name")
    for part in (parent, *parent.parents):
        if part.is_symlink():
            raise PackageError("Prepared destination has a linked parent")
    return parent


def extract_package(
    source: Path, destination: Path, expected_sha256: Optional[str] = None
) -> ExtractionResult:
    """Copy a checked package into a fresh directory; never edit the archive.

    This is an import copy, not a game profile. The caller must build a separate
    complete profile and bind it to an observed installation before activation.
    """
    source = Path(source).expanduser()
    destination = Path(destination).expanduser()
    # macOS exposes /var as a system link to /private/var. Canonicalize that
    # test/application-support alias, but reject other caller-supplied links.
    for part in (destination.parent, *destination.parent.parents):
        if part.is_symlink() and part != Path("/var"):
            raise PackageError("Prepared destination has a linked parent")
    destination = destination.parent.resolve() / destination.name
    inspection = inspect_package(source)
    if expected_sha256 is not None and inspection.source_sha256 != expected_sha256:
        raise PackageError("Package hash differs from the requested original")
    parent = _private_parent(destination)
    stage = Path(tempfile.mkdtemp(prefix=".smr-import-", dir=parent))
    hashes: Dict[str, str] = {}
    lib = _library()
    archive = lib.archive_read_new()
    if not archive:
        shutil.rmtree(stage)
        raise PackageError("Could not initialize archive reader")
    entries = list(inspection.entries)
    position = 0
    try:
        _check(lib.archive_read_support_filter_all(archive), lib, archive, "Enable archive filters")
        _check(lib.archive_read_support_format_all(archive), lib, archive, "Enable archive formats")
        _check(lib.archive_read_open_filename(archive, os.fsencode(source), 10240), lib, archive, "Open package")
        while True:
            header = ctypes.c_void_p()
            result = lib.archive_read_next_header(archive, ctypes.byref(header))
            if result == 1:
                break
            _check(result, lib, archive, "Read package entry")
            raw = lib.archive_entry_pathname(header)
            if raw is None:
                raise PackageError("Package entry lost its name")
            name = raw.decode("utf-8", "strict")
            kind = lib.archive_entry_filetype(header)
            if inspection.wrapper and name.rstrip("/") == inspection.wrapper and kind == stat.S_IFDIR:
                _check(lib.archive_read_data_skip(archive), lib, archive, "Skip package wrapper")
                continue
            if position >= len(entries) or name != entries[position].archive_path:
                raise PackageError("Package entry changed during extraction")
            expected = entries[position]
            position += 1
            if kind != (stat.S_IFDIR if expected.is_dir else stat.S_IFREG):
                raise PackageError("Package entry type changed during extraction")
            if lib.archive_entry_size(header) != expected.size:
                raise PackageError("Package entry size changed during extraction")
            if lib.archive_entry_symlink(header) or lib.archive_entry_hardlink(header):
                raise PackageError("Package link appeared during extraction")
            output = stage.joinpath(*expected.install_path.split("/"))
            output.parent.mkdir(parents=True, exist_ok=True)
            if expected.is_dir:
                output.mkdir(exist_ok=True)
                _check(lib.archive_read_data_skip(archive), lib, archive, "Skip package directory")
                continue
            digest = hashlib.sha256()
            count = 0
            buffer = ctypes.create_string_buffer(1 << 16)
            with output.open("xb") as stream:
                while True:
                    amount = lib.archive_read_data(archive, buffer, len(buffer))
                    _check(amount, lib, archive, "Read package payload")
                    if amount == 0:
                        break
                    count += amount
                    if count > expected.size:
                        raise PackageError("Package entry exceeds its declared size")
                    chunk = buffer.raw[:amount]
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if count != expected.size:
                raise PackageError("Package entry is shorter than declared")
            hashes[expected.install_path] = digest.hexdigest()
        if position != len(entries) or _digest(source) != inspection.source_sha256:
            raise PackageError("Package changed before extraction completed")
        for base, directories, files in os.walk(stage, onerror=_walk_error):
            for name in files:
                path = Path(base) / name
                relative = path.relative_to(stage).as_posix()
                if _digest(path) != hashes.get(relative):
                    raise PackageError("Extracted copy failed readback: " + relative)
        for base, directories, files in os.walk(stage, topdown=False, onerror=_walk_error):
            _fsync_directory(Path(base))
        _freeze_tree(stage)
        _publish_exclusive(stage, destination)
        return ExtractionResult(inspection, hashes)
    finally:
        lib.archive_read_free(archive)
        if stage.exists():
            _unfreeze_directories(stage)
            shutil.rmtree(stage)
