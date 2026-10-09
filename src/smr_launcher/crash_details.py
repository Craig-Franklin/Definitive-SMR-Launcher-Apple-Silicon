"""Typed diagnostic projection; raw text and unknown fields stay private.

Limits are retention bounds, never a claim of full report or memory capture.
The publisher separately validates this wire format before rendering.
"""
from __future__ import annotations
import json
import uuid

MAX_THREADS, MAX_FRAMES, MAX_IMAGES = 128, 1024, 512
MAX_COUNT = 4 * 1024 * 1024
U64 = 2**64 - 1
X86 = frozenset('rax rbx rcx rdx rdi rsi rbp rsp r8 r9 r10 r11 r12 r13 r14 r15 rip rflags cs fs gs trapno err cr2 rosetta_tmp0 rosetta_tmp1 rosetta_tmp2'.split())
ARM = frozenset([f'x{i}' for i in range(29)] + 'fp lr sp pc cpsr esr far'.split())
OMITTED = frozenset('raw_text symbols paths identifiers annotations vm_text memory_contents unknown_fields'.split())
REASONS = OMITTED | frozenset('thread_limit frame_limit image_limit invalid_numeric invalid_uuid invalid_reference unknown_registers exception_code_limit'.split())
UNAVAILABLE = frozenset('faulting_thread threads registers binary_images numeric_vm exception_codes'.split())
ANCILLARY = frozenset('uptime proc_start_absolute proc_exit_absolute termination_code vm_page_size vm_region_count'.split())


def uint(v):
    return type(v) is int and 0 <= v <= U64


def uid(v):
    try:
        return str(uuid.UUID(v)) if type(v) is str and len(v) in (32, 36) else None
    except ValueError:
        return None


def arch(v):
    return {'X86-64':'x86_64', 'ARM-64':'arm64', 'arm64e':'arm64'}.get(v, v) if type(v) is str and v in {'X86-64','ARM-64','arm64e','x86_64','arm64'} else 'UNKNOWN'


def role(path, executable):
    return 'game' if path == executable else 'system' if type(path) is str and path.startswith(('/usr/lib/', '/System/Library/')) else 'other'


def collection(source, items):
    return dict(source_count=source, retained_count=len(items), items=items)


