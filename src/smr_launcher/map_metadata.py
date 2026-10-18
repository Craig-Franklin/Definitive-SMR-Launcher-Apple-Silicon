"""Read package-authored map details without inventing provenance or dates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit
import os
import re
import stat
import xml.etree.ElementTree as ET


MAX_MAP_INFO_BYTES = 256 * 1024
_HEADERS = {
    "map name": "name",
    "map version": "version",
    "date created": "created",
    "date updated": "updated",
    "author": "author",
    "modified by": "modified_by",
    "type": "map_type",
}
_SOURCE_HEADERS = {"source", "source url", "map url", "website", "download url"}


@dataclass(frozen=True)
class MapMetadata:
    """Author-declared details; None means absent, invalid, or ambiguous.

    Dates describe the package author's declaration, never file timestamps or
    archive upload dates. raw_text retains the original decoded text, including
    unknown headers and unlabelled legacy details.
    """

    name: Optional[str] = None
    version: Optional[str] = None
    created: Optional[str] = None
    updated: Optional[str] = None
    author: Optional[str] = None
    modified_by: Optional[str] = None
    map_type: Optional[str] = None
    description: str = ""
    raw_text: str = ""
    source_urls: tuple[str, ...] = ()


def _date(value: str) -> Optional[str]:
    match = re.fullmatch(r"(\d{4})([-/])(\d{2})\2(\d{2})", value)
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[3]), int(match[4])).isoformat()
    except ValueError:
        return None


def safe_source_url(value: str) -> Optional[str]:
    """Accept a complete declared HTTP(S) URL, with no embedded credentials."""
    if not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    if "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        # Reading port validates malformed or out-of-range port declarations.
        parsed.port
    except ValueError:
        return None
    return value


def parse_map_metadata(payload: bytes) -> MapMetadata:
    """Parse bounded UTF-8 (or BOM-marked UTF-16) metadata; never infer dates.

    Only the initial header block is interpreted. Prose and legacy positional
    metadata remain available as description/raw_text. Conflicting duplicate
    headers are unknown rather than silently selecting one declaration.
    """
    if len(payload) > MAX_MAP_INFO_BYTES:
        return MapMetadata()
    try:
        encoding = "utf-16" if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = payload.decode(encoding)
    except UnicodeError:
        return MapMetadata()
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        return MapMetadata()
    values: dict[str, Optional[str]] = {}
    sources = []
    description = []
    in_headers = True
    seen_header = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped:
            if seen_header:
                in_headers = False
            description.append(line)
            continue
        label, separator, value = stripped.partition(":")
        label = " ".join(label.lower().split())
        if in_headers and separator and (label in _HEADERS or label in _SOURCE_HEADERS):
            seen_header = True
            value = value.strip()
            if label in _SOURCE_HEADERS:
                url = safe_source_url(value)
                if url and url not in sources:
                    sources.append(url)
            else:
                field = _HEADERS[label]
                normalized = _date(value) if field in {"created", "updated"} else value or None
                if field in values and values[field] != normalized:
                    values[field] = None
                else:
                    values[field] = normalized
        else:
            description.append(line)
            # Unknown headers are retained and do not hide following headers.
            # A non-header line begins the description even without a blank line.
            if not separator:
                in_headers = False
    return MapMetadata(
        **values,
        description="".join(description).strip(),
        raw_text=text,
        source_urls=tuple(sources),
    )


def _read_metadata(path: Path, maximum: int) -> bytes:
    """Read one regular file without following any symlink in its path.

    Descriptor-relative traversal also rejects directory substitutions during
    traversal. Missing, oversized, unreadable, and malformed files are unknown;
    they must not prevent browsing or change the immutable package.
    """
    path = Path(path).expanduser()
    if ".." in path.parts:
        return b""
    path = path.absolute()
    directory_fd = None
    file_fd = None
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory_fd = os.open(path.anchor, directory_flags)
        for component in path.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
        )
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            return b""
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = None
            payload = stream.read(maximum + 1)
        return payload if len(payload) <= maximum else b""
    except (OSError, ValueError):
        return b""
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def read_map_metadata(path: Path) -> MapMetadata:
    return parse_map_metadata(_read_metadata(path, MAX_MAP_INFO_BYTES))


def read_scenario_labels(path: Path) -> dict:
    """Read literal authored labels, never a rendered-title or Play assertion."""
    result = dict(title=None, subtitle=None)
    payload = _read_metadata(path, 8 * 1024 * 1024)
    declarations = payload.replace(b"\0", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        return result
    try:
        document = ET.fromstring(payload)
    except (ET.ParseError, ValueError):
        return result
    if document.tag != "RRTScenario":
        return result
    for field, tag in (("title", "szMapName"), ("subtitle", "szSubtitle")):
        nodes = document.findall("./" + tag)
        if len(nodes) == 1 and not len(nodes[0]):
            value = (nodes[0].text or "").strip()
            if value and len(value) <= 4000:
                result[field] = value
    return result
