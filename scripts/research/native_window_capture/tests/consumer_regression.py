"""Independent frozen controls. No real Popen, OS kill, native libraries or game."""
import ast,hashlib,importlib.util,json,pathlib,resource,sys,time,types
resource.setrlimit(resource.RLIMIT_CPU,(30,30))
W=pathlib.Path(__file__).resolve().parent
P=W
start=time.monotonic(); reads=0; method_calls=0; tests=[]; source_ok=True

def read(p):
 global reads
 b=p.read_bytes();reads+=len(b);assert reads<=16*1024**2;return b

def audit(p):
 tree=ast.parse(read(p),filename=str(p))
 for n in tree.body:
  assert isinstance(n,(ast.Import,ast.ImportFrom,ast.FunctionDef,ast.ClassDef,ast.Assign,ast.Expr))
  if isinstance(n,ast.Expr):assert isinstance(n.value,ast.Constant)
  if isinstance(n,ast.Assign):assert not any(isinstance(x,ast.Call) for x in ast.walk(n))
  if isinstance(n,(ast.FunctionDef,ast.ClassDef)):assert not n.decorator_list
 return {'name':p.name,'top_level_effectful_calls':0,'sha256':hashlib.sha256(read(p)).hexdigest()}
audits=[audit(P/n) for n in ('consumer.py','capture.py','ownedleaf.py')]

def module(name,p):
 s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
c=module('review_consumer',P/'consumer.py');owned=module('review_ownedleaf',P/'ownedleaf.py')
for name in ('__init__','guard','report','capture'):
 original=getattr(c.Consumer,name)
 def tracked(self,*a,_fn=original,**kw):
  global method_calls
  method_calls+=1;assert method_calls<=40
  assert time.monotonic()-start<90
  return _fn(self,*a,**kw)
 setattr(c.Consumer,name,tracked)

# Frozen oracle: late final gate MUST raise and hold. No production behavior mirrored.
class Clock:
 def __init__(self):self.now=0
 def __call__(self):return self.now
class Gate:
 def __init__(self,clock):self.clock=clock;self.calls=0;self.ok=True;self.delay_on=None
 def __call__(self):
  self.calls+=1
  if self.calls==self.delay_on:self.clock.now=181
  return self.ok
class FakeLeaves:
 def __init__(self,ledger,directory,probe,monitor=None):self.ledger=ledger;self.before_launch=lambda:None

def fake_capture(expected,window,directory,process,inspect,**kw):
 leaves=kw['leaves'];leaves.before_launch();l=leaves.ledger
 l.reserve('fake-image',100);l.actual_captures+=1;l.attempts.append({'status':'ACCEPTED','synthetic':True})
 return {'status':'ACCEPTED','synthetic':True}

def frozen_verify(rows):
 if not source_ok:raise c.Refused('Synthetic frozen source changed')

def fake_load(source,rows):
 frozen_verify(rows);return types.SimpleNamespace(Ledger=owned.Ledger,Leaves=FakeLeaves),types.SimpleNamespace(capture_once=fake_capture)
c.verify=frozen_verify;c.load=fake_load
bound={'pid':1,'birth_us':1,'executable':'/fake/game'}
def process_inspect():return dict(bound)
def footprint(pid):return 1

def make(label,clock=None,gate=None,**overrides):
 clock=clock or Clock();gate=gate or Gate(clock)
 # Gate callable instance is intentionally adapted by function in this frozen file.
 def gated():return gate()
 source=P;probe=W/'temp'/'fake-probe'
 paths={str(source/'capture.py'),str(source/'ownedleaf.py'),str(source/'WindowProbe.swift'),str(probe.resolve()),str((P/'consumer.py').resolve()),str(pathlib.Path(sys.executable).resolve()),'/usr/sbin/screencapture',str(pathlib.Path(__file__).resolve())}
 args=dict(directory=W/'temp'/label,source=source,probe=probe,rows=[{'path':p} for p in paths],game_anchor=0,expected=bound,window=10,geometry=(1200,700),gate=gated,process_inspect=process_inspect,child_footprint=footprint,clock=clock)
 args.update(overrides);return c.Consumer(**args),clock,gate

def check(name,observed,expected=True,**facts):tests.append(dict(name=name,passed=observed==expected,observed=observed,expected=expected,**facts))
def refuses(fn):
 try:fn();return False
 except Exception:return True

check('invalid_anchor_constructor_refuses',refuses(lambda:make('invalid',game_anchor=float('nan'))))
x=Clock();g=Gate(x);g.ok=False
check('constructor_gate_refuses',refuses(lambda:make('gatefalse',x,g)))
a,clk,g=make('healthy');ledger=a.ledger
r1=a.capture();r2=a.capture()
check('two_calls_share_ledger',a.ledger is ledger and a.ledger.actual_captures==2 and len(a.ledger.attempts)==2 and not a.ledger.held)
g.ok=False
check('fresh_gate_refuses_and_holds',refuses(a.capture) and a.ledger.held and a.ledger.actual_captures==2)
check('same_directory_reinit_refuses',refuses(lambda:make('healthy')))
late,clock,g=make('late');g.delay_on=5
late_returned=not refuses(late.capture)
check('final_callback_crossing180_refuses_and_holds',not late_returned and late.ledger.held,returned_success=late_returned,elapsed=clock.now,held=late.ledger.held,gate_calls=g.calls)
b,clock,g=make('reportfailure');(b.directory/'ledger.pending').write_text('occupied')
check('report_failure_refuses_and_holds',refuses(b.capture) and b.ledger.held)
late.ledger.children.append({'reaped':False})
check('unknown_child_refuses_and_holds',refuses(late.capture) and late.ledger.held)
source_ok=False
check('changed_source_refuses_before_capture',refuses(b.capture) and b.ledger.actual_captures==1)
result={'suite_invocations':1,'consumer_method_executions_including_internal':method_calls,'tests':tests,'audit':audits,'explicit_suite_read_bytes':reads,'wall_seconds':time.monotonic()-start,'no_native_children':True,'actual_capture_module_executed':False,'actual_consumer_and_ledger_executed':True,'fake_verify_and_load_for_constructor_controls':True,'all_passed':all(t['passed'] for t in tests)}
(W/'fake-results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
sys.exit(0 if result['all_passed'] else 1)
