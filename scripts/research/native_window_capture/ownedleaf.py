"""Fixed owned leaves only. Import has no external effects; fakes inject spawn/clock."""
from pathlib import Path
import json, resource, subprocess, time
IMAGE = 31 * 1024**2
TEXT = 65536
AGGREGATE = 256 * 1024**2
REPORT = 1024**2

class Refused(RuntimeError): pass

def error(exc): return type(exc).__name__ + ': ' + str(exc)[:512]

def limits(kind):
    cap = IMAGE if kind == 'capture' else TEXT
    resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))

class Ledger:
    def __init__(self, anchor, clock=time.monotonic):
        self.anchor, self.clock = anchor, clock
        self.reserved = REPORT
        self.reservations = [{'kind':'final-report', 'bytes':REPORT}]
        self.children, self.attempts = [], []
        self.held = False
        self.actual_captures = 0
    def margin(self, seconds):
        if self.held or self.clock() - self.anchor + seconds > 180:
            raise Refused('Held lane or insufficient original-clock margin')
    def reserve(self, kind, amount):
        if type(amount) is not int or amount < 0 or self.reserved + amount > AGGREGATE:
            raise Refused('Aggregate reservation exhausted')
        self.reserved += amount
        self.reservations.append({'kind':kind, 'bytes':amount})
    def overrun(self): return self.clock() - self.anchor > 180

class Leaves:
    def __init__(self, ledger, directory, probe, spawn=subprocess.Popen, monitor=None):
        self.ledger, self.directory, self.probe, self.spawn = ledger, Path(directory), str(probe), spawn
        self.monitor = monitor
        self.before_launch = lambda:None
    def start(self, kind, argument=None):
        l = self.ledger
        self.before_launch()
        if kind not in ('capture','inspect','decode','fixture','sleep'):
            raise Refused('Unknown fixed leaf')
        if kind in ('inspect','decode') and self.monitor is None: raise Refused('Missing operational owned-child footprint monitor')
        seconds = {'capture':7,'inspect':4,'decode':4,'fixture':15,'sleep':7}[kind]
        l.margin(seconds + 10)
        if kind == 'capture':
            window, image = argument
            if type(window) is not int or window <= 0 or Path(image).name != 'window.png':
                raise Refused('Invalid fixed capture arguments')
            if l.actual_captures >= 8: raise Refused('Capture count exhausted')
            argv = ['/usr/sbin/screencapture','-x','-o','-l'+str(window),str(image)]
            l.reserve(kind, IMAGE + 16384)
        else:
            argv = {'fixture':[self.probe,'fixture'], 'sleep':['/bin/sleep','10']}.get(kind)
            if argv is None: argv = [self.probe, 'inspect' if kind=='inspect' else 'image', str(argument)]
            l.reserve(kind, TEXT*2 + 16384)
        index = len(l.children)
        record = {'kind':kind,'argv':list(map(str,argv)),'pid':None,'exitcode':None,'reaped':False,'settlement':'not-launched','errors':[]}
        l.children.append(record)
        stdout = self.directory / ('child-%d.stdout'%index)
        stderr = self.directory / ('child-%d.stderr'%index)
        # Capture emits no diagnostic data; separate fsize limits cover small helper/fixture streams.
        handles = []
        try:
            if kind == 'capture': out = err = subprocess.DEVNULL
            else:
                out = stdout.open('xb'); handles.append(out)
                err = stderr.open('xb'); handles.append(err)
            if kind == 'capture': l.actual_captures += 1
            record['settlement'] = 'launch-requested'
            child = self.spawn(argv, stdout=out, stderr=err, preexec_fn=lambda:limits(kind))
            record['pid'] = child.pid; record['settlement'] = 'live'
        except Exception as exc:
            record['errors'].append(error(exc)); record['settlement'] = 'launch-failed-held'; l.held=True
            raise
        finally:
            for h in handles: h.close()
        return child, record, stdout, stderr
    def settle(self, child, record, *, fixture=False):
        # Even a failed signal must take the finite wait path. Only this exact child object is signalled.
        if fixture:
            if record.get('fixture_cleanup_attempted'): return
            record['fixture_cleanup_attempted']=True
            try: child.terminate()
            except Exception as exc: record['errors'].append(error(exc))
            try:
                code = child.wait(timeout=8)
                record.update(exitcode=code,reaped=True,settlement='reaped')
                return
            except Exception as exc: record['errors'].append(error(exc))
        try: child.kill()
        except Exception as exc: record['errors'].append(error(exc))
        try:
            code = child.wait(timeout=2)
            record.update(exitcode=code,reaped=True,settlement='reaped')
        except Exception as exc:
            record['errors'].append(error(exc)); record['settlement']='unknown-held'; self.ledger.held=True
    def run(self, kind, argument=None):
        child, record, out, err = self.start(kind, argument)
        timeout = 5 if kind in ('capture','sleep') else 2
        try:
            if kind in ('inspect','decode'):
                operation_deadline = self.ledger.clock() + 2
                for _ in range(100):
                    footprint=self.monitor(child.pid)
                    if type(footprint) is not int or footprint<0 or footprint>=256*1024**2:
                        raise Refused('Operational footprint unknown/exceeded')
                    record['peak_sampled_footprint']=max(record.get('peak_sampled_footprint',0),footprint)
                    record['footprint_qualification']='Sampled only; unsampled intervals/OS allocations untraced'
                    remaining=operation_deadline-self.ledger.clock()
                    if remaining<=0: raise subprocess.TimeoutExpired(record['argv'],2)
                    try: code=child.wait(timeout=min(.02,remaining)); break
                    except subprocess.TimeoutExpired: continue
                else: raise subprocess.TimeoutExpired(record['argv'],2)
            else: code = child.wait(timeout=timeout)
            record.update(exitcode=code,reaped=True,settlement='reaped')
        except Exception as exc:
            record['errors'].append(error(exc)); self.settle(child,record)
        if kind != 'capture':
            for path in (out,err):
                if path.stat().st_size > TEXT: record['errors'].append('Production text limit exceeded')
        if record['errors'] or not record['reaped'] or record['exitcode'] != 0 or self.ledger.overrun():
            raise Refused('Owned leaf failed: '+json.dumps(record,sort_keys=True))
        return record, out
    def json(self, kind, argument):
        record, out = self.run(kind,argument)
        return json.loads(out.read_bytes())
