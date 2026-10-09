import importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
W=Path(__file__).resolve().parent
for n,p in [('rootcandidate',W/'consumer.py'),('acceptedleaf',W/'ownedleaf.py')]:
 spec=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);globals()[n]=m
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=W);self.addCleanup(self.temp.cleanup);self.now=[50.];self.c=rootcandidate.Consumer.__new__(rootcandidate.Consumer);c=self.c;c.directory=Path(self.temp.name);c.rows=[];c.expected={'pid':1,'birth_us':1,'executable':'/exact/game'};c.window=12;c.geometry=(1512,982);c.ledger=acceptedleaf.Ledger(0,lambda:self.now[0]);c.gate=lambda:True;c.process_inspect=lambda:c.expected
  self.p=patch.object(rootcandidate,'verify',lambda rows:None);self.p.start();self.addCleanup(self.p.stop)
 def test_gate_delay_refuses(self):
  self.now[0]=150
  def gate():self.now[0]=181;return True
  self.c.gate=gate
  with self.assertRaises(acceptedleaf.Refused):self.c.guard()
 def test_inspection_delay_refuses(self):
  self.now[0]=150
  def inspect():self.now[0]=156;return self.c.expected
  self.c.process_inspect=inspect
  with self.assertRaises(acceptedleaf.Refused):self.c.guard()
 def test_callback_source_change_refuses(self):
  calls=[]
  def verify(rows):
   calls.append(1)
   if len(calls)==2:raise rootcandidate.Refused('Changed source')
  with patch.object(rootcandidate,'verify',verify):
   with self.assertRaises(rootcandidate.Refused):self.c.guard()
  self.assertEqual(len(calls),2)
 def test_report_entry_overrun_held(self):
  self.now[0]=181
  with self.assertRaises(rootcandidate.Refused):self.c.report()
  d=json.loads((self.c.directory/'ledger.json').read_text());self.assertTrue(d['held'] and d['overrun']);self.assertEqual(d['anchor'],0)
 def test_report_persistence_overrun_held(self):
  self.now[0]=179
  def slow(fd):self.now[0]=181
  with patch.object(os,'fsync',slow):
   with self.assertRaises(rootcandidate.Refused):self.c.report()
  d=json.loads((self.c.directory/'ledger.json').read_text());self.assertTrue(d['held'] and d['overrun'])
 def test_write_error_held(self):
  with patch.object(os,'replace',side_effect=OSError('write failed')):
   with self.assertRaises(OSError):self.c.report()
  self.assertTrue(self.c.ledger.held)
 def test_same_ledger_across_calls(self):
  c=self.c
  class FakeAPI:
   def capture_once(_,expected,window,directory,process,inspect,**kw):
    self.assertEqual(expected,process());self.assertEqual(kw['expected_geometry'],(1512,982));c.ledger.actual_captures+=1;c.ledger.attempts.append({'status':'ACCEPTED_FAKE'});return {'status':'ACCEPTED_FAKE'}
  c.capture_api=FakeAPI();c.leaves=None
  self.assertEqual(c.capture()['status'],'ACCEPTED_FAKE');self.assertEqual(c.capture()['status'],'ACCEPTED_FAKE');self.assertEqual(c.ledger.actual_captures,2);self.assertEqual(c.ledger.anchor,0)
 def test_unknown_child_holds(self):
  self.c.ledger.children=[{'reaped':False}]
  with self.assertRaises(rootcandidate.Refused):self.c.capture()
  self.assertTrue(self.c.ledger.held)
if __name__=='__main__':unittest.main()
