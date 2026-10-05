"""Bounded private activity history, independent of the game transaction journal."""
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import json
import os
import stat

from .activation import _assert_no_symlink_ancestor

MAX_BYTES = 2 * 1024 * 1024


class ActivityLog:
    def __init__(self, library: Path):
        self.path = library / 'activity.jsonl'

    def _open(self, create=False):
        _assert_no_symlink_ancestor(self.path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, (os.O_RDWR | os.O_CREAT if create else os.O_RDONLY) | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise ValueError('Activity history is not an independent regular file')
        return fd

    def append(self, action: str, state: str, detail: str = ''):
        # Messages come from launcher operations; no command environments or credentials.
        record = dict(time=datetime.now(timezone.utc).isoformat(), action=str(action)[:512],
                      state=str(state)[:40], detail=str(detail)[:2000])
        payload = (json.dumps(record, ensure_ascii=False) + '\n').encode()
        fd = self._open(True)
        with os.fdopen(fd, 'r+b') as stream:
            fcntl.flock(fd, fcntl.LOCK_EX)
            stream.seek(0, 2)
            if stream.tell() + len(payload) > MAX_BYTES:
                stream.seek(-min(stream.tell(), MAX_BYTES // 2), 2)
                retained = stream.read().split(b'\n', 1)[-1]
                stream.seek(0)
                stream.write(retained)
                stream.truncate()
            stream.write(payload)
            stream.flush()
            os.fsync(fd)

    def records(self):
        try:
            fd = self._open()
        except FileNotFoundError:
            return ()
        records = []
        with os.fdopen(fd, 'rb') as stream:
            fcntl.flock(fd, fcntl.LOCK_SH)
            size = os.fstat(fd).st_size
            if size > MAX_BYTES:
                stream.seek(size - MAX_BYTES)
                stream.readline()
            for line in stream:
                try:
                    item = json.loads(line)
                    if isinstance(item, dict) and all(isinstance(item.get(k), str) for k in ('time','action','state','detail')):
                        records.append(item)
                except (ValueError, UnicodeError):
                    continue
        return tuple(records)
