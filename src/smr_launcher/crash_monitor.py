"""Passive, local-first crash monitoring. This module never controls the game."""
from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import struct
import uuid
import tempfile
from threading import Event, Lock, RLock, Thread
import time
from typing import Callable

from .activation import _assert_no_symlink_ancestor

MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_SCAN_ENTRIES = 4096
MAX_REPORTS_PER_POLL = 16


class _BSDInfo(ctypes.Structure):
    _fields_ = [("prefix", ctypes.c_uint32 * 12), ("comm", ctypes.c_char * 16),
                ("name", ctypes.c_char * 32), ("suffix", ctypes.c_uint32 * 6),
                ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]


def running_game(executable: str) -> dict | None:
    """Read one exact game incarnation through libproc; never signal it."""
    if platform.system() != "Darwin":
        raise OSError("Game process observation requires macOS")
    location = ctypes.util.find_library("proc")
    if not location:
        raise OSError("Process observation unavailable")
    lib = ctypes.CDLL(location, use_errno=True)
    signatures = {
        "proc_listallpids": [ctypes.c_void_p, ctypes.c_int],
        "proc_pidpath": [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32],
        "proc_pidinfo": [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                         ctypes.c_void_p, ctypes.c_int],
    }
    for name, arguments in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = arguments, ctypes.c_int
    estimate = lib.proc_listallpids(None, 0)
    if not 0 < estimate < 32768:
        raise OSError("Process enumeration unavailable")
    capacity = max(1024, estimate * 2)
    pids = (ctypes.c_int * capacity)()
    count = lib.proc_listallpids(pids, ctypes.sizeof(pids))
    if not 0 < count < capacity:
        raise OSError("Process enumeration incomplete")
    expected = str(Path(executable).resolve())
    matches = []
    for pid in pids[:count]:
        if pid <= 0:
            continue
        path = ctypes.create_string_buffer(4096)
        if lib.proc_pidpath(pid, path, len(path)) <= 0:
            continue  # Unrelated unreadable processes are never diagnostic inputs.
        if os.fsdecode(path.value) != expected:
            continue
        before, after = _BSDInfo(), _BSDInfo()
        if lib.proc_pidinfo(pid, 3, 0, ctypes.byref(before), ctypes.sizeof(before)) != ctypes.sizeof(before):
            raise OSError("Game identity unavailable")
        if lib.proc_pidpath(pid, path, len(path)) <= 0 or os.fsdecode(path.value) != expected:
            raise OSError("Game identity changed")
        if lib.proc_pidinfo(pid, 3, 0, ctypes.byref(after), ctypes.sizeof(after)) != ctypes.sizeof(after):
            raise OSError("Game identity unavailable")
        birth = before.start_sec * 1000000 + before.start_usec
        if birth <= 0 or (before.start_sec, before.start_usec) != (after.start_sec, after.start_usec):
            raise OSError("Game incarnation changed")
        matches.append(dict(pid=int(pid), birth_us=int(birth), executable=expected))
    if len(matches) > 1:
        raise OSError("Multiple game processes need inspection")
    return matches[0] if matches else None


