"""Synthetic creation races; no user ledger, game data or diagnostics."""

import errno
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from smr_launcher.scenario_evidence import EvidenceError, open_plain


class EvidenceFileCreationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "events.jsonl"
        self.real_open = os.open

    def test_new_file_is_private_regular_and_returned_descriptor_is_usable(self):
        fd = open_plain(self.path, os.O_RDWR | os.O_CREAT)
        try:
            self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            self.assertEqual(stat.S_IMODE(os.fstat(fd).st_mode), 0o600)
            os.write(fd, b"invented fact\n")
        finally:
            os.close(fd)
        self.assertEqual(self.path.read_bytes(), b"invented fact\n")

    def test_competing_creation_preserves_bytes_and_opens_same_parent_once(self):
        calls = []

        def compete(path, flags, mode=0o777, *, dir_fd=None):
            if path == self.path.name:
                calls.append((flags, dir_fd))
                if len(calls) == 1:
                    self.path.write_bytes(b"original fact\n")
                    raise FileExistsError(errno.EEXIST, "Synthetic competing create", path)
            return self.real_open(path, flags, mode, dir_fd=dir_fd)

        with patch("smr_launcher.scenario_evidence.os.open", side_effect=compete):
            fd = open_plain(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND)
        try:
            self.assertEqual(os.read(fd, 100), b"original fact\n")
            os.write(fd, b"next fact\n")
        finally:
            os.close(fd)
        self.assertEqual(self.path.read_bytes(), b"original fact\nnext fact\n")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][0] & os.O_EXCL)
        self.assertFalse(calls[1][0] & (os.O_CREAT | os.O_EXCL))
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertTrue(all(flags & os.O_NOFOLLOW for flags, _ in calls))
        self.assertTrue(all(flags & os.O_NONBLOCK for flags, _ in calls))

    def test_explicit_exclusivity_does_not_open_existing_file(self):
        self.path.write_bytes(b"retained fact\n")
        with self.assertRaises(FileExistsError):
            open_plain(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        self.assertEqual(self.path.read_bytes(), b"retained fact\n")

    def test_competing_unsafe_entry_refuses_without_changing_target_or_leaking_fd(self):
        for kind in ("symlink", "hardlink", "directory", "fifo"):
            with self.subTest(kind=kind):
                target = self.root / ("target-" + kind)
                target.write_bytes(b"retained target\n")
                calls, opened = [], []

                def compete(path, flags, mode=0o777, *, dir_fd=None):
                    if path == self.path.name:
                        calls.append(flags)
                        if len(calls) == 1:
                            if kind == "symlink":
                                self.path.symlink_to(target)
                            elif kind == "hardlink":
                                os.link(target, self.path)
                            elif kind == "directory":
                                self.path.mkdir()
                            else:
                                os.mkfifo(self.path)
                            raise FileExistsError(errno.EEXIST, "Synthetic competing create", path)
                        fd = self.real_open(path, flags, mode, dir_fd=dir_fd)
                        opened.append(fd)
                        return fd
                    return self.real_open(path, flags, mode, dir_fd=dir_fd)

                with patch("smr_launcher.scenario_evidence.os.open", side_effect=compete):
                    with self.assertRaises((EvidenceError, OSError)):
                        open_plain(self.path, os.O_RDWR | os.O_CREAT)
                if kind == "directory":
                    self.path.rmdir()
                else:
                    self.path.unlink()
                self.assertEqual(target.read_bytes(), b"retained target\n")
                self.assertEqual(len(calls), 2)
                for fd in opened:
                    with self.assertRaises(OSError) as error:
                        os.fstat(fd)
                    self.assertEqual(error.exception.errno, errno.EBADF)

    def test_vanished_competing_entry_is_not_recreated(self):
        calls = []

        def vanished(path, flags, mode=0o777, *, dir_fd=None):
            if path == self.path.name:
                calls.append(flags)
                if len(calls) == 1:
                    raise FileExistsError(errno.EEXIST, "Synthetic vanished create", path)
            return self.real_open(path, flags, mode, dir_fd=dir_fd)

        with patch("smr_launcher.scenario_evidence.os.open", side_effect=vanished):
            with self.assertRaises(FileNotFoundError):
                open_plain(self.path, os.O_RDWR | os.O_CREAT)
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.path.exists())

    def test_ambiguous_create_errors_are_not_retried(self):
        for code in (errno.ENOENT, errno.EIO):
            with self.subTest(errno=code):
                calls = []

                def fail(path, flags, mode=0o777, *, dir_fd=None):
                    if path == self.path.name:
                        calls.append(flags)
                        raise OSError(code, "Synthetic open failure", path)
                    return self.real_open(path, flags, mode, dir_fd=dir_fd)

                with patch("smr_launcher.scenario_evidence.os.open", side_effect=fail):
                    with self.assertRaises(OSError) as error:
                        open_plain(self.path, os.O_RDWR | os.O_CREAT)
                self.assertEqual(error.exception.errno, code)
                self.assertEqual(len(calls), 1)
                self.assertFalse(self.path.exists())

    def test_read_only_open_never_creates_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            open_plain(self.path, os.O_RDONLY)
        self.assertFalse(self.path.exists())
