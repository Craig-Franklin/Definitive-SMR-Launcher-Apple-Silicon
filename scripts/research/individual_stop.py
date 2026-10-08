"""Offline, injected stop-only supervision; no CLI or host process adapter.

A private root-owned 0700 packet and one trusted receipt writer are assumptions,
not authentication against malicious same-uid/root writers. Receipts describe
completed successful observations, never heartbeats. Observation-start age60 or
anchor+540 requests stop; future jobs must begin save/reload by360 to retain180.
The outer570/600/900 and6GiB watchdog policies are unchanged. These are targets,
not unconditional OS scheduling guarantees. Prebinding launch gaps, actual OS
adapter identity races, arming/integration, root restoration and operational
assurance require separate acceptance. This component never launches, uses GUI,
saves, restores, retries or imports the rejected runner; it grants no runtime
admission. A monitor must inspect exact identity and request_stop(expected)
atomically refuse a replacement; a preceding inspection alone cannot prove that.
"""
import hashlib
import json
import math
import os
import stat
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

MAX_RECEIPT = 8192
MAX_RESULT = 4096


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _text(value):
    try:
        return type(value) is str and 0 < len(value.encode('utf-8')) <= 256
    except UnicodeError:
        return False


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    birth: str
    executable: str

    def __post_init__(self):
        if type(self.pid) is not int or self.pid <= 0 or not all(
                _text(v) for v in (self.birth, self.executable)):
            raise ValueError('invalid exact process identity')


@dataclass(frozen=True)
class Binding:
    job: str
    nonce: str
    anchor: float
    code: str
    process: ProcessIdentity

    def __post_init__(self):
        if not all(_text(v) for v in (self.job, self.nonce, self.code)):
            raise ValueError('invalid immutable binding')
        if not _number(self.anchor) or self.anchor < 0:
            raise ValueError('invalid monotonic anchor')
        if type(self.process) is not ProcessIdentity:
            raise ValueError('invalid process binding')


class Monitor(Protocol):
    def inspect(self, pid: int) -> Optional[ProcessIdentity]:
        """Return fresh identity, None only for observed absence, or raise."""

    def request_stop(self, expected: ProcessIdentity) -> None:
        """Bounded exact-instance stop, refusing replacement at action time."""


@dataclass(frozen=True)
class Result:
    state: str
    reason: str
    stop_requested: bool
    terminal: bool
    elapsed: Optional[float]


class ReceiptError(ValueError):
    pass