def _read_regular(path: Path, maximum: int) -> bytes:
    _assert_no_symlink_ancestor(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise ValueError("Diagnostic file exceeds its read limit")
        data = bytearray()
        while len(data) <= before.st_size:
            piece = os.read(fd, min(65536, before.st_size + 1 - len(data)))
            if not piece:
                break
            data.extend(piece)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(data) != before.st_size or identity(before) != identity(after) or identity(after) != identity(path.lstat()):
            raise ValueError("Diagnostic file changed while reading")
        return bytes(data)
    finally:
        os.close(fd)


def executable_image_uuid(executable: Path) -> str | None:
    """Read a bounded thin 64-bit Mach-O UUID; unsupported layouts stay unknown."""
    _assert_no_symlink_ancestor(executable)
    fd = os.open(executable, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return None
        header = os.read(fd, 32)
        if len(header) != 32 or header[:4] != b"\xcf\xfa\xed\xfe":
            return None
        count, size = struct.unpack_from("<II", header, 16)
        if count > 4096 or size > 65536 or size < count * 8:
            return None
        commands = os.read(fd, size)
        if len(commands) != size:
            return None
        offset, found = 0, []
        for _ in range(count):
            if offset + 8 > size:
                return None
            command, length = struct.unpack_from("<II", commands, offset)
            if length < 8 or length % 4 or offset + length > size:
                return None
            if command == 0x1b:
                if length != 24:
                    return None
                found.append(str(uuid.UUID(bytes=commands[offset + 8:offset + 24])))
            offset += length
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            return None
        return found[0] if offset == size and len(found) == 1 else None
    finally:
        os.close(fd)


def _private_json(path: Path, value: dict) -> None:
    _assert_no_symlink_ancestor(path)
    fd, temporary = tempfile.mkstemp(prefix=".monitor-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(value, sort_keys=True).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CrashMonitor:
    """One launcher-owned monitor with durable session and publication state."""

    def __init__(self, app, launcher_version: str, *, reports_directory=None,
                 process_probe: Callable = running_game, store=None, parser=None,
                 publisher=None, now=None, image_uuid_reader=executable_image_uuid) -> None:
        from .crash_reports import CrashStore, parse_crash_report
        from .crash_reporting import GitHubCrashPublisher
        self.app, self.launcher_version = app, launcher_version
        self.root = app.library / "crash-reports"
        self.store = store if store is not None else CrashStore(self.root)
        self.parser = parser if parser is not None else parse_crash_report
        source_root = Path(__file__).resolve().parents[2]
        checkout = source_root if (source_root / ".git").exists() else None
        self.publisher = publisher if publisher is not None else GitHubCrashPublisher(checkout=checkout)
        self.reports_directory = Path(reports_directory or (Path.home() / "Library/Logs/DiagnosticReports"))
        self.process_probe = process_probe
        self.image_uuid_reader = image_uuid_reader
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lock = RLock()
        self.publication_lock = Lock()
        self.fast_observation_until = 0.0
        self.stop_event = Event()
        self.thread = None
        self.owner_fd = None
        self.status = "Crash collection ready; keep the launcher open while playing."
        self.enabled, self.automatic_reporting = True, False
        self.session = None
        self.sessions = {}
        self.seen = set()
        self.next_publish = 0.0
        self.preferences = self.root / "monitor-preferences.json"
        self.session_file = self.root / "active-session.json"
        if self.preferences.exists():
            value = json.loads(_read_regular(self.preferences, 4096))
            if value.get("schema") != 1 or any(type(value.get(k)) is not bool for k in ("enabled", "automatic_reporting")):
                raise ValueError("Crash monitor preferences need inspection")
            self.enabled, self.automatic_reporting = value["enabled"], value["automatic_reporting"]
        self.sessions_directory = self.root / "sessions"
        self.sessions_directory.mkdir(mode=0o700, exist_ok=True)
        _assert_no_symlink_ancestor(self.sessions_directory)
        if stat.S_IMODE(self.sessions_directory.stat().st_mode) != 0o700:
            raise ValueError("Session directory must be private")
        for index, path in enumerate(sorted(self.sessions_directory.glob("*.json"))):
            if index >= 4096:
                raise ValueError("Session history needs inspection")
            value = json.loads(_read_regular(path, 65536))
            if (value.get("schema") == 1 and value.get("executable") == str(app.installation.executable)
                    and value.get("context", {}).get("game_executable_sha256") == app.installation.executable_sha256):
                self.sessions[path.stem] = value
        if self.sessions:
            self.session = max(self.sessions.values(), key=lambda item: item["started_at"])

    def configure(self, *, enabled: bool, automatic_reporting: bool) -> None:
        if type(enabled) is not bool or type(automatic_reporting) is not bool:
            raise ValueError("Crash monitoring choices must be booleans")
        with self.lock:
            _private_json(self.preferences, dict(schema=1, enabled=enabled,
                                               automatic_reporting=automatic_reporting))
            self.enabled, self.automatic_reporting = enabled, automatic_reporting
            self.next_publish = 0

    def arm(self, profile_id: str | None = None, *, process=None) -> None:
        """Called inside the existing guarded launch callback, before open."""
        with self.lock:
            context = dict(game_executable_sha256=self.app.installation.executable_sha256,
                           launcher_version=self.launcher_version,
                           game_version=self.app.installation.bundle_version,
                           macos_version=platform.mac_ver()[0] or "UNKNOWN")
            if getattr(self.app.installation, "bundle_id", None):
                context["expected_bundle_id"] = self.app.installation.bundle_id
            context["process_birth_tolerance_us"] = 1000
            try:
                image_uuid = self.image_uuid_reader(self.app.installation.executable)
            except (OSError, ValueError):
                image_uuid = None
            steam_common = Path.home() / "Library/Application Support/Steam/steamapps/common"
            executable = Path(self.app.installation.executable)
            if image_uuid:
                context["expected_image_uuid"] = image_uuid
            if image_uuid and executable.is_relative_to(steam_common):
                context["expected_redacted_path"] = ("/Users/USER/Library/Application Support/Steam/*/"
                                                     + str(executable.relative_to(executable.parents[3])))
            if profile_id:
                record = next((r for r in self.app.catalogue() if r.profile_id == profile_id), None)
                if record:
                    context.update(archive_sha256=record.archive_sha256, variant_id=record.variant_id)
            started = self.now()
            if process:
                started = datetime.fromtimestamp(process["birth_us"] / 1000000, timezone.utc)
            self.session = dict(schema=1, executable=str(self.app.installation.executable),
                                started_at=started.isoformat(), process=process, context=context)
            identity = hashlib.sha256(json.dumps(self.session, sort_keys=True).encode()).hexdigest()
            _private_json(self.sessions_directory / (identity + ".json"), self.session)
            self.sessions[identity] = self.session
            # A new session can confirm a previously unbound diagnostic.
            self.seen.clear()
            self.fast_observation_until = time.monotonic() + 20

    def _reports(self):
        if not self.reports_directory.exists():
            return []
        _assert_no_symlink_ancestor(self.reports_directory)
        found = []
        with os.scandir(self.reports_directory) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_SCAN_ENTRIES:
                    raise ValueError("Diagnostic report directory needs inspection")
                name = entry.name.lower()
                if (name.startswith(("sid meiers railroads", "sid meier's railroads", "smrailroads"))
                        and name.endswith((".ips", ".crash")) and entry.is_file(follow_symlinks=False)):
                    found.append(Path(entry.path))
        return sorted(found, key=lambda p: p.lstat().st_mtime_ns, reverse=True)

    def poll_once(self, *, reconcile=False) -> None:
        with self.lock:
            if not self.enabled:
                self.status = "Crash monitoring is off."
                return
            probe_failed = False
            try:
                process = self.process_probe(str(self.app.installation.executable))
            except Exception:
                process, probe_failed = None, True
            if process and (not self.session or self.session.get("process") != process):
                active = self.app.profiles.active_profile()
                self.arm(active, process=process)
            if self.sessions:
                count = 0
                earliest = min(datetime.fromisoformat(item["started_at"]).timestamp()
                               for item in self.sessions.values())
                for path in self._reports():
                    info = path.lstat()
                    if info.st_mtime < earliest:
                        continue
                    key = (str(path), info.st_ino, info.st_size, info.st_mtime_ns)
                    if key in self.seen:
                        continue
                    if count >= MAX_REPORTS_PER_POLL:
                        break
                    count += 1
                    try:
                        raw = _read_regular(path, MAX_REPORT_BYTES)
                        self._retain_unconfirmed(raw)
                        payload = None
                        for session in reversed(tuple(self.sessions.values())):
                            bound = session.get("process") or {}
                            if not bound:
                                continue
                            payload = self.parser(raw, expected_executable=session["executable"],
                                                  expected_pid=bound.get("pid"),
                                                  session_started_at=datetime.fromisoformat(session["started_at"]),
                                                  context=session["context"])
                            if payload is not None:
                                self.store.collect(raw, payload)
                                break
                        if payload is None:
                            self._retain_unconfirmed(raw)
                        self.seen.add(key)
                    except (OSError, ValueError):
                        # Incomplete OS writes remain eligible on the next poll.
                        continue
                if len(self.seen) > MAX_SCAN_ENTRIES:
                    self.seen.clear()
            self.status = "Monitoring Railroads; full crash reports stay on this Mac."
            if probe_failed:
                self.status = "Process observation unavailable; retained-session crash collection continues."
            publish_due = self.automatic_reporting and (reconcile or time.monotonic() >= self.next_publish)
        # Network work must never hold the session lock needed by Play.
        if publish_due and self.publication_lock.acquire(blocking=False):
            try:
                if self.automatic_reporting:
                    self._publish_pending(reconcile=reconcile)
                    self.next_publish = time.monotonic() + 300
            finally:
                self.publication_lock.release()

    def bind_after_launch(self, profile_id=None, *, maximum_seconds=2.0) -> bool:
        """Observe promptly within a finite part of the existing startup wait."""
        deadline = time.monotonic() + min(2.0, max(0.0, maximum_seconds))
        while time.monotonic() < deadline and not self.stop_event.is_set():
            process = self.process_probe(str(self.app.installation.executable))
            if process:
                self.arm(profile_id, process=process)
                return True
            self.stop_event.wait(0.05)
        self.status = "Launch not yet observed; unmatched crash reports stay private."
        return False

    def _retain_unconfirmed(self, raw: bytes) -> None:
        directory = self.root / "unconfirmed"
        directory.mkdir(mode=0o700, exist_ok=True)
        _assert_no_symlink_ancestor(directory)
        if stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise ValueError("Unconfirmed report directory must be private")
        path = directory / (hashlib.sha256(raw).hexdigest() + ".raw")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if _read_regular(path, MAX_REPORT_BYTES) != raw:
                raise ValueError("Private diagnostic retention mismatch")
            return
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def _publish_pending(self, *, reconcile=False):
        handled = set()
        pending = self.store.pending()
        uncertain = {e["fingerprint"] for e in pending if e["publication"]["status"] in ("sending", "unknown")}
        for entry in pending:
            payload = entry["payload"]
            fingerprint = payload["fingerprint"]
            if fingerprint in handled:
                continue
            if fingerprint in uncertain and entry["publication"]["status"] == "pending":
                self.status = "A matching crash report awaits publication reconciliation."
                continue
            if len(handled) >= 4:
                break
            handled.add(fingerprint)
            publication = entry["publication"]
            state = publication["status"]
            if state in ("sending", "unknown"):
                if not reconcile:
                    self.status = "A crash report awaits publication reconciliation."
                    continue
                result = self.publisher.publish(payload, existing_issue_url=publication.get("issue_url"),
                                                reconcile_only=True)
            else:
                self.store.mark_publication(fingerprint, "sending", incident_id=entry["incident_id"])
                try:
                    result = self.publisher.publish(payload, existing_issue_url=publication.get("issue_url"))
                except Exception:
                    self.store.mark_publication(fingerprint, "unknown", incident_id=entry["incident_id"])
                    self.status = "Publication outcome unknown; report retained locally."
                    continue
            status = result.get("status", "unknown")
            if status == "pending" and state not in ("sending", "unknown"):
                # Adapter guarantees pending means no POST began. Durable sending
                # is deliberately resolved only on this explicit no-mutation result.
                self.store.reconcile_publication(fingerprint, "pending", detail=result.get("detail"), incident_id=entry["incident_id"])
            else:
                self.store.mark_publication(fingerprint, status, issue_url=result.get("issue_url"), detail=result.get("detail"), incident_id=entry["incident_id"])
            self.status = ("Sanitized crash report published to GitHub." if status == "published"
                           else "Crash report retained locally; GitHub reporting is pending.")

    def start(self) -> None:
        if self.thread is not None:
            return
        lock_path = self.root / ".monitor-owner.lock"
        _assert_no_symlink_ancestor(lock_path)
        owner_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(owner_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise ValueError("Monitor ownership needs inspection")
            fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            os.close(owner_fd)
            raise
        self.owner_fd = owner_fd
        def work():
            try:
                while not self.stop_event.is_set():
                    try:
                        self.poll_once()
                    except Exception:
                        self.status = "Crash monitoring needs inspection; the game is unaffected."
                    self.stop_event.wait(0.1 if time.monotonic() < self.fast_observation_until else 5)
            finally:
                os.close(owner_fd)
                self.owner_fd = None
        self.thread = Thread(target=work, daemon=True, name="smr-passive-crash-monitor")
        try:
            self.thread.start()
        except Exception:
            os.close(owner_fd)
            self.owner_fd = None
            self.thread = None
            raise

    def close(self) -> None:
        self.stop_event.set()
