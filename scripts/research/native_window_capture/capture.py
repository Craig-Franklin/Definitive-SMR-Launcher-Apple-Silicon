"""Exact-window capture; no import-time work and no game controller."""
from pathlib import Path
import json, math, os, stat, struct, zlib
from ownedleaf import IMAGE as MAX_BYTES, Refused, error
MAX_DIMENSION = 4096
MAX_CHUNKS = 4096

def validate_record(expected, window, process, record):
    if not isinstance(expected,dict) or set(expected)!={'pid','birth_us','executable'} or type(expected['pid']) is not int or expected['pid']<=0 or type(expected['birth_us']) is not int or expected['birth_us']<=0 or not isinstance(expected['executable'],str) or not expected['executable'].startswith('/') or len(expected['executable'])>4096: raise Refused('Invalid primitive process identity')
    if type(window) is not int or window <= 0 or not isinstance(expected,dict) or process != expected:
        raise Refused('Process incarnation mismatch')
    if record.get('window') != window or record.get('pid') != expected['pid'] or record.get('layer') != 0:
        raise Refused('Window owner/identity mismatch')
    if set(record)!={'window','pid','layer','bounds'}: raise Refused('Unexpected window metadata')
    bounds=record.get('bounds',{})
    if set(bounds) != {'x','y','width','height'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in bounds.values()):
        raise Refused('Invalid geometry')
    if not 0 < bounds['width'] <= MAX_DIMENSION or not 0 < bounds['height'] <= MAX_DIMENSION: raise Refused('Unbounded geometry')
    return record

def integer_scale(width,height,points):
    w,h=points
    scale=width/w
    if scale < 1 or not scale.is_integer() or height != h*int(scale): raise Refused('Noninteger or nonuniform native backing scale')
    return int(scale)

def validate_png(path, *, tick=lambda:None):
    """Bounded complete RGB/RGBA8 noninterlaced PNG raster validation, without decoder stubs.

    Unsupported PNG encodings refuse conservatively. Native ImageIO still supplies marker samples.
    """
    s=path.lstat()
    if not stat.S_ISREG(s.st_mode) or not 33 <= s.st_size <= MAX_BYTES: raise Refused('Invalid output file/bounds')
    data=path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n': raise Refused('Invalid PNG signature')
    offset=8; count=0; idat=bytearray(); dims=None; ended=False; closed_idat=False; seen_idat=False
    while offset < len(data):
        tick(); count+=1
        if count > MAX_CHUNKS or offset+12>len(data): raise Refused('PNG chunk work/truncation bound')
        size=struct.unpack_from('>I',data,offset)[0]; end=offset+size+12
        if end>len(data): raise Refused('Truncated PNG chunk')
        kind=data[offset+4:offset+8]; body=memoryview(data)[offset+8:offset+8+size]
        crc=zlib.crc32(body,zlib.crc32(kind)) & 0xffffffff
        if crc != struct.unpack_from('>I',data,end-4)[0]: raise Refused('Invalid PNG CRC')
        if count==1 and (kind!=b'IHDR' or size!=13): raise Refused('Missing initial IHDR')
        if kind==b'IHDR':
            if dims is not None or size!=13: raise Refused('Duplicate/malformed IHDR')
            w,h,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',body)
            if not 0<w<=4096 or not 0<h<=4096 or depth!=8 or color not in (2,6) or (compression,filtering,interlace)!=(0,0,0): raise Refused('Unsupported/bounded IHDR')
            dims=(w,h,3 if color==2 else 4)
        elif kind==b'IDAT':
            if closed_idat: raise Refused('Noncontiguous IDAT')
            seen_idat=True; idat.extend(body)
        elif kind==b'IEND':
            if size or not seen_idat or end!=len(data): raise Refused('Invalid terminal IEND')
            ended=True
        else:
            if seen_idat: closed_idat=True
            if kind[:1].isupper() and kind!=b'PLTE': raise Refused('Unknown critical PNG chunk')
        offset=end
    if not ended or dims is None: raise Refused('Incomplete PNG')
    w,h,bpp=dims; stride=w*bpp; expected=h*(stride+1)
    if expected > 128*1024**2: raise Refused('Raster storage bound')
    try:
        decoder=zlib.decompressobj(); raster=decoder.decompress(idat,expected+1)
        if len(raster)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail: raise Refused('Invalid full PNG raster')
    except zlib.error as exc: raise Refused('Invalid raster compression') from exc
    # Reconstruct every scanline; only two rows retained, all PNG filters validated.
    previous=bytearray(stride)
    for y in range(h):
        tick(); start=y*(stride+1); mode=raster[start]
        if mode>4: raise Refused('Invalid PNG filter')
        row=bytearray(raster[start+1:start+stride+1])
        if mode:
            for x in range(stride):
                a=row[x-bpp] if x>=bpp else 0; b=previous[x]; c=previous[x-bpp] if x>=bpp else 0
                if mode==1: predictor=a
                elif mode==2: predictor=b
                elif mode==3: predictor=(a+b)//2
                else:
                    p=a+b-c; pa,pb,pc=abs(p-a),abs(p-b),abs(p-c)
                    predictor=a if pa<=pb and pa<=pc else b if pb<=pc else c
                row[x]=(row[x]+predictor)&255
        previous=row
    return {'width':w,'height':h,'bytes':s.st_size,'full_raster_validated':True}

