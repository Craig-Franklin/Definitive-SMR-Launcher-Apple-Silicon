"""Bounded loose-model diagnostics; no conversion or gameplay certification."""
from __future__ import annotations

import os
import re
from pathlib import Path

HEADER_BYTES = 256
MAX_MODEL_FILES = 10000
MAX_TREE_ENTRIES = 100000
# The inspected Feral loader takes its legacy NiAVObject collision branch below
# this version. Whether a particular block enters collision initialization also
# depends on its flags, velocity and bounding volume; a header cannot prove that.
LEGACY_COLLISION_BOUNDARY = (5, 0, 0, 19)


def nif_header(data: bytes) -> dict:
    """Read a bounded, four-component NIF header and cross-check its binary version."""
    line, separator, tail = data[:HEADER_BYTES].partition(b"\n")
    match = re.fullmatch(
        rb"(?:NetImmerse|Gamebryo) File Format, Version (\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\r?",
        line)
    if not separator or not match:
        return dict(status="unrecognized", reason="Unrecognized or incomplete NIF header")
    version = tuple(int(part) for part in match.groups())
    if any(part > 255 for part in version):
        return dict(status="unrecognized", reason="NIF version component is out of range")
    # Earlier formats have different headers and are not parsed by this check.
    if version < (4, 0, 0, 2):
        return dict(status="unrecognized", reason="Earlier NIF header layout requires separate inspection")
    if len(tail) < 4 or int.from_bytes(tail[:4], "little") != int.from_bytes(bytes(version), "big"):
        return dict(status="inconsistent", reason="NIF text and binary versions are missing or disagree")
    return dict(status="recognized", version=".".join(map(str, version)),
                legacy_collision_path=version < LEGACY_COLLISION_BOUNDARY)


def inspect_models(root: Path) -> dict:
    checked = 0
    findings = []
    complete = True
    entries = 0
    candidates = 0
    limit_reached = False
    root = Path(root)

    def unread(path: Path, reason: str, *, incomplete: bool = False) -> None:
        nonlocal complete
        findings.append(dict(path=path.relative_to(root).as_posix(), status="unread", reason=reason))
        if incomplete:
            complete = False

    # The import root itself is a caller-provided boundary. Do not accept a
    # symlink here: resolving it would make the scan cover a different tree.
    try:
        root_is_symlink = root.is_symlink()
        root_is_dir = root.is_dir() if not root_is_symlink else False
    except OSError:
        root_is_symlink = False
        root_is_dir = False
    if root_is_symlink:
        return dict(files_checked=0, complete=False,
                    findings=[dict(path=".", status="unread", reason="Import root is a symlink")],
                    limitations="Loose NIF headers only; packed assets, block contents and resource precedence are not checked. A legacy header is a risk flag, not proof of a crash; other versions are not certified safe.")
    if not root_is_dir:
        return dict(files_checked=0, complete=False,
                    findings=[dict(path=".", status="unread", reason="Import root is not a readable directory")],
                    limitations="Loose NIF headers only; packed assets, block contents and resource precedence are not checked. A legacy header is a risk flag, not proof of a crash; other versions are not certified safe.")

    for name in ("CustomAssets", "UserMaps"):
        scope = root / name
        try:
            scope_exists = scope.exists() or scope.is_symlink()
            scope_is_symlink = scope.is_symlink()
        except OSError as exc:
            unread(scope, f"Asset scan scope could not be inspected: {exc.strerror or exc}", incomplete=True)
            continue
        if not scope_exists:
            continue
        if scope_is_symlink:
            unread(scope, "Asset scan scope is a symlink", incomplete=True)
            continue
        try:
            if not scope.is_dir():
                unread(scope, "Asset scan scope is not a directory", incomplete=True)
                continue
        except OSError as exc:
            unread(scope, f"Asset scan scope could not be inspected: {exc.strerror or exc}", incomplete=True)
            continue

        pending = [scope]
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as listing:
                    for child in listing:
                        entries += 1
                        if entries > MAX_TREE_ENTRIES:
                            complete = False
                            limit_reached = True
                            break
                        path = directory / child.name
                        is_nif = path.suffix.casefold() == ".nif"

                        try:
                            is_link = child.is_symlink()
                            is_directory = child.is_dir(follow_symlinks=False) if not is_link else False
                        except OSError as exc:
                            unread(path, f"Filesystem entry could not be inspected: {exc.strerror or exc}", incomplete=True)
                            continue

                        if is_link:
                            # Count NIF links (including dangling links) as unread
                            # candidates. No symlink target is followed, and
                            # omitted links always make the scan incomplete
                            # because their type or contents may be unknown.
                            if is_nif:
                                candidates += 1
                                if candidates > MAX_MODEL_FILES:
                                    complete = False
                                    limit_reached = True
                                    break
                            unread(path, "Model or directory link requires separate inspection",
                                   incomplete=True)
                            continue
                        if is_directory:
                            pending.append(path)
                            continue
                        if not is_nif:
                            continue

                        candidates += 1
                        if candidates > MAX_MODEL_FILES:
                            complete = False
                            limit_reached = True
                            break
                        try:
                            is_regular_file = child.is_file(follow_symlinks=False)
                        except OSError as exc:
                            unread(path, f"Filesystem entry could not be inspected: {exc.strerror or exc}", incomplete=True)
                            continue
                        if not is_regular_file:
                            unread(path, "Model entry is not a regular file", incomplete=True)
                            continue
                        checked += 1
                        try:
                            with path.open("rb") as stream:
                                result = nif_header(stream.read(HEADER_BYTES))
                        except OSError:
                            result = dict(status="unread", reason="Model header could not be read")
                            complete = False
                        if result["status"] != "recognized" or result["legacy_collision_path"]:
                            findings.append(dict(path=path.relative_to(root).as_posix(), **result))
            except OSError as exc:
                unread(directory, f"Directory could not be scanned: {exc.strerror or exc}", incomplete=True)
            if limit_reached:
                break
        if limit_reached:
            break
    return dict(files_checked=checked, complete=complete, findings=sorted(findings, key=lambda f: f["path"]),
                limitations="Loose NIF headers only; packed assets, block contents and resource precedence are not checked. A legacy header is a risk flag, not proof of a crash; other versions are not certified safe.")
