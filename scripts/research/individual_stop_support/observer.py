"""Offline single-job observer API. No launcher, CLI, OS monitor or GUI.

Root owns one resolved private0700 packet and is its sole trusted writer.
request.json: version/job/nonce/code/anchor/request (clock-domain timestamp).
descriptor.json: same request fields plus process:{pid,birth,executable}.
sample.json: unchanged individual_stop.Supervisor completed-success schema.
Root may publish returned dataclasses using a separately accepted bounded writer.
ready and bound acknowledge only this observer; root MUST validate unchanged
watchdog same-incarnation acknowledgment separately before any GUI. No method
permits restoration/new jobs. Pending launch reconciliation is always root work.
The unchanged monitor contract still requires atomic replacement refusal; this
candidate does not implement or relax it. Proposal127 remains user-pending.
Readiness without a request expires20s; request deadline never resets anchor.
Observer failure/death must revoke dependent root gates; root must detect death.
"""
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from individual_stop import (Binding, ProcessIdentity, Supervisor, ReceiptError,
                             read_receipt, _directory, _number, _text)
import os

DEADLINES = {'save_start':360, 'stop_trigger':540, 'gui':570,
             'shutdown':600, 'restoration':900, 'watchdog':900}

@dataclass(frozen=True)
class Admission:
    job: str
    nonce: str
    code: str
    anchor: float
    def __post_init__(self):
        if not all(_text(x) for x in (self.job,self.nonce,self.code)):
            raise ValueError('invalid admission')
        if not _number(self.anchor) or self.anchor < 0:
            raise ValueError('invalid anchor')

@dataclass(frozen=True)
class State:
    phase: str
    reason: str
    ready: bool = False
    bound: bool = False
    terminal: bool = False
    process: object = None
    pending_launch: bool = True
    requires_root_reconciliation: bool = True
    gui_grant: bool = False
    restoration_grant: bool = False
    new_job_grant: bool = False

