"""Original synthetic independent table views and rule provenance contracts."""
import ast
from contextlib import ExitStack
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/research'))
import knowledge as k
from smr_launcher.industry_registry import _extract_registry


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.patches=ExitStack();self.addCleanup(self.patches.close)

    def fixture(self):
        # The native reader consumes bytes; the independent view supplies
        # explicitly specified Ghidra-style references/strings, never game data.
        data=bytearray(8192)
        struct.pack_into('<IIIIIIII',data,0,0xFEEDFACF,0x01000007,3,2,1,72,0,0)
        struct.pack_into('<II16sQQQQIIII',data,32,0x19,72,b'__TEXT',0x1000,8192,0,8192,7,5,0,0)
        refs=[];strings=[]
        for i in range(36):
            offset=1024+i*64;name='SyntheticFactory'+str(i)
            struct.pack_into('<Q',data,128+i*8,0x1000+offset)
            data[offset:offset+len(name)+1]=name.encode()+b'\0'
            refs.append({'from':format(0x1080+i*8,'x'),'to':format(0x1000+offset,'x'),'type':'DATA'})
            strings.append({'address':format(0x1000+offset,'x'),'value':name})
        data=bytes(data);digest=hashlib.sha256(data).hexdigest();mid=digest[:24]
        (self.root/'inputs').mkdir();k.c.write(self.root/'inputs'/f'{mid}.bin',data)
        m={'id':mid,'sha256':digest,'architecture':'x86_64','image_uuid':None,'copy':f'inputs/{mid}.bin','bytes':len(data),'sources':[]}
        k.c.write(self.root/'manifest.json',{'schema':1,'profile':k.c.profile(),'modules':[m],'resources':[],'symlinks':[]})
        out=self.root/'modules'/mid/'export';out.mkdir(parents=True)
        k.c.write(out/'identity.json',{'binary_sha256':digest,'ghidra_version':k.c.profile()['ghidra']})
        counts={x:0 for x in k.c.COUNTS};counts.update(references=36,strings=36)
        k.c.write(out/'COMPLETE.json',dict(counts,binary_sha256=digest,status='complete',ghidra_version=k.c.profile()['ghidra']))
        for name in k.c.FILES:
            rows=refs if name=='references.jsonl' else strings if name=='strings.jsonl' else []
            k.c.write(out/name,b''.join(k.c.canonical(row)+b'\n' for row in rows))
        k.c.write(out.parent/'receipt.json',{'status':'complete','export':k.c.export_digest(out,digest)})
        self.patches.enter_context(patch.object(k,'SUPPORTED_GAME_SHA256',digest))
        self.patches.enter_context(patch.object(k,'INDUSTRY_TABLE_ADDRESS',0x1080))
        self.patches.enter_context(patch.object(k,'extract_supported_industry_registry',lambda b:_extract_registry(b,digest,0x1080,36)))
        return digest,out

    def test_bound_membership_and_honest_partial_model(self):
        digest,_=self.fixture();self.assertEqual(k.bind(self.root)['entries_matched'],36)
        matched=k.consultation(digest,['industry_membership'],self.root)
        self.assertEqual(matched['status'],'matched');self.assertEqual(matched['evidence_findings'],['SMR-K-001'])
        partial=k.consultation(digest,['industry_outside_registry','bridge_outside_registry'],self.root)
        self.assertEqual(partial['status'],'partial');self.assertEqual(partial['gaps'],['SMR-K-003'])
        self.assertEqual(k.consultation('0'*64,['industry_membership'],self.root)['gaps'],['game-build-mismatch'])

    def test_wrong_independent_view_refuses_binding(self):
        _,out=self.fixture();rows=[k.c.json_load(x) for x in k.c.read(out/'strings.jsonl').splitlines()]
        rows[2]['value']='IndependentCounterexample'
        k.c.write(out/'strings.jsonl',b''.join(k.c.canonical(r)+b'\n' for r in rows),replace=True)
        rec=k.c.load(out.parent/'receipt.json');rec['export']=k.c.export_digest(out,rec['export'].get('binary_sha256',k.SUPPORTED_GAME_SHA256))
        k.c.write(out.parent/'receipt.json',rec,replace=True)
        with self.assertRaises(k.c.Refusal):k.bind(self.root)
        self.assertFalse((self.root/'knowledge-binding.json').exists())

    def test_unknown_rule_stale_catalog_and_changed_evidence(self):
        digest,out=self.fixture();k.bind(self.root)
        with self.assertRaises(k.c.Refusal):k.consultation(digest,['invented-rule'],self.root)
        binding=k.c.load(self.root/'knowledge-binding.json');binding['catalog_sha256']='0'*64
        k.c.write(self.root/'knowledge-binding.json',binding,replace=True)
        with self.assertRaises(k.c.Refusal):k.consultation(digest,['industry_membership'],self.root)
        binding['catalog_sha256']=k.catalog()[1];k.c.write(self.root/'knowledge-binding.json',binding,replace=True)
        k.c.write(out/'strings.jsonl',b'',replace=True)
        with self.assertRaises(k.c.Refusal):k.consultation(digest,['industry_membership'],self.root)

    def test_no_corpus_is_explicit_and_unknown_finding_rejected(self):
        self.assertEqual(k.consultation('a'*64,['industry_membership'])['status'],'unavailable')
        with self.assertRaises(k.c.Refusal):k.annotate_findings([{'code':'invented-rule'}])
        rows=[{'code':'industry_outside_registry'}];k.annotate_findings(rows)
        self.assertEqual(rows[0]['finding_id'],'SMR-K-001')

    def test_all_implemented_scanner_codes_have_references(self):
        tree=ast.parse((k.ROOT/'scripts/research/audit_map_library.py').read_text());codes=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='add' and node.args:
                codes.update(x.value for x in ast.walk(node.args[0]) if isinstance(x,ast.Constant) and isinstance(x.value,str))
        self.assertEqual(codes,set(k.catalog()[0]['rules'])-{'industry_membership'})


if __name__=='__main__':unittest.main()
