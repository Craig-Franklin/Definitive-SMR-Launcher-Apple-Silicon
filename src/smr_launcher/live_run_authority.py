"""Process-local observed runner authority; persisted receipts are inspection only.

This module does not discover installations, start games or interpret historical
receipts as capabilities. The runner's composition root retains the exact owner.
Python interpreter reflection and hostile replacement of trusted wiring are not
an isolation boundary. Successful fsync returns are not a power-loss proof.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import threading
import time
import uuid


CONSUMERS = ("completion", "calibration", "retry", "queue", "session",
             "already_restored", "reporting")
_observing = ContextVar("smr_observed_invocation", default=None)
_freeze_cache = {}


class AuthorityError(RuntimeError):
    pass


class Witness:
    __slots__ = ()

    def __new__(cls, *args, **kwargs):
        raise AuthorityError("Only the installed observer can issue a witness")


@dataclass(frozen=True)
class Decision:
    classification: str = "inspection"
    terminal_accepted: bool = False
    completion: bool = False
    calibration: bool = False
    retry: bool = False
    next_job_allowed: bool = False
    already_restored: bool = False


INSPECTION = Decision()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def snapshot_file(path, limit=512 * 1024 * 1024):
    """Observe bytes and identity without following a link or accepting an ABA."""
    path = Path(path).absolute()
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise AuthorityError("Linked ancestor")
    parents = tuple((str(p), p.stat().st_dev, p.stat().st_ino) for p in
                    (path.parent, *path.parent.parents))
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size > limit):
        raise AuthorityError("Plain bounded single-link file required")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        digest = hashlib.sha256()
        size = 0
        remaining = limit + 1
        while remaining:
            block = os.read(fd, min(65536, remaining))
            if not block:
                break
            digest.update(block)
            size += len(block)
            remaining -= len(block)
        after = os.fstat(fd)
        current = path.lstat()
        def key(s):
            return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size,
                    s.st_mtime_ns, s.st_ctime_ns)
        if not key(before) == key(opened) == key(after) == key(current):
            raise AuthorityError("File identity changed during observation")
        if size != after.st_size or size > limit:
            raise AuthorityError("Incomplete or oversized read")
        if parents != tuple((str(p), p.stat().st_dev, p.stat().st_ino) for p in
                            (path.parent, *path.parent.parents)):
            raise AuthorityError("Observed parent replaced")
        return (str(path), key(after), digest.hexdigest(), parents)
    finally:
        os.close(fd)


def frozen_file(path):
    """Reuse an observed digest only while its full inode/ctime fence is exact.

    This avoids rereading immutable source bytes at every admission/consumer.
    A changed inode, mode, size, mtime or ctime forces a fresh observation and
    therefore a different freeze; restoring old bytes cannot reset the fence.
    """
    path = Path(path).absolute()
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise AuthorityError("Linked freeze ancestor")
    info = path.lstat()
    key = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
           info.st_mtime_ns, info.st_ctime_ns)
    parents = tuple((str(p), p.stat().st_dev, p.stat().st_ino) for p in
                    (path.parent, *path.parent.parents))
    cache_key = (str(path), key, parents)
    value = _freeze_cache.get(cache_key)
    if value is None:
        value = snapshot_file(path)
        _freeze_cache[cache_key] = value
    return value


def observed(name, function, *args, **kwargs):
    """Record a successful real call return, including descriptor/context exits."""
    owner = _observing.get()
    if owner is None:
        return function(*args, **kwargs)
    try:
        result = function(*args, **kwargs)
    except BaseException as exc:
        owner._invalidate(name, exc)
        raise
    owner._events.append((name, "returned"))
    return result


@contextmanager
def observed_context(name, context):
    owner = _observing.get()
    entered = False
    try:
        with context as result:
            entered = True
            if owner is not None:
                owner._events.append((name + ".enter", "returned"))
            yield result
    except BaseException as exc:
        if owner is not None:
            owner._invalidate(name + (".exit" if entered else ".enter"), exc)
        raise
    if owner is not None:
        owner._events.append((name + ".exit", "returned"))


@contextmanager
def output_stream(path):
    """The child's output descriptor is synced and closed before full return."""
    path = Path(path)
    mutate(path)
    stream = observed("log.open", path.open, "xb")
    try:
        yield stream
    finally:
        try:
            observed("log.flush", stream.flush)
            observed("log.fsync", os.fsync, stream.fileno())
        finally:
            observed("log.close", stream.close)
        observed("log.parent_sync", sync_directory, path.parent)


