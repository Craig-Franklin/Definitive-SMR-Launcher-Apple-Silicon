"""Exact, recoverable cold trees using shared compressed bytes and per-tree metadata.

Callers own application/profile locks and stopped-state admission. This module never
selects a tree, prunes blobs, follows links, or changes an active game profile.
"""

from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import uuid
import zlib

from .activation import (
    _assert_no_symlink_ancestor,
    _assert_regular_tree,
    _atomic_json,
    _fsync_dir,
    _fsync_tree,
)
from .extraction import _unfreeze_directories, _publish_exclusive


class ColdStorageError(RuntimeError):
    """Cold data needs inspection; original or recoverable bytes remain retained."""


def _relative(value):
    if not isinstance(value, str):
        raise ColdStorageError("Invalid cold-storage path")
    path = PurePosixPath(value)
    if (
        not value
        or not path.parts
        or path.is_absolute()
        or path.as_posix() != value
        or any(p in (".", "..") for p in path.parts)
        or "\\" in value
        or "\0" in value
    ):
        raise ColdStorageError("Invalid cold-storage path")
    return value


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _checkpoint(cancelled):
    if cancelled is not None and cancelled():
        raise ColdStorageError("Storage preparation cancelled")


def _xattrs(path):
    if sys.platform != "darwin":
        return {
            name: os.getxattr(path, name, follow_symlinks=False)
            for name in os.listxattr(path, follow_symlinks=False)
        }
    library = ctypes.CDLL(None, use_errno=True)
    library.listxattr.restype = ctypes.c_ssize_t
    library.getxattr.restype = ctypes.c_ssize_t
    raw_path = os.fsencode(path)
    size = library.listxattr(raw_path, None, ctypes.c_size_t(0), 1)
    if size < 0:
        raise OSError(ctypes.get_errno(), "Cannot list extended attributes")
    buffer = ctypes.create_string_buffer(size)
    if library.listxattr(raw_path, buffer, ctypes.c_size_t(size), 1) != size:
        raise ColdStorageError("Extended attributes changed")
    result = {}
    for name in buffer.raw.split(b"\0"):
        if not name:
            continue
        length = library.getxattr(raw_path, name, None, ctypes.c_size_t(0), 0, 1)
        if length < 0:
            raise OSError(ctypes.get_errno(), "Cannot read extended attribute")
        value = ctypes.create_string_buffer(length)
        if library.getxattr(raw_path, name, value, ctypes.c_size_t(length), 0, 1) != length:
            raise ColdStorageError("Extended attribute changed")
        result[os.fsdecode(name)] = value.raw
    return result


def _set_xattr(path, name, data):
    if sys.platform != "darwin":
        os.setxattr(path, name, data, follow_symlinks=False)
        return
    function = ctypes.CDLL(None, use_errno=True).setxattr
    if function(os.fsencode(path), os.fsencode(name), data, ctypes.c_size_t(len(data)), 0, 1):
        raise OSError(ctypes.get_errno(), "Cannot restore extended attribute")


def _acl(path, text=None):
    if sys.platform != "darwin":
        return None
    library = ctypes.CDLL(None, use_errno=True)
    library.acl_get_file.restype = ctypes.c_void_p
    library.acl_from_text.restype = ctypes.c_void_p
    library.acl_init.restype = ctypes.c_void_p
    library.acl_to_text.restype = ctypes.c_void_p
    library.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.acl_free.argtypes = [ctypes.c_void_p]
    library.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    acl = (
        library.acl_get_file(os.fsencode(path), 0x100)
        if text is None
        else (library.acl_from_text(text.encode()) if text else library.acl_init(0))
    )
    if not acl and text is None and ctypes.get_errno() == errno.ENOENT:
        return ""
    if not acl:
        raise OSError(ctypes.get_errno(), "Cannot read preservation ACL")
    try:
        if text is not None:
            if library.acl_set_file(os.fsencode(path), 0x100, acl):
                raise OSError(ctypes.get_errno(), "Cannot restore preservation ACL")
            return None
        raw = library.acl_to_text(acl, None)
        if not raw:
            raise OSError(ctypes.get_errno(), "Cannot serialize preservation ACL")
        try:
            return ctypes.string_at(raw).decode()
        finally:
            library.acl_free(raw)
    finally:
        library.acl_free(acl)