class Observer:
    """One lifetime, one job, bounded synchronous step; no sleeps or retry loop.

    Caller must schedule step, detect observer death, and publish/read back state.
    Injected clock must be shared conservative clock including suspension; this
    API validates finite nondecreasing values, not the clock's OS semantics.
    Monitor calls must be bounded by adapter contract. No OS latency guarantee.
    """
    def __init__(self, admission, packet, monitor, clock):
        if type(admission) is not Admission:
            raise ValueError('Admission required')
        self.admission=admission
        self.packet=Path(packet)
        self.monitor=monitor
        self.clock=clock
        self._last=admission.anchor
        self._ready_at=None
        self._request=None
        self._request_fp=None
        self._descriptor_fp=None
        self._supervisor=None
        self._fault=False
        self._state=State('new','not_ready')
        self._lock=threading.Lock()

    @property
    def state(self):
        return self._state

    def _now(self):
        n=self.clock()
        if not _number(n) or n < self._last or not math.isfinite(n-self.admission.anchor):
            raise ValueError('invalid clock')
        self._last=n
        return n

    def _supervisor_clock(self):
        return float('nan') if self._fault else self._now()

    def _common(self, data, descriptor=False):
        keys={'version','job','nonce','code','anchor','request'}
        if descriptor: keys.add('process')
        a=self.admission
        if (set(data)!=keys or type(data['version']) is not int or data['version']!=1
            or data['job']!=a.job or data['nonce']!=a.nonce or data['code']!=a.code
            or not _number(data['anchor']) or data['anchor']!=a.anchor
            or not _number(data['request'])):
            raise ReceiptError('binding/schema')

    def _read(self, name):
        return read_receipt(self.packet/name)

    def _fail(self, reason):
        # Once constructed the exact component owns stop, including persistence
        # failures. A synthetic invalid clock forces its existing fail-stop path.
        if self._supervisor is not None:
            self._fault=True
            try: self._supervisor.step()
            except Exception: pass  # never claim durable publication or absence
        self._state=State('unknown',reason,terminal=True)
        return self._state

    def _sample(self, binding, now):
        d,fp=self._read('sample.json')
        keys={'version','job','nonce','code','anchor','process','seq','status','start','complete'}
        if (set(d)!=keys or type(d['version']) is not int or d['version']!=1
            or d['job']!=binding.job or d['nonce']!=binding.nonce
            or d['code']!=binding.code or not _number(d['anchor'])
            or d['anchor']!=binding.anchor or d['process']!=binding.process.__dict__
            or type(d['process'].get('pid')) is not int
            or type(d['seq']) is not int or not 0<=d['seq']<2**63
            or d['status']!='success' or not _number(d['start'])
            or not _number(d['complete'])
            or not binding.anchor<=d['start']<=d['complete']<=now
            or now-d['start']>=60):
            raise ReceiptError('initial completed-success sample required')
        return fp

    def step(self):
        with self._lock:
            if self._state.terminal: return self._state
            try: return self._step()
            except Exception as exc:
                # Fixed bounded reason, never include arbitrary exception text.
                return self._fail('protocol_failure:'+type(exc).__name__[:40])

    def _step(self):
        now=self._now()
        a=self.admission
        if self._ready_at is None:
            fd=_directory(self.packet)
            os.close(fd)
            if now>=a.anchor+540: return self._fail('absolute540_before_ready')
            self._ready_at=now
            self._state=State('ready','observer_ready',ready=True)
            return self._state
        if self._request is None:
            try: d,fp=self._read('request.json')
            except ReceiptError:
                # Missing file can wait; malformed existing receipt fails now.
                try: (self.packet/'request.json').lstat()
                except FileNotFoundError:
                    if now>=min(self._ready_at+20,a.anchor+540):
                        return self._fail('request_timeout')
                    return self._state
                raise
            self._common(d)
            if not self._ready_at<=d['request']<=now:
                raise ReceiptError('request predates ready or future')
            if now>=min(self._ready_at+20,a.anchor+540):
                return self._fail('late_request')
            self._request=d
            self._request_fp=fp
        if self._supervisor is None and now>=min(self._request['request']+20,a.anchor+540):
            return self._fail('binding_deadline')
        d,fp=self._read('request.json')
        if d!=self._request or fp!=self._request_fp:
            raise ReceiptError('request replacement')
        if self._supervisor is not None:
            d,fp=self._read('descriptor.json')
            if fp!=self._descriptor_fp: raise ReceiptError('descriptor replacement')
            # Bound polling must run beyond20: deadline applies only to binding.
            return self._poll()
        try: desc,dfp=self._read('descriptor.json')
        except ReceiptError:
            try: (self.packet/'descriptor.json').lstat()
            except FileNotFoundError:
                self._state=State('awaiting_binding','descriptor_pending',ready=True)
                return self._state
            raise
        self._common(desc,True)
        if {k:v for k,v in desc.items() if k!='process'}!=self._request:
            raise ReceiptError('descriptor request mismatch')
        p=desc['process']
        if type(p) is not dict or set(p)!={'pid','birth','executable'}:
            raise ReceiptError('exact process schema')
        expected=ProcessIdentity(**p)
        b=Binding(a.job,a.nonce,a.anchor,a.code,expected)
        sample_fp=self._sample(b,now)
        current=self.monitor.inspect(expected.pid)
        if type(current) is not ProcessIdentity or current!=expected:
            raise ReceiptError('initial identity unknown/mismatch')
        now=self._now()
        if now>=min(self._request['request']+20,a.anchor+540):
            return self._fail('late_initial_identity')
        if self._read('request.json')[1]!=self._request_fp or self._read('descriptor.json')[1]!=dfp:
            raise ReceiptError('binding receipts replaced')
        if self._sample(b,now)!=sample_fp: raise ReceiptError('initial sample replaced')
        self._descriptor_fp=dfp
        self._supervisor=Supervisor(b,self.monitor,self._supervisor_clock,
                                    self.packet/'sample.json',self.packet/'result.json')
        return self._poll(initial=True)

    def _poll(self, initial=False):
        r=self._supervisor.step()
        if r.terminal:
            self._state=State(r.state,r.reason,terminal=True,process=self._supervisor.binding.process)
            return self._state
        p=self._supervisor.binding.process
        current=self.monitor.inspect(p.pid)
        if type(current) is not ProcessIdentity or current!=p:
            return self._fail('bound_identity_unknown_or_mismatch')
        now=self._now()
        if initial and now>=min(self._request['request']+20,self.admission.anchor+540):
            return self._fail('late_holding_ack')
        # Identity inspection may have consumed the entire sample lease.
        # Revalidate freshness at acknowledgment, using the original anchor.
        if now>=self.admission.anchor+540:
            return self._fail('absolute540_before_ack')
        # This sole owner holds the Observer lock. Reuse the pinned component's
        # replay/fingerprint validator here, after the final identity inspection;
        # syntax/freshness alone cannot reject a lower sequence swapped at ack.
        if self._supervisor._receipt(now) >= 60:
            return self._fail('stale_sample_before_ack')
        if self._read('request.json')[1]!=self._request_fp:
            return self._fail('request_changed_before_ack')
        if self._read('descriptor.json')[1]!=self._descriptor_fp:
            return self._fail('descriptor_changed_before_ack')
        self._state=State('bound','holding_exact_identity',ready=True,bound=True,process=p)
        return self._state
