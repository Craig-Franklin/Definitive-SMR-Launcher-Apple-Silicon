"""Original synthetic contracts; no installed game/tool dependency."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH=Path(__file__).resolve().parents[1]/'scripts/research/corpus.py'
SPEC=importlib.util.spec_from_file_location('corpus',PATH)
c=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(c)

class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
    def tearDown(self):self.temp.cleanup()
    def fixture(self):
        (self.root/'logs').mkdir()
        inp=self.root/'inputs';inp.mkdir();data=b'original synthetic executable';digest=c.sha(data);mid=digest[:24]
        c.write(inp/(mid+'.bin'),data)
        module={'id':mid,'sha256':digest,'architecture':'x86_64','image_uuid':None,'copy':'inputs/'+mid+'.bin','bytes':len(data),'sources':[]}
        c.write(self.root/'manifest.json',{'schema':1,'profile':c.profile(),'modules':[module],'resources':[],'symlinks':[]})
        out=self.root/'modules'/mid/'export';out.mkdir(parents=True)
        c.write(out/'identity.json',{'binary_sha256':digest})
        counts={'functions_seen':1,'decompiled':1,'failed':0,'external':0,'references':0,'call_edges':0,'strings':0,'string_references':0}
        c.write(out/'COMPLETE.json',dict(counts,binary_sha256=digest,status='complete'))
        (out/'functions').mkdir();code=b'int original_fixture(void) { return 7; }\n';c.write(out/'functions/f.c',code)
        row={'address':'001000','name':'original_fixture','status':'decompiled','decomp_file':'functions/f.c','decomp_sha256':c.sha(code),'elapsed_seconds':1.0,'reused':False,'body_ranges':[{'start':'001000','end':'001007'}]}
        c.write(out/'functions.jsonl',c.canonical(row)+b'\n')
        for name in c.FILES[1:]:c.write(out/name,b'')
        ex=c.export_digest(out,digest);c.write(out.parent/'receipt.json',{'status':'complete','export':ex})
        return module,out,row
    def test_private_roots_and_escapes(self):
        for p in (Path('.'),PATH.parent,self.root/'..'/'escape'):
            with self.assertRaises(c.Refusal):c.private_root(p)
        (self.root/'link').symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(c.Refusal):c.private_root(self.root/'link')
        for p in ('../outside','/absolute'):
            with self.assertRaises(c.Refusal):c.contained(self.root,p)
    def test_exclusive_output_and_symlink_reads(self):
        c.write(self.root/'original',b'preserved')
        with self.assertRaises(FileExistsError):c.write(self.root/'original',b'bad')
        (self.root/'link').symlink_to(self.root/'original')
        with self.assertRaises(c.Refusal):c.read(self.root/'link')
        self.assertEqual(c.read(self.root/'original'),b'preserved')
    def test_manifest_build_and_profile_mismatch(self):
        m,_,_=self.fixture();self.assertEqual(c.manifest(self.root)['modules'][0]['sha256'],m['sha256'])
        c.write(self.root/m['copy'],b'changed',replace=True)
        with self.assertRaises(c.Refusal):c.manifest(self.root)
    def test_export_hash_and_count_mismatch(self):
        m,out,_=self.fixture()
        c.write(out/'functions/f.c',b'changed',replace=True)
        with self.assertRaises(c.Refusal):c.export_digest(out,m['sha256'])
    def test_timing_and_reuse_not_semantic_difference(self):
        m,out,row=self.fixture();before=c.export_digest(out,m['sha256'])
        row.update(elapsed_seconds=99,reused=True);c.write(out/'functions.jsonl',c.canonical(row)+b'\n',replace=True)
        self.assertEqual(c.export_digest(out,m['sha256']),before)
    def test_index_private_lookup_and_bounds(self):
        self.fixture();self.assertEqual(c.build_index(self.root)['functions'],1)
        self.assertEqual(c.lookup(self.root,0,'function','original','lookup.json')['matches'],1)
        with self.assertRaises(c.Refusal):c.lookup(self.root,0,'function','x','../leak')
        with self.assertRaises(c.Refusal):c.lookup(self.root,0,'function','x','bad.json',101)
        with self.assertRaises(FileExistsError):c.lookup(self.root,0,'function','original','lookup.json')
    def test_duplicate_json_and_bad_identity(self):
        with self.assertRaises(c.Refusal):c.json_load('{"a":1,"a":2}')
        self.fixture();v=c.load(self.root/'manifest.json');v['profile']['ghidra']='wrong';c.write(self.root/'manifest.json',v,replace=True)
        with self.assertRaises(c.Refusal):c.manifest(self.root)
    def test_verify_reports_incomplete(self):
        m,out,_=self.fixture();self.assertEqual(c.verify(self.root)['status'],'verified')
        c.write(out.parent/'receipt.json',{'status':'failed'},replace=True)
        self.assertEqual(c.verify(self.root)['status'],'partial')
        with self.assertRaises(c.Refusal):c.compare(self.root,self.root)
    def test_closed_report_drops_unknown_fields_and_refuses_disguised_counts(self):
        m,out,_=self.fixture();summary=c.load(out/'COMPLETE.json')
        summary.update(secret={'text':'proprietary sentinel'},path='/private/sentinel')
        c.write(out/'COMPLETE.json',summary,replace=True)
        report=c.publication(self.root,'report.json')
        self.assertEqual(set(report),{'schema','report','modules','counts','inputs_unchanged','evidence','human_review','runtime_checks'})
        self.assertNotIn('sentinel',c.canonical(report).decode())
        with self.assertRaises(FileExistsError):c.publication(self.root,'report.json')
        for bad in ('disguised text',{'raw':'text'},True,-1):
            summary['strings']=bad;c.write(out/'COMPLETE.json',summary,replace=True)
            with self.assertRaises(c.Refusal):c.publication(self.root,'leak.json')
        self.assertFalse((self.root/'leak.json').exists())
    def test_address_lookup_code_budget_and_stale_index(self):
        _,out,_=self.fixture();c.build_index(self.root)
        self.assertEqual(c.lookup(self.root,0,'address','1003','address.json',include_code=True)['matches'],1)
        self.assertIn('return 7',c.load(self.root/'address.json')['code'][0]['text'])
        self.assertEqual(c.lookup(self.root,0,'address','1008','missing.json')['matches'],0)
        c.write(out/'functions/f.c',b'tamper',replace=True)
        with self.assertRaises(c.Refusal):c.lookup(self.root,0,'address','1003','tampered.json',include_code=True)
        c.write(self.root/'index.sqlite',b'invalid',replace=True)
        with self.assertRaises(c.Refusal):c.lookup(self.root,0,'function','original','stale.json')
    def test_unknown_status_cannot_escape_operational_projection(self):
        _,out,_=self.fixture();c.write(out.parent/'receipt.json',{'status':'raw sentinel'},replace=True)
        with self.assertRaises(c.Refusal):c.verify(self.root)
    def test_lock_and_run_bounds(self):
        self.fixture()
        with c.owned(self.root):
            with self.assertRaises(c.Refusal):
                with c.owned(self.root):pass
        for indices,seconds in (([9],60),([],60),([0],0)):
            with self.assertRaises(c.Refusal):c.run(self.root,indices,seconds)
    def test_timeout_records_failure_and_scopes_termination(self):
        m,out,_=self.fixture();(out.parent/'receipt.json').unlink()
        # The fake provider represents a whole-module timeout, independent of
        # the exporter implementation. A stale completion must not be accepted.
        proc=unittest.mock.Mock(pid=424242)
        import subprocess
        proc.wait.side_effect=[subprocess.TimeoutExpired('synthetic',60),0]
        real_popen=c.subprocess.Popen
        def provider_only(argv,**kwargs):
            return proc if argv[0]==str(c.GHIDRA/'support/analyzeHeadless') else real_popen(argv,**kwargs)
        with patch.object(c.subprocess,'Popen',side_effect=provider_only),patch.object(c.os,'killpg') as kill:
            self.assertEqual(c.run(self.root,[0],60)['status'],'partial')
            kill.assert_called_once_with(424242,c.signal.SIGTERM)
        self.assertEqual(c.load(out.parent/'receipt.json')['status'],'failed')
        self.assertFalse((out/'COMPLETE.json').exists())

if __name__=='__main__':unittest.main()
