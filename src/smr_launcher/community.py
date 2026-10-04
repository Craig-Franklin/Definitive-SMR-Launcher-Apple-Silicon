"""Optional upstream community declarations, separate from local Mac testing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import json
import os
import re
import ssl
import stat
import unicodedata
import uuid


COMMUNITY_INDEX_URL = (
    "https://raw.githubusercontent.com/ageekhere/Definitive-SMR-Launcher/"
    "main/map_ratings/map_ratings_list.json"
)
DISCUSSION_ROOT = "https://github.com/ageekhere/Definitive-SMR-Launcher/discussions/"
MAX_INDEX_BYTES = 1024 * 1024
MAX_COMMUNITY_MAPS = 2000
_MAX_CACHE_BYTES = MAX_INDEX_BYTES + 1024


class CommunityError(ValueError):
    """Optional community information is unavailable or invalid."""


@dataclass(frozen=True)
class CommunityMap:
    name: str
    discussion_url: str
    stability: str
    multiplayer: Optional[bool]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CommunityError("Community index contains duplicate keys")
        result[key] = value
    return result


def _decode(payload: bytes, maximum: int) -> dict:
    if len(payload) > maximum:
        raise CommunityError("Community index exceeds its size limit")
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CommunityError("Community index is not valid JSON") from exc
    if not isinstance(value, dict):
        raise CommunityError("Community index must be an object")
    return value


def _flag(value) -> Optional[bool]:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in {"y", "n"}:
        raise CommunityError("Community index has an invalid flag")
    return value == "y"


def _parse_document(document: dict) -> dict[str, CommunityMap]:
    if len(document) > MAX_COMMUNITY_MAPS:
        raise CommunityError("Community index exceeds its map limit")
    result = {}
    names = set()
    for stem, item in document.items():
        if (not isinstance(stem, str) or not stem or len(stem) > 252
                or stem.startswith(".") or stem != stem.strip()
                or unicodedata.normalize("NFC", stem) != stem
                or any(char in stem for char in "/\\:")
                or any(ord(char) < 32 or ord(char) == 127 for char in stem)):
            raise CommunityError("Community index has an unsafe archive name")
        if not isinstance(item, dict):
            raise CommunityError("Community map must be an object")
        name = item.get("map_name")
        discussion = item.get("url")
        if (not isinstance(name, str) or len(name) > 255
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
                or not isinstance(discussion, str)
                or not re.fullmatch(r"[1-9][0-9]{0,9}", discussion)):
            raise CommunityError("Community map name or discussion ID is invalid")
        stable = _flag(item.get("is_stable"))
        multiplayer = _flag(item.get("multiplayer"))
        # Upstream includes one empty template, not a playable map.
        if stem == "blank" and name == "":
            continue
        if not name.strip():
            raise CommunityError("Community map name is empty")
        filename = stem if stem.endswith(".7z") else stem + ".7z"
        if filename.casefold() in names:
            raise CommunityError("Community index contains ambiguous archive names")
        names.add(filename.casefold())
        label = "Not reported" if stable is None else "Reported stable" if stable else "Reported unstable"
        result[filename] = CommunityMap(name, DISCUSSION_ROOT + discussion, label, multiplayer)
    return result


def parse_community_index(payload: bytes) -> dict[str, CommunityMap]:
    """Return exact archive filenames; upstream flags do not confer Mac badges."""
    return _parse_document(_decode(payload, MAX_INDEX_BYTES))


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, newurl):
        raise CommunityError("Community index redirected away from its fixed source")


def _opener():
    # Keep certificate and hostname validation explicit; never weaken TLS.
    return build_opener(_NoRedirects(), HTTPSHandler(context=ssl.create_default_context()))


def _parent_fd(path: Path, *, create: bool = False) -> tuple[Path, int]:
    path = Path(path).expanduser()
    if ".." in path.parts:
        raise CommunityError("Community cache path contains a parent traversal")
    path = path.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:-1]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return path, fd
    except BaseException:
        os.close(fd)
        raise


def _cache_document(path: Path) -> dict:
    path, parent = _parent_fd(path)
    fd = None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_CACHE_BYTES:
            raise CommunityError("Community cache is not a bounded regular file")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            payload = stream.read(_MAX_CACHE_BYTES + 1)
        return _decode(payload, _MAX_CACHE_BYTES)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def read_cached_community_index(cache_path: Path) -> dict[str, CommunityMap]:
    """Read local cache only; absent, corrupt, or unsafe caches mean no records."""
    try:
        document = _cache_document(cache_path)
        if (type(document.get("schema")) is not int or document["schema"] != 1
                or document.get("source") != COMMUNITY_INDEX_URL
                or not isinstance(document.get("maps"), dict)
                or not isinstance(document.get("fetched_at"), str)):
            return {}
        if datetime.fromisoformat(document["fetched_at"]).tzinfo is None:
            return {}
        return _parse_document(document["maps"])
    except (OSError, ValueError, TypeError, RecursionError):
        return {}


def _write_cache(path: Path, document: dict) -> None:
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > _MAX_CACHE_BYTES:
        raise CommunityError("Community cache exceeds its size limit")
    path, parent = _parent_fd(path, create=True)
    temporary = ".community-" + uuid.uuid4().hex
    fd = None
    try:
        try:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise CommunityError("Community cache destination is not a regular file")
        except FileNotFoundError:
            pass
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def fetch_community_index(cache_path: Path) -> dict[str, CommunityMap]:
    """Explicitly refresh HTTPS data, validate all entries, then replace cache.

    Failures raise CommunityError and leave any previous cache intact. No fetch
    occurs during cached reads or imports, and no community ratings are posted.
    """
    request = Request(COMMUNITY_INDEX_URL, headers={"User-Agent": "Definitive-SMR-Launcher-Apple-Silicon"})
    try:
        with _opener().open(request, timeout=15) as response:
            if response.geturl() != COMMUNITY_INDEX_URL:
                raise CommunityError("Community response differs from its fixed source")
            payload = response.read(MAX_INDEX_BYTES + 1)
        document = _decode(payload, MAX_INDEX_BYTES)
        records = _parse_document(document)
        _write_cache(cache_path, {
            "schema": 1, "source": COMMUNITY_INDEX_URL,
            "fetched_at": datetime.now(timezone.utc).isoformat(), "maps": document,
        })
        return records
    except (OSError, ValueError) as exc:
        if isinstance(exc, CommunityError):
            raise
        raise CommunityError("Could not refresh community information") from exc
