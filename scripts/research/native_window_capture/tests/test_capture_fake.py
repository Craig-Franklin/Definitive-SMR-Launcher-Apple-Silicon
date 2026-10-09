"""Offline pure fake controls. Never invokes a real child/native/GUI/game API."""
import copy, hashlib, importlib.util, json, os, stat, struct, tempfile, unittest, zlib
from pathlib import Path
from unittest.mock import patch
import ownedleaf as o
import capture as c
import proof as p

ROOT=Path(__file__).resolve().parent

def png(width=2,height=2,*,raw=None,compression=0,tiny=0):
    def chunk(k,b): return struct.pack('>I',len(b))+k+b+struct.pack('>I',zlib.crc32(k+b)&0xffffffff)
    if raw is None: raw=(b'\0'+b'\xff\0\0'*width)*height
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,compression,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'tEXt',b'')*tiny+chunk(b'IEND',b'')

class Fake:
    pid=777
    def __init__(self, waits=(0,),kill_error=False,term_error=False): self.waits=list(waits);self.kill_error=kill_error;self.term_error=term_error;self.actions=[]
    def wait(self,timeout):
        self.actions.append(('wait',timeout)); value=self.waits.pop(0)
        if isinstance(value,Exception): raise value
        return value
    def kill(self):
        self.actions.append(('kill',))
        if self.kill_error: raise OSError('kill injected')
    def terminate(self):
        self.actions.append(('terminate',))
        if self.term_error: raise OSError('terminate injected')
    def poll(self): return None

