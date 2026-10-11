#!/usr/bin/env python3
"""Local native-code corpus. Raw data goes to private files; stdout is operational only."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import subprocess
import time
import uuid

HERE = Path(__file__).resolve().parent
EXPORTER = HERE / 'ExportEngine.java'
GHIDRA = Path('/opt/homebrew/opt/ghidra/libexec')
JAVA = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home')
FILES = ('functions.jsonl', 'references.jsonl', 'callgraph.jsonl', 'strings.jsonl', 'string-references.jsonl')
ARCHES = ('x86_64', 'i386', 'arm64')
MAGIC = {bytes.fromhex(x) for x in ('feedface','cefaedfe','feedfacf','cffaedfe','cafebabe','bebafeca','cafebabf','bfbafeca')}
COUNTS = ('functions_seen','decompiled','failed','external','references','call_edges','strings','string_references')
RUNNER_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

class Refusal(ValueError):
    pass


class ClosedParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2,'Refused arguments; see --help. No input values emitted.\n')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def integer(value, maximum=10**10):
    if type(value) is not int or not 0 <= value <= maximum:
        raise Refusal('bounded integer required')
    return value


def digest_string(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise Refusal('SHA-256 required')
    return value


@contextmanager
def owned(root):
    fd=os.open(no_links(root/'runner.lock'),os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Refusal('another runner owns this root')
        yield
    finally:os.close(fd)


def no_links(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refusal('absolute path required')
    for p in (path, *path.parents):
        if p.is_symlink():
            raise Refusal('symlink refused')
    return path


def tree_no_links(path):
    no_links(path)
    if not path.exists():return
    for directory, dirs, names in os.walk(path,followlinks=False):
        for name in dirs+names:no_links(Path(directory)/name)


def private_root(path, create=False):
    path = no_links(Path(path))
    ancestor = path
    while not ancestor.exists():
        ancestor = ancestor.parent
    probe = subprocess.run(['git', '-C', str(ancestor), 'rev-parse', '--git-dir'], capture_output=True)
    if probe.returncode == 0:
        raise Refusal('private output must be outside Git')
    if any(p.startswith('.') for p in path.parts):
        raise Refusal('Ghidra roots cannot have dot components')
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise Refusal('private directory required')
    return path


def contained(root, relative):
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts:
        raise Refusal('relative contained path required')
    return no_links(root / p)


def read(path, limit=128*1024*1024):
    path = no_links(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise Refusal('regular bounded file required')
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    current = os.stat(path,follow_symlinks=False)
    key = lambda s: (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if len(data) > limit or key(before) != key(after) or key(after) != key(current) or len(data) != before.st_size:
        raise Refusal('file changed while reading')
    return data


def file_sha(path):
    # Streaming for large resource archives; never follows symlinks.
    path = no_links(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    h = hashlib.sha256()
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise Refusal('regular file required')
        for data in iter(lambda: stream.read(1024*1024), b''):
            h.update(data)
        after = os.fstat(stream.fileno())
    now = os.stat(path,follow_symlinks=False)
    key = lambda s: (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if key(before) != key(after) or key(after) != key(now):
        raise Refusal('input changed')
    return h.hexdigest()


def json_load(data):
    def unique(items):
        d = {}
        for k,v in items:
            if k in d:
                raise Refusal('duplicate JSON key')
            d[k] = v
        return d
    return json.loads(data, object_pairs_hook=unique)


def load(path):
    return json_load(read(path))


def json_lines(path, limit=512*1024*1024):
    """Stream bounded local records and detect replacement/modification."""
    path=no_links(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>limit:raise Refusal('bounded JSONL required')
        size=0
        while True:
            line=stream.readline(2*1024*1024+1)
            if not line:break
            size+=len(line)
            if len(line)>2*1024*1024 or size>limit:raise Refusal('JSONL budget exceeded')
            yield json_load(line)
        after=os.fstat(stream.fileno())
    now=os.stat(path,follow_symlinks=False)
    key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if size!=before.st_size or key(before)!=key(after) or key(after)!=key(now):raise Refusal('JSONL changed')


def write(path, data, replace=False):
    path = no_links(path)
    data = data if isinstance(data,bytes) else canonical(data)+b'\n'
    if not replace:
        fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f: f.write(data)
        return
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        write(temp,data)
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def profile():
    return {'schema':1,'ghidra':'12.1.4','jdk_major':21,'exporter_sha256':file_sha(EXPORTER),
            'analysis':'default','max_cpu':2,'per_function_seconds':20}


def command(argv, log, env=None, timeout=None):
    no_links(log)
    with log.open('xb') as stream:
        try:
            return subprocess.run(argv,stdout=stream,stderr=subprocess.STDOUT,env=env,timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return 124


def inventory(root, apps):
    root = private_root(root,True)
    if (root/'manifest.json').exists():
        raise Refusal('inventory already exists')
    os.umask(0o077)
    no_links(root/'inputs').mkdir(mode=0o700,exist_ok=True)
    no_links(root/'logs').mkdir(mode=0o700,exist_ok=True)
    resources=[];modules={};links=[]
    for app in apps:
        app=no_links(Path(app))
        if not app.is_dir():
            raise Refusal('app directory required')
        for directory, dirs, names in os.walk(app,followlinks=False):
            parent=Path(directory)
            dirs.sort();names.sort()
            for name in list(dirs)+names:
                p=parent/name
                if p.is_symlink():
                    resolved=p.resolve()
                    links.append({'source':str(p),'destination':str(resolved),
                                  'scope':'inside-app' if resolved.is_relative_to(app) else 'external-reference',
                                  'exists':resolved.exists()})
            dirs[:]=[name for name in dirs if not (parent/name).is_symlink()]
            for name in names:
                p=parent/name
                if p.is_symlink():continue
                info=p.stat()
                if not stat.S_ISREG(info.st_mode):continue
                digest=file_sha(p)
                with p.open('rb') as stream: magic=stream.read(4)
                row={'source':str(p),'app':str(app),'relative':str(p.relative_to(app)),
                     'sha256':digest,'bytes':info.st_size,'kind':'native' if magic in MAGIC else 'resource'}
                resources.append(row)
                if magic not in MAGIC:continue
                architectures=subprocess.check_output(['/usr/bin/lipo','-archs',str(p)],text=True,stderr=subprocess.DEVNULL).strip().split()
                if not architectures or any(a not in ARCHES for a in architectures):
                    raise Refusal('unsupported architecture')
                for arch in architectures:
                    temp=root/'inputs'/('slice-'+uuid.uuid4().hex+'.bin')
                    if len(architectures)==1:write(temp,read(p))
                    else:
                        result=command(['/usr/bin/lipo',str(p),'-thin',arch,'-output',str(temp)],root/'logs'/('lipo-'+uuid.uuid4().hex+'.log'))
                        if result:raise Refusal('slice extraction failed')
                    slice_sha=file_sha(temp);mid=slice_sha[:24]
                    dest=root/'inputs'/(mid+'.bin')
                    if dest.exists():
                        if file_sha(dest)!=slice_sha:raise Refusal('slice identity collision')
                        temp.unlink()
                    else:temp.rename(dest)
                    uuid_result=subprocess.run(['/usr/bin/dwarfdump','--uuid',str(dest)],capture_output=True,text=True)
                    uuids=uuid_result.stdout
                    # Keep tool text private, plus exact normalized UUID for matching.
                    image_uuid=uuids.split('UUID: ',1)[1].split(' ',1)[0].lower() if 'UUID: ' in uuids else None
                    if mid not in modules:
                        modules[mid]={'id':mid,'sha256':slice_sha,'architecture':arch,'image_uuid':image_uuid,
                                      'copy':str(dest.relative_to(root)),'bytes':dest.stat().st_size,'sources':[]}
                    modules[mid]['sources'].append({'path':str(p),'container_sha256':digest})
                if file_sha(p)!=digest:raise Refusal('source changed during slicing')
    manifest={'schema':1,'profile':profile(),'modules':sorted(modules.values(),key=lambda m:('/MacOS/' not in m['sources'][0]['path'],m['architecture']!='x86_64',m['sources'][0]['path'])),'resources':resources,'symlinks':links}
    write(root/'manifest.json',manifest)
    return {'modules':len(modules),'resources':len(resources),'symlinks':len(links),'status':'inventoried'}


def manifest(root):
    root=private_root(root)
    value=load(root/'manifest.json')
    if set(value)!= {'schema','profile','modules','resources','symlinks'} or value['schema']!=1 or value['profile']!=profile():
        raise Refusal('manifest or tool profile mismatch')
    if type(value['modules']) is not list or not 1 <= len(value['modules']) <= 256:
        raise Refusal('bounded nonempty module list required')
    ids=set()
    for m in value['modules']:
        digest_string(m['sha256'])
        if m['id'] in ids or m['id']!=m['sha256'][:24] or m['architecture'] not in ARCHES:
            raise Refusal('invalid module identity')
        ids.add(m['id'])
        if file_sha(contained(root,m['copy']))!=m['sha256']:raise Refusal('copied input mismatch')
    return value


def dependencies(root):
    root=private_root(root);value=manifest(root);edges=[];classes={}
    sources={r['source']:r for r in value['resources']}
    main=value['modules'][0]['sources'][0]['path']
    for m in value['modules']:
        result=subprocess.run(['/usr/bin/otool','-L',str(contained(root,m['copy']))],capture_output=True,timeout=30)
        if result.returncode:raise Refusal('dependency inspection failed')
        write(root/'logs'/('dependencies-'+m['id']+'.log'),result.stdout+result.stderr)
        for line in result.stdout.decode('utf-8').splitlines()[1:]:
            target=line.strip().split(' (compatibility version',1)[0]
            # A dylib's own install-name is retained as a reference too.
            resolved=target
            if target.startswith('@loader_path/'):
                resolved=str((Path(m['sources'][0]['path']).parent/target[len('@loader_path/'):]).resolve())
            elif target.startswith('@executable_path/'):
                resolved=str((Path(main).parent/target[len('@executable_path/'):]).resolve())
            if resolved in sources:kind='inventoried-app-file'
            elif target.startswith(('/usr/lib/','/System/Library/')):kind='system-reference'
            else:kind='unresolved-reference'
            edges.append({'module':m['id'],'target':target,'resolved_candidate':resolved,'kind':kind})
            classes[kind]=classes.get(kind,0)+1
    wrappers=[]
    for r in value['resources']:
        if r['bytes']>1024*1024 or r['kind']=='native':continue
        data=read(Path(r['source']),1024*1024)
        if data.startswith(b'#!') or b'steam://' in data:
            wrappers.append({'source':r['source'],'sha256':r['sha256'],'script':data.startswith(b'#!'),'steam_url_reference':b'steam://' in data})
    write(root/'dependencies.json',{'schema':1,'edges':edges,'wrapper_candidates':wrappers,
          'limitation':'Runtime search paths and system-library implementations are not resolved or decompiled.'})
    return {'status':'dependencies-recorded','modules':len(value['modules']),'links':len(edges),
            'classes':classes,'wrapper_candidates':len(wrappers)}


def export_digest(directory, expected_sha):
    directory=no_links(directory)
    identity=load(directory/'identity.json')
    summary=load(directory/'COMPLETE.json')
    if identity['binary_sha256']!=expected_sha or summary['binary_sha256']!=expected_sha or summary['status']!='complete':
        raise Refusal('export identity/completion mismatch')
    if identity.get('ghidra_version')!=profile()['ghidra'] or summary.get('ghidra_version')!=profile()['ghidra']:
        raise Refusal('provider build mismatch')
    counts={key:integer(summary[key]) for key in COUNTS}
    if counts['functions_seen']!=counts['decompiled']+counts['failed']+counts['external']:
        raise Refusal('inconsistent completion counts')
    h=hashlib.sha256();count=0;statuses={'decompiled':0,'failed':0,'external':0}
    count_keys=dict(zip(FILES,('functions_seen','references','call_edges','strings','string_references')))
    for name in FILES:
        p=directory/name
        rows_seen=0
        # Function results have timestamps/reuse/timing only in metadata; exclude those from semantic equality.
        for row in json_lines(p):
            rows_seen+=1
            if name=='functions.jsonl':
                count+=1
                if row.get('status') not in ('decompiled','external','failed'):
                    raise Refusal('unknown function status')
                statuses[row['status']]+=1
                rel=row.get('decomp_file')
                if row.get('status') in ('decompiled','external'):
                    code=contained(directory,rel)
                    if file_sha(code)!=row['decomp_sha256']:raise Refusal('function output digest mismatch')
                for key in ('elapsed_seconds','reused'):row.pop(key,None)
            h.update(name.encode()+b'\0'+canonical(row)+b'\n')
        if rows_seen!=counts[count_keys[name]]:raise Refusal('export row count mismatch')
    if count!=summary['functions_seen']:raise Refusal('function inventory count mismatch')
    if any(statuses[key]!=counts[key] for key in statuses):raise Refusal('function status count mismatch')
    return {'digest':h.hexdigest(),'counts':counts}


def run(root, indices=None, seconds=3600, reuse=True):
    root=private_root(root);value=manifest(root);started=time.monotonic()
    if type(seconds) is not int or not 60 <= seconds <= 14400 or (indices is not None and (not indices or any(type(i) is not int or not 0 <= i < len(value['modules']) for i in indices))):
        raise Refusal('run bounds')
    env=dict(os.environ,JAVA_HOME=str(JAVA),GHIDRA_HEADLESS_MAXMEM='2G')
    results=[]
    for index,m in enumerate(value['modules']):
        if indices is not None and index not in indices:continue
        module_dir=contained(root,'modules/'+m['id']);module_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        out=module_dir/'export';receipt=module_dir/'receipt.json'
        tree_no_links(out)
        if reuse and receipt.exists():
            old=load(receipt)
            if old.get('status')=='complete':
                observed=export_digest(out,m['sha256'])
                if observed!=old['export']:raise Refusal('completed output changed')
                results.append({'module':index,'status':'reused','counts':observed['counts']});continue
        project=contained(root,'projects/'+m['id']);project.mkdir(parents=True,exist_ok=True,mode=0o700)
        tree_no_links(project)
        database=project/'Corpus.gpr'
        attempt=uuid.uuid4().hex
        log=root/'logs'/('ghidra-'+m['id']+'-'+attempt+'.log')
        argv=[str(GHIDRA/'support/analyzeHeadless'),str(project),'Corpus']
        # An interrupted database may have unfinished analysis. Let Ghidra resume
        # analysis rather than treating database existence as an analysis receipt.
        if database.exists():argv+=['-process',Path(m['copy']).name,'-analysisTimeoutPerFile',str(seconds)]
        else:argv+=['-import',str(contained(root,m['copy'])),'-analysisTimeoutPerFile',str(seconds)]
        argv+=['-scriptPath',str(HERE),'-postScript','ExportEngine.java',str(out),'20','-max-cpu','2']
        if (out/'COMPLETE.json').exists():
            (out/'COMPLETE.json').rename(out/('COMPLETE-'+attempt+'.previous.json'))
        provenance={'attempt':attempt,'input_sha256':m['sha256'],'argv':argv,'log':str(log),
                    'started':time.time(),'runner_sha256':RUNNER_SHA,'profile':value['profile']}
        write(receipt,dict(provenance,status='running'),replace=True)
        # Separate owned process group permits scoped termination. Never kill unrelated Java/Ghidra.
        with log.open('xb') as stream:
            proc=subprocess.Popen(argv,stdout=stream,stderr=subprocess.STDOUT,env=env,start_new_session=True)
            try:code=proc.wait(timeout=seconds)
            except (subprocess.TimeoutExpired,KeyboardInterrupt):
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:proc.wait(timeout=15)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                code=124
        item=dict(provenance,status='failed',exit_code=code,finished=time.time(),provider_pid=proc.pid)
        item['elapsed_seconds']=round(item['finished']-item['started'],3)
        if file_sha(contained(root,m['copy']))!=m['sha256']:raise Refusal('analysis changed input')
        if code==0 and (out/'COMPLETE.json').exists():
            item['export']=export_digest(out,m['sha256']);item['status']='complete'
        write(receipt,item,replace=True)
        results.append({'module':index,'status':item['status'],'counts':item.get('export',{}).get('counts')})
        write(root/'progress.json',{'results':results,'seconds':round(time.monotonic()-started,3)},replace=True)
        print(json.dumps({'module':index,'status':item['status'],'counts':item.get('export',{}).get('counts')}),flush=True)
    return {'status':'complete' if all(r['status'] in ('complete','reused') for r in results) else 'partial','modules':len(results)}


def verify(root):
    root=private_root(root);value=manifest(root);items=[]
    for index,m in enumerate(value['modules']):
        path=contained(root,'modules/'+m['id'])
        if not (path/'receipt.json').exists():items.append({'module':index,'status':'pending'});continue
        rec=load(path/'receipt.json')
        if rec['status'] not in ('complete','running','failed'):
            raise Refusal('unknown receipt status')
        if rec['status']!='complete':items.append({'module':index,'status':rec['status']});continue
        ex=export_digest(path/'export',m['sha256'])
        if ex!=rec['export']:raise Refusal('export changed')
        items.append({'module':index,'status':'complete','export':ex})
    sources_unchanged=all(file_sha(Path(r['source']))==r['sha256'] for r in value['resources'])
    if not sources_unchanged:raise Refusal('installed resources changed')
    result={'schema':1,'profile':value['profile'],'modules':items,'inputs_unchanged':True}
    write(root/'verification.json',result,replace=True)
    return {'status':'verified' if all(x['status']=='complete' for x in items) else 'partial','modules':len(items),'inputs_unchanged':True}


def fingerprint(root):
    """Bind the lookup database to exactly the verified module exports."""
    value=manifest(root);parts=[]
    for m in value['modules']:
        rec=load(contained(root,'modules/'+m['id']+'/receipt.json'))
        if rec.get('status')!='complete':raise Refusal('index requires complete exports')
        parts.append([m['sha256'],rec['export']['digest']])
    return sha(canonical({'profile':value['profile'],'exports':parts}))


def build_index(root):
    root=private_root(root);value=manifest(root)
    binding=fingerprint(root)
    temp=root/('index-'+uuid.uuid4().hex+'.sqlite')
    connection=sqlite3.connect(temp)
    count=0
    try:
        connection.executescript('CREATE TABLE metadata(binding TEXT);CREATE TABLE functions(module TEXT,address TEXT,name TEXT,status TEXT,code TEXT,sha256 TEXT,PRIMARY KEY(module,address));CREATE TABLE ranges(module TEXT,start INTEGER,end INTEGER,address TEXT);CREATE INDEX range_start ON ranges(module,start);CREATE TABLE refs(module TEXT,source TEXT,target TEXT,kind TEXT);CREATE INDEX ref_source ON refs(module,source);CREATE INDEX ref_target ON refs(module,target);CREATE VIRTUAL TABLE strings USING fts5(module UNINDEXED,address UNINDEXED,value);')
        connection.execute('INSERT INTO metadata VALUES(?)',(binding,))
        for m in value['modules']:
            out=contained(root,'modules/'+m['id']+'/export')
            export_digest(out,m['sha256'])
            for r in json_lines(out/'functions.jsonl'):
                connection.execute('INSERT INTO functions VALUES(?,?,?,?,?,?)',(m['id'],r['address'],r['name'],r['status'],r['decomp_file'],r['decomp_sha256']));count+=1
                for extent in r.get('body_ranges',[]):
                    if any(re.fullmatch('[0-9a-fA-F]+',extent[k]) is None for k in ('start','end')):continue
                    start=int(extent['start'],16);end=int(extent['end'],16)
                    if not 0<=start<=end<2**63:raise Refusal('unsupported address range')
                    connection.execute('INSERT INTO ranges VALUES(?,?,?,?)',(m['id'],start,end,r['address']))
            for r in json_lines(out/'references.jsonl'):
                connection.execute('INSERT INTO refs VALUES(?,?,?,?)',(m['id'],r['from'],r['to'],r['type']))
            for r in json_lines(out/'strings.jsonl'):
                connection.execute('INSERT INTO strings VALUES(?,?,?)',(m['id'],r['address'],r['value']))
        connection.commit()
    except Exception:
        connection.close();temp.unlink(missing_ok=True);raise
    else:connection.close()
    no_links(root/'index.sqlite');os.replace(temp,root/'index.sqlite')
    write(root/'index-receipt.json',{'binding':binding,'sha256':file_sha(root/'index.sqlite'),'functions':count},replace=True)
    return {'status':'indexed','functions':count}


def lookup(root,module,kind,query,output,limit=20,include_code=False):
    root=private_root(root);value=manifest(root)
    if not 1<=limit<=100 or not 0<=module<len(value['modules']) or len(query)>256:raise Refusal('query bounds')
    output=contained(root,output)
    m=value['modules'][module];db=no_links(root/'index.sqlite')
    rec=load(root/'index-receipt.json')
    if rec['binding']!=fingerprint(root) or rec['sha256']!=file_sha(db):raise Refusal('stale or changed index')
    connection=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)
    try:
        if connection.execute('SELECT binding FROM metadata').fetchone()!=(rec['binding'],):raise Refusal('index binding mismatch')
        if kind=='function':
            rows=connection.execute('SELECT address,name,status,code,sha256 FROM functions WHERE module=? AND (address=? OR name LIKE ?) ORDER BY address LIMIT ?',(m['id'],query,'%'+query+'%',limit)).fetchall()
        elif kind=='references':
            rows=connection.execute('SELECT source,target,kind FROM refs WHERE module=? AND (source=? OR target=?) LIMIT ?',(m['id'],query,query,limit)).fetchall()
        elif kind=='string':
            rows=connection.execute('SELECT address,value FROM strings WHERE module=? AND strings MATCH ? LIMIT ?',(m['id'],query,limit)).fetchall()
        elif kind=='address':
            address=int(query,16)
            if not 0<=address<2**63:raise Refusal('unsupported address')
            rows=connection.execute('SELECT f.address,f.name,f.status,f.code,f.sha256 FROM functions f JOIN ranges r ON r.module=f.module AND r.address=f.address WHERE r.module=? AND r.start<=? AND r.end>=? LIMIT ?',(m['id'],address,address,limit)).fetchall()
        else:raise Refusal('unknown lookup kind')
    finally:connection.close()
    code=[]
    if include_code:
        if kind not in ('function','address'):raise Refusal('code requires function or address lookup')
        total=0
        for row in rows:
            if row[2] not in ('decompiled','external'):continue
            data=read(contained(root,'modules/'+m['id']+'/export/'+row[3]),1024*1024)
            if sha(data)!=row[4]:raise Refusal('retrieved function digest mismatch')
            total+=len(data)
            if total>2*1024*1024:raise Refusal('retrieval byte budget')
            code.append({'address':row[0],'text':data.decode('utf-8')})
    write(output,{'module':m,'kind':kind,'query':query,'rows':rows,'code':code,'limit':limit,'index_binding':rec['binding']})
    return {'status':'retrieved-private','matches':len(rows),'limit':limit}


def compare(left,right):
    left=private_root(left);right=private_root(right)
    if verify(left)['status']!='verified' or verify(right)['status']!='verified':raise Refusal('comparison requires fresh complete verification')
    a=load(left/'verification.json');b=load(right/'verification.json')
    am=manifest(left);bm=manifest(right)
    if [m['sha256'] for m in am['modules']] != [m['sha256'] for m in bm['modules']] or a['profile']!=b['profile']:
        raise Refusal('comparison input/profile mismatch')
    if len(a['modules'])!=len(b['modules']):raise Refusal('comparison coverage mismatch')
    changed=[i for i,(x,y) in enumerate(zip(a['modules'],b['modules'])) if x!=y]
    result={'status':'equivalent' if not changed else 'different','modules':len(a['modules']),'changed_modules':changed,'same_machine':True,'comparison':'canonical exports; excludes timestamps/reuse/elapsed fields'}
    write(right/'comparison.json',result,replace=True)
    return result


def publication(root, output):
    """Closed operational projection; never forwards a field from raw JSON."""
    root=private_root(root)
    if verify(root)['status']!='verified':raise Refusal('publication requires complete verification')
    v=load(root/'verification.json')
    counts={k:sum(integer(m['export']['counts'][k]) for m in v['modules']) for k in COUNTS}
    report={'schema':1,'report':'local-native-corpus','modules':len(v['modules']),
            'counts':counts,'inputs_unchanged':True,'evidence':'static-export',
            'human_review':'required','runtime_checks':0}
    write(contained(root,output),report)
    return report


def main():
    os.umask(0o077)
    parser=ClosedParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('inventory');p.add_argument('--root',type=Path,required=True);p.add_argument('--app',type=Path,action='append',required=True)
    p=sub.add_parser('run');p.add_argument('--root',type=Path,required=True);p.add_argument('--module',type=int,action='append');p.add_argument('--seconds',type=int,default=3600)
    for name in ('verify','index','dependencies'):
        p=sub.add_parser(name);p.add_argument('--root',type=Path,required=True)
    p=sub.add_parser('lookup');p.add_argument('--root',type=Path,required=True);p.add_argument('--module',type=int,required=True);p.add_argument('--kind',choices=['function','references','string','address'],required=True);p.add_argument('--query',required=True);p.add_argument('--output',required=True);p.add_argument('--limit',type=int,default=20);p.add_argument('--include-code',action='store_true')
    p=sub.add_parser('report');p.add_argument('--root',type=Path,required=True);p.add_argument('--output',required=True)
    p=sub.add_parser('compare');p.add_argument('--left',type=Path,required=True);p.add_argument('--right',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.action=='inventory':result=inventory(args.root,args.app)
        elif args.action=='run':
            with owned(private_root(args.root)):result=run(args.root,args.module,args.seconds)
        elif args.action=='verify':result=verify(args.root)
        elif args.action=='index':result=build_index(args.root)
        elif args.action=='dependencies':result=dependencies(args.root)
        elif args.action=='lookup':result=lookup(args.root,args.module,args.kind,args.query,args.output,args.limit,args.include_code)
        elif args.action=='report':result=publication(args.root,args.output)
        else:result=compare(args.left,args.right)
        print(json.dumps(result,sort_keys=True))
    except Exception:
        # Never echo private paths, provider logs, SQL parameters or arbitrary diagnostics.
        parser.exit(2,'Refused; inspect private receipts. No raw diagnostics emitted.\n')

if __name__=='__main__':main()
