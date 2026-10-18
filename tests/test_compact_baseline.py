"""Characterize existing storage consumers before compact-storage changes."""

from pathlib import Path
import hashlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import test_application as application_fixture
from smr_launcher.activation import ORIGINAL
from smr_launcher.application import ApplicationError
from smr_launcher.variants import assets_manifest_hash


class StorageBaselineTests(unittest.TestCase):
    setUp = application_fixture.ApplicationTests.setUp
    snapshot_files = staticmethod(application_fixture.ApplicationTests.snapshot_files)

    def imported(self):
        self.application.setup()
        return self.application.import_archive(self.archive)

    def test_import_keeps_three_independent_asset_trees_and_offline_archive(self):
        record = self.imported()
        app = self.application
        imported = app.imports / record.imported_directory
        prepared = app._prepared_root(record)
        profile = app.profiles._profile_path(record.profile_id)
        self.assertEqual(assets_manifest_hash(imported), assets_manifest_hash(prepared))
        self.assertEqual(assets_manifest_hash(prepared), assets_manifest_hash(profile))
        self.assertEqual(
            len(
                {
                    (p / "CustomAssets/Example.txt").stat().st_ino
                    for p in (imported, prepared, profile)
                }
            ),
            3,
        )
        self.archive.unlink()
        self.assertEqual(
            app.import_archive(app.originals / (record.archive_sha256 + ".7z")), record
        )

    def test_browsing_and_current_checks_do_not_write_asset_or_profile_trees(self):
        record = self.imported()
        app = self.application
        before = self.snapshot_files(app.library)
        self.assertEqual(app.catalogue(), (record,))
        app.map_metadata(record)
        self.assertEqual(app.scenario_metadata(record)[0]["path"], record.scenarios[0])
        self.assertIsNotNone(app.import_checks(record))
        self.assertIsNone(app.verification_for(record))
        self.assertEqual(self.snapshot_files(app.library), before)

    def test_metadata_timestamps_and_empty_directories_survive_profile_registration(self):
        record = self.imported()
        app = self.application
        prepared = app._prepared_root(record)
        path = prepared / "CustomAssets/Example.txt"
        profile = app.profiles._profile_path(record.profile_id)
        self.assertEqual(
            path.stat().st_mtime_ns, (profile / "CustomAssets/Example.txt").stat().st_mtime_ns
        )
        self.assertTrue((profile / "Saves").is_dir())
        self.assertEqual(list((profile / "Saves").iterdir()), [])

    def test_switch_preserves_future_saves_settings_and_exact_original(self):
        record = self.imported()
        app = self.application
        original = self.snapshot_files(app.profiles.live)
        app.activate(record.profile_id)
        (app.profiles.live / "Saves/future.sav").write_bytes(b"future" * 1000)
        (app.profiles.live / "Settings.ini").write_bytes(b"personal settings")
        app.activate(ORIGINAL)
        self.assertEqual(self.snapshot_files(app.profiles.live), original)
        app.activate(record.profile_id)
        self.assertEqual((app.profiles.live / "Saves/future.sav").read_bytes(), b"future" * 1000)
        self.assertEqual((app.profiles.live / "Settings.ini").read_bytes(), b"personal settings")

    def test_running_or_unknown_game_refuses_storage_mutation(self):
        record = self.imported()
        app = self.application
        before = self.snapshot_files(app.library)
        for running in (True, None):
            self.installation.running = running
            with self.assertRaises(ApplicationError):
                app.activate(record.profile_id)
            with self.assertRaises(ApplicationError):
                app.remove_map(record)
        self.assertEqual(self.snapshot_files(app.library), before)

    def test_removal_retains_save_and_reimport_restores_it(self):
        record = self.imported()
        app = self.application
        save = app.profiles._profile_path(record.profile_id) / "Saves/retained.sav"
        save.write_bytes(b"retained save")
        retained = app.remove_map(record)
        self.assertEqual((retained / save.name).read_bytes(), b"retained save")
        record = app.import_archive(self.archive)
        self.assertEqual(
            (app.profiles._profile_path(record.profile_id) / "Saves" / save.name).read_bytes(),
            b"retained save",
        )

    def test_pending_activation_readonly_refuses_recovery(self):
        record = self.imported()
        app = self.application
        app.profiles.journal.write_text('{"unknown":true}')
        before = self.snapshot_files(app.library)
        with self.assertRaises(RuntimeError):
            app.profiles.recover(recover_pending=False)
        self.assertEqual(self.snapshot_files(app.library), before)
        self.assertEqual(
            hashlib.sha256(self.archive.read_bytes()).hexdigest(), record.archive_sha256
        )

    def test_guarded_play_does_not_recover_pending_removal(self):
        record = self.imported()
        app = self.application
        pending = app.library / "removal.json"
        pending.write_text('{"unknown":true}')
        before = self.snapshot_files(app.library)
        with patch.object(
            app, "_authorize_profile", side_effect=AssertionError("must refuse first")
        ):
            with self.assertRaisesRegex(ApplicationError, "Pending removal"):
                app.play(record.profile_id, launch=lambda: None, mutation_guard=lambda: None)
        self.assertEqual(self.snapshot_files(app.library), before)
