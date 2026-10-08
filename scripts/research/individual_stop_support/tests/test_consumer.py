import copy,unittest
from consumer import permit,Denied
class Cases(unittest.TestCase):
 def setUp(self):
  self.e=dict(job='j',nonce='n',code='c',anchor=100.,expected_executable='/game',interpreter='/python',clock_domain='root-venv-time.monotonic-v1')
  self.p=dict(pid=42,birth='123',executable='/game');self.h=dict(pid=50,birth='124',executable='/python')
  detail=dict(phase='bound',reason='holding_exact_identity',ready=True,bound=True,terminal=False,process=self.p,gui_grant=False,restoration_grant=False,new_job_grant=False)
  self.s=dict(self.e,version=1,kind='state',state_seq=2,observed=101.,state=detail);self.b=dict(self.s,kind='bound')
  self.sample=dict(version=1,job='j',nonce='n',code='c',anchor=100.,process=self.p,status='success',seq=1,start=101.,complete=101.)
 def gate(self,**kw):
  a=dict(expected=self.e,state=self.s,bound=self.b,now=102.,helper_expected=self.h,helper_current=self.h,game_current=self.p,watchdog_process=self.p,sample=self.sample,code_ok=True,freeze_ok=True,domain_verified=True);a.update(kw);return permit(**a)
 def test_valid_and_identical_tick(self):
  first=self.gate();self.assertEqual(self.gate(previous=first)['seq'],2)
 def test_independent_guards(self):
  for kw in [dict(helper_current=None),dict(helper_current=dict(self.h,birth='125')),dict(game_current=None),dict(watchdog_process=dict(self.p,pid=43)),dict(code_ok=False),dict(freeze_ok=False),dict(domain_verified=False),dict(now=640.),dict(now=105.),dict(now=99.)]:
   with self.subTest(kw=kw),self.assertRaises(Denied):self.gate(**kw)
 def test_exact_admission_and_partial(self):
  for k in self.e:
   s=copy.deepcopy(self.s);s[k]='wrong'
   with self.subTest(k=k),self.assertRaises(Denied):self.gate(state=s)
  b=copy.deepcopy(self.b);b['state_seq']=3
  with self.assertRaises(Denied):self.gate(bound=b)
 def test_terminal_grants_and_future(self):
  for k,v in [('bound',False),('terminal',True),('gui_grant',True),('restoration_grant',True),('new_job_grant',True),('phase','unknown')]:
   s=copy.deepcopy(self.s);s['state'][k]=v
   with self.subTest(k=k),self.assertRaises(Denied):self.gate(state=s,bound=dict(s,kind='bound'))
  for n in [float('nan'),float('inf'),True,103.]:
   s=copy.deepcopy(self.s);s['observed']=n
   with self.subTest(n=n),self.assertRaises(Denied):self.gate(state=s,bound=dict(s,kind='bound'))
 def test_completed_monitor_required(self):
  for changes in [dict(status='pending'),dict(complete=103.),dict(start=float('nan')),dict(nonce='old'),dict(process=dict(self.p,birth='9'))]:
   with self.subTest(changes=changes),self.assertRaises(Denied):self.gate(sample=dict(self.sample,**changes))
 def test_replay_or_rewrite(self):
  first=self.gate()
  for changes in [dict(state_seq=1),dict(observed=100.5),dict(observed=101.5)]:
   s=dict(self.s,**changes)
   with self.subTest(changes=changes),self.assertRaises(Denied):self.gate(state=s,bound=dict(s,kind='bound'),previous=first)
if __name__=='__main__':unittest.main(verbosity=2)
