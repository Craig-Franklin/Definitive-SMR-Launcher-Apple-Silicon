"""Immutable, operation-local gallery presentation; no Tk or gameplay cache."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import stat
import struct

from .map_metadata import MapMetadata

# Prepared icons are at most 360px. Oversized/invalid art and icons beyond
# the operation's aggregate budget use the existing placeholder card artwork.
MAX_ART_BYTES = 1024 * 1024
MAX_ART_DIMENSION = 360
MAX_SNAPSHOT_ART_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class GalleryItem:
    record: object
    metadata: MapMetadata
    verification: str
    notice: str = ""
    art: bytes = b""
    art_key: str = ""


def _read_art(path, maximum=MAX_ART_BYTES):
    """Read a bounded regular PNG using descriptor-relative, no-follow traversal."""
    if path is None:
        return b""
    path = Path(path).absolute()
    if ".." in path.parts:
        return b""
    parent = fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(path.anchor, flags)
        for part in path.parts[1:-1]:
            next_fd = os.open(part, flags, dir_fd=parent)
            os.close(parent)
            parent = next_fd
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            return b""
        with os.fdopen(fd, "rb") as stream:
            fd = None
            data = stream.read(maximum + 1)
        if len(data) > maximum or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR" or len(data) < 24:
            return b""
        width, height = struct.unpack(">II", data[16:24])
        return data if 0 < width <= MAX_ART_DIMENSION and 0 < height <= MAX_ART_DIMENSION else b""
    except OSError:
        return b""
    finally:
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)


def gallery_snapshot(app, cancelled, progress=None):
    """Re-read secure metadata and contextual verification for this presentation."""
    result = []
    remaining_art = MAX_SNAPSHOT_ART_BYTES
    records = app.catalogue()
    if progress:
        progress(0, len(records))
    for record in records:
        if cancelled.is_set():
            return ()
        metadata = app.map_metadata(record)
        notice = ""
        try:
            verification = app.verification_for(record)
            label = verification.status if verification else "Not verified"
        except Exception as exc:
            label = "Needs inspection"
            notice = "Gameplay record needs inspection: " + str(exc)
        art = _read_art(app.map_icon(record), min(MAX_ART_BYTES, remaining_art)) if remaining_art else b""
        remaining_art -= len(art)
        result.append(GalleryItem(record, metadata, label, notice, art,
                                  hashlib.sha256(art).hexdigest() if art else ""))
        if progress:
            progress(len(result), len(records))
    return tuple(result) if not cancelled.is_set() else ()


def filter_snapshot(snapshot, query, wanted, order):
    query = query.strip().casefold()
    records = []
    for item in snapshot:
        record, metadata = item.record, item.metadata
        if query not in " ".join((record.name, metadata.name or "", metadata.author or "", metadata.description)).casefold():
            continue
        if wanted in ("Single player", "Multiplayer") and wanted.replace(" ", "").casefold() not in (metadata.map_type or "").replace(" ", "").casefold():
            continue
        if wanted in ("Verified", "Not verified", "Known issue") and item.verification != wanted:
            continue
        records.append(item)
    if order in ("Created newest", "Created oldest", "Updated newest"):
        field = "updated" if order == "Updated newest" else "created"
        known = [r for r in records if getattr(r.metadata, field)]
        unknown = [r for r in records if not getattr(r.metadata, field)]
        known.sort(key=lambda r: (getattr(r.metadata, field), r.record.name.casefold()), reverse=order != "Created oldest")
        return tuple(known + sorted(unknown, key=lambda r: r.record.name.casefold()))
    return tuple(sorted(records, key=lambda r: ((r.metadata.author or "~").casefold() if order == "Author" else "", r.record.name.casefold())))
