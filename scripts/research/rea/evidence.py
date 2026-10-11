#!/usr/bin/env python3
"""Private synthetic evidence store and fail-closed publication projection."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
MAX_BYTES = 1024 * 1024
CASE = 'synthetic-bounded-index'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def identity():
    return {'case': CASE, 'target': digest((HERE / 'fixture.c').read_bytes()),
            'tool': digest((HERE / '../../../docs/rea-research/toolchain.json').resolve().read_bytes())}

def exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError('Invalid schema')

def load(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate field')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)

def root_check(root):
    root = Path(root)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('Private root must be absolute')
    for part in [root, *root.parents]:
        if part.is_symlink():
            raise ValueError('Symlink refused')
    root = root.resolve()
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    if subprocess.run(['git', '-C', str(ancestor), 'rev-parse', '--git-dir'],
                      capture_output=True).returncode == 0:
        raise ValueError('Private root must be outside Git')
    return root

def read_file(path, maximum=MAX_BYTES):
    # O_NOFOLLOW also rejects a final-component symlink at open time.
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError('Symlink refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    import stat
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Regular file required')
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('Size limit exceeded')
    return data

def create(path, data):
    # Exclusive creation preserves all existing outputs, including symlinks.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)

def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()

def validate_manifest(value):
    exact(value, ['schema', 'identity', 'entries'])
    if type(value['schema']) is not int or value['schema'] != 1 or value['identity'] != identity():
        raise ValueError('Case, target or tool build mismatch')
    if type(value['entries']) is not list or len(value['entries']) > 128:
        raise ValueError('Entry limit exceeded')
    seen = set()
    for entry in value['entries']:
        exact(entry, ['sha256', 'bytes', 'kind'])
        sha = entry['sha256']
        if type(sha) is not str or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError('Invalid digest')
        if sha in seen or entry['kind'] not in ('synthetic-query', 'synthetic-receipt'):
            raise ValueError('Invalid entry')
        seen.add(sha)
        if type(entry['bytes']) is not int or not 0 <= entry['bytes'] <= MAX_BYTES:
            raise ValueError('Invalid length')
    return value

class Store:
    def __init__(self, root):
        self.root = root_check(root)
    def index(self, manifest='index.json'):
        self.manifest_name(manifest)
        value = validate_manifest(load(read_file(self.root / manifest, 65536)))
        for entry in value['entries']:
            data = read_file(self.root / entry['sha256'])
            if digest(data) != entry['sha256'] or len(data) != entry['bytes']:
                raise ValueError('Evidence digest mismatch')
        return value
    def manifest_name(self, name):
        if name != 'index.json':
            raise ValueError('Only index.json is supported')
        return name
    def ingest(self, relative, kind):
        if kind not in ('synthetic-query', 'synthetic-receipt'):
            raise ValueError('Unknown kind')
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or not path.parts:
            raise ValueError('Evidence must be inside private root')
        data = read_file(self.root / path)
        sha = digest(data)
        destination = self.root / sha
        if destination.exists() or destination.is_symlink():
            if read_file(destination) != data:
                raise ValueError('Existing digest object mismatch')
        else:
            create(destination, data)
        return {'sha256': sha, 'bytes': len(data), 'kind': kind}
    def retrieve(self, sha, offset=0, length=4096):
        entries = self.index('index.json')['entries']
        if not any(e['sha256'] == sha for e in entries):
            raise ValueError('Digest is not indexed')
        if type(offset) is not int or type(length) is not int or offset < 0 or not 1 <= length <= 4096:
            raise ValueError('Retrieval bounds invalid')
        return read_file(self.root / sha)[offset:offset + length]
    def report(self, approval, human_reviewed=False):
        self.index('index.json')
        exact(approval, ['schema', 'case', 'decision', 'finding'])
        if (type(approval['schema']) is not int or approval['schema'] != 1 or approval['case'] != CASE
            or approval['decision'] != 'approved-synthetic-summary'
            or approval['finding'] not in ('cache-unproven', 'tooling-validated') or not human_reviewed):
            raise ValueError('Explicit reviewed synthetic summary required')
        return {'schema': 1, 'case': CASE, 'decision': approval['decision'],
                'finding': approval['finding'], 'scope': 'original-synthetic-only',
                'legal_clearance': False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('index')
    add.add_argument('input', nargs='+')
    add.add_argument('--kind', choices=['synthetic-query', 'synthetic-receipt'], required=True)
    get = sub.add_parser('retrieve')
    get.add_argument('sha256')
    get.add_argument('--offset', type=int, default=0)
    get.add_argument('--length', type=int, default=4096)
    get.add_argument('--output', required=True)
    publish = sub.add_parser('report')
    publish.add_argument('--approval', required=True)
    publish.add_argument('--human-reviewed', action='store_true')
    publish.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        store = Store(args.private_root)
        if args.command == 'index':
            if len(args.input) > 128:
                raise ValueError('Entry limit exceeded')
            value = {'schema': 1, 'identity': identity(),
                     'entries': [store.ingest(p, args.kind) for p in args.input]}
            validate_manifest(value)
            create(store.root / 'index.json', encode(value))
        elif args.command == 'retrieve':
            output = Path(args.output)
            if not output.is_absolute() or '..' in output.parts or not output.is_relative_to(store.root):
                raise ValueError('Raw retrieval output must stay private')
            root_check(output.parent)
            create(output, store.retrieve(args.sha256, args.offset, args.length))
        else:
            approval = Path(args.approval)
            if approval.is_absolute() or '..' in approval.parts:
                raise ValueError('Approval must stay private')
            report = store.report(load(read_file(store.root / approval, 4096)), args.human_reviewed)
            output = Path(args.output).absolute()
            for part in output.parents:
                if part.is_symlink():
                    raise ValueError('Symlink refused')
            create(output, encode(report))
    except (ValueError, OSError, KeyError, TypeError):
        parser.exit(2, 'Refused: invalid identity, schema, path, bounds or existing output\n')

if __name__ == '__main__':
    main()
