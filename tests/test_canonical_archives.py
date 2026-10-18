"""Archive preservation characterization and canonical reuse with synthetic packages."""

from unittest.mock import patch
import hashlib
import unittest

import test_application
from smr_launcher import application as application_module
from smr_launcher.collection import RemoteMap


# Reuse only fixture setup, keeping the runnable scope explicit.
class ArchivePreservationTests(unittest.TestCase):
    setUp = test_application.ApplicationTests.setUp

    def remote_download(self):
        payload = self.archive.read_bytes()
        remote = RemoteMap("Example.7z", len(payload), hashlib.sha1(payload).hexdigest())
        directory = self.application.library / "downloads"
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / (remote.sha1 + ".7z")
        source.write_bytes(payload)
        return remote, source

    def test_preserved_archive_is_independent_readonly_and_bad_existing_copy_refused(self):
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        path = self.application._preserve_archive(self.archive, digest)
        self.assertEqual(path.read_bytes(), self.archive.read_bytes())
        self.assertNotEqual(path.stat().st_ino, self.archive.stat().st_ino)
        self.assertEqual(path.stat().st_mode & 0o222, 0)
        path.chmod(0o600)
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(application_module.ApplicationError, "differs"):
            self.application._preserve_archive(self.archive, digest)
        self.assertTrue(self.archive.exists())

    def test_catalogue_failure_retains_download_and_verified_canonical_archive(self):
        self.application.setup()
        remote, source = self.remote_download()
        real_write = application_module._atomic_json

        def write(path, value):
            if path == self.application.catalogue_file:
                raise OSError("catalogue commit failed")
            return real_write(path, value)

        with patch.object(application_module, "_atomic_json", write):
            with self.assertRaisesRegex(OSError, "catalogue commit failed"):
                self.application.import_archive(
                    source, original_filename=remote.name, remote=remote
                )
        self.assertTrue(source.exists())
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(
            (self.application.originals / (digest + ".7z")).read_bytes(), source.read_bytes()
        )
        self.assertEqual(self.application.catalogue(), ())

    def test_archive_source_drift_never_publishes_canonical(self):
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        original = application_module._digest

        def changed(path):
            if path == self.archive:
                return "f" * 64
            return original(path)

        with patch.object(application_module, "_digest", changed):
            with self.assertRaisesRegex(application_module.ApplicationError, "changed"):
                self.application._preserve_archive(self.archive, digest)
        self.assertFalse((self.application.originals / (digest + ".7z")).exists())
        self.assertEqual(list(self.application.originals.glob(".archive-*")), [])

    def test_committed_import_discards_only_its_verified_download_then_reuses_offline(self):
        self.application.setup()
        remote, source = self.remote_download()
        record = self.application.import_archive(
            source, original_filename=remote.name, remote=remote
        )
        self.assertFalse(source.exists())
        canonical = self.application.originals / (record.archive_sha256 + ".7z")
        with patch("smr_launcher.collection._opener", side_effect=AssertionError("network")):
            self.assertEqual(self.application.download_archive(remote), canonical)
            again = self.application.import_archive(
                canonical, original_filename=remote.name, remote=remote
            )
        self.assertEqual(again.variant_id, record.variant_id)
        self.assertTrue(canonical.exists())
        self.assertTrue(self.archive.exists())

    def test_unknown_canonical_without_catalogue_is_verified_and_reused(self):
        self.application.setup()
        remote, source = self.remote_download()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        canonical = self.application._preserve_archive(source, digest)
        source.unlink()
        with patch("smr_launcher.collection._opener", side_effect=AssertionError("network")):
            self.assertEqual(self.application.download_archive(remote), canonical)
        canonical.chmod(0o600)
        canonical.write_bytes(b"x" * remote.size)
        with patch("smr_launcher.collection._opener", side_effect=AssertionError("network")):
            with self.assertRaises(application_module.ApplicationError):
                self.application.download_archive(remote)

    def test_duplicate_import_cleans_verified_download_but_keeps_shared_archive(self):
        self.application.setup()
        remote, source = self.remote_download()
        record = self.application.import_archive(
            source, original_filename=remote.name, remote=remote
        )
        source.write_bytes(self.archive.read_bytes())
        again = self.application.import_archive(
            source, original_filename=remote.name, remote=remote
        )
        self.assertEqual(record, again)
        self.assertFalse(source.exists())
        self.assertTrue((self.application.originals / (record.archive_sha256 + ".7z")).exists())

    def test_import_does_not_delete_external_source_even_with_collection_identity(self):
        self.application.setup()
        remote, source = self.remote_download()
        external = self.root / "external.7z"
        external.write_bytes(source.read_bytes())
        self.application.import_archive(external, original_filename=remote.name, remote=remote)
        self.assertTrue(external.exists())
        self.assertTrue(source.exists())

    def test_bulk_download_uses_canonical_resolver_without_network(self):
        from threading import Event
        from smr_launcher.collection import download_all_maps

        self.application.setup()
        remote, source = self.remote_download()
        self.application.import_archive(source, original_filename=remote.name, remote=remote)
        with patch("smr_launcher.collection._opener", side_effect=AssertionError("network")):
            result = download_all_maps(
                (remote,),
                self.application.library / "downloads",
                lambda *args: None,
                Event(),
                downloader=self.application.download_archive,
                importer=lambda item, path: self.application.import_archive(
                    path, original_filename=item.name, remote=item
                ),
            )
        self.assertEqual(result, (1, 0, 0))
        self.assertFalse(source.exists())

    def test_duplicate_cleanup_failure_retains_committed_catalogue_and_retry_archive(self):
        from pathlib import Path

        self.application.setup()
        remote, source = self.remote_download()
        real_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == source:
                raise PermissionError("download cleanup blocked")
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", unlink):
            with self.assertRaisesRegex(PermissionError, "cleanup blocked"):
                self.application.import_archive(
                    source, original_filename=remote.name, remote=remote
                )
        self.assertEqual(len(self.application.catalogue()), 1)
        self.assertTrue(source.exists())
        self.application.import_archive(source, original_filename=remote.name, remote=remote)
        self.assertFalse(source.exists())
