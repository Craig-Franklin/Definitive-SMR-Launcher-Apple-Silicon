"""Synthetic cache trust boundaries and last-good preservation."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json
import os

from smr_launcher import collection as c


class CatalogueCacheTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "private" / "catalogue.json"
        self.records = (c.RemoteMap("Alpha.7z", 4, "a" * 40, "2026-01-01"),)
        c.write_cached_catalogue(self.path, self.records)

    def test_roundtrip_local_only(self):
        with patch.object(c, "_opener", side_effect=AssertionError("network forbidden")):
            saved = c.read_cached_catalogue(self.path)
        self.assertEqual(saved.records, self.records)
        self.assertTrue(saved.fetched_at)

    def test_invalid_schema_source_fields_dates_duplicate_names_and_bytes_rejected(self):
        good = json.loads(self.path.read_bytes())
        cases = [dict(good, schema=True), dict(good, schema=2), dict(good, source="https://foreign.invalid"),
                 dict(good, fetched_at="2026-01-01"), dict(good, fetched_at="9999-01-01T00:00:00+00:00"),
                 dict(good, maps="bad"), dict(good, extra=True),
                 dict(good, maps=[dict(good["maps"][0], name="../Map.7z")]),
                 dict(good, maps=[dict(good["maps"][0], size=True)]),
                 dict(good, maps=[dict(good["maps"][0], sha1="bad")]),
                 dict(good, maps=[dict(good["maps"][0], archive_modified="tomorrow")]),
                 dict(good, maps=good["maps"] * 2)]
        for document in cases:
            with self.subTest(document=document):
                self.path.write_text(json.dumps(document))
                self.assertIsNone(c.read_cached_catalogue(self.path))
        for payload in (b'{"schema":1,"schema":1}', b"[", b"\xff", b"x" * (c.MAX_CACHE_BYTES + 1)):
            self.path.write_bytes(payload)
            self.assertIsNone(c.read_cached_catalogue(self.path))
        with patch.object(c, "MAX_CACHED_MAPS", 0):
            self.path.write_text(json.dumps(good))
            self.assertIsNone(c.read_cached_catalogue(self.path))
            with self.assertRaises(c.CollectionError):
                c.write_cached_catalogue(self.path, self.records)

    def test_links_leaf_ancestor_fifo_directory_and_parent_traversal_rejected(self):
        leaf = self.root / "leaf.json"
        leaf.symlink_to(self.path)
        ancestor = self.root / "alias"
        ancestor.symlink_to(self.path.parent, target_is_directory=True)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for path in (leaf, ancestor / self.path.name, fifo, self.root, self.path.parent / ".." / "private" / self.path.name):
            with self.subTest(path=path):
                self.assertIsNone(c.read_cached_catalogue(path))
                with self.assertRaises((OSError, ValueError)):
                    c.write_cached_catalogue(path, self.records)
        self.assertEqual(c.read_cached_catalogue(self.path).records, self.records)

    def test_atomic_replace_or_write_failure_preserves_prior_bytes_and_cleans_stage(self):
        before = self.path.read_bytes()
        newer = (c.RemoteMap("Beta.7z", 5, "b" * 40),)
        for target in ("replace", "fsync"):
            with patch.object(c.os, target, side_effect=OSError("synthetic interrupted write")):
                with self.assertRaises(OSError):
                    c.write_cached_catalogue(self.path, newer)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(tuple(self.path.parent.iterdir()), (self.path,))

    def test_directory_fsync_failure_after_rename_leaves_complete_new_catalogue(self):
        newer = (c.RemoteMap("Beta.7z", 5, "b" * 40),)
        fsync = c.os.fsync
        calls = []
        def fail_after_rename(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError("synthetic directory sync failure after replace")
            fsync(fd)
        with patch.object(c.os, "fsync", side_effect=fail_after_rename):
            with self.assertRaises(OSError):
                c.write_cached_catalogue(self.path, newer)
        self.assertEqual(c.read_cached_catalogue(self.path).records, newer)
        self.assertEqual(tuple(self.path.parent.iterdir()), (self.path,))