def _stamp(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _directory(path):
    # Caller supplies an already resolved private packet parent; no ancestor
    # symlinks are allowed. Open each component without following links.
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ReceiptError('absolute packet path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=fd)
            os.close(fd)
            fd = nxt
        s = os.fstat(fd)
        if stat.S_IMODE(s.st_mode) != 0o700 or s.st_uid != os.getuid():
            raise ReceiptError('packet must be current-owner private0700')
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_receipt(path):
    """Bounded regular-file read; reject links, replacement and in-read edits."""
    path = Path(path)
    try:
        directory = _directory(path.parent)
    except (OSError, ValueError) as exc:
        raise ReceiptError('packet unavailable') from exc
    fd = None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_RECEIPT:
            raise ReceiptError('nonregular or oversized receipt')
        chunks = []
        remaining = MAX_RECEIPT + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b''.join(chunks)
        after = os.fstat(fd)
        named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        rebound = _directory(path.parent)
        try:
            if _stamp(os.fstat(rebound))[:2] != _stamp(os.fstat(directory))[:2]:
                raise ReceiptError('packet replaced during read')
        finally:
            os.close(rebound)
        if (_stamp(before) != _stamp(after) or _stamp(after) != _stamp(named)
                or len(raw) != before.st_size or len(raw) > MAX_RECEIPT):
            raise ReceiptError('receipt changed during read')
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ReceiptError('duplicate JSON key')
                value[key] = item
            return value
        value = json.loads(raw, object_pairs_hook=unique)
        if type(value) is not dict:
            raise ReceiptError('receipt must be object')
        return value, (_stamp(after), hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, RecursionError) as exc:
        raise ReceiptError('receipt unavailable or invalid') from exc
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def publish_result(path, binding, result):
    """Publish bounded JSON by atomic replacement with file/directory fsync.

    Persistence failure raises; it never manufactures a durable-success receipt.
    The supervisor retains its terminal result even if publication fails.
    """
    path = Path(path)
    raw = (json.dumps({'version': 1, 'job': binding.job, 'nonce': binding.nonce,
                       'anchor': binding.anchor, 'code': binding.code,
                       'process': binding.process.__dict__,
                       'result': result.__dict__}, allow_nan=False,
                      sort_keys=True, ensure_ascii=False) + '\n').encode()
    if len(raw) > MAX_RESULT:
        raise ValueError('result exceeds bound')
    directory = _directory(path.parent)
    name = '.stop-' + uuid.uuid4().hex
    fd = None
    def verify_parent():
        rebound = _directory(path.parent)
        try:
            if _stamp(os.fstat(rebound))[:2] != _stamp(os.fstat(directory))[:2]:
                raise ReceiptError('result packet replaced during publication')
        finally:
            os.close(rebound)

    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError('short result write')
            view = view[count:]
        os.fsync(fd)
        written = os.fstat(fd)
        os.close(fd)
        fd = None
        verify_parent()
        os.replace(name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        verify_parent()
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        observed = os.fstat(fd)
        if (_stamp(observed)[:3] != _stamp(written)[:3]
                or not stat.S_ISREG(observed.st_mode)
                or os.read(fd, MAX_RESULT + 1) != raw):
            raise ReceiptError('published result replaced or changed')
        named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if _stamp(named) != _stamp(os.fstat(fd)):
            raise ReceiptError('published result changed during readback')
        verify_parent()
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


class Supervisor:
    """One bound job; caller schedules bounded step() calls, with no sleeps.

    A successful receipt has exactly version/job/nonce/anchor/code/process/seq/
    status/start/complete. Process is pid/birth/executable; times are absolute
    values in the injected monotonic clock domain. Status must be success.
    Re-reading identical bytes is allowed but never refreshes observation age.
    Atomic new receipts require increasing seq and nondecreasing start/complete.
    A missing first receipt fails closed, with no implicit initial grace.
    """
    def __init__(self, binding: Binding, monitor: Monitor,
                 clock: Callable[[], float], receipt_path, result_path):
        if type(binding) is not Binding:
            raise ValueError('Binding required')
        self._binding = binding
        self._monitor = monitor
        self._clock = clock
        self._receipt_path = Path(receipt_path)
        self._result_path = Path(result_path)
        if (self._receipt_path == self._result_path or
                self._receipt_path.parent != self._result_path.parent):
            raise ValueError('distinct paths in one private packet required')
        self._last_now = binding.anchor
        self._last_receipt = None
        self._last_fingerprint = None
        self._terminal = None
        self._requested = False
        self._lock = threading.Lock()
        self._publication_error = None

    @property
    def binding(self):
        return self._binding

    def _receipt(self, now):
        data, fingerprint = read_receipt(self._receipt_path)
        b = self._binding
        keys = {'version', 'job', 'nonce', 'anchor', 'code', 'process', 'seq',
                'status', 'start', 'complete'}
        if set(data) != keys or type(data['version']) is not int or data['version'] != 1:
            raise ReceiptError('receipt schema')
        if (data['job'] != b.job or data['nonce'] != b.nonce or
                type(data['anchor']) not in (int, float) or data['anchor'] != b.anchor or
                data['code'] != b.code or data['process'] != b.process.__dict__ or
                type(data['process'].get('pid')) is not int):
            raise ReceiptError('receipt binding mismatch')
        if type(data['seq']) is not int or not 0 <= data['seq'] < 2**63:
            raise ReceiptError('receipt sequence')
        if (data['status'] != 'success' or not _number(data['start']) or
                not _number(data['complete']) or
                not b.anchor <= data['start'] <= data['complete'] <= now):
            raise ReceiptError('incomplete or invalid observation time')
        previous = self._last_receipt
        if previous is not None:
            if data['seq'] == previous['seq']:
                if fingerprint != self._last_fingerprint:
                    raise ReceiptError('replayed or replaced receipt')
            elif (data['seq'] < previous['seq'] or data['start'] < previous['start']
                  or data['complete'] < previous['complete']):
                raise ReceiptError('backwards observation')
        self._last_receipt, self._last_fingerprint = data, fingerprint
        return now - data['start']

    def _stop(self, reason, elapsed):
        expected = self._binding.process
        try:
            current = self._monitor.inspect(expected.pid)
        except Exception:
            return Result('unknown', reason + ':inspect_error', False, True, elapsed)
        if current is None:
            return Result('observed_exited', reason, False, True, elapsed)
        if type(current) is not ProcessIdentity or current != expected:
            return Result('identity_denied', reason, False, True, elapsed)
        self._requested = True
        try:
            self._monitor.request_stop(expected)
        except Exception:
            reason += ':stop_error'
        try:
            current = self._monitor.inspect(expected.pid)
        except Exception:
            return Result('unknown', reason + ':reinspect_error', True, True, elapsed)
        if current is None:
            return Result('observed_exited', reason, True, True, elapsed)
        if type(current) is not ProcessIdentity or current != expected:
            return Result('identity_denied', reason + ':replacement', True, True, elapsed)
        return Result('unknown', reason + ':exit_unobserved', True, True, elapsed)

    def step(self):
        with self._lock:
            return self._step()

    def _step(self):
        if self._publication_error is not None:
            raise self._publication_error
        if self._terminal is not None:
            return self._terminal
        try:
            now = self._clock()
            if not _number(now) or now < self._last_now or now < self._binding.anchor:
                raise ValueError('invalid clock')
            elapsed = now - self._binding.anchor
            if not math.isfinite(elapsed):
                raise ValueError('invalid elapsed')
            self._last_now = now
        except Exception:
            result = self._stop('invalid_clock', None)
        else:
            if elapsed >= 540:
                result = self._stop('absolute540', elapsed)
            else:
                try:
                    age = self._receipt(now)
                except (ReceiptError, TypeError, AttributeError):
                    result = self._stop('invalid_receipt', elapsed)
                else:
                    result = (self._stop('stale60', elapsed) if age >= 60 else
                              Result('holding', 'recent_success', False, False, elapsed))
        if result.terminal:
            self._terminal = result
        try:
            publish_result(self._result_path, self._binding, result)
        except Exception as exc:
            # Durable reporting must never disable the safety action. Stop now
            # if even a holding observation cannot be published; retain the
            # original error without claiming the stop result was persisted.
            if not result.terminal:
                result = self._stop('publication_failed', result.elapsed)
            self._terminal = result
            self._publication_error = exc
            raise
        return result
