"""Handwritten useful-detail and secrecy expectations; synthetic inputs only."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from smr_launcher.crash_details import MAX_THREADS, MAX_FRAMES, MAX_IMAGES, validate_details
from smr_launcher.crash_reports import CrashReportError, CrashStore, _validate_payload, parse_crash_report
from smr_launcher.crash_reporting import GitHubCrashPublisher, PublicPayloadError, render_issue, validate_public_payload

EXE='/Applications/Synthetic.app/Contents/MacOS/Game'
START=datetime(2026,1,2,12,tzinfo=timezone.utc)
UUID='12345678-1234-5678-1234-567812345678'
CANARY='PRIVATE_/Users/secret/token=SECRET_CANARY\n@someone'


def source():
    return dict(procPath=EXE,pid=123,procLaunch=START.isoformat(),captureTime='2026-01-02T12:01:00Z',
        cpuType='X86-64',translated=True,uptime=1234,procStartAbsTime=100,procExitAbsTime=200,
        exception=dict(type='EXC_BAD_ACCESS',signal='SIGSEGV',rawCodes=[1,'0x1234'],codes=CANARY),
        termination=dict(namespace='SIGNAL',code=11,indicator=CANARY),faultingThread=1,
        usedImages=[dict(path=EXE,uuid=UUID,base=4096,size=8192,arch='x86_64',name=CANARY),
                    dict(path='/usr/lib/synthetic.dylib',uuid='abcdefab-cdef-abcd-efab-cdefabcdefab',base=32768,size=4096,arch='arm64')],
        threads=[dict(triggered=False,name=CANARY,frames=[dict(imageIndex=1,imageOffset=32,instructionAddr=32800,symbol=CANARY)],
                      threadState=dict(flavor='ARM_THREAD_STATE64',x=[dict(value=9),dict(value=10)],pc=dict(value=32800),sp=dict(value=50000))),
                 dict(triggered=True,queue=CANARY,frames=[dict(imageIndex=0,imageOffset=256,instructionAddr=4352)],
                      threadState=dict(flavor='x86_THREAD_STATE',rax=dict(value=5),rip=dict(value=4352),rsp=dict(value=60000),private=CANARY))],
        vmInfo=dict(pageSize=4096,regionCount=12,private=CANARY),vmSummary=CANARY,asi=CANARY,private=CANARY)


def raw(s):
    return (json.dumps(dict(bug_type='309',private=CANARY))+'\n'+json.dumps(s)).encode()


def parse(s=None):
    return parse_crash_report(raw(source() if s is None else s),expected_executable=EXE,expected_pid=123,
        session_started_at=START,context=dict(game_executable_sha256='a'*64,launcher_version='1.2.3'))


def test_handwritten_useful_all_threads_registers_images_and_codes():
    p=parse();d=p['diagnostics']
    assert p['schema']==2 and d['format']=='modern_ips' and d['faulting_thread']==1
    assert d['threads']['source_count']==d['threads']['retained_count']==2
    assert d['threads']['items'][0]['frames']['items']==[dict(index=0,image_index=1,offset=32,address=32800)]
    assert d['threads']['items'][1]['frames']['items']==[dict(index=0,image_index=0,offset=256,address=4352)]
    assert d['threads']['items'][0]['registers']==dict(architecture='arm64',source_count=4,retained_count=4,values=dict(x0=9,x1=10,pc=32800,sp=50000))
    assert d['threads']['items'][1]['registers']==dict(architecture='rosetta_x86_64',source_count=4,retained_count=3,values=dict(rax=5,rip=4352,rsp=60000))
    assert d['images']['items'][0]==dict(index=0,uuid=UUID,base=4096,size=8192,architecture='x86_64',role='game')
    assert d['images']['items'][1]['role']=='system'
    assert d['exception_codes']==dict(source_count=2,retained_count=2,items=[1,4660])
    assert d['ancillary']==dict(uptime=1234,proc_start_absolute=100,proc_exit_absolute=200,termination_code=11,vm_page_size=4096,vm_region_count=12)
    assert d['unavailable']==[]
    assert 'unknown_registers' in d['omitted'] and 'memory_contents' in d['omitted']
    assert 'SECRET_CANARY' not in json.dumps(p)
    assert validate_public_payload(p)==p and _validate_payload(p)==p
    body=render_issue(p)[1]
    assert 'frame_columns' in body and 'image_columns' in body and UUID in body and 'rosetta_x86_64' in body


def test_fingerprint_unaffected_by_added_nonfaulting_details():
    s=source();first=parse(s)
    s['threads'][0]['frames'][0]['imageOffset']=999
    s['threads'][1]['threadState']['rax']['value']=999
    s['usedImages'][0]['base']=999
    s['vmInfo']['regionCount']=99
    second=parse(s)
    assert first['fingerprint']==second['fingerprint']
    assert first['report_sha256']!=second['report_sha256']


def _paths(value, prefix=()):
    if type(value) is dict:
        for k,v in value.items():
            yield from _paths(v,prefix+(k,))
    elif type(value) is list:
        for i,v in enumerate(value): yield from _paths(v,prefix+(i,))
    else: yield prefix


def _node(value,path):
    for k in path: value=value[k]
    return value


def test_canary_and_wrong_type_at_every_nested_leaf_rejected_by_both_gates():
    original=parse()
    for path in _paths(original['diagnostics']):
        p=copy.deepcopy(original); _node(p['diagnostics'],path[:-1])[path[-1]]=CANARY
        with unittest.TestCase().assertRaises(CrashReportError): _validate_payload(p)
        with unittest.TestCase().assertRaises(PublicPayloadError): validate_public_payload(p)
        calls=[]
        result=GitHubCrashPublisher(lambda *a,**k:calls.append(a)).publish(p)
        assert result['status']=='rejected' and calls==[] and CANARY not in str(result)


def test_extra_keys_in_every_nested_object_and_register_names_rejected():
    original=parse()
    objects=[(),('threads',),('threads','items',0),('threads','items',0,'frames'),
             ('threads','items',0,'frames','items',0),('threads','items',0,'registers'),
             ('threads','items',0,'registers','values'),('images',),('images','items',0),('ancillary',),('exception_codes',)]
    for path in objects:
        p=copy.deepcopy(original);_node(p['diagnostics'],path)[CANARY]=CANARY
        with unittest.TestCase().assertRaises(CrashReportError):_validate_payload(p)
        with unittest.TestCase().assertRaises(PublicPayloadError):validate_public_payload(p)


def test_counts_bounds_bad_indices_and_invalid_reasons_rejected():
    mutations=[(('threads','source_count'),True),(('threads','source_count'),2**64),
        (('threads','retained_count'),3),(('threads','items',0,'index'),1),
        (('faulting_thread',),2),(('images','source_count'),-1),
        (('threads','items',0,'frames','items',0,'image_index'),2),
        (('threads','items',0,'frames','items',0,'offset'),True),
        (('threads','items',0,'registers','values','pc'),-1),
        (('unavailable',),['registers']),(('omitted',),[])]
    for path,v in mutations:
        p=parse();_node(p['diagnostics'],path[:-1])[path[-1]]=v
        with unittest.TestCase().assertRaises(CrashReportError):_validate_payload(p)
        with unittest.TestCase().assertRaises(PublicPayloadError):validate_public_payload(p)


def test_source_unknown_numeric_and_strings_drop_safely():
    s=source();s['usedImages'][0].update(uuid=CANARY,base=True,size=-1,arch=CANARY)
    s['threads'][1]['threadState'].update(rax=dict(value=CANARY),rip=True)
    s['threads'][0]['frames'][0].update(imageIndex=CANARY,instructionAddr=CANARY)
    s['vmInfo'].update(pageSize=CANARY)
    p=parse(s);d=p['diagnostics']
    assert d['images']['items'][0]['uuid'] is None and d['images']['items'][0]['base'] is None
    assert d['threads']['items'][0]['frames']['items'][0]['image_index'] is None
    assert 'invalid_numeric' in d['omitted'] and 'invalid_uuid' in d['omitted'] and 'invalid_reference' in d['omitted']
    assert CANARY not in json.dumps(p)
    validate_public_payload(p)


def test_malformed_source_containers_fail_safely():
    cases=[('threads',{}),('threads',[True]),('threads',[dict(frames={})]),
        ('threads',[dict(frames=[True])]),('threads',[dict(threadState=[])]),('usedImages',{}),('usedImages',[True]),
        ('exception',dict(type='EXC_BAD_ACCESS',signal='SIGSEGV',rawCodes={}))]
    for field,value in cases:
        s=source();s[field]=value
        with unittest.TestCase().assertRaises(CrashReportError):parse(s)


def test_truthful_limits_and_unavailable():
    s=source();s['threads']=[dict(frames=[dict(imageIndex=0,imageOffset=0)]*(MAX_FRAMES+1))]*(MAX_THREADS+1)
    s['usedImages']=[s['usedImages'][0]]*(MAX_IMAGES+1)
    # This intentionally large valid report remains below the raw input bound.
    s['threads']=[dict(frames=[])]*MAX_THREADS+[dict(frames=[])]
    s['threads'][0]=dict(frames=[dict(imageIndex=0,imageOffset=0)]*(MAX_FRAMES+1))
    p=parse(s);d=p['diagnostics']
    assert d['threads']['source_count']==129 and d['threads']['retained_count']==128
    assert d['images']['source_count']==513 and d['images']['retained_count']==512
    assert d['threads']['items'][0]['frames']['source_count']==1025
    assert d['threads']['items'][0]['frames']['retained_count']==1024
    assert {'thread_limit','frame_limit','image_limit'} <= set(d['omitted'])
    assert 'registers' in d['unavailable']
    validate_public_payload(p)


def normal_sized():
    s=source();s['usedImages']=[dict(path=EXE if i==0 else '/usr/lib/synthetic.dylib',uuid=UUID,base=4096+i*8192,size=8192,arch='x86_64') for i in range(39)]
    s['threads']=[dict(triggered=i==1,frames=[dict(imageIndex=j%39,imageOffset=256+j,instructionAddr=4096+j) for j in range(47 if i==0 else 10)],threadState=dict(flavor='x86_THREAD_STATE',rax=dict(value=7),rip=dict(value=8192),rsp=dict(value=60000))) for i in range(48)]
    return s


def test_normal_48_threads_517_frames_39_images_fit_without_loss():
    p=parse(normal_sized());d=p['diagnostics'];body=render_issue(p)[1]
    assert len(d['threads']['items'])==48 and sum(len(t['frames']['items']) for t in d['threads']['items'])==517
    assert len(d['images']['items'])==39 and len(body)<=60000
    assert not {'thread_limit','frame_limit','image_limit'} & set(d['omitted'])


def test_oversized_body_pending_before_runner_and_metadata_retained():
    s=normal_sized();s['threads']=[dict(triggered=i==1,frames=[dict(imageIndex=0,imageOffset=256+j,instructionAddr=4096+j) for j in range(1024)]) for i in range(20)]
    p=parse(s);calls=[]
    result=GitHubCrashPublisher(lambda *a,**k:calls.append(a)).publish(p)
    assert result==dict(status='pending',detail='body_too_large') and calls==[]
    with tempfile.TemporaryDirectory(dir=os.environ.get('CRASH_TEST_TEMP_ROOT')) as t:
        root=Path(t).resolve()/'store'
        with CrashStore(root) as store:
            entry=store.collect(raw(s),p)
            assert (root/(entry['incident_id']+'.json')).stat().st_size>256*1024
            store.mark_publication(p['fingerprint'],'sending',incident_id=entry['incident_id'])
            store.reconcile_publication(p['fingerprint'],'pending',detail=result['detail'],incident_id=entry['incident_id'])
        with CrashStore(root) as restarted:
            assert restarted.pending()[0]['publication']['detail']=='body_too_large'
            assert (root/entry['raw_file']).read_bytes()==raw(s)


def test_schema1_immutable_restart_upgrade_and_fresh_schema2_state():
    first=parse();old=copy.deepcopy(first);old.pop('diagnostics');old['schema']=1
    with tempfile.TemporaryDirectory(dir=os.environ.get('CRASH_TEST_TEMP_ROOT')) as t:
        root=Path(t).resolve()/'store'
        with CrashStore(root) as store:
            e=store.collect(raw(source()),old);store.mark_publication(old['fingerprint'],'published','https://example.invalid/old')
        originals={f.name:f.read_bytes() for f in root.iterdir()}
        with CrashStore(root) as store:
            assert store.collect(raw(source()),first)['payload']==old
            s=source();s['uptime']=999;fresh=parse(s);e=store.collect(raw(s),fresh)
            assert e['publication']==dict(status='pending',issue_url=None,detail=None)
            store.mark_publication(fresh['fingerprint'],'sending',incident_id=e['incident_id'])
            assert store.pending()[0]['publication']['status']=='unknown'
            with unittest.TestCase().assertRaises(CrashReportError):store.mark_publication(fresh['fingerprint'],'pending',incident_id=e['incident_id'])
        for name,b in originals.items():assert (root/name).read_bytes()==b


def test_known_rosetta_temporaries_retained_unknown_rosetta_fields_dropped():
    s=source();s['threads'][1]['threadState']['rosetta']=dict(tmp0=dict(value=10),tmp1=dict(value=20),tmp2=dict(value=30),secret=CANARY)
    p=parse(s);r=p['diagnostics']['threads']['items'][1]['registers']
    assert r['source_count']==8 and r['retained_count']==6
    assert {k:r['values'][k] for k in ('rosetta_tmp0','rosetta_tmp1','rosetta_tmp2')}==dict(rosetta_tmp0=10,rosetta_tmp1=20,rosetta_tmp2=30)
    assert CANARY not in json.dumps(p)
    validate_public_payload(p)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name,value in sorted(globals().items())
                              if name.startswith('test_') and callable(value))