def _identity(path):
    info = path.lstat()
    return dict(device=info.st_dev, inode=info.st_ino, birth=getattr(info, "st_birthtime", None))


def _same_inode(first, second):
    return all(first[key] == second[key] for key in ("device", "inode"))


def _metadata(path):
    info = path.lstat()
    if getattr(info, "st_flags", 0) or stat.S_IMODE(info.st_mode) & ~0o777:
        raise ColdStorageError("Flagged or special-mode files require separate preservation")
    return dict(
        mode=stat.S_IMODE(info.st_mode),
        mtime_ns=info.st_mtime_ns,
        uid=info.st_uid,
        gid=info.st_gid,
        acl=_acl(path),
        xattrs={
            name: base64.b64encode(data).decode() for name, data in sorted(_xattrs(path).items())
        },
    )


def _restore_metadata(path, entry):
    if (path.stat().st_uid, path.stat().st_gid) != (entry["uid"], entry["gid"]):
        os.chown(path, entry["uid"], entry["gid"])
    for name in _xattrs(path):
        if name in entry["xattrs"]:
            continue
        if sys.platform == "darwin":
            function = ctypes.CDLL(None, use_errno=True).removexattr
            if function(os.fsencode(path), os.fsencode(name), 1):
                raise OSError(ctypes.get_errno(), "Cannot remove inherited extended attribute")
        else:
            os.removexattr(path, name, follow_symlinks=False)
    for name, data in entry["xattrs"].items():
        _set_xattr(path, name, base64.b64decode(data, validate=True))
    os.chmod(path, entry["mode"])
    if entry["acl"] is not None:
        _acl(path, entry["acl"])
    os.utime(path, ns=(entry["mtime_ns"], entry["mtime_ns"]))


