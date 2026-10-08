"""Handwritten criteria instantiated with ONLY fake process/clock/sleep inputs."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import wrapper as w
from individual_stop import ProcessIdentity
from adapter import Adapter

class Clock:
    def __init__(self): self.now=100.; self.sleeps=[]
    def __call__(self): return self.now
    def sleep(self,n): self.sleeps.append(n); self.now+=n

class Fake:
    def __init__(self):
        self.current=SimpleNamespace(pid=42,birth_us=12345,executable='/fake/game')
        self.calls=[]; self.stops=[]; self.alive=False; self.error=False
    def inspect(self,pid):
        self.calls.append(pid)
        if self.error: raise ValueError('fake inspection error')
        return self.current
    def stop(self,p):
        self.stops.append((p.pid,p.birth_us,p.executable))
        if not self.alive: self.current=None

class Cases(unittest.TestCase):
    def setUp(self):
        # All paths remain inside owned output, no temporary /var symlink chain.
        self.tmp=tempfile.TemporaryDirectory(dir=w.HERE)
        self.packet=Path(self.tmp.name); os.chmod(self.packet,0o700)
        self.clock=Clock(); self.fake=Fake(); self.constructions=[]
        self.code=hashlib.sha256((w.HERE/'pins.json').read_bytes()).hexdigest()
        self.a=w.RootAdmission('job','nonce',self.code,100.,'/fake/game','/fake/python','fake-domain')
        proposal=json.loads((w.HERE/'proposal.json').read_text())
        self.approval={'issue':127,'status':'approved','proposal_sha256':proposal['sha256'],
                       'job':'job','nonce':'nonce','code':self.code,'anchor':100.}
        self.accept={'issue':129,'status':'accepted','observer_sha256':w.OBS,'component_sha256':w.COMP}
    def tearDown(self): self.tmp.cleanup()
    def factory(self,exe): self.constructions.append(exe); return self.fake
    def wrapper(self,approval='default',accept='default'):
        return w.Wrapper(self.a,self.packet,self.approval if approval=='default' else approval,
                         self.accept if accept=='default' else accept,self.factory,self.clock,self.clock.sleep)
    def put(self,name,data): (self.packet/name).write_text(json.dumps(data))
    def inputs(self,birth='12345',exe='/fake/game',seq=5):
        common={'version':1,'job':'job','nonce':'nonce','code':self.code,'anchor':100.,'request':101.}
        process={'pid':42,'birth':birth,'executable':exe}
        self.put('request.json',common);self.put('descriptor.json',dict(common,process=process))
        self.sample={'version':1,'job':'job','nonce':'nonce','code':self.code,'anchor':100.,
                     'process':process,'seq':seq,'status':'success','start':101.,'complete':101.}
        self.put('sample.json',self.sample)
    def bound(self):
        x=self.wrapper(); self.assertTrue(x.run_once().ready)
        self.inputs();self.clock.now=101.;self.assertTrue(x.run_once().bound);return x
    def test_admission_denies_factory(self):
        bad=[None,{},dict(self.approval,status='pending'),dict(self.approval,issue=126),
             dict(self.approval,nonce='other'),dict(self.approval,anchor=101),dict(self.approval,proposal_sha256='wrong')]
        for a in bad:
            with self.subTest(a=a), self.assertRaises(w.Denied): self.wrapper(a)
        self.assertEqual(self.constructions,[]);self.assertEqual(self.fake.calls,[])
    def test_review_denial(self):
        for record in (None,{},dict(self.accept,status='rejected')):
            with self.assertRaises(w.Denied): self.wrapper(accept=record)
        self.assertEqual(self.constructions,[])
    def test_ready_bound_receipts(self):
        x=self.wrapper();s=x.run_once();self.assertIsNone(s.process)
        ready=json.loads((self.packet/'ready.json').read_text());self.assertIsNone(ready['state']['process'])
        self.inputs();self.clock.now=101;s=x.run_once();self.assertTrue(s.bound)
        state=json.loads((self.packet/'state.json').read_text());bound=json.loads((self.packet/'bound.json').read_text())
        self.assertEqual(state['state_seq'],bound['state_seq']);self.assertEqual(state['anchor'],100)
        self.assertEqual(bound['state']['process'],self.sample['process']);self.assertFalse(s.gui_grant)
    def test_stale60(self):
        x=self.bound();self.sample.update(start=105,complete=109,seq=6);self.put('sample.json',self.sample)
        self.clock.now=109;self.assertTrue(x.run_once().bound)
        self.clock.now=165;self.assertTrue(x.run_once().terminal);self.assertEqual(len(self.fake.stops),1)
        self.assertEqual(x.state.reason,'stale60')
        for _ in range(3): x.run_once()
        self.assertEqual(len(self.fake.stops),1)
    def test_absolute540(self):
        x=self.bound();self.sample.update(start=639,complete=640,seq=6);self.put('sample.json',self.sample)
        self.clock.now=640;s=x.run_once();self.assertEqual(s.reason,'absolute540');self.assertEqual(len(self.fake.stops),1)
    def test_receipt_bad_variants(self):
        for mutation in ({'seq':4},{'nonce':'wrong'},{'complete':999},{'status':'incomplete'},{'anchor':99}):
            with self.subTest(mutation=mutation):
                self.fake=Fake();self.clock.now=100
                for p in self.packet.iterdir(): p.unlink()
                x=self.bound();self.sample.update(mutation);self.put('sample.json',self.sample)
                self.clock.now=102;self.assertTrue(x.run_once().terminal);self.assertEqual(len(self.fake.stops),1)
    def test_malformed_sample(self):
        x=self.bound();(self.packet/'sample.json').write_text('{');self.clock.now=102
        self.assertTrue(x.run_once().terminal);self.assertEqual(len(self.fake.stops),1)
    def test_publication_failure(self):
        x=self.bound();self.clock.now=102
        with patch.object(w,'publish',side_effect=OSError('fake disk fail')):s=x.run_once()
        self.assertEqual((s.phase,s.reason),('unknown','publication_failure'));self.assertEqual(len(self.fake.stops),1)
        self.assertEqual(json.loads((self.packet/'state.json').read_text())['state']['phase'],'bound')
        x.run_once();self.assertEqual(len(self.fake.stops),1)
    def test_final_publication_failure(self):
        x=self.bound();self.clock.now=640
        with patch.object(w,'publish',side_effect=OSError()):s=x.run_once()
        self.assertEqual(s.reason,'publication_failure');self.assertEqual(s.phase,'unknown');self.assertEqual(len(self.fake.stops),1)
    def test_state_symlink(self):
        x=self.bound();(self.packet/'state.json').unlink();(self.packet/'state.json').symlink_to('sample.json')
        self.clock.now=102;self.assertEqual(x.run_once().reason,'publication_failure');self.assertEqual(len(self.fake.stops),1)
    def test_parent_replaced(self):
        x=self.bound();old=self.packet.with_name(self.packet.name+'-old');self.packet.rename(old);self.packet.mkdir(mode=0o700)
        try:
            self.clock.now=102;s=x.run_once();self.assertTrue(s.terminal);self.assertFalse(s.bound);self.assertEqual(len(self.fake.stops),1)
        finally:
            for p in old.iterdir():p.unlink()
            old.rmdir()
    def test_result_replaced_during_readback(self):
        x=self.bound();self.clock.now=102
        original=w.read_receipt
        def altered(path):
            if Path(path).name=='state.json':raise w.Denied('injected replacement at readback')
            return original(path)
        with patch.object(w,'read_receipt',side_effect=altered):s=x.run_once()
        self.assertEqual(s.reason,'publication_failure');self.assertEqual(len(self.fake.stops),1)
    def test_descriptor_birth_mismatch(self):
        x=self.wrapper();x.run_once();self.inputs(birth='98765');self.clock.now=101
        self.assertTrue(x.run_once().terminal);self.assertEqual(self.fake.stops,[])
    def test_descriptor_executable_mismatch(self):
        x=self.wrapper();x.run_once();self.inputs(exe='/fake/other');self.clock.now=101
        self.assertTrue(x.run_once().terminal);self.assertEqual(self.fake.calls,[])
    def test_unbound_deadline(self):
        x=self.wrapper();x.run_once();self.clock.now=120
        self.assertTrue(x.run_once().terminal);self.assertEqual(self.fake.stops,[])
    def test_request_deadline(self):
        x=self.wrapper();x.run_once();self.inputs();(self.packet/'descriptor.json').unlink();self.clock.now=101
        self.assertFalse(x.run_once().bound);self.clock.now=121
        self.assertTrue(x.run_once().terminal);self.assertEqual(self.fake.stops,[])
    def test_normalquit_unknown_alive(self):
        for mode in ('normal','error','alive'):
            with self.subTest(mode=mode):
                self.fake=Fake();self.clock.now=100
                for p in self.packet.iterdir():p.unlink()
                x=self.bound()
                if mode=='normal':self.fake.current=None
                if mode=='error':self.fake.error=True
                if mode=='alive':self.fake.alive=True
                self.clock.now=161;s=x.run_once();self.assertTrue(s.terminal)
                self.assertEqual(s.phase,'observed_exited' if mode=='normal' else 'unknown')
                x.run_once();self.assertLessEqual(len(self.fake.stops),1)
    def test_1hz_and_finite_loop(self):
        x=self.wrapper();x.run_once();seq=x.seq
        for _ in range(10):x.run_once()
        self.assertEqual(x.seq,seq);s=x.run(max_steps=3)
        self.assertEqual(self.clock.sleeps,[1,1]);self.assertEqual(s.reason,'finite_loop_exhausted');self.assertEqual(x.a.anchor,100)
    def test_anchor_clock_denied(self):
        for value in (99,640,float('nan')):
            self.clock.now=value
            with self.assertRaises(w.Denied):self.wrapper()
        self.assertEqual(self.constructions,[])
    def test_clock_regression_stops(self):
        x=self.bound();self.clock.now=99;s=x.run_once();self.assertTrue(s.terminal);self.assertEqual(len(self.fake.stops),1)
    def test_pin_replacement(self):
        x=self.bound();path=w.HERE/'adapter.py';original=path.read_bytes()
        try:
            path.write_bytes(original+b'\n# synthetic replacement\n');self.clock.now=102
            self.assertTrue(x.run_once().terminal);self.assertEqual(len(self.fake.stops),1)
        finally:path.write_bytes(original)
    def test_adapter_exact_and_mismatch(self):
        adapter=Adapter(self.factory,'/fake/game');p=ProcessIdentity(42,'12345','/fake/game')
        for expected in (ProcessIdentity(42,'9','/fake/game'),ProcessIdentity(42,'12345','/fake/other')):
            with self.assertRaises(ValueError):adapter.request_stop(expected)
        self.assertEqual(self.fake.stops,[]);self.fake.error=True
        with self.assertRaises(ValueError):adapter.request_stop(p)
        self.assertEqual(self.fake.stops,[]);self.fake.error=False;adapter.request_stop(p)
        self.assertEqual(self.fake.stops,[(42,12345,'/fake/game')])
    def test_no_host_module_or_grants(self):
        import sys
        self.assertNotIn('frozen_mac_processes',sys.modules)
        x=self.bound();s=x.state
        self.assertFalse(any((s.gui_grant,s.restoration_grant,s.new_job_grant)))
        self.assertNotIn('helper_pid',json.loads((self.packet/'state.json').read_text()))

if __name__=='__main__':unittest.main(verbosity=2)