def mutate(path):
    """Fence a previously issued resource before starting a fallible mutation."""
    owner = _observing.get()
    if owner is not None:
        owner._before_mutation(Path(path).absolute())


def sync_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise AuthorityError("Linked directory")
    fd = observed("directory.open", os.open, path,
                  os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise AuthorityError("Directory descriptor required")
        observed("directory.fsync", os.fsync, fd)
    finally:
        observed("directory.close", os.close, fd)


def write_file(path, data, *, append=False, exclusive=False, parent_sync=sync_directory):
    """Length-checked writes; flush, file sync, close and parent sync all return.

    The caller's parent-sync function remains an actual injectable boundary.
    A partial record is preserved if any operation or observation fails.
    """
    path = Path(path)
    owner = _observing.get()
    event_start = len(owner._events) if owner is not None else 0
    if type(data) is not bytes:
        raise AuthorityError("Immutable bytes required")
    mutate(path)
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise AuthorityError("Linked write ancestor")
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    # Verify the opened descriptor before truncation; an unexpected hard link
    # must not damage its other name merely because validation will later fail.
    flags |= os.O_EXCL if exclusive else os.O_APPEND if append else 0
    parent_before = path.parent.lstat()
    fd = observed("file.open", os.open, path, flags, 0o600)
    stream = None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AuthorityError("Plain single-link output required")
        if not exclusive and not append:
            observed("file.truncate", os.ftruncate, fd, 0)
        stream = observed("file.fdopen", os.fdopen, fd, "wb")
        written = observed("file.write", stream.write, data)
        if written != len(data):
            owner = _observing.get()
            exc = AuthorityError("Short write")
            if owner is not None:
                owner._invalidate("file.write_count", exc)
            raise exc
        observed("file.write_count", lambda: written)
        observed("file.flush", stream.flush)
        observed("file.fsync", os.fsync, stream.fileno())
    finally:
        if stream is None:
            observed("file.close", os.close, fd)
        else:
            observed("file.close", stream.close)
    observed("file.parent_sync", parent_sync, path.parent)
    parent_after = path.parent.lstat()
    if (parent_before.st_dev, parent_before.st_ino) != (parent_after.st_dev, parent_after.st_ino):
        owner = _observing.get()
        exc = AuthorityError("Output parent identity changed")
        if owner is not None:
            owner._invalidate("file.parent_identity", exc)
        raise exc
    if owner is not None:
        names = [name for name, returned in owner._events[event_start:]]
        expected = ("file.open", "file.fdopen", "file.write", "file.write_count",
                    "file.flush", "file.fsync", "file.close", "directory.open",
                    "directory.fsync", "directory.close", "file.parent_sync")
        cursor = 0
        for name in expected:
            try:
                cursor = names.index(name, cursor) + 1
            except ValueError as exc:
                owner._invalidate("unobserved_leaf:" + name, exc)
                raise AuthorityError("Unobserved mandatory I/O leaf: " + name) from exc


def atomic_json(path, value, *, parent_sync=sync_directory):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    mutate(path)
    # Retain an interrupted temporary rather than destroying diagnostic evidence.
    write_file(temporary, encoded(value) + b"\n", exclusive=True,
               parent_sync=parent_sync)
    observed("file.rename", os.replace, temporary, path)
    observed("rename.parent_sync", parent_sync, path.parent)


def sync_tree(root):
    """Every regular file sync and close, then every directory sync and close."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise AuthorityError("Plain tree required")
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories:
            if (Path(parent) / name).is_symlink():
                raise AuthorityError("Linked directory in sync tree")
        for name in files:
            path = Path(parent) / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise AuthorityError("Unsupported file in sync tree")
            fd = observed("tree.file_open", os.open, path,
                          os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise AuthorityError("Tree file replaced")
                observed("tree.file_fsync", os.fsync, fd)
            finally:
                observed("tree.file_close", os.close, fd)
    for parent, directories, files in os.walk(root, topdown=False, followlinks=False):
        observed("tree.directory_sync", sync_directory, Path(parent))


def copy_file(source, destination):
    """Independent length-checked copy with complete observed descriptor returns."""
    source, destination = Path(source), Path(destination)
    preimage = observed("copy.source_capture", SettingsPreimage.capture, source)
    if preimage.kind != "file":
        raise AuthorityError("Plain copy source required")
    write_file(destination, preimage.data, exclusive=True)
    observed("copy.mode", os.chmod, destination, preimage.mode, follow_symlinks=False)
    observed("copy.mtime", os.utime, destination,
             ns=(destination.stat().st_atime_ns, preimage.mtime_ns), follow_symlinks=False)
    if observed("copy.source_readback", SettingsPreimage.capture, source) != preimage:
        raise AuthorityError("Copy source changed")
    readback = observed("copy.destination_readback", SettingsPreimage.capture, destination)
    if (readback.data, readback.mode, readback.mtime_ns) != (
            preimage.data, preimage.mode, preimage.mtime_ns):
        raise AuthorityError("Copy readback mismatch")
    return observed("copy.complete", lambda: str(destination))


def copy_tree(source, destination):
    """Copy every independent file and directory through explicit observed I/O."""
    source, destination = Path(source), Path(destination)
    preimage = observed("copy.tree_preimage", SettingsPreimage.capture, source)
    if preimage.kind != "directory" or not preimage.supported():
        raise AuthorityError("Unsupported copy tree")
    def walk(entry, target):
        if entry.kind == "file":
            copy_file(entry.path, target)
            return
        observed("copy.mkdir", os.mkdir, target, 0o700)
        for child in entry.entries:
            walk(child, target / Path(child.path).name)
        observed("copy.directory_mode", os.chmod, target, entry.mode, follow_symlinks=False)
        observed("copy.directory_mtime", os.utime, target,
                 ns=(target.stat().st_atime_ns, entry.mtime_ns), follow_symlinks=False)
        sync_directory(target)
    walk(preimage, destination)
    if observed("copy.tree_source_readback", SettingsPreimage.capture, source) != preimage:
        raise AuthorityError("Copy tree source changed")
    if portable_tree(SettingsPreimage.capture(destination)) != portable_tree(preimage):
        raise AuthorityError("Complete tree bytes/modes/mtimes differ after copy")
    sync_directory(destination.parent)
    return observed("copy.tree_complete", lambda: str(destination))


def portable_tree(entry):
    return (entry.kind, entry.mode, entry.mtime_ns, entry.data,
            tuple((Path(e.path).name, portable_tree(e)) for e in entry.entries))


class ObservedIO:
    """Explicit adapter used by owned activation and runner leaves, never global patches."""
    def call(self, name, function, *args, **kwargs):
        return observed(name, function, *args, **kwargs)

    def atomic_json(self, path, value):
        return observed("activation.atomic_json", atomic_json, path, value)

    def fsync_dir(self, path):
        return observed("activation.parent_sync", sync_directory, path)

    def fsync_tree(self, path):
        return observed("activation.tree_sync", sync_tree, path)

    def copytree(self, source, destination):
        return observed("activation.copytree", copy_tree, source, destination)

    def readbytes(self, path):
        captured = observed("activation.readbytes", SettingsPreimage.capture, path)
        if captured.kind != "file":
            raise AuthorityError("Plain bounded file required")
        return captured.data


@dataclass(frozen=True)
class SettingsPreimage:
    """A type-aware owner observation; links and conflicts are retained/refused.

    Regular files can be restored in place under stopped-game guards, retaining
    their resource inode. A changed type, a replacement inode or an unexpected
    new resource is never deleted to simulate successful restoration. Directory
    preimages are complete and can prove unchanged state; directory mutation is
    conservatively retained for an independently guarded recovery route.
    """
    path: str
    kind: str
    parent_identity: tuple
    resource_identity: tuple | None
    mode: int | None
    mtime_ns: int | None
    data: bytes | None
    entries: tuple = ()

    @classmethod
    def capture(cls, path, limit=16 * 1024 * 1024):
        path = Path(path).absolute()
        for parent in (path.parent, *path.parent.parents):
            if parent.is_symlink():
                raise AuthorityError("Settings ancestor is linked")
        parent = path.parent.lstat()
        parent_key = (parent.st_dev, parent.st_ino)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return cls(str(path), "absent", parent_key, None, None, None, None)
        key = (info.st_dev, info.st_ino)
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            return cls(str(path), "symlink", parent_key, key, mode, info.st_mtime_ns,
                       os.fsencode(os.readlink(path)))
        if stat.S_ISDIR(info.st_mode):
            entries = tuple(cls.capture(p, limit) for p in sorted(path.iterdir()))
            if sum(len(e.data or b"") for e in _settings_entries(entries)) > limit:
                raise AuthorityError("Settings directory exceeds preservation bound")
            after = path.lstat()
            if (after.st_dev, after.st_ino, after.st_mtime_ns) != (
                    info.st_dev, info.st_ino, info.st_mtime_ns):
                raise AuthorityError("Settings directory changed during capture")
            return cls(str(path), "directory", parent_key, key, mode, info.st_mtime_ns,
                       None, entries)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            return cls(str(path), "conflict", parent_key, key, mode, info.st_mtime_ns, None)
        fd = observed("settings.open", os.open, path,
                      os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(fd)
            data = bytearray()
            while len(data) <= limit:
                part = observed("settings.read", os.read, fd,
                                min(65536, limit + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
            after = os.fstat(fd)
            current = path.lstat()
            def exact(s):
                return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size,
                        s.st_mtime_ns, s.st_ctime_ns)
            if not exact(info) == exact(opened) == exact(after) == exact(current):
                raise AuthorityError("Settings changed during capture")
            if len(data) != info.st_size or len(data) > limit:
                raise AuthorityError("Settings read incomplete")
        finally:
            observed("settings.close", os.close, fd)
        return cls(str(path), "file", parent_key, key, mode, info.st_mtime_ns, bytes(data))

    def supported(self):
        return self.kind in ("absent", "file", "directory") and all(
            e.supported() for e in self.entries)

    def restore(self, stopped_guard, expected_current):
        # expected_current is captured by the owned post-stop adapter, never a
        # consumer receipt. Changes between that capture and restore conflict.
        if stopped_guard() is not True:
            raise AuthorityError("Known stopped/preservation state required")
        current = observed("settings.restore_read", SettingsPreimage.capture, self.path)
        if current != expected_current:
            raise AuthorityError("External settings conflict; all contents retained")
        if not self.supported() or not current.supported():
            raise AuthorityError("Unsupported settings type; retained")
        if self.parent_identity != current.parent_identity:
            raise AuthorityError("Settings parent identity changed")
        if self.kind != current.kind or self.resource_identity != current.resource_identity:
            raise AuthorityError("New/replaced settings resource retained")
        if self.kind != "file":
            if self != current:
                raise AuthorityError("Changed settings directory retained")
            return observed("settings.restore_complete", lambda: self)
        path = Path(self.path)
        mutate(path)
        write_file(path, self.data)
        observed("settings.chmod", os.chmod, path, self.mode, follow_symlinks=False)
        observed("settings.utime", os.utime, path,
                 ns=(path.stat().st_atime_ns, self.mtime_ns), follow_symlinks=False)
        fd = observed("settings.sync_open", os.open, path,
                      os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            observed("settings.metadata_sync", os.fsync, fd)
        finally:
            observed("settings.sync_close", os.close, fd)
        observed("settings.parent_sync", sync_directory, path.parent)
        restored = observed("settings.readback", SettingsPreimage.capture, path)
        if restored != self:
            raise AuthorityError("External settings readback differs from preimage")
        return observed("settings.restore_complete", lambda: restored)


def _settings_entries(entries):
    for entry in entries:
        yield entry
        yield from _settings_entries(entry.entries)


class FiniteSupervisor:
    """Independent deadline signal stops admission even while the owner is busy.

    This thread never signals a game or restores a profile. It cannot interrupt a
    kernel storage call or promise a hard recovery time. The finite process and
    driver watchdogs remain the runner's separate exact-identity responsibility.
    """
    def __init__(self, deadline):
        if not math.isfinite(deadline) or deadline <= time.monotonic():
            raise AuthorityError("Finite future deadline required")
        self.deadline = deadline
        self.expired = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._wait, daemon=True,
                                        name="smr-admission-deadline")
        self._thread.start()

    def _wait(self):
        if not self._stop.wait(max(0, self.deadline - time.monotonic())):
            self.expired.set()

    def check(self, reserve=0):
        if self.expired.is_set() or time.monotonic() + reserve >= self.deadline:
            self.expired.set()
            raise AuthorityError("Finite observation/recovery admission exhausted")

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1)
        if self._thread.is_alive():
            raise AuthorityError("Supervisor did not return")


@dataclass(frozen=True)
class _Issued:
    token: object
    epoch: str
    generation: int
    run: str
    case: str
    role: str
    context: bytes
    row: bytes
    freeze: tuple
    snapshots: tuple
    events: tuple


class InvocationOwner:
    def __init__(self, *args, **kwargs):
        raise AuthorityError("Caller construction does not install an invocation")

    def _initialize(self, freeze_paths, jobs, supervisor, guard):
        self.lock = getattr(self, "lock", None) or threading.RLock()
        self._epoch = getattr(self, "_epoch", None) or uuid.uuid4().hex
        self._generation = 0
        self._revoked = False
        self._registry = {}
        self._events = []
        self._errors = []
        self._raw_incidents = {}
        self._queue_stopped = False
        self._failed_versions = {"rejected-v1", "rejected-v2", "rejected-v3"}
        self._paths = tuple(Path(p).absolute() for p in freeze_paths)
        self._freeze = tuple(frozen_file(p) for p in self._paths)
        self._jobs = {j["input_identity"]: encoded(j) for j in jobs}
        self._supervisor = supervisor
        self._guard = guard

    def _invalidate(self, boundary, error=None):
        # No private boolean can reset the monotonic generation or registry.
        self._generation += 1
        self._revoked = True
        self._registry.clear()
        self._errors.append((boundary, type(error).__name__ if error else None))

    def close(self):
        with self.lock:
            self._invalidate("closed_or_restart")

    def _before_mutation(self, path):
        for issued in tuple(self._registry.values()):
            if any(path == Path(s[0]) or path in Path(s[0]).parents
                   for s in issued.snapshots):
                self._invalidate("issued_resource_mutation")
                break

    def _preflight(self, job=None):
        if self._revoked or self._generation != 0:
            raise AuthorityError("Invocation permanently revoked")
        try:
            self._supervisor.check()
            if tuple(frozen_file(p) for p in self._paths) != self._freeze:
                raise AuthorityError("Code/input/interpreter/provider freeze changed")
            if job is not None and self._jobs.get(job.get("input_identity")) != encoded(job):
                raise AuthorityError("Case/context/protocol/options not frozen")
            if self._guard() is not True:
                raise AuthorityError("Actual preservation/process admission unavailable")
            # The preservation/process guard is a real fallible helper. Fence
            # mutations on its return as well as on entry, before a constructor
            # or a consumer can use this admission.
            if tuple(frozen_file(p) for p in self._paths) != self._freeze:
                raise AuthorityError("Code/input/interpreter/provider freeze changed during admission")
        except BaseException as exc:
            self._invalidate("preflight", exc)
            raise

    def execute(self, job, role, executor):
        """Only root-retained callers use this; no supplied history can issue."""
        with self.lock:
            self._preflight(job)
            generation = self._generation
            self._events = []
            cookie = _observing.set(self)
            try:
                row, paths = observed("runner.outer_return", executor)
                if self._revoked or generation != self._generation:
                    raise AuthorityError("A mandatory return was lost")
                names = [name for name, returned in self._events]
                required = ("journal.started", "journal.terminal_pending", "journal.selected",
                            "journal.raw_outcome", "journal.stopped", "process_and_driver.complete",
                            "restore.complete",
                            "journal.restored", "index.complete", "cleanup.complete",
                            "journal.terminal_finished", "checkpoint.complete",
                            "report.complete", "application.lock_close", "application.lock_exit",
                            "application.lock.exit", "runner.outer_return")
                cursor = 0
                for name in required:
                    try:
                        cursor = names.index(name, cursor) + 1
                    except ValueError as exc:
                        raise AuthorityError("Missing ordered mandatory boundary: " + name) from exc
                if not any(name == "file.write_count" for name in names):
                    raise AuthorityError("No observed length-checked I/O")
                self._preflight(job)
                snapshots = tuple(snapshot_file(p) for p in paths)
                token = object.__new__(Witness)
                self._registry[id(token)] = _Issued(token, self._epoch, self._generation,
                    row["run_id"], job["input_identity"], role, encoded(job), encoded(row),
                    self._freeze, snapshots, tuple(self._events))
                self._raw_incidents.setdefault(row["raw_outcome_id"], encoded(row["raw_outcome"]))
                if row["status"] not in {"loaded", "crash_or_exit", "memory_limit", "timeout"}:
                    self._queue_stopped = True
                return token
            except BaseException as exc:
                self._invalidate("execution_failed_or_unobserved", exc)
                raise
            finally:
                _observing.reset(cookie)

    def decide(self, token, *, case=None, context=None):
        with self.lock:
            issued = self._registry.get(id(token))
            if (issued is None or issued.token is not token or type(token) is not Witness
                    or self._revoked or issued.epoch != self._epoch
                    or issued.generation != self._generation
                    or case is not None and case != issued.case
                    or context is not None and encoded(context) != issued.context):
                return INSPECTION
            try:
                self._preflight()
                if (issued.freeze != self._freeze or
                        tuple(snapshot_file(s[0]) for s in issued.snapshots) != issued.snapshots):
                    self._invalidate("terminal_file_fence")
                    return INSPECTION
            except BaseException as exc:
                self._invalidate("reader_uncertainty", exc)
                return INSPECTION
            row = json.loads(issued.row)
            clean = (row["status"] in {"loaded", "crash_or_exit", "memory_limit", "timeout"}
                     and row["restored"] is True and all(row.get(k) is None for k in
                         ("restoration_error", "harness_error", "cleanup_error")))
            calibrated = clean and row["status"] == "loaded" and issued.role == "calibration"
            return Decision("live_clean" if clean else "live_failed", True, clean,
                            calibrated, False, clean and not self._queue_stopped, True)

    def consumer(self, name, token, **binding):
        if name not in CONSUMERS:
            raise AuthorityError("Unknown authority purpose")
        return self.decide(token, **binding)

    def token_for_row(self, row):
        raw = encoded(row)
        return next((v.token for v in self._registry.values() if v.row == raw), None)

    def reporting_change(self):
        with self.lock:
            self._invalidate("fallible_reporting_or_reconciliation")


def _new_owner(freeze_paths, jobs, supervisor, guard):
    """Private trusted composition only; this does not install the owner."""
    owner = object.__new__(InvocationOwner)
    owner._initialize(freeze_paths, jobs, supervisor, guard)
    return owner


class RuntimeFreeze:
    """Exact current code, interpreter, environment, provider and input closure.

    Root supplies actual provider closure paths; filenames are inputs, never
    authority. Unknown linked/native provider closure is refused by composition.
    """
    def __init__(self, paths, roots, bindings, *, modules=None, environment=None, link_roots=()):
        self.paths = tuple(sorted({Path(p).resolve(strict=True) for p in paths}))
        self.roots = tuple(Path(p).absolute() for p in roots)
        self.link_roots = frozenset(Path(p).absolute() for p in link_roots)
        self.binding_source = bindings if callable(bindings) else lambda: tuple(bindings)
        self.module_source = modules if modules is not None else lambda: tuple(sys.modules.items())
        self.environment = environment if environment is not None else lambda: (
            tuple(sorted(os.environ.items())), tuple(sys.path), tuple(sys.argv), os.getcwd(), sys.executable,
            sys.version, sys.implementation.cache_tag, sys.flags)
        self.modules = self._modules()
        self.environment_preimage = hashlib.sha256(encoded(self.environment())).digest()
        self.binding_preimage = self._bindings()
        self.trees = tuple(self._tree(p, allow_internal_links=p in self.link_roots) for p in self.roots)
        self.files = tuple(frozen_file(p) for p in self.paths)

    def _modules(self):
        return tuple(sorted((name, id(module), getattr(module, '__file__', None),
            getattr(getattr(module, '__spec__', None), 'origin', None))
            for name, module in self.module_source()))

    def _bindings(self):
        return tuple((id(value), id(getattr(value, '__code__', None)),
            tuple(id(cell.cell_contents) for cell in (getattr(value, '__closure__', None) or ())))
            for value in self.binding_source())

    @staticmethod
    def _tree(root, *, allow_internal_links=False):
        for p in (root, *root.parents):
            if p.is_symlink(): raise AuthorityError('Linked frozen input tree')
        if not root.is_dir(): raise AuthorityError('Frozen input tree absent')
        entries = []
        for path in sorted((root, *root.rglob('*'))):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) and allow_internal_links:
                target = path.resolve(strict=True)
                if root != target and root not in target.parents:
                    raise AuthorityError('External linked artifact dependency')
                entries.append((str(path), info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns, os.readlink(path)))
                continue
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise AuthorityError('Unsupported frozen input type')
            entries.append((str(path), info.st_dev, info.st_ino, info.st_mode,
                info.st_nlink, info.st_size if path.is_file() else None,
                info.st_mtime_ns, info.st_ctime_ns))
        return tuple(entries)

    def check(self):
        if (self._modules() != self.modules or hashlib.sha256(encoded(self.environment())).digest() != self.environment_preimage
                or self._bindings() != self.binding_preimage
                or tuple(self._tree(p, allow_internal_links=p in self.link_roots) for p in self.roots) != self.trees
                or tuple(frozen_file(p) for p in self.paths) != self.files):
            raise AuthorityError('Interpreter/driver/provider/options/input closure changed')
        return True


class IdentitySupervisor:
    """Finite process checks and stop outside the invocation owner's lock.

    An independent thread observes exact identity and code/input guards while
    the foreground executor is in a helper. Any trip permanently revokes first,
    preserves an unknown/raw disposition if possible, then stops only the exact
    observed game and an unreaped owned driver. No storage/power-loss or parent
    process-death recovery bound is claimed. Kernel calls remain interruptible
    only by the operating system; failure retains all resources and denies.
    """
    def __init__(self, owner, processes, process, driver, journal, deadline, memory,
                 guard, stop_driver, interval=.1):
        if type(owner) is not InvocationOwner or _observing.get() is not owner:
            raise AuthorityError('Supervisor requires exact genuine observed invocation context')
        if (not math.isfinite(deadline) or deadline <= time.monotonic()
                or not 0 < interval <= 1 or type(memory) is not int or memory <= 0):
            raise AuthorityError('Finite identity supervisor bounds required')
        self.owner, self.processes, self.process = owner, processes, process
        self.driver, self.journal, self.guard, self.stop_driver = driver, journal, guard, stop_driver
        self.deadline, self.memory, self.interval = deadline, memory, interval
        self.stop = threading.Event()
        self.failed = threading.Event()
        self.errors = []
        self.thread = threading.Thread(target=self._monitor, daemon=True, name='smr-exact-identity-supervisor')
        self.thread.start()

    def _monitor(self):
        cookie = _observing.set(self.owner)
        try:
            while not self.stop.wait(self.interval):
                try:
                    self.owner._supervisor.check()
                    if self.guard() is not True: raise AuthorityError('Runtime freeze/profile guard failed')
                    current = self.processes.inspect(self.process.pid)
                    if current is None: return
                    if not self.process.same(current): raise AuthorityError('Observed PID was replaced')
                    games = self.processes.games()
                    if len(games) != 1 or not self.process.same(games[0]):
                        raise AuthorityError('Concurrent/unidentified game')
                    if current.footprint >= self.memory: raise AuthorityError('Independent memory ceiling')
                    if time.monotonic() >= self.deadline: raise AuthorityError('Independent job deadline')
                except BaseException as exc:
                    self.owner._invalidate('independent_supervisor', exc)
                    self.failed.set();self.errors.append(type(exc).__name__ + ': ' + str(exc))
                    try:
                        if self.journal.read('raw_outcome') is None:
                            self.journal.outcome(dict(status='safety_stop', cause='independent_supervisor',
                                reason=str(exc), error_type=type(exc).__name__))
                    except BaseException as error:
                        self.errors.append('raw preservation: ' + str(error))
                    try: self.stop_driver(self.driver)
                    except BaseException as error: self.errors.append('driver stop: ' + str(error))
                    try:
                        current = self.processes.inspect(self.process.pid)
                        if current is not None:
                            if not self.process.same(current): raise AuthorityError('Changed PID never signalled')
                            self.processes.stop(self.process)
                    except BaseException as error: self.errors.append('game stop: ' + str(error))
                    return
        finally:
            _observing.reset(cookie)

    def check(self):
        if self.failed.is_set(): raise AuthorityError('Independent supervisor revoked invocation')

    def close(self):
        self.stop.set();self.thread.join(timeout=12)
        if self.thread.is_alive():
            self.owner._invalidate('supervisor_return_lost')
            raise AuthorityError('Supervisor still active; no restoration/issuance')
        self.check()
