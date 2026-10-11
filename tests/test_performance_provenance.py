"""Synthetic provenance batching, byte preservation and operation-local hashes."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import patch
import hashlib
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.application import ApplicationError, LauncherApplication, MapRecord
from smr_launcher.collection import RemoteMap
from smr_launcher.activation import ActivationError
import smr_launcher.application as module
import smr_launcher.activation as activation


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = LauncherApplication.__new__(LauncherApplication)
        self.app.library = self.root / "library"
        self.app.library.mkdir()
        self.app.originals = self.app.library / "original-archives"
        self.app.originals.mkdir()
        self.app.catalogue_file = self.app.library / "catalogue.json"
        self.records = []
        self.protected = self.root / "untouched-sentinel"
        self.protected.write_bytes(b"synthetic protected bytes")

    def add(self, payload=b"archive-one", *, shared=None):
        archive = hashlib.sha256(payload).hexdigest() if shared is None else shared.archive_sha256
        variant = hashlib.sha256(str(len(self.records)).encode()).hexdigest()
        record = MapRecord(
            "Map %s" % len(self.records),
            archive,
            variant,
            "map-" + variant,
            ("scenario.xml",),
            variant,
            "e" * 64,
            variant,
            "Local.7z",
            imported_at="2026-10-11T00:00:00Z",
            recipe_receipt_sha256="f" * 64,
        )
        self.records.append(record)
        path = self.app.originals / (archive + ".7z")
        if shared is None:
            path.write_bytes(payload)
        self.save()
        return (
            record,
            path,
            RemoteMap(
                "Remote_%d.7z" % len(self.records),
                len(payload),
                hashlib.sha1(payload).hexdigest(),
                "2026-01-08",
            ),
        )

    def save(self):
        self.app.catalogue_file.write_text(
            json.dumps(dict(schema=1, maps=[r.as_json() for r in self.records]))
        )

    def before(self):
        return self.app.catalogue_file.read_bytes()

    def test_same_size_mismatch_and_first_duplicate_remote_are_deterministic(self):
        record, path, remote = self.add()
        wrong = replace(remote, name="Mismatch.7z", sha1="a" * 40)
        duplicate = replace(remote, name="Later.7z", archive_modified="2026-02-02")
        with (
            patch.object(
                self.app, "_collection_archive_sha1", wraps=self.app._collection_archive_sha1
            ) as hashing,
            patch.object(module, "_atomic_json", wraps=module._atomic_json) as writing,
        ):
            self.assertEqual(self.app.match_collection_sources((wrong, remote, duplicate)), 1)
        self.assertEqual(hashing.call_count, 1)
        self.assertEqual(writing.call_count, 1)
        (updated,) = self.app.catalogue()
        self.assertEqual(
            updated,
            replace(
                record,
                archive_filename=remote.name,
                source_url=remote.source_url,
                source_label="Internet Archive · verified archive",
                archive_modified=remote.archive_modified,
                archive_sha1=remote.sha1,
            ),
        )
        self.assertEqual(path.read_bytes(), b"archive-one")
        self.assertEqual(self.protected.read_bytes(), b"synthetic protected bytes")

    def test_shared_archive_editions_hash_once_and_write_once(self):
        record, path, remote = self.add()
        self.add(shared=record)
        with (
            patch.object(
                self.app, "_collection_archive_sha1", wraps=self.app._collection_archive_sha1
            ) as hashing,
            patch.object(module, "_atomic_json", wraps=module._atomic_json) as writing,
        ):
            self.assertEqual(self.app.match_collection_sources((remote,)), 2)
        self.assertEqual(hashing.call_count, 1)
        self.assertEqual(writing.call_count, 1)
        self.assertEqual([r.archive_sha1 for r in self.app.catalogue()], [remote.sha1] * 2)
        self.assertEqual(path.read_bytes(), b"archive-one")

    def test_no_match_leaves_catalogue_byte_identical(self):
        _, _, remote = self.add()
        before = self.before()
        with patch.object(module, "_atomic_json") as writing:
            self.assertEqual(
                self.app.match_collection_sources((replace(remote, sha1="b" * 40),)), 0
            )
        writing.assert_not_called()
        self.assertEqual(self.before(), before)

    def test_empty_remote_or_wrong_size_does_not_hash_or_write(self):
        _, _, remote = self.add()
        before = self.before()
        with (
            patch.object(self.app, "_collection_archive_sha1") as hashing,
            patch.object(module, "_atomic_json") as writing,
        ):
            self.assertEqual(self.app.match_collection_sources(()), 0)
            self.assertEqual(
                self.app.match_collection_sources((replace(remote, size=remote.size + 1),)), 0
            )
        hashing.assert_not_called()
        writing.assert_not_called()
        self.assertEqual(self.before(), before)

    def test_missing_and_nonregular_archive_are_skipped(self):
        _, first, remote = self.add()
        _, second, _ = self.add(b"archive-two")
        first.unlink()
        second.unlink()
        second.mkdir()
        before = self.before()
        with patch.object(module, "_atomic_json") as writing:
            self.assertEqual(self.app.match_collection_sources((remote,)), 0)
        writing.assert_not_called()
        self.assertEqual(self.before(), before)

    def test_already_sourced_record_is_not_rehashed_or_rewritten(self):
        _, _, remote = self.add()
        self.assertEqual(self.app.match_collection_sources((remote,)), 1)
        before = self.before()
        with (
            patch.object(self.app, "_collection_archive_sha1") as hashing,
            patch.object(module, "_atomic_json") as writing,
        ):
            self.assertEqual(self.app.match_collection_sources((remote,)), 0)
        hashing.assert_not_called()
        writing.assert_not_called()
        self.assertEqual(self.before(), before)

    def test_hash_cache_does_not_survive_an_operation(self):
        _, path, remote = self.add()
        self.assertEqual(self.app.match_collection_sources((replace(remote, sha1="b" * 40),)), 0)
        path.write_bytes(b"archive-two")
        self.assertEqual(self.app.match_collection_sources((remote,)), 0)
        self.assertEqual(self.app.catalogue()[0].source_url, "")

    def test_symlink_archive_aborts_pending_matches(self):
        _, _, first_remote = self.add()
        _, path, second_remote = self.add(b"archive-two")
        path.unlink()
        path.symlink_to(self.protected)
        before = self.before()
        with self.assertRaises(ActivationError):
            self.app.match_collection_sources((first_remote, second_remote))
        self.assertEqual(self.before(), before)
        self.assertEqual(self.protected.read_bytes(), b"synthetic protected bytes")

    def test_symlink_parent_is_rejected(self):
        _, _, remote = self.add()
        moved = self.app.library / "moved"
        self.app.originals.rename(moved)
        self.app.originals.symlink_to(moved, target_is_directory=True)
        before = self.before()
        with self.assertRaises(ActivationError):
            self.app.match_collection_sources((remote,))
        self.assertEqual(self.before(), before)

    def test_read_failure_aborts_all_pending_matches(self):
        _, _, first = self.add()
        _, _, second = self.add(b"archive-two")
        before = self.before()
        original = self.app._collection_archive_sha1
        calls = []

        def fail_second(path, identity):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("injected read failure")
            return original(path, identity)

        with patch.object(self.app, "_collection_archive_sha1", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.app.match_collection_sources((first, second))
        self.assertEqual(self.before(), before)

    def test_archive_changed_after_hash_aborts_before_commit(self):
        _, path, remote = self.add()
        before = self.before()
        original = self.app._collection_archive_sha1

        def changed(path, identity):
            digest = original(path, identity)
            path.write_bytes(b"archive-two")
            return digest

        with patch.object(self.app, "_collection_archive_sha1", side_effect=changed):
            with self.assertRaises(ApplicationError):
                self.app.match_collection_sources((remote,))
        self.assertEqual(self.before(), before)

    def test_archive_changed_while_reading_is_rejected(self):
        _, path, remote = self.add()
        before = self.before()
        original = hashlib.sha1

        class ChangedDigest:
            def __init__(self):
                self.digest = original()

            def update(self, data):
                self.digest.update(data)
                path.write_bytes(b"archive-two")

            def hexdigest(self):
                return self.digest.hexdigest()

        with patch.object(module.hashlib, "sha1", ChangedDigest):
            with self.assertRaises(ApplicationError):
                self.app.match_collection_sources((remote,))
        self.assertEqual(self.before(), before)

    def test_leaf_replaced_with_symlink_between_stat_and_open_is_rejected(self):
        _, path, remote = self.add()
        before = self.before()
        original = self.app._collection_archive_sha1

        def changed(path, identity):
            path.unlink()
            path.symlink_to(self.protected)
            return original(path, identity)

        with patch.object(self.app, "_collection_archive_sha1", side_effect=changed):
            with self.assertRaises(OSError):
                self.app.match_collection_sources((remote,))
        self.assertEqual(self.before(), before)

    def test_parent_replaced_with_symlink_between_stat_and_open_is_rejected(self):
        _, _, remote = self.add()
        before = self.before()
        original = self.app._collection_archive_sha1

        def changed(path, identity):
            moved = self.app.library / "moved"
            self.app.originals.rename(moved)
            self.app.originals.symlink_to(moved, target_is_directory=True)
            return original(path, identity)

        with patch.object(self.app, "_collection_archive_sha1", side_effect=changed):
            with self.assertRaises(OSError):
                self.app.match_collection_sources((remote,))
        self.assertEqual(self.before(), before)

    def test_archive_disappearing_after_stat_aborts_pending_batch(self):
        _, path, remote = self.add()
        before = self.before()
        original = self.app._collection_archive_sha1

        def changed(path, identity):
            path.unlink()
            return original(path, identity)

        with patch.object(self.app, "_collection_archive_sha1", side_effect=changed):
            with self.assertRaises(FileNotFoundError):
                self.app.match_collection_sources((remote,))
        self.assertEqual(self.before(), before)

    def test_atomic_rename_failure_preserves_catalogue_and_cleans_stage(self):
        _, path, remote = self.add()
        before = self.before()
        with patch.object(activation.os, "replace", side_effect=OSError("injected rename failure")):
            with self.assertRaises(OSError):
                self.app.match_collection_sources((remote,))
        self.assertEqual(self.before(), before)
        self.assertEqual(list(self.app.library.glob("catalogue.json.tmp-*")), [])
        self.assertEqual(path.read_bytes(), b"archive-one")

    def test_atomic_stage_fsync_failure_preserves_catalogue(self):
        _, _, remote = self.add()
        before = self.before()
        with patch.object(activation.os, "fsync", side_effect=OSError("injected fsync failure")):
            with self.assertRaises(OSError):
                self.app.match_collection_sources((remote,))
        self.assertEqual(self.before(), before)
        self.assertEqual(list(self.app.library.glob("catalogue.json.tmp-*")), [])

    def test_post_rename_failure_leaves_complete_batch_not_partial_catalogue(self):
        _, _, first = self.add()
        _, _, second = self.add(b"archive-two")
        with patch.object(
            activation, "_fsync_dir", side_effect=OSError("injected directory fsync failure")
        ):
            with self.assertRaises(OSError):
                self.app.match_collection_sources((first, second))
        self.assertEqual([r.archive_sha1 for r in self.app.catalogue()], [first.sha1, second.sha1])

    def test_concurrent_reconciliation_reloads_after_real_lock(self):
        _, _, remote = self.add()
        entered = Event()
        release = Event()
        second_lock_attempt = Event()
        original_hash = self.app._collection_archive_sha1
        original_flock = module.fcntl.flock
        second_thread = []

        def blocking_hash(path, identity):
            entered.set()
            if not release.wait(5):
                raise AssertionError("first worker was not released")
            return original_hash(path, identity)

        def observed_flock(fd, operation):
            if (
                operation == module.fcntl.LOCK_EX
                and second_thread
                and get_ident() == second_thread[0]
            ):
                second_lock_attempt.set()
            return original_flock(fd, operation)

        def second_operation():
            second_thread.append(get_ident())
            return self.app.match_collection_sources((remote,))

        with (
            patch.object(
                self.app, "_collection_archive_sha1", side_effect=blocking_hash
            ) as hashing,
            patch.object(module.fcntl, "flock", side_effect=observed_flock),
            patch.object(module, "_atomic_json", wraps=module._atomic_json) as writing,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            try:
                first = pool.submit(self.app.match_collection_sources, (remote,))
                self.assertTrue(entered.wait(5))
                second = pool.submit(second_operation)
                self.assertTrue(second_lock_attempt.wait(5))
                self.assertFalse(second.done())
                self.assertEqual(self.app.catalogue()[0].source_url, "")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5), 1)
            self.assertEqual(second.result(timeout=5), 0)
        self.assertEqual(hashing.call_count, 1)
        self.assertEqual(writing.call_count, 1)


if __name__ == "__main__":
    unittest.main()
