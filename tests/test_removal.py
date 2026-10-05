"""Removal and crash recovery use only synthetic independent profiles."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_application
from smr_launcher import removal, editions
from smr_launcher.application import LauncherApplication, ApplicationError
from smr_launcher.activation import ORIGINAL, ActivationError


class RemovalTests(unittest.TestCase):
    setUp = test_application.ApplicationTests.setUp

    def imported(self):
        self.application.setup()
        record = self.application.import_archive(self.archive)
        self.application.activate(record.profile_id)
        (self.installation.profile_root / 'Saves/map.sav').write_bytes(b'my future save')
        self.application.activate(ORIGINAL)
        return record

    def test_remove_download_and_reimport_restores_independent_saves(self):
        record = self.imported()
        sha1 = hashlib.sha1(self.archive.read_bytes()).hexdigest()
        downloaded = self.application.library / 'downloads' / (sha1 + '.7z')
        downloaded.parent.mkdir(); downloaded.write_bytes(self.archive.read_bytes())
        saved = self.application.remove_map(record)
        self.assertEqual((saved/'map.sav').read_bytes(), b'my future save')
        self.assertFalse(downloaded.exists())
        self.assertFalse((self.application.originals/(record.archive_sha256+'.7z')).exists())
        self.assertFalse((self.application.imports/record.imported_directory).exists())
        self.assertFalse((self.application.prepared/record.prepared_directory).exists())
        self.assertEqual(self.application.catalogue(), ())
        self.assertEqual((self.installation.profile_root/'Saves/stock.sav').read_text(), 'stock save')
        again = self.application.import_archive(self.archive)
        self.assertEqual(again.variant_id, record.variant_id)
        self.application.activate(again.profile_id)
        restored = self.installation.profile_root/'Saves/map.sav'
        self.assertEqual(restored.read_bytes(), b'my future save')
        self.assertNotEqual(restored.stat().st_ino, (saved/'map.sav').stat().st_ino)
        self.assertTrue(self.archive.exists())

    def test_active_and_running_rejected_without_snapshot(self):
        record = self.imported()
        self.installation.running = True
        with self.assertRaises(ApplicationError): self.application.remove_map(record)
        self.installation.running = False
        self.application.activate(record.profile_id)
        with self.assertRaises(removal.RemovalError): self.application.remove_map(record)
        self.assertFalse((self.application.library/'retained-saves').exists())
        self.assertEqual(len(self.application.catalogue()), 1)

    def test_failed_save_copy_never_removes_map(self):
        record = self.imported()
        with patch.object(removal.shutil, 'copytree', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.application.remove_map(record)
        self.assertTrue(self.application.profiles._profile_path(record.profile_id).exists())
        self.assertEqual(len(self.application.catalogue()), 1)
        self.assertFalse((self.application.library/'removal.json').exists())

    def test_resume_after_rename_catalogue_and_partial_cleanup(self):
        for point in ('rename', 'catalogue', 'cleanup'):
            with self.subTest(point=point):
                # Each successful recovery empties the catalogue; reimport same archive.
                if not self.application.profiles.state_file.exists(): self.application.setup()
                record = self.application.import_archive(self.archive)
                self.application.activate(record.profile_id)
                (self.installation.profile_root/'Saves/recover.sav').write_text(point)
                self.application.activate(ORIGINAL)
                if point == 'rename':
                    real = Path.rename
                    count = [0]
                    def interrupt(path, target):
                        result = real(path, target)
                        count[0] += 1
                        if count[0] == 1: raise RuntimeError('power loss')
                        return result
                    guard = patch.object(Path, 'rename', interrupt)
                elif point == 'catalogue':
                    guard = patch.object(removal, '_discard', side_effect=RuntimeError('power loss'))
                else:
                    real = removal._discard
                    def interrupt(path):
                        child = next(path.iterdir())
                        real(child)
                        raise RuntimeError('power loss')
                    guard = patch.object(removal, '_discard', interrupt)
                with guard:
                    with self.assertRaises(RuntimeError): self.application.remove_map(record)
                reopened = LauncherApplication(self.installation, self.application.library)
                self.assertEqual(reopened.active(), ORIGINAL)
                self.assertEqual(reopened.catalogue(), ())
                self.assertFalse((reopened.library/'removal.json').exists())
                self.assertEqual((removal.retained_saves(reopened, record.variant_id)/'recover.sav').read_text(), point)

    def test_missing_or_changed_snapshot_blocks_recovery(self):
        record = self.imported()
        with patch.object(removal, '_discard', side_effect=RuntimeError('power loss')):
            with self.assertRaises(RuntimeError): self.application.remove_map(record)
        saved = removal.retained_saves(self.application, record.variant_id)
        (saved/'map.sav').write_text('changed')
        with self.assertRaises(removal.RemovalError): self.application.active()
        self.assertTrue((self.application.library/'removal.json').exists())
        index = saved.parent.parent/(record.variant_id+'.json')
        index.unlink()
        with self.assertRaises(removal.RemovalError): self.application.active()

    def test_symlink_and_bad_storage_identity_are_rejected(self):
        record = self.imported()
        icon = self.application.icons/(record.variant_id+'.png')
        icon.parent.mkdir(exist_ok=True)
        icon.symlink_to(self.archive)
        with self.assertRaises(ActivationError): self.application.remove_map(record)
        self.assertTrue(self.archive.exists())
        icon.unlink()
        hostile = replace(record, prepared_directory='../outside')
        from smr_launcher.activation import _atomic_json
        _atomic_json(self.application.catalogue_file, dict(schema=1, maps=[hostile.as_json()]))
        with self.assertRaises(removal.RemovalError): self.application.remove_map(hostile)
        self.assertTrue(self.application.profiles._profile_path(record.profile_id).exists())

    def test_original_reimport_with_edition_and_last_shared_archive_removal(self):
        record = self.imported()
        with patch.object(editions, 'SUPPORTED_GAME', self.installation.executable_sha256):
            edition = self.application.create_edition(record, editor=False, difficulty=True)
        self.application.remove_map(record)
        archive = self.application.originals/(record.archive_sha256+'.7z')
        imported = self.application.imports/record.imported_directory
        self.assertTrue(archive.exists()); self.assertTrue(imported.exists())
        again = self.application.import_archive(self.archive)
        self.assertEqual(again.variant_id, record.variant_id)
        self.assertNotEqual(again.variant_id, edition.variant_id)
        self.application.remove_map(again)
        self.application.remove_map(edition)
        self.assertFalse(archive.exists()); self.assertFalse(imported.exists())

    def test_changed_archive_does_not_receive_other_variant_saves(self):
        record = self.imported(); self.application.remove_map(record)
        from io import BytesIO
        import tarfile
        with tarfile.open(self.archive, 'w') as stream:
            data=b'<Changed/>'
            item=tarfile.TarInfo('UserMaps/Example/RRT_Scenario_User_Example.xml'); item.size=len(data)
            stream.addfile(item, BytesIO(data))
        other = self.application.import_archive(self.archive)
        self.assertNotEqual(other.variant_id, record.variant_id)
        self.application.activate(other.profile_id)
        self.assertEqual(list((self.installation.profile_root/'Saves').iterdir()), [])


if __name__ == '__main__': unittest.main()
