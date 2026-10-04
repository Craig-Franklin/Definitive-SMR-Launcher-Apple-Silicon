"""Bounded, read-only inspection of custom-map archives through macOS libarchive.

Archive contents are never written by this module. Callers must keep original
packages separate from prepared game generations and treat inspection as static
evidence, not as a successful game test.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
import ctypes
import ctypes.util
import hashlib
import os
import stat
import unicodedata


class PackageError(ValueError):
    """Archive cannot be safely treated as one conventional map package."""


MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
INSTALL_ROOTS = {"CustomAssets", "UserMaps"}
METADATA_FILES = {"mapIcon.jpg", "mapInfo.txt"}


@dataclass(frozen=True)
class PackageEntry:
    archive_path: str
    install_path: str
    is_dir: bool
    size: int


@dataclass(frozen=True)
class PackageInspection:
    source_sha256: str
    source_size: int
    wrapper: Optional[str]
    entries: Tuple[PackageEntry, ...]
    scenarios: Tuple[str, ...]
    total_uncompressed_bytes: int


def _library():
    location = ctypes.util.find_library("archive")
    if not location:
        raise PackageError("macOS libarchive is unavailable")
    lib = ctypes.CDLL(location)
    signatures = (
        ("archive_read_new", ctypes.c_void_p, []),
        ("archive_read_support_filter_all", ctypes.c_int, [ctypes.c_void_p]),
        ("archive_read_support_format_all", ctypes.c_int, [ctypes.c_void_p]),
        ("archive_read_open_filename", ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]),
        ("archive_read_next_header", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]),
        ("archive_read_data_skip", ctypes.c_int, [ctypes.c_void_p]),
        ("archive_read_data", ctypes.c_ssize_t, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]),
        ("archive_read_free", ctypes.c_int, [ctypes.c_void_p]),
        ("archive_error_string", ctypes.c_char_p, [ctypes.c_void_p]),
        ("archive_entry_pathname", ctypes.c_char_p, [ctypes.c_void_p]),
        ("archive_entry_filetype", ctypes.c_uint, [ctypes.c_void_p]),
        ("archive_entry_size", ctypes.c_int64, [ctypes.c_void_p]),
        ("archive_entry_symlink", ctypes.c_char_p, [ctypes.c_void_p]),
        ("archive_entry_hardlink", ctypes.c_char_p, [ctypes.c_void_p]),
    )
    for name, result, arguments in signatures:
        function = getattr(lib, name)
        function.restype = result
        function.argtypes = arguments
    return lib


def _check(result: int, lib, archive, action: str) -> None:
    if result < 0:
        detail = lib.archive_error_string(archive)
        message = detail.decode("utf-8", "replace") if detail else "unknown archive error"
        raise PackageError(action + ": " + message)


def _safe_path(value: str) -> Tuple[str, ...]:
    if not value or len(value) > 1024 or value.startswith("/") or "\\" in value:
        raise PackageError("Unsafe archive path: " + repr(value))
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PackageError("Control character in archive path")
    stripped = value[:-1] if value.endswith("/") else value
    parts = tuple(stripped.split("/"))
    if not parts or any(part in ("", ".", "..") or ":" in part for part in parts):
        raise PackageError("Unsafe archive path: " + repr(value))
    return parts


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            result.update(chunk)
    return result.hexdigest()


def inspect_package(path: Path) -> PackageInspection:
    """Inspect a 7z/other supported archive without extracting any payload."""
    source = Path(path).expanduser()
    if source.is_symlink() or not source.is_file():
        raise PackageError("Package must be a regular, nonlinked file")
    size = source.stat().st_size
    if size == 0 or size > MAX_ARCHIVE_BYTES:
        raise PackageError("Package size is empty or above the inspection limit")
    before = _digest(source)
    lib = _library()
    archive = lib.archive_read_new()
    if not archive:
        raise PackageError("Could not initialize archive reader")
    raw: List[Tuple[str, Tuple[str, ...], bool, int]] = []
    total = 0
    try:
        _check(lib.archive_read_support_filter_all(archive), lib, archive, "Enable archive filters")
        _check(lib.archive_read_support_format_all(archive), lib, archive, "Enable archive formats")
        _check(lib.archive_read_open_filename(archive, os.fsencode(source), 10240), lib, archive, "Open package")
        while True:
            entry = ctypes.c_void_p()
            result = lib.archive_read_next_header(archive, ctypes.byref(entry))
            if result == 1:  # ARCHIVE_EOF
                break
            _check(result, lib, archive, "Read package entry")
            if len(raw) >= MAX_ENTRIES:
                raise PackageError("Package has too many entries")
            pathname = lib.archive_entry_pathname(entry)
            if pathname is None:
                raise PackageError("Package entry has no name")
            try:
                name = pathname.decode("utf-8", "strict")
            except UnicodeError as exc:
                raise PackageError("Package entry name is not UTF-8") from exc
            parts = _safe_path(name)
            kind = lib.archive_entry_filetype(entry)
            if lib.archive_entry_symlink(entry) or lib.archive_entry_hardlink(entry):
                raise PackageError("Package contains a linked entry: " + name)
            if kind not in (stat.S_IFDIR, stat.S_IFREG):
                raise PackageError("Package contains a special entry: " + name)
            is_dir = kind == stat.S_IFDIR
            length = lib.archive_entry_size(entry)
            if length < 0 or (is_dir and length) or length > MAX_ENTRY_BYTES:
                raise PackageError("Invalid or oversized entry: " + name)
            total += length
            if total > MAX_TOTAL_BYTES:
                raise PackageError("Package expands beyond the inspection limit")
            raw.append((name, parts, is_dir, length))
            _check(lib.archive_read_data_skip(archive), lib, archive, "Skip package data")
    finally:
        lib.archive_read_free(archive)
    if _digest(source) != before:
        raise PackageError("Package changed during inspection")
    if not raw:
        raise PackageError("Package is empty")
    firsts = {item[1][0] for item in raw}
    wrapper = None
    if len(firsts) == 1 and not firsts.intersection(INSTALL_ROOTS | METADATA_FILES):
        candidate = next(iter(firsts))
        if any(len(parts) > 1 and parts[1] == "UserMaps" for _, parts, _, _ in raw):
            wrapper = candidate
    entries: List[PackageEntry] = []
    keys = {}
    scenario_paths = []
    found_user_maps = False
    for name, parts, is_dir, length in raw:
        if wrapper:
            if parts == (wrapper,):
                if not is_dir:
                    raise PackageError("Archive wrapper must be a directory")
                continue
            parts = parts[1:]
        top = parts[0]
        if top == "UserMaps":
            found_user_maps = True
        if top in METADATA_FILES:
            if len(parts) != 1 or is_dir:
                raise PackageError("Invalid package metadata entry: " + name)
        elif top not in INSTALL_ROOTS:
            raise PackageError("Unexpected package root: " + top)
        elif len(parts) == 1 and not is_dir:
            raise PackageError("Install root is a file: " + top)
        normalized = "/".join(parts)
        key = unicodedata.normalize("NFC", normalized).casefold()
        if key in keys:
            raise PackageError("Duplicate or case-colliding package path: " + normalized)
        keys[key] = "dir" if is_dir else "file"
        entries.append(PackageEntry(name, normalized, is_dir, length))
        if not is_dir and top == "UserMaps" and parts[-1].lower().startswith("rrt_scenario_") and parts[-1].lower().endswith(".xml"):
            scenario_paths.append(normalized)
    for entry in entries:
        parts = entry.install_path.split("/")
        for index in range(1, len(parts)):
            key = unicodedata.normalize("NFC", "/".join(parts[:index])).casefold()
            if keys.get(key) == "file":
                raise PackageError("File occupies a package directory: " + entry.install_path)
    if not found_user_maps or not scenario_paths:
        raise PackageError("No UserMaps scenario was found")
    return PackageInspection(before, size, wrapper, tuple(entries), tuple(sorted(scenario_paths)), total)
