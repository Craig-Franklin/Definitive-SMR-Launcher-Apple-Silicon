import copy
import json
from pathlib import Path
import tempfile
import unittest
import evidence as e

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.store = e.Store(self.root)
        (self.root/'raw').write_bytes(b'private synthetic function text {"secret":"sentinel"}')
        self.entry = self.store.ingest('raw', 'synthetic-query')
        self.manifest = {'schema': 1, 'identity': e.identity(), 'entries': [self.entry]}
        self.save()
        self.approval = {'schema': 1, 'case': e.CASE, 'decision': 'approved-synthetic-summary', 'finding': 'cache-unproven'}
    def tearDown(self):
        self.tmp.cleanup()
    def save(self):
        (self.root/'index.json').write_bytes(e.encode(self.manifest))
    def test_roundtrip_and_projection(self):
        self.assertEqual(self.store.retrieve(self.entry['sha256'], 8, 9), b'synthetic')
        report = self.store.report(self.approval, True)
        self.assertEqual(set(report), {'schema','case','decision','finding','scope','legal_clearance'})
        for forbidden in ('sentinel','function',str(self.root),self.entry['sha256']):
            self.assertNotIn(forbidden, json.dumps(report))
    def test_wrong_roots(self):
        for root in ('.', e.HERE, self.root/'..'/'escape'):
            with self.assertRaises(ValueError): e.Store(root)
    def test_symlinks_escapes(self):
        (self.root/'link').symlink_to(self.root/'raw')
        (self.root/'directory').symlink_to(self.root, target_is_directory=True)
        for path in ('link','directory/raw','../outside',str(self.root/'raw')):
            with self.assertRaises(ValueError): self.store.ingest(path,'synthetic-query')
        with self.assertRaises(ValueError): e.Store(self.root/'directory')
    def test_unknown_and_disguised_fields(self):
        for field in self.approval:
            for payload in ('int proprietary(){return 1;}', '{"code":"secret"}', '/private/path', {'secret':'payload'}):
                approval = dict(self.approval, **{field:payload})
                with self.assertRaises((ValueError,TypeError)): self.store.report(approval, True)
        for field in ('raw','path','notes','reviewer'):
            with self.assertRaises(ValueError): self.store.report(dict(self.approval, **{field:'secret'}),True)
        with self.assertRaises(ValueError): self.store.report(self.approval)
        with self.assertRaises(ValueError): e.load('{"schema":1,"schema":1}')
    def test_identity_and_schema_mismatch(self):
        for field in ('case','target','tool'):
            value = copy.deepcopy(self.manifest); value['identity'][field] = 'wrong'
            with self.assertRaises(ValueError): e.validate_manifest(value)
        for value in ([],{},dict(self.manifest, unknown='secret'),dict(self.manifest,schema=True)):
            with self.assertRaises(ValueError): e.validate_manifest(value)
        value=copy.deepcopy(self.manifest); value['entries'][0]['path']='secret'
        with self.assertRaises(ValueError): e.validate_manifest(value)
    def test_corruption_bounds_preservation(self):
        sha=self.entry['sha256']
        for offset,length in ((-1,1),(0,4097),(0,0)):
            with self.assertRaises(ValueError): self.store.retrieve(sha,offset,length)
        with self.assertRaises(ValueError): self.store.retrieve('0'*64)
        with self.assertRaises(FileExistsError): e.create(self.root/'raw',b'overwrite')
        self.assertIn(b'sentinel',(self.root/'raw').read_bytes())
        (self.root/sha).write_bytes(b'changed')
        with self.assertRaises(ValueError): self.store.index('index.json')
    def test_limits(self):
        (self.root/'large').write_bytes(b'x'*(e.MAX_BYTES+1))
        with self.assertRaises(ValueError): self.store.ingest('large','synthetic-query')
        with self.assertRaises(ValueError): self.store.ingest('raw','unknown')


class CLITests(unittest.TestCase):
    setUp = EvidenceTests.setUp
    tearDown = EvidenceTests.tearDown
    save = EvidenceTests.save
    def run_cli(self,*args):
        import subprocess, sys
        return subprocess.run([sys.executable,str(e.HERE/'evidence.py'),'--private-root',str(self.root),*args],capture_output=True)
    def test_cli_report_and_preservation(self):
        (self.root/'approval.json').write_bytes(e.encode(self.approval))
        out=self.root/'report.json'
        args=('report','--approval','approval.json','--human-reviewed','--output',str(out))
        self.assertEqual(self.run_cli(*args).returncode,0)
        before=out.read_bytes()
        self.assertEqual(self.run_cli(*args).returncode,2)
        self.assertEqual(out.read_bytes(),before)
        self.assertEqual(self.run_cli('retrieve',self.entry['sha256'],'--output',str(e.HERE/'leak')).returncode,2)
    def test_cli_malformed_preserves_output(self):
        (self.root/'approval.json').write_text('{bad json')
        out=self.root/'existing'; out.write_bytes(b'preserved')
        self.assertEqual(self.run_cli('report','--approval','approval.json','--human-reviewed','--output',str(out)).returncode,2)
        self.assertEqual(out.read_bytes(),b'preserved')

if __name__ == '__main__': unittest.main()