class ColdStorage:
    def __init__(self, library: Path, *, fault_hook=None):
        self.library = Path(library)
        self.blobs = self.library / "cold-storage/blobs"
        self.fault_hook = fault_hook or (lambda stage: None)
        self._reference_cache = None

    def _path(self, path):
        path = Path(path)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path == self.library
            or not path.is_relative_to(self.library)
            or path.is_relative_to(self.library / "cold-storage")
        ):
            raise ColdStorageError("Cold tree must be inside its library")
        _assert_no_symlink_ancestor(path)
        return path

    def _index(self, path):
        path = self._path(path)
        result = path.with_name(path.name + ".cold.json")
        _assert_no_symlink_ancestor(result)
        return result

    def is_cold(self, path):
        # Live profiles may be outside Application Support. Predicates never adopt them.
        if not Path(path).is_relative_to(self.library):
            return False
        return self._index(path).exists()

    def inspect(self, path, *, cancelled=None):
        """Capture exact bytes, empty directories, modes, ownership, xattrs and mtimes."""
        path = self._path(path)
        _assert_regular_tree(path)
        entries = {".": dict(kind="directory", **_metadata(path))}
        for item in sorted(path.rglob("*")):
            _checkpoint(cancelled)
            entry = dict(kind="directory" if item.is_dir() else "file", **_metadata(item))
            if entry["kind"] == "file":
                before = item.stat()
                digest = hashlib.sha256()
                with item.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        _checkpoint(cancelled)
                        digest.update(chunk)
                after = item.stat()
                if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise ColdStorageError("Cold-storage input changed during inspection")
                entry.update(size=before.st_size, sha256=digest.hexdigest())
            entries[item.relative_to(path).as_posix()] = entry
        _assert_regular_tree(path)
        return entries

    def _blob(self, digest):
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ColdStorageError("Invalid compressed content identity")
        result = self.blobs / digest[:2] / (digest + ".z")
        _assert_no_symlink_ancestor(result)
        return result

    def references(self):
        index = self.library / "cold-storage/archive-references.json"
        _assert_no_symlink_ancestor(index)
        if not index.exists():
            return {}
        info = index.stat()
        identity = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if self._reference_cache is not None and self._reference_cache[0] == identity:
            return self._reference_cache[1]
        if not index.is_file() or info.st_nlink != 1 or info.st_size > 64 * 1024**2:
            raise ColdStorageError("Invalid archive-reference index")
        try:
            value = json.loads(index.read_bytes())
            references = value["references"]
            if value["schema"] != 1 or not isinstance(references, dict) or len(references) > 250000:
                raise ValueError("reference index")
            for digest, entry in references.items():
                self._blob(digest)
                self._blob(entry["archive"])
                _relative(entry["member"])
                if type(entry["size"]) is not int or not 0 <= entry["size"] <= 512 * 1024**2:
                    raise ValueError("reference size")
        except (KeyError, TypeError, ValueError) as exc:
            raise ColdStorageError("Archive-reference index needs inspection") from exc
        self._reference_cache = (identity, references)
        return references

    def _read_references(self, entries, consume, *, cancelled=None):
        from .archive_references import stream_members

        references = self.references()
        grouped = {}
        for digest, entry in entries.items():
            ref = references[digest]
            if ref["size"] != entry["size"]:
                raise ColdStorageError("Archive-reference size differs from tree manifest")
            grouped.setdefault(ref["archive"], {})[ref["member"]] = digest
        for archive, members in grouped.items():
            finished = set()

            def finish(name, digest, size):
                if digest != members[name] or size != entries[digest]["size"]:
                    raise ColdStorageError("Canonical member differs from exact cold payload")
                finished.add(name)

            stream_members(
                self.library / "original-archives" / (archive + ".7z"),
                lambda name, data: consume(members[name], data),
                finish,
                wanted=set(members),
                cancelled=cancelled,
            )
            if finished != set(members):
                raise ColdStorageError("Referenced canonical member is missing")

    def _verify_entries(self, entries):
        references = self.references()
        referenced = {}
        with open(os.devnull, "wb") as sink:
            for entry in entries.values():
                if entry["kind"] != "file":
                    continue
                if entry["sha256"] in references and not self._blob(entry["sha256"]).exists():
                    referenced[entry["sha256"]] = entry
                else:
                    self._inflate(entry, sink)
        self._read_references(referenced, lambda digest, chunk: None)

    def _inflate(self, entry, output, *, cancelled=None):
        if entry["sha256"] in self.references() and not self._blob(entry["sha256"]).exists():
            self._read_references(
                {entry["sha256"]: entry},
                lambda digest, chunk: output.write(chunk),
                cancelled=cancelled,
            )
            return
        path = self._blob(entry["sha256"])
        if not path.is_file() or path.stat().st_nlink != 1:
            raise ColdStorageError("Compressed content is missing or linked")
        decoder, digest, size = zlib.decompressobj(), hashlib.sha256(), 0
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    _checkpoint(cancelled)
                    while chunk:
                        data = decoder.decompress(chunk, min(1024 * 1024, entry["size"] - size + 1))
                        chunk = decoder.unconsumed_tail
                        size += len(data)
                        if size > entry["size"]:
                            raise ColdStorageError("Compressed content exceeds its manifest")
                        digest.update(data)
                        output.write(data)
                    if decoder.unused_data:
                        raise ColdStorageError("Trailing compressed content")
        except zlib.error as exc:
            raise ColdStorageError("Compressed content is corrupt") from exc
        if not decoder.eof or size != entry["size"] or digest.hexdigest() != entry["sha256"]:
            raise ColdStorageError("Compressed content does not match its manifest")

    def _save_blob(self, path, entry, *, cancelled=None, presentation=False):
        if entry["sha256"] in self.references() and not presentation:
            return  # The complete archive-backed round trip is verified before commit.
        blob = self._blob(entry["sha256"])
        if blob.exists():
            with open(os.devnull, "wb") as sink:
                self._inflate(entry, sink, cancelled=cancelled)
            return
        blob.parent.mkdir(parents=True, exist_ok=True)
        temporary = blob.with_name(blob.name + ".tmp-" + uuid.uuid4().hex)
        identity = None
        written = hashlib.sha256()
        written_size = 0
        try:
            encoder = zlib.compressobj(6)
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as source, temporary.open("xb") as target:
                identity = _identity(temporary)
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    _checkpoint(cancelled)
                    digest.update(chunk)
                    size += len(chunk)
                    data = encoder.compress(chunk)
                    target.write(data)
                    written.update(data)
                    written_size += len(data)
                data = encoder.flush()
                target.write(data)
                written.update(data)
                written_size += len(data)
                target.flush()
                os.fsync(target.fileno())
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise ColdStorageError("Input changed while compressing")
            os.rename(temporary, blob)
            _fsync_dir(blob.parent)
        finally:
            if identity is not None and temporary.exists():
                self._discard_file(temporary, identity, written.hexdigest(), written_size)

    @staticmethod
    def _discard_file(path, identity, digest, size):
        _assert_no_symlink_ancestor(path)
        if (
            _identity(path) != identity
            or not path.is_file()
            or path.stat().st_nlink != 1
            or path.stat().st_size != size
        ):
            raise ColdStorageError("Temporary file changed; retained")
        actual = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                actual.update(chunk)
        if actual.hexdigest() != digest or _identity(path) != identity:
            raise ColdStorageError("Temporary bytes changed; retained")
        path.unlink()
        _fsync_dir(path.parent)

    def _load(self, path):
        index = self._index(path)
        if (
            not index.is_file()
            or index.stat().st_nlink != 1
            or index.stat().st_size > 128 * 1024**2
        ):
            raise ColdStorageError("Invalid cold-tree manifest")
        try:
            value = json.loads(index.read_bytes())
            entries = value["entries"]
            if (
                value["schema"] != 2
                or value["path"] != path.relative_to(self.library).as_posix()
                or _sha(_canonical(entries)) != value["sha256"]
                or value["phase"] not in ("checking", "prepared", "verified")
                or not re.fullmatch(r"\.cold-backup-[0-9a-f]{32}", value["backup"])
                or not isinstance(entries, dict)
                or len(entries) > 250000
                or entries["."]["kind"] != "directory"
            ):
                raise ValueError("manifest identity")
            for identity in (
                value["backup_identity"],
                *(value[key]["identity"] for key in ("check", "restore") if value[key] is not None),
            ):
                if identity is not None and (
                    not isinstance(identity, dict)
                    or any(
                        type(identity[field]) is not int or identity[field] < 0
                        for field in ("device", "inode")
                    )
                    or (
                        identity["birth"] is not None
                        and type(identity["birth"]) not in (int, float)
                    )
                ):
                    raise ValueError("directory identity")
            if value["phase"] != "verified" and value["backup_identity"] is None:
                raise ValueError("missing original identity")
            for key in ("check", "restore"):
                record = value[key]
                if record is not None and (
                    not isinstance(record, dict)
                    or type(record["complete"]) is not bool
                    or type(record["cleaning"]) is not bool
                    or (
                        record["metadata_birth"] is not None
                        and type(record["metadata_birth"]) not in (int, float)
                    )
                    or any(
                        type(record["identity"][field]) is not int for field in ("device", "inode")
                    )
                ):
                    raise ValueError("scratch identity")
            if type(value["disposal_started"]) is not bool:
                raise ValueError("disposal phase")
            for name, entry in entries.items():
                if name != ".":
                    _relative(name)
                    parent = str(PurePosixPath(name).parent)
                    if entries[parent]["kind"] != "directory":
                        raise ValueError("missing parent")
                if (
                    entry["kind"] not in ("file", "directory")
                    or type(entry["mode"]) is not int
                    or not 0 <= entry["mode"] <= 0o777
                    or any(
                        type(entry[k]) is not int or entry[k] < 0
                        for k in ("mtime_ns", "uid", "gid")
                    )
                    or not isinstance(entry["xattrs"], dict)
                ):
                    raise ValueError("metadata")
                for key, data in entry["xattrs"].items():
                    if not isinstance(key, str) or not key or "\0" in key:
                        raise ValueError("xattr name")
                    base64.b64decode(data, validate=True)
                if entry["kind"] == "file":
                    self._blob(entry["sha256"])
                    if type(entry["size"]) is not int or not 0 <= entry["size"] <= 64 * 1024**3:
                        raise ValueError("file size")
            if sum(e.get("size", 0) for e in entries.values()) > 256 * 1024**3:
                raise ValueError("tree size")
            return value
        except (KeyError, TypeError, ValueError) as exc:
            raise ColdStorageError("Cold-tree manifest needs inspection") from exc

    def _restore(self, path, entries, *, cancelled=None, progress=None, created=None):
        required = sum(entry.get("size", 0) for entry in entries.values())
        if shutil.disk_usage(path.parent).free < required + 64 * 1024**2:
            raise ColdStorageError("Not enough free space to prepare this map safely")
        path.mkdir(mode=0o700)
        _fsync_dir(path.parent)
        if created:
            created(path, False)
        directories = [(".", entries["."])]
        referenced, outputs = {}, {}
        references = self.references()
        for name, entry in sorted(entries.items()):
            if name == ".":
                continue
            _checkpoint(cancelled)
            target = path / name
            if entry["kind"] == "directory":
                target.mkdir(mode=0o700)
                directories.append((name, entry))
            else:
                if entry["sha256"] in references and not self._blob(entry["sha256"]).exists():
                    target.touch(exist_ok=False)
                    referenced[entry["sha256"]] = entry
                    outputs.setdefault(entry["sha256"], []).append((target, entry))
                else:
                    with target.open("xb") as stream:
                        self._inflate(entry, stream, cancelled=cancelled)
                    _restore_metadata(target, entry)
            if progress:
                progress(name, len(entries))

        def consume(digest, data):
            for target, _ in outputs[digest]:
                with target.open("ab") as stream:
                    stream.write(data)

        self._read_references(referenced, consume, cancelled=cancelled)
        for copies in outputs.values():
            for target, entry in copies:
                _restore_metadata(target, entry)
        for name, entry in reversed(directories):
            _restore_metadata(path if name == "." else path / name, entry)
        if created:
            created(path, True)
        if self.inspect(path, cancelled=cancelled) != entries:
            raise ColdStorageError("Restored tree does not match its exact manifest")
        _fsync_tree(path)

    def _discard(self, path, *, entries=None, identity=None, partial=False, scratch=False):
        """Remove only a creation-bound tree whose survivors contain no new bytes."""
        if identity is None or entries is None:
            raise ColdStorageError("Cleanup lacks directory creation evidence")
        _assert_no_symlink_ancestor(path)
        if _identity(path) != identity:
            raise ColdStorageError("Cleanup directory identity changed; retained")
        current = self.inspect(path)
        if not partial and current != entries:
            raise ColdStorageError("Cleanup tree changed; retained")
        if partial:
            if set(current) - set(entries):
                raise ColdStorageError("Cleanup has unowned added entries; retained")
            for name, actual in current.items():
                expected = entries[name]
                if actual["kind"] != expected["kind"]:
                    raise ColdStorageError("Cleanup entry type changed; retained")
                if actual["kind"] == "file":
                    if scratch:
                        if actual["size"] > expected["size"]:
                            raise ColdStorageError("Scratch exceeds planned content; retained")
                        if actual["size"] == expected["size"]:
                            if actual["sha256"] != expected["sha256"]:
                                raise ColdStorageError("Scratch bytes changed; retained")
                        elif actual["size"]:
                            with (path / name).open("rb") as prefix:

                                class ComparePrefix:
                                    def write(self, data):
                                        found = prefix.read(len(data))
                                        if found != data[: len(found)]:
                                            raise ColdStorageError(
                                                "Scratch prefix changed; retained"
                                            )

                                self._inflate(expected, ComparePrefix())
                    elif actual != expected:
                        raise ColdStorageError("Backup survivor changed; retained")
                elif not scratch:
                    for key in expected:
                        if key in ("mtime_ns", "mode"):
                            continue  # Disposal changes directory membership and owner mode.
                        if actual[key] != expected[key]:
                            raise ColdStorageError("Backup directory metadata changed; retained")
                    if actual["mode"] not in (expected["mode"], 0o700):
                        raise ColdStorageError("Backup directory mode changed; retained")
        # Repeat identity at the destructive boundary; names alone never authorize disposal.
        if _identity(path) != identity:
            raise ColdStorageError("Cleanup directory replaced; retained")
        _unfreeze_directories(path)
        shutil.rmtree(path)
        _fsync_dir(path.parent)

    def _scratch_callback(self, root, value, key):
        def recorded(path, complete):
            identity = _identity(path)
            previous = value[key]
            if previous is not None and not _same_inode(previous["identity"], identity):
                raise ColdStorageError("Scratch replaced during preparation; retained")
            seconds, nanos = divmod(value["entries"]["."]["mtime_ns"], 10**9)
            # macOS utime may lower birthtime to the restored mtime. Record that
            # exact allowed transition before any metadata work. Python stat
            # constructs its float as seconds plus fractional nanoseconds.
            metadata_birth = identity["birth"]
            if metadata_birth is not None:
                metadata_birth = min(metadata_birth, seconds + nanos * 1e-9)
            value[key] = dict(
                identity=identity, complete=complete, cleaning=False, metadata_birth=metadata_birth
            )
            _atomic_json(self._index(root), value)

        return recorded

    def _clean_scratch(self, root, value, key):
        name = ".cold-check-" if key == "check" else ".cold-restore-"
        path = root.with_name(name + value["backup"][-32:])
        _assert_no_symlink_ancestor(path)
        record = value[key]
        if path.exists():
            if record is None:
                raise ColdStorageError("Unowned scratch directory retained")
            actual_identity = _identity(path)
            if actual_identity != record["identity"]:
                if (
                    not _same_inode(actual_identity, record["identity"])
                    or actual_identity["birth"] != record["metadata_birth"]
                    or self.inspect(path) != value["entries"]
                ):
                    raise ColdStorageError(
                        "Scratch identity changed outside planned metadata; retained"
                    )
                # Only a complete exact tree on the created inode can settle
                # the already-recorded birthtime transition after interruption.
                record["identity"] = actual_identity
                record["complete"] = True
                _atomic_json(self._index(root), value)
            if record["complete"] and not record["cleaning"]:
                if _identity(path) != record["identity"] or self.inspect(path) != value["entries"]:
                    raise ColdStorageError("Completed scratch changed; retained")
                record["cleaning"] = True
                _atomic_json(self._index(root), value)
            self._discard(
                path,
                entries=value["entries"],
                identity=record["identity"],
                partial=True,
                scratch=True,
            )
        if record is not None:
            value[key] = None
            _atomic_json(self._index(root), value)

    def register_profile(self, source, destination, marker, *, saved_games=None):
        """Publish a new inactive writable profile without cloning its complete tree."""
        source, destination = self._path(source), self._path(destination)
        if destination.exists() or self.is_cold(destination):
            raise ColdStorageError("New cold profile destination already exists")
        before = self.inspect(source)
        entries = {
            name: {
                **entry,
                "mode": entry["mode"]
                | stat.S_IWUSR
                | (stat.S_IXUSR if entry["kind"] == "directory" else 0),
            }
            for name, entry in before.items()
        }
        for name, entry in before.items():
            if entry["kind"] == "file":
                basename = PurePosixPath(name).name.casefold()
                presentation = basename == "mapinfo.txt" or basename.startswith("rrt_scenario_")
                self._save_blob(source / name, entry, presentation=presentation)
        saved_manifest = None
        if saved_games is not None:
            if any(name.startswith("Saves/") for name in entries):
                raise ColdStorageError("Cannot merge retained saves with prepared saves")
            saved_games = self._path(saved_games)
            saved_manifest = self.inspect(saved_games)
            for name, entry in saved_manifest.items():
                key = "Saves" if name == "." else "Saves/" + name
                entries[key] = {
                    **entry,
                    "mode": entry["mode"]
                    | stat.S_IWUSR
                    | (stat.S_IXUSR if entry["kind"] == "directory" else 0),
                }
                if entry["kind"] == "file":
                    self._save_blob(saved_games / name, entry)
        self.blobs.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.blobs.parent / (".profile-marker-" + uuid.uuid4().hex)
        identity = None
        data = _canonical(marker)
        try:
            with temporary.open("xb") as stream:
                identity = _identity(temporary)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            entry = dict(kind="file", **_metadata(temporary), sha256=_sha(data), size=len(data))
            entries[".smr-launcher-profile.json"] = entry
            self._save_blob(temporary, entry)
        finally:
            if identity is not None and temporary.exists():
                self._discard_file(temporary, identity, _sha(data), len(data))
        self._verify_entries(entries)
        if self.inspect(source) != before or (
            saved_manifest is not None and self.inspect(saved_games) != saved_manifest
        ):
            raise ColdStorageError("Profile inputs changed during cold registration")
        value = dict(
            schema=2,
            backup_identity=None,
            check=None,
            restore=None,
            disposal_started=False,
            path=destination.relative_to(self.library).as_posix(),
            entries=entries,
            sha256=_sha(_canonical(entries)),
            phase="verified",
            backup=".cold-backup-" + uuid.uuid4().hex,
        )
        _atomic_json(self._index(destination), value)
        self._load(destination)

    def pack(self, path, *, expected, cancelled=None, progress=None):
        """Commit verified compact bytes before disposing this exact inactive tree."""
        path = self._path(path)
        if self.is_cold(path):
            raise ColdStorageError("Existing cold-tree transaction requires recovery")
        if self.inspect(path, cancelled=cancelled) != expected:
            raise ColdStorageError("Tree differs from the approved manifest")
        for name, entry in expected.items():
            _checkpoint(cancelled)
            if entry["kind"] == "file":
                basename = PurePosixPath(name).name.casefold()
                presentation = (
                    basename == "mapinfo.txt"
                    or (basename.startswith("rrt_scenario_") and basename.endswith(".xml"))
                ) and entry["size"] <= 8 * 1024**2
                self._save_blob(path / name, entry, cancelled=cancelled, presentation=presentation)
                if progress:
                    progress(name, len(expected))
        # Record scratch ownership before the round trip, including process death.
        value = dict(
            schema=2,
            backup_identity=_identity(path),
            check=None,
            restore=None,
            disposal_started=False,
            path=path.relative_to(self.library).as_posix(),
            entries=expected,
            sha256=_sha(_canonical(expected)),
            phase="checking",
            backup=".cold-backup-" + uuid.uuid4().hex,
        )
        _atomic_json(self._index(path), value)
        temporary = path.with_name(".cold-check-" + value["backup"][-32:])
        try:
            self._restore(
                temporary,
                expected,
                cancelled=cancelled,
                created=self._scratch_callback(path, value, "check"),
            )
            if self.inspect(path, cancelled=cancelled) != expected:
                raise ColdStorageError("Tree changed before compact commit")
        finally:
            self._clean_scratch(path, value, "check")
        _checkpoint(cancelled)
        value["phase"] = "prepared"
        _atomic_json(self._index(path), value)
        self.fault_hook("after_manifest")
        self.recover(path)

    def recover(self, path):
        """Finish only this tree's durable transaction; caller rechecks stopped locks."""
        path = self._path(path)
        if not self.is_cold(path):
            return
        value = self._load(path)
        backup = path.with_name(value["backup"])
        _assert_no_symlink_ancestor(backup)
        if value["phase"] == "checking":
            if (
                not path.exists()
                or backup.exists()
                or self.inspect(path) != value["entries"]
                or _identity(path) != value["backup_identity"]
            ):
                raise ColdStorageError("Round-trip check recovery conflicts with original tree")
            self._clean_scratch(path, value, "check")
            self._index(path).unlink()
            _fsync_dir(path.parent)
            return
        if value["phase"] == "prepared":
            if path.exists() and not backup.exists():
                if (
                    self.inspect(path) != value["entries"]
                    or _identity(path) != value["backup_identity"]
                ):
                    raise ColdStorageError("Original tree changed; compact recovery refused")
                _publish_exclusive(path, backup)
                _fsync_dir(path.parent)
                self.fault_hook("after_rename")
            if (
                path.exists()
                or not backup.is_dir()
                or self.inspect(backup) != value["entries"]
                or _identity(backup) != value["backup_identity"]
            ):
                raise ColdStorageError("Ambiguous compact recovery; all trees retained")
            # Recheck blobs on recovery before granting backup disposal.
            self._verify_entries(value["entries"])
            value["phase"] = "verified"
            _atomic_json(self._index(path), value)
            self.fault_hook("after_verified")
        if backup.exists():
            if path.exists():
                raise ColdStorageError("Conflicting compact recovery trees")
            # Disposal may have been interrupted after its durable verified phase.
            self._verify_entries(value["entries"])
            if not value["disposal_started"]:
                if (
                    _identity(backup) != value["backup_identity"]
                    or self.inspect(backup) != value["entries"]
                ):
                    raise ColdStorageError("Verified backup changed before disposal; retained")
                value["disposal_started"] = True
                _atomic_json(self._index(path), value)
            self._discard(
                backup, entries=value["entries"], identity=value["backup_identity"], partial=True
            )
        self._clean_scratch(path, value, "restore")
        if path.exists():
            if self.inspect(path) != value["entries"]:
                raise ColdStorageError("Materialized tree changed during recovery")
            self._index(path).unlink()
            _fsync_dir(path.parent)

    def materialize(self, path, *, cancelled=None, progress=None):
        path = self._path(path)
        if not self.is_cold(path):
            return
        self.recover(path)
        if not self.is_cold(path):
            return
        value = self._load(path)
        temporary = path.with_name(".cold-restore-" + value["backup"][-32:])
        try:
            self._restore(
                temporary,
                value["entries"],
                cancelled=cancelled,
                progress=progress,
                created=self._scratch_callback(path, value, "restore"),
            )
            _checkpoint(cancelled)
            if path.exists():
                raise ColdStorageError("Materialization destination already exists")
            _publish_exclusive(temporary, path)
            _fsync_dir(path.parent)
            self.fault_hook("after_materialize")
            self._index(path).unlink()
            _fsync_dir(path.parent)
        finally:
            if self.is_cold(path):
                self._clean_scratch(path, value, "restore")

    def assets_manifest_hash(self, path):
        """Original identity lookup only; this does not establish current game evidence."""
        path = self._path(path)
        value = self._load(path)
        if (
            value["phase"] != "verified"
            or path.exists()
            or path.with_name(value["backup"]).exists()
        ):
            raise ColdStorageError("Pending cold transaction requires its owner")
        files, directories = {}, []
        for name, entry in value["entries"].items():
            if not any(name.startswith(root + "/") for root in ("CustomAssets", "UserMaps")):
                continue
            if entry["kind"] == "file":
                files[name] = entry["sha256"]
            else:
                directories.append(name)
        return _sha(
            _canonical(dict(files=dict(sorted(files.items())), directories=sorted(directories)))
        )

    def read_file(self, path, name, limit):
        """Read a bounded presentation file without creating a working tree."""
        from io import BytesIO

        _relative(name)
        path = self._path(path)
        value = self._load(path)
        if (
            value["phase"] != "verified"
            or path.exists()
            or path.with_name(value["backup"]).exists()
        ):
            raise ColdStorageError("Pending compact transaction requires its owner")
        entry = value["entries"].get(name)
        if entry is None:
            return b""
        if entry["kind"] != "file" or entry["size"] > limit:
            raise ColdStorageError("Presentation file exceeds its read limit")
        output = BytesIO()
        self._inflate(entry, output)
        return output.getvalue()