class Controls(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(dir=ROOT/'temp0700'); self.root=Path(self.t.name);self.root.chmod(0o700)
        self.now=[0.0];self.l=o.Ledger(0,lambda:self.now[0]);self.child=Fake()
        self.expected={'pid':123,'birth_us':456,'executable':'/synthetic/fixture'}
        self.record={'window':77,'pid':123,'layer':0,'bounds':{'x':0,'y':0,'width':1200,'height':700}}
        self.spawn_calls=0;self.decoded=0
        def spawn(argv,**kwargs):
            self.spawn_calls+=1
            if argv[0]=='/usr/sbin/screencapture': Path(argv[-1]).write_bytes(png(1200,700))
            elif hasattr(kwargs['stdout'],'write'): kwargs['stdout'].write(b'{}')
            return self.child
        self.leaves=o.Leaves(self.l,self.root,'/synthetic/WindowProbe',spawn,monitor=lambda pid:0)
    def tearDown(self): self.t.cleanup()
    def decode(self,path): self.decoded+=1;return {'width':1200,'height':700}
    def capture(self,**kwargs):
        return c.capture_once(self.expected,77,self.root/'case',kwargs.pop('process_inspect',lambda:self.expected),kwargs.pop('window_inspect',lambda w:self.record),expected_geometry=kwargs.pop('expected_geometry',(1200,700)),leaves=self.leaves,image_inspect=kwargs.pop('image_inspect',self.decode),**kwargs)
    def refused_capture(self,**kw):
        with self.assertRaises(o.Refused):self.capture(**kw)
    def writepng(self,data): path=self.root/'image.png';path.write_bytes(data);return path
    # Original twelve controls, ported to actual corrected functions and valid raster.
    def test_owned_and_geometry(self):
        r=self.capture();self.assertTrue(r['image']['full_raster_validated']);self.assertEqual(r['scale'],1);self.assertEqual(self.l.actual_captures,1);self.assertEqual(self.l.children[0]['pid'],777)
    def test_wrong_owner_refuses_before_native(self):
        wrong={**self.record,'pid':999};self.refused_capture(window_inspect=lambda w:wrong);self.assertEqual(self.spawn_calls,0)
    def test_changed_incarnation_post_refuses(self):
        values=iter([self.expected,{**self.expected,'birth_us':457}]);self.refused_capture(process_inspect=lambda:next(values));self.assertFalse((self.root/'case/window.png').exists());self.assertEqual(self.decoded,0)
    def test_changed_geometry_post_refuses(self):
        changed=copy.deepcopy(self.record);changed['bounds']['width']=1199;values=iter([self.record,changed]);self.refused_capture(window_inspect=lambda w:next(values));self.assertEqual(self.l.attempts[0]['disposition'],'exact-owned-suspect-deleted')
    def test_decode_mismatch_refuses(self): self.refused_capture(image_inspect=lambda p:{'width':1,'height':1})
    def test_missing_window_error_refuses(self):
        def missing(w):raise o.Refused('missing')
        self.refused_capture(window_inspect=missing);self.assertEqual(self.spawn_calls,0)
    def test_timeout_preserved_no_acceptance(self):
        self.child=Fake((o.subprocess.TimeoutExpired('fake',5),-9));self.refused_capture();self.assertTrue(self.l.children[0]['reaped']);self.assertEqual(self.l.actual_captures,1)
    def test_invalid_png_dimensions(self):
        with self.assertRaises(o.Refused):c.validate_png(self.writepng(png(4097,1)))
    def test_partial_png_refused(self):
        with self.assertRaises(o.Refused):c.validate_png(self.writepng(png()[:-12]))
    def test_bad_crc_refused(self):
        data=bytearray(png());data[-1]^=1
        with self.assertRaises(o.Refused):c.validate_png(self.writepng(data))
    def test_permission_denial_simulated(self):
        self.child=Fake((2,));self.refused_capture();self.assertEqual(self.l.children[0]['exitcode'],2);self.assertTrue(self.l.children[0]['reaped']);self.assertEqual(self.decoded,0)
    def test_path_ledger_serializable(self):
        self.leaves.run('decode',Path('/synthetic/image.png'));self.assertEqual(json.loads(json.dumps(self.l.children))[0]['argv'],['/synthetic/WindowProbe','image','/synthetic/image.png'])
    # Discriminating F1-F8 additions; no repeated baseline test count.
    def test_F1_kill_error_still_waits(self):
        self.child=Fake((o.subprocess.TimeoutExpired('fake',5),-9),kill_error=True);self.refused_capture();self.assertTrue(self.l.children[0]['reaped']);self.assertIn(('wait',2),self.child.actions);self.assertTrue(self.l.children[0]['errors'])
    def test_F1_unknown_wait_blocks_next_leaf(self):
        self.child=Fake((OSError('wait failed'),OSError('settlement failed')));self.refused_capture();self.assertTrue(self.l.held)
        with self.assertRaises(o.Refused):self.leaves.run('inspect',77)
        self.assertEqual(self.spawn_calls,1)
    def test_F2_fixture_terminate_error_report(self):
        self.child=Fake((0,),term_error=True);child,r,_,_=self.leaves.start('fixture');report=p.finalize(self.l,'original-validation-error',(child,r),self.leaves,self.root/'report.json');self.assertEqual(report['status'],'FAILED');self.assertEqual(report['primary_error'],'original-validation-error');self.assertTrue(report['cleanup_errors']);json.dumps(report)
    def test_F2_fixture_final_wait_timeout(self):
        self.child=Fake((o.subprocess.TimeoutExpired('fake',8),o.subprocess.TimeoutExpired('fake',2)));child,r,_,_=self.leaves.start('fixture');report=p.finalize(self.l,'primary',(child,r),self.leaves,self.root/'report.json');self.assertTrue(report['held']);self.assertFalse(report['all_children_reaped']);self.assertEqual(report['status'],'FAILED')
    def test_F2_report_writer_failure_explicit(self):
        def fail(path,data):raise OSError('write injected')
        report=p.finalize(self.l,'primary',None,self.leaves,self.root/'report.json',writer=fail);self.assertEqual(report['primary_error'],'primary');self.assertEqual(report['status'],'FAILED');self.assertIn('report_error',report)
    def test_F3_sibling_untouched_unlink_error_holds(self):
        sibling=self.root/'sibling.png';sibling.write_bytes(b'untouched');values=iter([self.expected,{}])
        with patch.object(Path,'unlink',side_effect=PermissionError('injected')):self.refused_capture(process_inspect=lambda:next(values))
        self.assertTrue(self.l.held);self.assertEqual(sibling.read_bytes(),b'untouched');self.assertEqual(self.decoded,0)
    def test_F3_symlink_unknown_inode_holds(self):
        directory=self.root/'case';directory.mkdir(mode=0o700);outside=self.root/'other';outside.write_bytes(b'untouched');output=directory/'window.png';output.symlink_to(outside);a={};c.dispose_suspect(output,None,a,self.l);self.assertTrue(self.l.held);self.assertEqual(outside.read_bytes(),b'untouched')
    def test_F4_production_rlimits_literal(self):
        with patch.object(o.resource,'setrlimit') as limit:
            o.limits('capture');self.assertEqual(limit.call_args_list[0].args[1],(32505856,32505856));limit.reset_mock();o.limits('fixture');self.assertEqual(limit.call_args_list[0].args[1],(65536,65536))
    def test_F4_image_boundary_and_one_over_no_allocation(self):
        class FakePath:
            def __init__(self,size):self.size=size;self.reads=0
            def lstat(self):return type('Stat',(),{'st_mode':stat.S_IFREG,'st_size':self.size})()
            def read_bytes(self):self.reads+=1;return png()
        exact=FakePath(32505856);self.assertTrue(c.validate_png(exact)['full_raster_validated'])
        over=FakePath(32505857)
        with self.assertRaises(o.Refused):c.validate_png(over)
        self.assertEqual(over.reads,0)
    def test_F4_text_boundary_and_over(self):
        for size,accepted in [(65536,True),(65537,False)]:
            root=self.root/str(size);root.mkdir();l=o.Ledger(0,lambda:0)
            def spawn(argv,**kw):kw['stdout'].write(b'x'*size);return Fake()
            leaves=o.Leaves(l,root,'/synthetic/WindowProbe',spawn,monitor=lambda pid:0)
            if accepted:leaves.run('inspect',77)
            else:
                with self.assertRaises(o.Refused):leaves.run('inspect',77)
    def test_F4_aggregate_boundary_and_refusal_before_spawn(self):
        self.l.reserve('partial-invalid-images',o.AGGREGATE-self.l.reserved)
        with self.assertRaises(o.Refused):self.l.reserve('one-over',1)
        with self.assertRaises(o.Refused):self.leaves.run('inspect',77)
        self.assertEqual(self.spawn_calls,0);self.assertEqual(self.l.reserved,268435456)
    def test_F5_original_clock_margin_and_overrun(self):
        self.now[0]=164
        with self.assertRaises(o.Refused):self.leaves.run('capture',(77,self.root/'window.png'))
        self.assertEqual(self.spawn_calls,0);self.now[0]=181;report=p.finalize(self.l,None,None,self.leaves,self.root/'report.json');self.assertTrue(report['overrun']);self.assertEqual(report['status'],'FAILED')
    def test_F6_valid_and_crc_correct_invalid_raster(self):
        self.assertTrue(c.validate_png(self.writepng(png()))['full_raster_validated'])
        # Equal-size raster compressed payload changed to invalid filter5; CRC is correctly regenerated.
        bad=bytearray(png()); size=struct.unpack_from('>I',bad,33)[0]; bad[41+size-1]^=1; struct.pack_into('>I',bad,41+size,zlib.crc32(bad[37:41+size])&0xffffffff); self.assertEqual(len(bad),len(png()))
        with self.assertRaises(o.Refused):c.validate_png(self.writepng(bad))
    def test_F6_malformed_ihdr_and_tiny_chunks(self):
        for data in (png(compression=1),png(tiny=4097)):
            with self.assertRaises(o.Refused):c.validate_png(self.writepng(data))
    def test_F7_original_integer_scale_and_distortion(self):
        self.assertEqual(c.integer_scale(2400,1400,(1200,700)),2);self.assertEqual(c.integer_scale(640,420,(640,420)),1)
        for dims in ((1800,1050,1200,700),(2400,1401,1200,700),(2400,1400,1202,702)):
            with self.assertRaises(o.Refused):c.integer_scale(dims[0],dims[1],dims[2:])
    def sources(self):
        rows=[]
        for name in sorted(p.REQUIRED):
            path=self.root/name;path.write_bytes(b'fixed');s=path.stat();rows.append({'path':str(path),'bytes':5,'mtime_ns':s.st_mtime_ns,'mode':stat.S_IMODE(s.st_mode),'sha256':hashlib.sha256(b'fixed').hexdigest()})
        return rows,[r['path'] for r in rows]
    def test_F8_empty_missing_mutated_source_and_binary(self):
        rows,required=self.sources();self.assertEqual(p.verify_sources(rows,required),25)
        for altered in ([],rows[:-1]):
            with self.assertRaises(p.Refused):p.verify_sources(altered,required)
        path=Path(next(r['path'] for r in rows if Path(r['path']).name=='WindowProbe'));s=path.stat();path.write_bytes(b'other');os.utime(path,ns=(s.st_atime_ns,s.st_mtime_ns))
        with self.assertRaises(p.Refused):p.verify_sources(rows,required)
    def test_import_no_launch_and_no_admission(self):
        with patch.object(o.subprocess,'Popen',side_effect=AssertionError('real child forbidden')):
            spec=importlib.util.spec_from_file_location('side_effect_free_proof',ROOT/'proof.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            with self.assertRaises(module.Refused):module.run_proof({},self.root/'proof',lambda pid:None)
    def test_F1_spawn_error_retains_unavailable_child_and_count(self):
        def fail(*a,**kw):raise OSError('spawn error')
        self.leaves.spawn=fail
        with self.assertRaises(OSError):self.capture()
        self.assertEqual(self.l.actual_captures,1);self.assertIsNone(self.l.children[0]['pid']);self.assertTrue(self.l.held)
    def test_F8_prelaunch_freeze_blocks_spawn(self):
        def fail():raise o.Refused('source mutated')
        self.leaves.before_launch=fail
        with self.assertRaises(o.Refused):self.leaves.run('inspect',77)
        self.assertEqual(self.spawn_calls,0)
    def test_F4_operational_footprint_denial_settles(self):
        self.leaves.monitor=lambda pid:256*1024**2;self.child=Fake((-9,))
        with self.assertRaises(o.Refused):self.leaves.run('decode',self.root/'image.png')
        self.assertTrue(self.l.children[0]['reaped']);self.assertIn(('kill',),self.child.actions)
    def test_F8_callback_source_refuses_before_candidate_import(self):
        admission={'source_go':True,'concrete_user_circuit_exception':True,'fresh_root_admission':True,'source_freeze':[],'required_sources':['/synthetic/other.py'],'process_inspector_sources':['/synthetic/other.py']}
        with patch.object(p,'load_candidates',side_effect=AssertionError('candidate imported')):
            with self.assertRaises(p.Refused):p.run_proof(admission,self.root/'proof',lambda pid:None,child_footprint=lambda pid:0)
        self.assertFalse((self.root/'proof').exists())

if __name__=='__main__':
    # Test runner itself never creates children; constrain CPU only for this process.
    o.resource.setrlimit(o.resource.RLIMIT_CPU,(30,30))
    unittest.main(verbosity=2)
