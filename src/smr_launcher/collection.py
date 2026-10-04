"""Bounded Internet Archive map catalogue and verified private downloads."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event
from typing import Callable, Optional
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import hashlib
import json
import os
import re
import tempfile
import unicodedata

from .activation import _assert_no_symlink_ancestor, _fsync_dir
from .packages import MAX_ARCHIVE_BYTES


IDENTIFIER = "sid-meiers-railroads-custom-maps-collection"
METADATA_URL = "https://archive.org/metadata/" + IDENTIFIER
DOWNLOAD_ROOT = "https://archive.org/download/" + IDENTIFIER + "/"
COLLECTION_URL = "https://archive.org/details/" + IDENTIFIER
MAX_METADATA_BYTES = 8 * 1024 * 1024
USER_AGENT = "Definitive-SMR-Launcher-Apple-Silicon/0.2"


class CollectionError(ValueError):
    """Collection metadata or download is incomplete or unsafe."""


@dataclass(frozen=True)
class RemoteMap:
    name: str
    size: int
    sha1: str
    archive_modified: str = ""

    def __post_init__(self) -> None:
        if (not isinstance(self.name, str) or not self.name.lower().endswith(".7z")
                or len(self.name) > 255 or self.name.startswith(".")
                or unicodedata.normalize("NFC", self.name) != self.name
                or any(char in self.name for char in ("/", "\\", ":"))
                or any(ord(char) < 32 or ord(char) == 127 for char in self.name)):
            raise CollectionError("Map filename is unsafe")
        if (not isinstance(self.size, int) or not 0 < self.size <= MAX_ARCHIVE_BYTES
                or not isinstance(self.sha1, str) or not re.fullmatch(r"[0-9a-f]{40}", self.sha1)):
            raise CollectionError("Map size or SHA-1 is invalid")

    @property
    def url(self) -> str:
        return DOWNLOAD_ROOT + quote(self.name, safe="")

    @property
    def source_url(self) -> str:
        return COLLECTION_URL + "/" + quote(self.name, safe="")


class _ArchiveRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, newurl):
        parsed = urlparse(newurl)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (host == "archive.org" or host.endswith(".archive.org")):
            raise CollectionError("Map download redirected outside Internet Archive HTTPS")
        return super().redirect_request(request, file_pointer, code, message, headers, newurl)


def _opener():
    return build_opener(_ArchiveRedirects())


def parse_catalogue(payload: bytes) -> tuple[RemoteMap, ...]:
    if len(payload) > MAX_METADATA_BYTES:
        raise CollectionError("Collection metadata exceeds the size limit")
    try:
        document = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise CollectionError("Collection metadata is not valid JSON") from exc
    if not isinstance(document, dict) or not isinstance(document.get("metadata"), dict):
        raise CollectionError("Collection metadata is incomplete")
    if document["metadata"].get("identifier") != IDENTIFIER or not isinstance(document.get("files"), list):
        raise CollectionError("Collection identity or file list differs")
    if len(document["files"]) > 10000:
        raise CollectionError("Collection file list exceeds the limit")
    records = []
    names = set()
    for file in document["files"]:
        if not isinstance(file, dict) or file.get("source") != "original":
            continue
        name = file.get("name")
        if not isinstance(name, str) or not name.lower().endswith(".7z"):
            continue
        if (not name or len(name) > 255 or name.startswith(".")
                or unicodedata.normalize("NFC", name) != name
                or any(char in name for char in ("/", "\\", ":"))
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise CollectionError("Collection contains an unsafe map filename")
        try:
            size = int(file["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CollectionError("Map size is missing or invalid: " + name) from exc
        sha1 = file.get("sha1")
        if not 0 < size <= MAX_ARCHIVE_BYTES or not isinstance(sha1, str) or not re.fullmatch(r"[0-9a-f]{40}", sha1):
            raise CollectionError("Map size or SHA-1 is invalid: " + name)
        key = name.casefold()
        if key in names:
            raise CollectionError("Collection contains duplicate map names")
        names.add(key)
        # Archive mtime describes the uploaded file, not the map's creation.
        modified = ""
        try:
            modified = datetime.fromtimestamp(int(file.get("mtime", "")), timezone.utc).date().isoformat()
        except (ValueError, TypeError, OverflowError, OSError):
            pass
        records.append(RemoteMap(name, size, sha1, modified))
    return tuple(sorted(records, key=lambda item: item.name.casefold()))


def fetch_catalogue() -> tuple[RemoteMap, ...]:
    request = Request(METADATA_URL, headers={"User-Agent": USER_AGENT})
    try:
        with _opener().open(request, timeout=20) as response:
            payload = response.read(MAX_METADATA_BYTES + 1)
    except OSError as exc:
        raise CollectionError("Could not load the Internet Archive collection") from exc
    return parse_catalogue(payload)


Progress = Callable[[RemoteMap, str, int, int, str], None]


def _verified_cached(target: Path, record: RemoteMap) -> bool:
    if not target.is_file() or target.stat().st_size != record.size:
        return False
    digest = hashlib.sha1()
    with target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == record.sha1


def download_map(record: RemoteMap, private_directory: Path, *,
                 progress: Optional[Callable[[int, int], None]] = None,
                 cancelled: Optional[Callable[[], bool]] = None) -> Path:
    """Download once to a private file, checking the archive's declared length and SHA-1."""
    directory = Path(private_directory).expanduser()
    _assert_no_symlink_ancestor(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (record.sha1 + ".7z")
    _assert_no_symlink_ancestor(target)
    if cancelled and cancelled():
        raise CollectionError("Download canceled")
    if target.exists():
        if _verified_cached(target, record):
            if progress:
                progress(record.size, record.size)
            return target
        raise CollectionError("Previously downloaded map differs from collection metadata")
    fd, name = tempfile.mkstemp(prefix=".map-download-", dir=directory)
    stage = Path(name)
    try:
        request = Request(record.url, headers={"User-Agent": USER_AGENT})
        with os.fdopen(fd, "wb") as output, _opener().open(request, timeout=60) as response:
            count = 0
            digest = hashlib.sha1()
            if progress:
                progress(0, record.size)
            while True:
                if cancelled and cancelled():
                    raise CollectionError("Download canceled")
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                count += len(chunk)
                if count > record.size:
                    raise CollectionError("Map download exceeds its declared size")
                output.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(count, record.size)
            output.flush()
            os.fsync(output.fileno())
        if count != record.size or digest.hexdigest() != record.sha1:
            raise CollectionError("Map download failed size or SHA-1 verification")
        if cancelled and cancelled():
            raise CollectionError("Download canceled")
        stage.chmod(0o400)
        try:
            os.link(stage, target)
        except FileExistsError:
            if not _verified_cached(target, record):
                raise CollectionError("Concurrent map download differs from collection metadata")
        _fsync_dir(directory)
        return target
    finally:
        stage.unlink(missing_ok=True)
        _fsync_dir(directory)


def download_all_maps(records: tuple[RemoteMap, ...], private_directory: Path,
                      progress: Progress, cancelled: Event, *,
                      importer: Optional[Callable[[RemoteMap, Path], object]] = None,
                      max_workers: int = 4) -> tuple[int, int, int]:
    """Download in a bounded pool; import verified archives serially if asked."""
    if not 1 <= max_workers <= 4:
        raise CollectionError("Parallel download worker count is outside the limit")
    completed = failed = stopped = 0

    def one(record: RemoteMap) -> tuple[str, Optional[Path]]:
        if cancelled.is_set():
            progress(record, "Canceled", 0, record.size, "")
            return "canceled", None
        progress(record, "Downloading", 0, record.size, "")
        try:
            path = download_map(record, private_directory,
                                progress=lambda count, total: progress(record, "Downloading", count, total, ""),
                                cancelled=cancelled.is_set)
        except CollectionError as exc:
            if cancelled.is_set() and str(exc) == "Download canceled":
                progress(record, "Canceled", 0, record.size, "")
                return "canceled", None
            progress(record, "Failed", 0, record.size, str(exc))
            return "failed", None
        except OSError as exc:
            progress(record, "Failed", 0, record.size, str(exc))
            return "failed", None
        progress(record, "Archive verified", record.size, record.size, "")
        return "completed", path

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="map-download") as pool:
        futures = {pool.submit(one, record): record for record in records}
        for future in as_completed(futures):
            record = futures[future]
            result, path = future.result()
            if result == "completed":
                if importer is not None:
                    if cancelled.is_set():
                        progress(record, "Canceled", record.size, record.size, "Archive retained")
                        stopped += 1
                        continue
                    progress(record, "Importing", record.size, record.size, "")
                    try:
                        importer(record, path)
                    except Exception as exc:
                        progress(record, "Failed", record.size, record.size, str(exc))
                        failed += 1
                        continue
                progress(record, "Imported" if importer is not None else "Archive verified",
                         record.size, record.size, "")
                completed += 1
            elif result == "failed":
                failed += 1
            else:
                stopped += 1
    return completed, failed, stopped
