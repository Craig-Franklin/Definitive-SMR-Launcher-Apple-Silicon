"""Explicit future synthetic proof only. Importing this module imports no candidates.

Root must separately review/admit the exact source closure and circuit exception.
No profile restoration, game launch, GUI control, retries or arbitrary commands.
"""
from pathlib import Path
import hashlib, importlib.util, json, stat, sys, time
REQUIRED = frozenset(('proof.py','capture.py','ownedleaf.py','WindowProbe.swift','WindowProbe'))
MAX_SOURCE_BYTES = 32*1024**2

class Refused(RuntimeError): pass

def verify_sources(rows, required_paths):
    """Required paths are independently frozen root admission inputs, not derived from rows."""
    if not rows or not required_paths or len(set(required_paths))!=len(required_paths): raise Refused('Missing required source set')
    paths=[r['path'] for r in rows]
    if len(paths)!=len(set(paths)) or set(paths)!=set(required_paths) or not REQUIRED <= {Path(p).name for p in paths}: raise Refused('Incomplete exact source closure')
    consumed=0
    for row in rows:
        p=Path(row['path']); s=p.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_size!=row['bytes'] or s.st_mtime_ns!=row['mtime_ns'] or stat.S_IMODE(s.st_mode)!=row['mode']: raise Refused('Changed source metadata')
        consumed+=s.st_size
        if consumed>MAX_SOURCE_BYTES: raise Refused('Source read bound')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']: raise Refused('Changed source hash')
    return consumed

def load_candidates(rows, required_paths):
    verify_sources(rows,required_paths)  # BEFORE every candidate import, including transitive ownedleaf.
    directory=Path(__file__).resolve().parent
    if {str(directory/n) for n in ('proof.py','capture.py','ownedleaf.py')} - set(required_paths): raise Refused('Wrong candidate path')
    for name in ('ownedleaf','capture'):
        if name in sys.modules: raise Refused('Preimported candidate not accepted')
        spec=importlib.util.spec_from_file_location(name,directory/(name+'.py'))
        module=importlib.util.module_from_spec(spec); sys.modules[name]=module
        try: spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name,None); raise
        verify_sources(rows,required_paths)
    return sys.modules['ownedleaf'],sys.modules['capture']

def finalize(ledger, primary, fixture, leaves, path, *, writer=None):
    """Cleanup failure never prevents assembling or returning an explicit primitive failure."""
    cleanup=[]
    if fixture is not None:
        try: leaves.settle(fixture[0],fixture[1],fixture=True)
        except Exception as exc: cleanup.append(type(exc).__name__+': '+str(exc)[:512]); ledger.held=True
        cleanup.extend(fixture[1]['errors'])
    failed=bool(primary or cleanup or ledger.held or ledger.overrun())
    report={'status':'FAILED' if failed else 'PASS_SYNTHETIC_ONLY','primary_error':primary,'cleanup_errors':cleanup,'held':ledger.held,'elapsed':ledger.clock()-ledger.anchor,'overrun':ledger.overrun(),'actual_captures':ledger.actual_captures,'children':ledger.children,'attempts':ledger.attempts,'all_children_reaped':all(r['reaped'] for r in ledger.children),'reserved_bytes':ledger.reserved,'reservations':ledger.reservations,'live_failures_retained':3,'capture_circuit':'CLOSED','game_admitted':False,'full_scenario_credit':False,'cargo_credit':False,'visual_label_text_stripes':'ROOT_INSPECTION_REQUIRED','platform_reads_allocations':'OS/dependency/transitive reads and allocations untraced; not strict whole-process cap'}
    if not report['all_children_reaped']: report.update(status='FAILED',held=True); ledger.held=True
    try:
        data=(json.dumps(report,sort_keys=True)+'\n').encode()
        if len(data)>1024**2: raise Refused('Reserved final report bound')
        if writer is None:
            with Path(path).open('xb') as stream: stream.write(data)
        else: writer(path,data)
    except Exception as exc:
        report.update(status='FAILED',report_error=type(exc).__name__+': '+str(exc)[:512],held=True); ledger.held=True
    return report