def extract_details(report, executable, *, legacy=False):
    omissions = set(OMITTED)
    unavailable = {'numeric_vm'}
    if legacy:
        return dict(version=1, format='legacy_crash', faulting_thread=None,
                    threads=collection(None, []), images=collection(None, []),
                    exception_codes=collection(None, []), ancillary={k:None for k in sorted(ANCILLARY)},
                    unavailable=sorted(UNAVAILABLE), omitted=sorted(omissions))
    def number(v):
        if v is None:
            return None
        if uint(v):
            return v
        omissions.add('invalid_numeric')
        return None
    def image_uuid(v):
        result = uid(v)
        if v is not None and result is None:
            omissions.add('invalid_uuid')
        return result
    images, threads = report.get('usedImages', []), report.get('threads', [])
    if type(images) is not list or type(threads) is not list:
        raise ValueError('invalid diagnostic containers')
    fault = report.get('faultingThread')
    if type(fault) is not int or not 0 <= fault < len(threads):
        triggered = [i for i,t in enumerate(threads) if type(t) is dict and t.get('triggered') is True]
        fault = triggered[0] if len(triggered) == 1 else None
    if fault is None:
        unavailable.add('faulting_thread')
    if not images: unavailable.add('binary_images')
    if not threads: unavailable.add('threads')
    image_items = []
    for i, image in enumerate(images[:MAX_IMAGES]):
        if type(image) is not dict: raise ValueError('invalid diagnostic image')
        image_items.append(dict(index=i, uuid=image_uuid(image.get('uuid')), base=number(image.get('base')),
                                size=number(image.get('size')), architecture=arch(image.get('arch')),
                                role=role(image.get('path'), executable)))
    if len(images) > MAX_IMAGES: omissions.add('image_limit')
    thread_items = []
    any_registers = False
    for i, thread in enumerate(threads[:MAX_THREADS]):
        if type(thread) is not dict: raise ValueError('invalid diagnostic thread')
        frames = thread.get('frames', [])
        if type(frames) is not list: raise ValueError('invalid diagnostic frames')
        frame_items = []
        for j, frame in enumerate(frames[:MAX_FRAMES]):
            if type(frame) is not dict: raise ValueError('invalid diagnostic frame')
            ix = frame.get('imageIndex')
            if type(ix) is not int or not 0 <= ix < len(images):
                ix, image = None, {}
                omissions.add('invalid_reference')
            else:
                image = images[ix]
                if type(image) is not dict: raise ValueError('invalid diagnostic image')
            frame_items.append(dict(index=j, image_index=ix, offset=number(frame.get('imageOffset')),
                                    address=number(frame.get('instructionAddr'))))
        if len(frames) > MAX_FRAMES: omissions.add('frame_limit')
        state = thread.get('threadState', {})
        if type(state) is not dict: raise ValueError('invalid register state')
        flavor = state.get('flavor')
        architecture = 'arm64' if flavor in ('ARM_THREAD_STATE64','ARM_THREAD_STATE') else ('rosetta_x86_64' if report.get('translated') is True else 'x86_64') if flavor in ('x86_THREAD_STATE','x86_THREAD_STATE64') else 'UNKNOWN'
        allowed = ARM if architecture == 'arm64' else X86 if architecture in ('x86_64','rosetta_x86_64') else frozenset()
        candidates = dict(state)
        candidates.pop('flavor', None)
        x = candidates.pop('x', None)
        rosetta = candidates.pop('rosetta', None)
        source_count = len(candidates)
        if type(rosetta) is dict:
            source_count += len(rosetta)
            for key in ('tmp0','tmp1','tmp2'):
                if key in rosetta: candidates['rosetta_' + key] = rosetta[key]
            if set(rosetta) - {'tmp0','tmp1','tmp2'}: omissions.add('unknown_registers')
        elif rosetta is not None:
            source_count += 1
            omissions.add('invalid_numeric')
        if type(x) is list:
            source_count += len(x)
            for n, value in enumerate(x[:29]): candidates[f'x{n}'] = value
            if len(x) > 29: omissions.add('unknown_registers')
        elif x is not None:
            source_count += 1
            omissions.add('invalid_numeric')
        values = {}
        for key, value in candidates.items():
            if key not in allowed:
                omissions.add('unknown_registers'); continue
            if type(value) is dict: value = value.get('value')
            value = number(value)
            if value is not None: values[key] = value
        any_registers |= bool(values)
        thread_items.append(dict(index=i, triggered=thread.get('triggered') if type(thread.get('triggered')) is bool else None,
                                 frames=collection(len(frames), frame_items),
                                 registers=dict(architecture=architecture, source_count=source_count, retained_count=len(values), values=values)))
    if len(threads) > MAX_THREADS: omissions.add('thread_limit')
    if not any_registers: unavailable.add('registers')
    exception = report.get('exception', {})
    codes = exception.get('rawCodes', []) if type(exception) is dict else []
    if type(codes) is not list: raise ValueError('invalid diagnostic exception codes')
    retained_codes = []
    for code in codes[:8]:
        if type(code) is str:
            import re
            if re.fullmatch(r'(?:0x[0-9a-fA-F]{1,16}|[0-9]{1,20})', code): code = int(code, 16 if code.startswith('0x') else 10)
        code = number(code)
        if code is not None: retained_codes.append(code)
    if len(codes) > 8: omissions.add('exception_code_limit')
    if not retained_codes: unavailable.add('exception_codes')
    termination = report.get('termination') or {}
    vm = report.get('vmInfo', {})
    if type(vm) is not dict: vm = {}
    ancillary = dict(uptime=number(report.get('uptime')), proc_start_absolute=number(report.get('procStartAbsTime')),
                     proc_exit_absolute=number(report.get('procExitAbsTime')), termination_code=number(termination.get('code')),
                     vm_page_size=number(vm.get('pageSize')), vm_region_count=number(vm.get('regionCount')))
    if ancillary['vm_page_size'] is not None or ancillary['vm_region_count'] is not None: unavailable.discard('numeric_vm')
    return dict(version=1, format='modern_ips', faulting_thread=fault, threads=collection(len(threads), thread_items),
                images=collection(len(images), image_items), exception_codes=collection(len(codes), retained_codes),
                ancillary=ancillary, unavailable=sorted(unavailable), omitted=sorted(omissions))


