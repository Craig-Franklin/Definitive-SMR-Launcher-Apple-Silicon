"""Explicit root-owned capture calls; no automatic game or desktop operation."""
from pathlib import Path
import hashlib, importlib.util, json, math, os, stat, sys, time

class Refused(RuntimeError): pass

def verify(rows):
    paths=[r['path'] for r in rows]
    if not paths or len(paths)!=len(set(paths)): raise Refused('Duplicate/missing closure')
    for r in rows:
        p=Path(r['path']);s=p.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_size!=r['bytes'] or stat.S_IMODE(s.st_mode)!=r['mode'] or s.st_mtime_ns!=r['mtime_ns'] or hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256']:raise Refused('Frozen source changed')

def load(source, rows):
    verify(rows)
    if any(name in sys.modules for name in ('ownedleaf','capture')):raise Refused('Preimported candidate')
    for name in ('ownedleaf','capture'):
        path=source/(name+'.py')
        if str(path) not in {r['path'] for r in rows}:raise Refused('Missing candidate source')
        spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m
        try:spec.loader.exec_module(m)
        except BaseException:sys.modules.pop(name,None);raise
        verify(rows)
    return sys.modules['ownedleaf'],sys.modules['capture']

class Consumer:
    def __init__(self, *, directory, source, probe, rows, game_anchor, expected, window, geometry, gate, process_inspect, child_footprint, clock=time.monotonic):
        if type(game_anchor) not in (int,float) or not math.isfinite(game_anchor) or game_anchor>clock():raise Refused('Invalid original game anchor')
        if type(geometry) is not tuple or len(geometry)!=2 or any(type(v) is not int or not 1<=v<=4096 for v in geometry):raise Refused('Invalid frozen geometry')
        if type(window) is not int or window<=0:raise Refused('Invalid exact window')
        expected=dict(expected)
        if set(expected)!={'pid','birth_us','executable'} or type(expected['pid']) is not int or expected['pid']<=0 or type(expected['birth_us']) is not int or expected['birth_us']<=0 or not isinstance(expected['executable'],str) or not expected['executable'].startswith('/'):raise Refused('Invalid bound game')
        paths={r['path'] for r in rows}
        for callback in (gate,process_inspect,child_footprint):
            f=getattr(callback,'__func__',callback);code=getattr(f,'__code__',None)
            if code is None or str(Path(code.co_filename).resolve()) not in paths:raise Refused('Unfrozen root callback')
        source=Path(source).resolve();probe=Path(probe).resolve();directory=Path(directory)
        needed={str(source/n) for n in ('capture.py','ownedleaf.py','WindowProbe.swift')}|{str(probe),str(Path(__file__).resolve()),str(Path(sys.executable).resolve()),'/usr/sbin/screencapture'}
        if not needed<=paths:raise Refused('Incomplete capture closure')
        verify(rows)
        if directory.exists() or directory.is_symlink() or not directory.parent.is_dir() or stat.S_IMODE(directory.parent.stat().st_mode)!=0o700:raise Refused('Fresh private consumer directory required')
        if gate() is not True:raise Refused('Fresh root admission refused')
        if process_inspect()!=expected:raise Refused('Bound game changed')
        # One exclusive persistent consumer per exact job; no per-call reset or reinit.
        directory.mkdir(mode=0o700)
        with (directory/'consumer-start.json').open('x') as f:json.dump({'game_anchor':game_anchor,'expected':expected,'window':window,'geometry':geometry},f)
        owned,capture=load(source,rows)
        self.directory,self.rows,self.expected,self.window,self.geometry=directory,rows,expected,window,geometry
        self.gate,self.process_inspect,self.capture_api=gate,process_inspect,capture
        self.ledger=owned.Ledger(game_anchor,clock)
        self.leaves=owned.Leaves(self.ledger,directory,str(probe),monitor=child_footprint)
        self.leaves.before_launch=self.guard
        self.guard();self.report()
    def guard(self):
        verify(self.rows);self.ledger.margin(25)
        if self.gate() is not True:raise Refused('Fresh root gate refused')
        if self.process_inspect()!=self.expected:raise Refused('Bound game changed')
        verify(self.rows)
        self.ledger.margin(25)  # Recheck after potentially blocking callbacks.
    def report(self):
        l=self.ledger
        if l.overrun():l.held=True
        d={'anchor':l.anchor,'elapsed':l.clock()-l.anchor,'actual_captures':l.actual_captures,'children':l.children,'attempts':l.attempts,'reserved_bytes':l.reserved,'reservations':l.reservations,'held':l.held,'overrun':l.overrun(),'all_children_reaped':all(r['reaped'] for r in l.children),'gameplay_or_scenario_credit':False}
        try:
            raw=(json.dumps(d,sort_keys=True)+'\n').encode()
            if len(raw)>1024**2:raise Refused('Reserved report bound')
            tmp=self.directory/'ledger.pending'
            with tmp.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.directory/'ledger.json')
        except BaseException:l.held=True;raise
        if l.overrun():
            l.held=True
            d.update(held=True,overrun=True,elapsed=l.clock()-l.anchor)
            # One bounded failed-status update, never another capture or clock anchor.
            try:
                raw=(json.dumps(d,sort_keys=True)+'\n').encode()
                with tmp.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
                os.replace(tmp,self.directory/'ledger.json')
            except BaseException:l.held=True;raise
            raise Refused('Original capture sub-window overrun')
        return d
    def capture(self):
        try:
            if not all(r['reaped'] for r in self.ledger.children):self.ledger.held=True;raise Refused('Unsettled child')
            self.guard()
            result=self.capture_api.capture_once(self.expected,self.window,self.directory/('capture-%d'%len(self.ledger.attempts)),self.process_inspect,lambda w:self.leaves.json('inspect',w),expected_geometry=self.geometry,leaves=self.leaves,image_inspect=lambda p:self.leaves.json('decode',p))
            self.guard()
            return result
        except BaseException:self.ledger.held=True;raise
        finally:self.report()
