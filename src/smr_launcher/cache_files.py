"""Shared filesystem mechanics for the catalogue and community caches."""

import os
from pathlib import Path
import stat
import uuid


def open_parent(path: Path, *, create: bool = False) -> tuple[Path, int]:
    """Walk an absolute, traversal-checked cache path without following links."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:-1]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            next_fd = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = next_fd
        return path, parent
    except BaseException:
        os.close(parent)
        raise


def replace_bytes(
    path: Path, parent: int, payload: bytes, *, prefix: str, invalid_destination: ValueError
) -> None:
    """Write validated bytes atomically, then close the owned parent descriptor.

    A failure before replacement keeps the old file. A directory fsync failure
    after replacement can leave the complete new file, never a partial document.
    Callers retain their size limits, serialization and domain-specific errors.
    """
    temporary = prefix + uuid.uuid4().hex
    fd = None
    try:
        try:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise invalid_destination
        except FileNotFoundError:
            pass
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)
