"""Independent map asset copies, using native macOS copy-on-write when supported."""

from pathlib import Path
import ctypes
import errno
import os
import shutil
import stat
import sys
import tempfile


def _clone(source_fd: int, directory_fd: int, name: bytes) -> None:
    if sys.platform != "darwin":
        raise OSError(errno.ENOTSUP, "Native asset cloning requires macOS")
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = library.fclonefileat
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    function.restype = ctypes.c_int
    # CLONE_NOOWNERCOPY | CLONE_ACL: independent metadata, including source ACLs.
    if function(source_fd, directory_fd, name, 0x0002 | 0x0004):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def _ordinary_copy(source_fd: int, destination: Path) -> None:
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        if sys.platform == "darwin":
            library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            function = library.fcopyfile
            function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            function.restype = ctypes.c_int
            # COPYFILE_ALL copies data, stat, ACLs and extended attributes without cloning.
            if function(source_fd, fd, None, 0x000F):
                number = ctypes.get_errno()
                raise OSError(number, os.strerror(number))
        else:
            with (
                os.fdopen(os.dup(source_fd), "rb") as source,
                os.fdopen(os.dup(fd), "wb") as output,
            ):
                shutil.copyfileobj(source, output)
    finally:
        os.close(fd)


def _identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_nlink,
        info.st_size,
        info.st_mode,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def copy_asset(source, destination):
    """Copy an independent regular file; publish exclusively after source readback.

    Unsupported/cross-volume clones use ordinary copying. Permission, I/O and
    capacity errors propagate. Private scratch output makes partial failures safe
    without ever removing an existing destination. Callers own tree publication.
    """
    from .activation import _assert_no_symlink_ancestor, _canonical_directory_path

    source, destination = Path(source), Path(destination)
    if sys.platform == "darwin":
        source = _canonical_directory_path(source.parent) / source.name
        destination = _canonical_directory_path(destination.parent) / destination.name
    _assert_no_symlink_ancestor(source)
    _assert_no_symlink_ancestor(destination)
    if destination.exists():
        raise FileExistsError(errno.EEXIST, "Asset destination already exists", str(destination))
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OSError(errno.EINVAL, "Asset source must be an independent regular file")
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if _identity(os.fstat(fd)) != _identity(before):
            raise OSError(errno.EIO, "Asset source changed before copying")
        with tempfile.TemporaryDirectory(
            prefix=".asset-copy-", dir=destination.parent
        ) as temporary:
            directory = Path(temporary)
            staged = directory / "asset"
            directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                try:
                    _clone(fd, directory_fd, b"asset")
                except OSError as exc:
                    if exc.errno not in (errno.ENOTSUP, errno.EXDEV, errno.ENOSYS):
                        raise
                    # Failed native calls must not leave ambiguous partial output.
                    if staged.exists():
                        raise OSError(errno.EIO, "Failed asset clone left partial output") from exc
                    _ordinary_copy(fd, staged)
            finally:
                os.close(directory_fd)
            shutil.copystat(source, staged, follow_symlinks=False)
            copied = staged.stat()
            if (
                _identity(os.fstat(fd)) != _identity(before)
                or _identity(source.lstat()) != _identity(before)
                or copied.st_nlink != 1
                or copied.st_ino == before.st_ino
                or copied.st_size != before.st_size
                or copied.st_mtime_ns != before.st_mtime_ns
                or stat.S_IMODE(copied.st_mode) != stat.S_IMODE(before.st_mode)
            ):
                raise OSError(errno.EIO, "Asset source or copied metadata changed during copying")
            # link is an exclusive publish, never a replacement for independent data.
            # The temporary name is removed before the caller can validate the tree.
            os.link(staged, destination)
    finally:
        os.close(fd)
    return str(destination)
