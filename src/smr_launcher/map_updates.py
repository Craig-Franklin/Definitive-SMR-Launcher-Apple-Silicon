"""Conservative map update discovery; importing creates a separate variant."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Protocol
from urllib.parse import quote
import re

from .collection import COLLECTION_URL, RemoteMap


_VERSIONED_ARCHIVE = re.compile(r"(.+)_v([0-9]{1,4})_([0-9]{1,4})(?:_([0-9]{1,4}))?\.7z")


class InstalledMap(Protocol):
    variant_id: str
    archive_filename: str
    source_url: str


@dataclass(frozen=True)
class MapUpdate:
    installed_variant_id: str
    installed_filename: str
    remote: RemoteMap
    kind: str
    installed_version: str
    available_version: str


def _version(name: str) -> Optional[tuple[str, tuple[int, int, int]]]:
    match = _VERSIONED_ARCHIVE.fullmatch(name)
    if not match:
        return None
    return match[1], (int(match[2]), int(match[3]), int(match[4] or 0))


def _version_label(value: Optional[tuple[str, tuple[int, int, int]]]) -> str:
    return ".".join(str(part) for part in value[1]) if value else ""


def _known_source(record: InstalledMap) -> bool:
    name = record.archive_filename
    return (isinstance(name, str) and bool(name) and not name.startswith(".")
            and not any(char in name for char in "/\\:")
            and record.source_url == COLLECTION_URL + "/" + quote(name, safe=""))


def find_map_updates(installed_records: Iterable[InstalledMap],
                     remotes: Iterable[RemoteMap]) -> tuple[MapUpdate, ...]:
    """Discover exact-family newer versions or verified same-name revisions.

    Only installed records with canonical collection provenance participate.
    Names, dates, and Mac verification badges never establish package identity.
    Same-name revisions require a previously verified archive_sha1 on the record;
    absence of that field leaves revisions unknown. No files or saves are changed.
    """
    installed = [record for record in installed_records if _known_source(record)]
    remote_records = tuple(remotes)
    # Ambiguous input is excluded rather than selecting an arbitrary revision.
    by_name: dict[str, list[RemoteMap]] = {}
    for remote in remote_records:
        by_name.setdefault(remote.name, []).append(remote)
    unique = {name: items[0] for name, items in by_name.items() if len(items) == 1}
    by_family: dict[str, list[tuple[tuple[int, int, int], RemoteMap]]] = {}
    for remote in unique.values():
        parsed = _version(remote.name)
        if parsed:
            by_family.setdefault(parsed[0], []).append((parsed[1], remote))
    installed_highest = {}
    for record in installed:
        parsed = _version(record.archive_filename)
        if parsed:
            installed_highest[parsed[0]] = max(installed_highest.get(parsed[0], (0, 0, 0)), parsed[1])
    results = []
    seen = set()
    for record in installed:
        parsed = _version(record.archive_filename)
        newer = None
        if parsed:
            candidates = [(version, remote) for version, remote in by_family.get(parsed[0], [])
                          if version > installed_highest[parsed[0]]]
            if candidates:
                maximum = max(version for version, _ in candidates)
                matches = [remote for version, remote in candidates if version == maximum]
                if len(matches) == 1:
                    newer = matches[0]
        candidate = newer or unique.get(record.archive_filename)
        if candidate is None:
            continue
        if newer:
            kind = "new_version"
        else:
            previous_sha1 = getattr(record, "archive_sha1", "")
            if (not isinstance(previous_sha1, str) or not re.fullmatch(r"[0-9a-f]{40}", previous_sha1)
                    or previous_sha1 == candidate.sha1):
                continue
            # If these exact current bytes are already present, no import is due.
            if any(other.archive_filename == candidate.name
                   and getattr(other, "archive_sha1", "") == candidate.sha1 for other in installed):
                continue
            kind = "revised_archive"
        identity = (record.variant_id, candidate.name, candidate.sha1)
        if identity in seen:
            continue
        seen.add(identity)
        results.append(MapUpdate(record.variant_id, record.archive_filename, candidate, kind,
                                 _version_label(parsed), _version_label(_version(candidate.name))))
    return tuple(sorted(results, key=lambda item: (item.installed_filename, item.installed_variant_id)))
