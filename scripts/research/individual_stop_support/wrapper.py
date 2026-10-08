"""Original Python3.9 single-observer API. Offline candidate; no executable CLI.

All clocks, process factories and sleep are supplied by root. Root must separately
bind the actual pinned interpreter and conservative clock domain, helper birth,
watchdog, monitor, phase and freeze before EVERY GUI action. No receipt grants GUI.
Sole-root, private same-uid trust; no cryptographic authentication is invented.
"""
import hashlib
import json
import os
import stat
import threading
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from adapter import Adapter
from observer import Admission, Observer, State
from individual_stop import _directory, _stamp, _number, _text, read_receipt

HERE = Path(__file__).absolute().parent
OBS = '8683b919f8d3bd0b68675e6c6731839144a7c68555a75efafab8309ca18b19b3'
COMP = 'c7a4a0eee7e2ceb2cf900eeabcc93689119c75e8f61fe96bac933c5247079848'
NAMES = {'wrapper.py', 'adapter.py', 'observer.py', 'individual_stop.py',
         'frozen_mac_processes.py', 'proposal.json'}

class Denied(ValueError):
    pass

@dataclass(frozen=True)
class RootAdmission:
    job: str
    nonce: str
    code: str
    anchor: float
    expected_executable: str
    interpreter: str
    clock_domain: str


def bounded(path, cap=65536):
    fdparent = _directory(Path(path).parent)
    fd = None
    try:
        fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=fdparent)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= cap:
            raise Denied('bounded regular file required')
        raw = b''
        while len(raw) <= cap:
            part = os.read(fd, cap + 1 - len(raw))
            if not part: break
            raw += part
        named = os.stat(Path(path).name, dir_fd=fdparent, follow_symlinks=False)
        rebound = _directory(Path(path).parent)
        try:
            if _stamp(os.fstat(rebound))[:2] != _stamp(os.fstat(fdparent))[:2]:
                raise Denied('code parent changed')
        finally: os.close(rebound)
        if len(raw) != before.st_size or _stamp(before) != _stamp(os.fstat(fd)) or _stamp(before) != _stamp(named):
            raise Denied('changed file')
        return raw
    finally:
        if fd is not None: os.close(fd)
        os.close(fdparent)


def pins(code):
    raw = bounded(HERE/'pins.json', 8192)
    if hashlib.sha256(raw).hexdigest() != code:
        raise Denied('root code pin mismatch')
    spec = json.loads(raw)
    if set(spec) != NAMES or spec['observer.py'] != OBS or spec['individual_stop.py'] != COMP:
        raise Denied('fixed dependency set required')
    for name, expected in spec.items():
        if hashlib.sha256(bounded(HERE/name)).hexdigest() != expected:
            raise Denied('code replaced')
    return spec


def parent_stamp(packet):
    fd = _directory(packet)
    try: return _stamp(os.fstat(fd))[:2]
    finally: os.close(fd)