def validate_details(d):
    """Store-side strict schema; publisher has its own independent validator."""
    def need(ok):
        if not ok: raise ValueError('invalid structured diagnostics')
    def keys(v, expected): need(type(v) is dict and set(v) == set(expected.split()))
    def enum(v, choices): need(type(v) is str and v in choices)
    def nullable(v): need(v is None or uint(v))
    def uuid_value(v): need(v is None or (type(v) is str and uid(v) == v))
    def coll(c, cap):
        keys(c, 'source_count retained_count items')
        if c['source_count'] is None:
            need(d['format'] == 'legacy_crash' and c['retained_count'] == 0 and type(c['retained_count']) is int and c['items'] == [])
            return []
        need(uint(c['source_count']) and c['source_count'] <= MAX_COUNT)
        need(type(c['items']) is list and type(c['retained_count']) is int and c['retained_count'] == len(c['items']) <= min(cap,c['source_count']))
        return c['items']
    def indexed(items, count):
        prev = -1
        for item in items:
            need(type(item) is dict and type(item.get('index')) is int and prev < item['index'] < count)
            prev = item['index']
    keys(d, 'version format faulting_thread threads images exception_codes ancillary unavailable omitted')
    need(type(d['version']) is int and d['version'] == 1)
    enum(d['format'], {'modern_ips','legacy_crash'})
    images = coll(d['images'], MAX_IMAGES); threads = coll(d['threads'], MAX_THREADS)
    codes = coll(d['exception_codes'], 8)
    need(all(uint(c) for c in codes))
    indexed(images,d['images']['source_count']); indexed(threads,d['threads']['source_count'])
    need(d['faulting_thread'] is None or (type(d['faulting_thread']) is int and 0 <= d['faulting_thread'] < d['threads']['source_count']))
    for image in images:
        keys(image, 'index uuid base size architecture role'); uuid_value(image['uuid'])
        nullable(image['base']); nullable(image['size']); enum(image['architecture'], {'x86_64','arm64','UNKNOWN'}); enum(image['role'], {'game','system','other'})
    for thread in threads:
        keys(thread, 'index triggered frames registers')
        need(thread['triggered'] is None or type(thread['triggered']) is bool)
        frames = coll(thread['frames'], MAX_FRAMES); indexed(frames,thread['frames']['source_count'])
        for frame in frames:
            keys(frame, 'index image_index offset address')
            ix=frame['image_index']; need(ix is None or (type(ix) is int and 0 <= ix < d['images']['source_count']))
            nullable(frame['offset']); nullable(frame['address'])
        r=thread['registers']; keys(r, 'architecture source_count retained_count values')
        enum(r['architecture'], {'arm64','x86_64','rosetta_x86_64','UNKNOWN'})
        allowed = ARM if r['architecture']=='arm64' else X86 if r['architecture'] in ('x86_64','rosetta_x86_64') else frozenset()
        need(type(r['values']) is dict and set(r['values']) <= allowed and all(uint(v) for v in r['values'].values()))
        need(uint(r['source_count']) and r['source_count'] <= MAX_COUNT and type(r['retained_count']) is int and r['retained_count'] == len(r['values']) <= r['source_count'])
    keys(d['ancillary'], ' '.join(ANCILLARY))
    for v in d['ancillary'].values(): nullable(v)
    for key, choices in (('unavailable',UNAVAILABLE),('omitted',REASONS)):
        need(type(d[key]) is list and all(type(v) is str and v in choices for v in d[key]) and len(set(d[key])) == len(d[key]))
    need(OMITTED <= set(d['omitted']))
    need(('faulting_thread' in d['unavailable']) == (d['faulting_thread'] is None))
    need(('threads' in d['unavailable']) == (not threads))
    need(('binary_images' in d['unavailable']) == (not images))
    need(('registers' in d['unavailable']) == (not any(t['registers']['values'] for t in threads)))
    need(('exception_codes' in d['unavailable']) == (not codes))
    need(('numeric_vm' in d['unavailable']) == all(d['ancillary'][k] is None for k in ('vm_page_size','vm_region_count')))
    for label, c, cap in [('thread_limit', d['threads'], MAX_THREADS), ('image_limit',d['images'],MAX_IMAGES),('exception_code_limit',d['exception_codes'],8)]:
        need((label in d['omitted']) == (c['source_count'] is not None and c['source_count'] > cap))
    need(('frame_limit' in d['omitted']) == any(t['frames']['source_count'] > MAX_FRAMES for t in threads))
    if d['format']=='legacy_crash':
        need(d['faulting_thread'] is None and all(c['source_count'] is None for c in (d['threads'],d['images'],d['exception_codes'])) and all(v is None for v in d['ancillary'].values()))
    return json.loads(json.dumps(d))