def run_proof(admission, directory, process_inspect, *, child_footprint=None, clock=time.monotonic, spawn=None):
    """Only a concrete future root admission invokes this. Process callback only inspects exact owned PID.

    Caller must pin callback and every transitive/helper/binary dependency in required_sources;
    identified OS/platform internals remain the reviewed assurance limitation.
    """
    anchor=clock()  # Single original proof anchor; includes freeze, imports and cleanup.
    if not all(admission.get(k) is True for k in ('source_go','concrete_user_circuit_exception','fresh_root_admission')): raise Refused('Source-only: fourth proof not admitted')
    rows=admission['source_freeze']; required=admission['required_sources']
    # Required closure must include the exact process inspector code independently identified by root.
    if child_footprint is None: raise Refused('Missing operational owned-child footprint monitor')
    if not admission.get('process_inspector_sources') or not set(admission['process_inspector_sources'])<=set(required): raise Refused('Missing process inspector closure')
    for callback in (process_inspect,child_footprint):
        function=getattr(callback,'__func__',callback); code=getattr(function,'__code__',None)
        if code is None or str(Path(code.co_filename).resolve()) not in admission['process_inspector_sources']: raise Refused('Unverified inspector/monitor callback source')
    owned,capture=load_candidates(rows,required)
    ledger=owned.Ledger(anchor,clock); directory=Path(directory)
    if directory.exists() or directory.is_symlink(): raise Refused('Fresh root private packet required')
    directory.mkdir(mode=0o700)
    probe=next(r['path'] for r in rows if Path(r['path']).name=='WindowProbe')
    leaves=owned.Leaves(ledger,directory,probe,monitor=child_footprint,**({'spawn':spawn} if spawn else {}))
    fixture=None; primary=None
    def freeze(): verify_sources(rows,required); ledger.margin(10)
    leaves.before_launch=freeze
    def inspect(w): freeze(); return leaves.json('inspect',w)
    def decode(p): freeze(); return leaves.json('decode',p)
    try:
        freeze(); fixture=leaves.start('fixture')
        child,record,out,err=fixture; fixture=(child,record)
        ready_deadline=min(anchor+5,anchor+180-10)
        while out.stat().st_size==0:
            if clock()>=ready_deadline or child.poll() is not None: raise Refused('Fixture readiness failed')
            time.sleep(.02)
        if out.stat().st_size>owned.TEXT or err.stat().st_size>owned.TEXT: raise Refused('Fixture output bound')
        ready=json.loads(out.read_bytes())
        if ready['pid']!=child.pid: raise Refused('Fixture PID mismatch')
        expected=process_inspect(child.pid)
        if expected is None: raise Refused('Missing owned fixture identity')
        windows={r['kind']:r['window'] for r in ready['windows']}
        for kind in ('A','B'):
            freeze()
            attempt=capture.capture_once(expected,windows[kind],directory/('capture-'+kind),lambda:process_inspect(child.pid),inspect,expected_geometry={'A':(1200,700),'B':(640,420)}[kind],leaves=leaves,image_inspect=decode)
            if (attempt['before']['bounds']['width'],attempt['before']['bounds']['height']) != {'A':(1200,700),'B':(640,420)}[kind]: raise Refused('Wrong original target geometry')
            left,right=attempt['decoded']['left_right_rgba']
            if kind=='A': valid=left[0]>200 and max(left[1:3])<60 and right[2]>200 and max(right[:2])<60
            else: valid=left[1]>200 and max(left[::2][:2])<60 and min(right[:2])>200 and right[2]<60
            if not valid: raise Refused('Native target markers mismatch')
        wrong={**expected,'birth_us':expected['birth_us']+1}; count=ledger.actual_captures
        try: capture.capture_once(wrong,windows['A'],directory/'wrong-pair',lambda:process_inspect(child.pid),inspect,expected_geometry=(1200,700),leaves=leaves,image_inspect=decode)
        except owned.Refused as exc:
            if str(exc)!='Process incarnation mismatch': raise
        else: raise Refused('Wrong pairing accepted')
        if ledger.actual_captures!=count: raise Refused('Wrong pairing launched capture')
        freeze()
        try: leaves.run('sleep')
        except owned.Refused:
            r=ledger.children[-1]
            if r['kind']!='sleep' or not r['reaped'] or r['exitcode']!=-9 or len(r['errors'])!=1 or not r['errors'][0].startswith('TimeoutExpired:'): raise Refused('Inert timeout not settled')
        else: raise Refused('Inert timeout missing')
        leaves.settle(fixture[0],fixture[1],fixture=True)
        if fixture[1]['errors'] or not fixture[1]['reaped']: raise Refused('Fixture cleanup failed')
        fixture=None
        if process_inspect(child.pid) is not None: raise Refused('Fixture absence unknown')
        try: inspect(windows['B'])
        except owned.Refused:
            r=ledger.children[-1]
            if r['kind']!='inspect' or not r['reaped'] or r['exitcode']!=3 or r['errors']: raise
        else: raise Refused('Closed owned window still present')
        freeze()
    except Exception as exc: primary=type(exc).__name__+': '+str(exc)[:1024]
    return finalize(ledger,primary,fixture,leaves,directory/'proof-result.json')
