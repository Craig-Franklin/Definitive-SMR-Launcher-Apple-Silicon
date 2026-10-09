"""Handwritten independent final-guard controls; all children and inspector results simulated."""
import json, pathlib, tempfile, unittest, types
from unittest.mock import patch
import ownedleaf as o, capture as c, proof as p
from test_capture_fake import Fake
ROOT=pathlib.Path(__file__).resolve().parent
class Independent(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(dir=ROOT/'temp0700');self.root=pathlib.Path(self.t.name);self.root.chmod(0o700)
 def tearDown(self):self.t.cleanup()
 def test_held_fixture_cleanup_idempotence(self):
  l=o.Ledger(0,lambda:0); child=Fake((OSError('first wait'),OSError('settlement wait')))
  leaves=o.Leaves(l,self.root,'/fake',spawn=lambda *a,**kw:child)
  child,r,_,_=leaves.start('fixture');leaves.settle(child,r,fixture=True);actions=list(child.actions)
  report=p.finalize(l,'primary',(child,r),leaves,self.root/'report.json')
  self.assertEqual(child.actions,actions);self.assertEqual(sum(a[0]=='terminate' for a in actions),1);self.assertEqual(sum(a[0]=='kill' for a in actions),1)
  self.assertEqual(report['status'],'FAILED');self.assertTrue(report['held']);self.assertFalse(report['all_children_reaped']);json.dumps(report)
 def exercise_proof(self,closed_error=False,wrong_error=False,sleep_error=False,closed_code=3,closed_reaped=True,closed_errors=None,closed_message="Exact window absent"):
  state={'absent':False}; observed={}
  identity={'pid':123,'birth_us':456,'executable':'/fake/WindowProbe'}
  def inspect(pid):return None if state['absent'] else identity
  def footprint(pid):return 0
  class Leaves:
   def __init__(inner,ledger,directory,probe,**kw):inner.ledger=ledger;inner.directory=directory;inner.before_launch=lambda:None
   def start(inner,kind):
    r={'kind':kind,'argv':['/fake','fixture'],'pid':123,'reaped':False,'exitcode':None,'settlement':'live','errors':[]};inner.ledger.children.append(r)
    out=inner.directory/'ready';out.write_text(json.dumps({'pid':123,'windows':[{'kind':'A','window':77},{'kind':'B','window':88}]}));err=inner.directory/'err';err.write_bytes(b'')
    child=Fake();child.pid=123
    return child,r,out,err
   def json(inner,kind,arg):
    # Dedicated absence3 differs from generic inspector failure2; all are fake outcomes.
    r={'kind':'inspect','argv':['/fake','inspect',str(arg)],'pid':777,'reaped':closed_reaped,'exitcode':2 if closed_error else closed_code,'settlement':'reaped' if closed_reaped else 'unknown-held','errors':list(closed_errors or [])}
    inner.ledger.children.append(r);observed['inspector_diagnostic']='Window metadata unavailable' if closed_error else closed_message
    if not closed_reaped: inner.ledger.held=True
    if closed_code==0: return {'window':arg,'pid':123}
    raise o.Refused(observed['inspector_diagnostic'])
   def run(inner,kind):
    r={'kind':'sleep','argv':['/bin/sleep','10'],'pid':778,'reaped':True,'exitcode':-9,'settlement':'reaped','errors':['OSError: unrelated' if sleep_error else 'TimeoutExpired: fake timeout']};inner.ledger.children.append(r);raise o.Refused('owned leaf failed')
   def settle(inner,child,r,fixture=False):r.update(reaped=True,exitcode=0,settlement='reaped');state['absent']=True
  def capture(expected,window,directory,*args,leaves,**kw):
   if kw.get('expected_geometry')!=((1200,700) if window==77 else (640,420)):raise AssertionError('Proof caller pinned geometry mismatch')
   if expected['birth_us']!=456:raise o.Refused('Inspector unavailable' if wrong_error else 'Process incarnation mismatch')
   leaves.ledger.actual_captures+=1
   is_a=window==77
   return {'before':{'bounds':{'width':1200 if is_a else 640,'height':700 if is_a else 420}},'decoded':{'left_right_rgba':[[255,0,0,255],[0,0,255,255]] if is_a else [[0,255,0,255],[255,255,0,255]]}}
  filename=str(pathlib.Path(__file__).resolve())
  admission={'source_go':True,'concrete_user_circuit_exception':True,'fresh_root_admission':True,'source_freeze':[{'path':'/fake/WindowProbe'}],'required_sources':[filename],'process_inspector_sources':[filename]}
  with patch.object(p,'load_candidates',return_value=(o,c)),patch.object(p,'verify_sources',return_value=0),patch.object(o,'Leaves',Leaves),patch.object(c,'capture_once',side_effect=capture):
   report=p.run_proof(admission,self.root/'proof',inspect,child_footprint=footprint,clock=lambda:0)
  observed['report']=report;return observed
 def test_valid_negative_schedule_positive(self):
  result=self.exercise_proof();self.assertEqual(result['report']['status'],'PASS_SYNTHETIC_ONLY')
 def test_unrelated_wrong_pair_error_is_failure(self):
  result=self.exercise_proof(wrong_error=True);self.assertEqual(result['report']['status'],'FAILED');self.assertIn('Inspector unavailable',result['report']['primary_error'])
 def test_unrelated_sleep_error_is_failure(self):
  result=self.exercise_proof(sleep_error=True);self.assertEqual(result['report']['status'],'FAILED');self.assertIn('Inert timeout not settled',result['report']['primary_error'])
 def test_unrelated_closed_window_exit2_must_fail(self):
  result=self.exercise_proof(closed_error=True)
  (ROOT/'closed-window-corrected-result.json').write_text(json.dumps(result,indent=2)+'\n')
  self.assertEqual(result['inspector_diagnostic'],'Window metadata unavailable')
  self.assertEqual(result['report']['status'],'FAILED','Unrelated inspector metadata failure must not satisfy absent-window control')

 def test_malformed_metadata_exit2_fails(self):
  result=self.exercise_proof(closed_code=2,closed_message='Malformed window metadata');self.assertEqual(result['report']['status'],'FAILED');self.assertIn('Malformed window metadata',result['report']['primary_error'])
 def test_ambiguous_metadata_exit2_fails(self):
  result=self.exercise_proof(closed_code=2,closed_message='Ambiguous exact window');self.assertEqual(result['report']['status'],'FAILED')
 def test_permission_exit2_fails(self):
  result=self.exercise_proof(closed_code=2,closed_message='Permission denied');self.assertEqual(result['report']['status'],'FAILED')
 def test_other_generic_exit2_fails(self):
  result=self.exercise_proof(closed_code=2,closed_message='Generic inspector failure');self.assertEqual(result['report']['status'],'FAILED')
 def test_absence3_timeout_error_fails(self):
  result=self.exercise_proof(closed_errors=['TimeoutExpired: injected']);self.assertEqual(result['report']['status'],'FAILED')
 def test_absence3_unknown_settlement_fails_held(self):
  result=self.exercise_proof(closed_reaped=False);self.assertEqual(result['report']['status'],'FAILED');self.assertTrue(result['report']['held']);self.assertFalse(result['report']['all_children_reaped'])
 def test_still_present_exit0_fails(self):
  result=self.exercise_proof(closed_code=0);self.assertEqual(result['report']['status'],'FAILED');self.assertIn('Closed owned window still present',result['report']['primary_error'])
 def test_swift_dedicated_absence_static_only(self):
  source=(ROOT/'WindowProbe.swift').read_text()
  self.assertEqual(source.count('exit(3)'),1)
  self.assertIn('if matches.isEmpty {',source)
  self.assertLess(source.index('guard let rows = CGWindowListCopyWindowInfo'),source.index('if matches.isEmpty {'))
  self.assertLess(source.index('if matches.isEmpty {'),source.index('guard matches.count == 1'))
  self.assertIn('exit(2)',source[source.index('func fail'):source.index('func emit')])
  self.assertIn('else { fail("Exact window ambiguous or malformed") }',source)