def dispose_suspect(output, identity, attempt, ledger):
    """No image open/read/decode here. Exact private path and inode only."""
    try:
        parent=output.parent.lstat(); s=output.lstat()
        if output.name!='window.png' or stat.S_IMODE(parent.st_mode)!=0o700 or not stat.S_ISREG(s.st_mode) or identity!=(s.st_dev,s.st_ino): raise Refused('Suspect image identity unavailable/changed')
        output.unlink(); attempt['disposition']='exact-owned-suspect-deleted'
    except FileNotFoundError:
        attempt['disposition']='absent'
    except Exception as exc:
        attempt['disposition']='unknown-held'; attempt['disposal_error']=error(exc); ledger.held=True

def capture_once(expected, window, directory, process_inspect, window_inspect, *, expected_geometry, leaves, image_inspect):
    if type(expected_geometry) not in (tuple,list) or len(expected_geometry)!=2 or any(type(v) is not int or not 1<=v<=MAX_DIMENSION for v in expected_geometry): raise Refused('Invalid pinned point geometry')
    expected_geometry=tuple(expected_geometry)
    l=leaves.ledger; l.margin(7+4+4+10)
    directory=Path(directory); parent=directory.parent.lstat()
    if directory.exists() or directory.is_symlink() or not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode)!=0o700: raise Refused('Fresh private directory required')
    l.reserve('attempt-metadata',16384); directory.mkdir(mode=0o700); output=directory/'window.png'
    attempt={'process':dict(expected),'window':window,'output':str(output),'expected_geometry':list(expected_geometry),'status':'REFUSED','error':None,'disposition':None}; l.attempts.append(attempt)
    try:
        before=validate_record(expected,window,process_inspect(),window_inspect(window))
        if (before['bounds']['width'],before['bounds']['height']) != expected_geometry: raise Refused('Pinned point geometry mismatch')
        leaves.run('capture',(window,output))
        # Own the newly created directory/name; establish inode with lstat before any pixel access.
        identity=None
        try:
            s=output.lstat()
            if stat.S_ISREG(s.st_mode): identity=(s.st_dev,s.st_ino)
            after=validate_record(expected,window,process_inspect(),window_inspect(window))
            if after!=before: raise Refused('Window changed during capture')
        except Exception:
            dispose_suspect(output,identity,attempt,l); raise
        image=validate_png(output,tick=lambda:l.margin(10))
        scale=integer_scale(image['width'],image['height'],(before['bounds']['width'],before['bounds']['height']))
        decoded=image_inspect(output)
        if not isinstance(decoded,dict) or set(decoded)-{'width','height','left_right_rgba'}: raise Refused('Unexpected decoded metadata')
        if 'left_right_rgba' in decoded:
            samples=decoded['left_right_rgba']
            if not isinstance(samples,list) or len(samples)!=2 or any(not isinstance(row,list) or len(row)!=4 or any(type(v) is not int or not 0<=v<=255 for v in row) for row in samples): raise Refused('Invalid bounded pixel samples')
        if decoded.get('width')!=image['width'] or decoded.get('height')!=image['height']: raise Refused('Full native decode geometry mismatch')
        attempt.update(status='ACCEPTED',before=before,after=after,image=image,scale=scale,decoded=decoded,atomic_isolation_proved=False)
    except Exception as exc:
        attempt['error']=error(exc); raise
    finally:
        (directory/'attempt.json').write_text(json.dumps(attempt,sort_keys=True)+'\n')
    return attempt