def publish(packet, name, value, expected_parent):
    """Bounded atomic replacement, fsync and logical parent/path readback."""
    raw = (json.dumps(value, sort_keys=True, allow_nan=False)+'\n').encode()
    if len(raw) > 8192: raise Denied('state too large')
    directory = _directory(packet)
    tmp = '.wrapper-'+uuid.uuid4().hex
    fd = None
    try:
        if _stamp(os.fstat(directory))[:2] != expected_parent:
            raise Denied('packet replaced')
        try:
            s = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(s.st_mode): raise Denied('state path nonregular')
        except FileNotFoundError: pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        view = memoryview(raw)
        while view:
            n = os.write(fd, view)
            if n <= 0: raise OSError('short write')
            view = view[n:]
        os.fsync(fd)
        written = _stamp(os.fstat(fd))
        os.close(fd); fd = None
        if parent_stamp(packet) != expected_parent: raise Denied('packet replaced')
        os.replace(tmp, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        data, fp = read_receipt(Path(packet)/name)
        if (fp[0][:3] != written[:3] or fp[1] != hashlib.sha256(raw).hexdigest() or
                data != value or parent_stamp(packet) != expected_parent):
            raise Denied('publication readback changed')
    finally:
        if fd is not None: os.close(fd)
        try: os.unlink(tmp, dir_fd=directory)
        except FileNotFoundError: pass
        os.close(directory)


class Wrapper:
    """Finite single lifetime. Admission checked before any monitor factory.

    approval is root's exact issue127 amendment record; source_acceptance binds
    separately accepted129. Synthetic fixtures exercise gates, never authorize OS.
    Actual interpreter/clock provenance is a root consumer dependency, not proved
    by these strings. No dynamic module path, process scan or environment lookup.
    """
    def __init__(self, admission, packet, approval, source_acceptance,
                 process_factory, clock, sleep):
        if type(admission) is not RootAdmission: raise Denied('root admission required')
        a = admission
        if (not all(_text(x) for x in (a.job,a.nonce,a.code,a.expected_executable,a.interpreter,a.clock_domain))
                or not _number(a.anchor) or a.anchor < 0 or
                not Path(a.expected_executable).is_absolute() or not Path(a.interpreter).is_absolute()):
            raise Denied('invalid root admission')
        spec = pins(a.code)
        proposal = json.loads(bounded(HERE/'proposal.json'))
        expected = {'issue':127, 'status':'approved', 'proposal_sha256':proposal['sha256'],
                    'job':a.job,'nonce':a.nonce,'code':a.code,'anchor':a.anchor}
        if type(approval) is not dict or approval != expected:
            raise Denied('exact127 approval absent/pending/wrong')
        if source_acceptance != {'issue':129,'status':'accepted','observer_sha256':OBS,'component_sha256':COMP}:
            raise Denied('separate source129 acceptance absent/rejected')
        self.a = a
        self.packet = Path(packet)
        self.parent = parent_stamp(self.packet)
        n = clock()
        if not _number(n) or not a.anchor <= n < a.anchor+540:
            raise Denied('clock outside immutable anchor deadline')
        self.clock, self.sleep = clock, sleep
        self.last = n
        self.next_tick = n
        self.seq = 0
        self.terminal = False
        self.state = State('new','not_ready')
        self.error = None
        self.lock = threading.Lock()
        # Exact descriptor executable is checked BEFORE Observer sees it.
        self.monitor = Adapter(process_factory, a.expected_executable)
        self.observer = Observer(Admission(a.job,a.nonce,a.code,a.anchor), self.packet,
                                 self.monitor, clock)

    def _failure(self, reason):
        self.observer._fail(reason)  # existing exact bound stop; no target prebind
        p = (self.observer._supervisor.binding.process
             if self.observer._supervisor is not None else None)
        self.state = State('unknown',reason,terminal=True,process=p)
        self.terminal = True
        return self.state

    def _envelope(self, state, now, kind):
        a = self.a
        return {'version':1,'kind':kind,'job':a.job,'nonce':a.nonce,'code':a.code,
                'anchor':a.anchor,'expected_executable':a.expected_executable,
                'interpreter':a.interpreter,'clock_domain':a.clock_domain,
                'state_seq':self.seq,'observed':now,'state':asdict(state)}

    def run_once(self):
        with self.lock:
            if self.terminal: return self.state
            try:
                now = self.clock()
                if not _number(now) or now < self.last: raise Denied('invalid clock')
                if now < self.next_tick: return self.state
                self.last = now
                self.next_tick = now + 1  # no catch-up bursts
                pins(self.a.code)
                if parent_stamp(self.packet) != self.parent: raise Denied('packet replaced')
                if self.observer._supervisor is None:
                    try: desc, _ = read_receipt(self.packet/'descriptor.json')
                    except Exception:
                        try: (self.packet/'descriptor.json').lstat()
                        except FileNotFoundError: desc = None
                        else: raise
                    if desc is not None and (type(desc.get('process')) is not dict or
                            desc['process'].get('executable') != self.a.expected_executable):
                        raise Denied('descriptor executable mismatch')
                self.state = self.observer.step()
            except Exception:
                self._failure('wrapper_validation_failure')
            self.seq += 1
            try:
                state = self.state
                if state.ready: publish(self.packet,'ready.json',self._envelope(state,self.last,'ready'),self.parent)
                if state.bound: publish(self.packet,'bound.json',self._envelope(state,self.last,'bound'),self.parent)
                # State is commit record; root must match state_seq across receipts.
                publish(self.packet,'state.json',self._envelope(state,self.last,'state'),self.parent)
            except Exception as exc:
                self.error = type(exc).__name__
                self._failure('publication_failure')
                # No second publication attempt and no durable-success claim.
            self.terminal = self.state.terminal
            return self.state

    def run(self, max_steps=541):
        if type(max_steps) is not int or not 1 <= max_steps <= 541:
            raise Denied('finite max_steps required')
        for index in range(max_steps):
            state = self.run_once()
            if state.terminal: return state
            if index+1 < max_steps: self.sleep(1)
        with self.lock:
            state = self._failure('finite_loop_exhausted')
            self.seq += 1
            try:
                publish(self.packet,'state.json',self._envelope(state,self.last,'state'),self.parent)
            except Exception as exc:
                self.error = type(exc).__name__
                self._failure('publication_failure')
            return self.state
