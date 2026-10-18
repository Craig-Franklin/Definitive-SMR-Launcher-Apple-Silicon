"""Compact storage through the real application consumers, using synthetic maps."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import test_application as fixture
from smr_launcher.activation import ORIGINAL
from smr_launcher.application import ApplicationError
from smr_launcher.cold_storage import ColdStorageError


class CompactLibraryTests(unittest.TestCase):
    setUp = fixture.ApplicationTests.setUp
    snapshot_files = staticmethod(fixture.ApplicationTests.snapshot_files)
    compatibility_rule = fixture.ApplicationTests.compatibility_rule

    def compact(self):
        app = self.application
        app.setup()
        record = app.import_archive(self.archive)
        app.profiles._profile_path(record.profile_id).joinpath("Saves/future.sav").write_bytes(
            b"future" * 10000
        )
        plan = app.plan_compaction()
        app.compact_library(plan)
        return record

    def test_compaction_preserves_original_archive_and_all_cold_browsing_is_readonly(self):
        record = self.compact()
        app = self.application
        before = self.snapshot_files(app.library)
        self.assertFalse(app._prepared_root(record).exists())
        self.assertFalse(app.profiles._profile_path(record.profile_id).exists())
        self.assertEqual(app.catalogue(), (record,))
        app.map_metadata(record)
        self.assertEqual(app.scenario_metadata(record)[0]["path"], record.scenarios[0])
        self.assertIsNone(app.import_checks(record))
        self.assertIsNone(app.verification_for(record))
        self.assertTrue(app._is_original_variant(record))
        self.assertEqual(self.snapshot_files(app.library), before)
        self.assertTrue((app.originals / (record.archive_sha256 + ".7z")).is_file())

    def test_cold_activation_roundtrip_and_working_set_eviction_preserve_saves_settings(self):
        record = self.compact()
        app = self.application
        app.activate(record.profile_id)
        self.assertEqual((app.profiles.live / "Saves/future.sav").read_bytes(), b"future" * 10000)
        (app.profiles.live / "Settings.ini").write_bytes(b"personal")
        app.activate(ORIGINAL)
        self.assertFalse(app.profiles._profile_path(record.profile_id).exists())
        self.assertFalse(app._prepared_root(record).exists())
        app.activate(record.profile_id)
        self.assertEqual((app.profiles.live / "Settings.ini").read_bytes(), b"personal")
        self.assertEqual((app.profiles.live / "Saves/future.sav").read_bytes(), b"future" * 10000)

    def test_stale_plan_or_running_game_never_enables_or_compacts_library(self):
        app = self.application
        app.setup()
        record = app.import_archive(self.archive)
        plan = app.plan_compaction()
        save = app.profiles._profile_path(record.profile_id) / "Saves/new.sav"
        save.write_bytes(b"future")
        with self.assertRaises((ApplicationError, ColdStorageError)):
            app.compact_library(plan)
        self.assertTrue(save.exists())
        self.assertFalse((app.library / "compact-library.json").exists())
        self.installation.running = True
        with self.assertRaises(ApplicationError):
            app.plan_compaction()

    def test_unknown_profiles_and_explicit_pins_are_excluded_and_quantified(self):
        app = self.application
        app.setup()
        record = app.import_archive(self.archive)
        unknown = app.profiles.profiles / "unknown"
        unknown.mkdir()
        (unknown / "private").write_bytes(b"preserve")
        pin = str(app._prepared_root(record).relative_to(app.library))
        plan = app.plan_compaction(exclude=(pin,))
        excluded = {entry["path"]: entry for entry in plan["excluded"]}
        self.assertIn("managed/profiles/unknown", excluded)
        self.assertEqual(excluded["managed/profiles/unknown"]["logical_bytes"], 8)
        self.assertIn(pin, excluded)
        app.compact_library(plan)
        self.assertTrue((unknown / "private").exists())
        self.assertTrue(app._prepared_root(record).exists())

    def test_guarded_play_and_readonly_recipe_paths_never_unpack(self):
        record = self.compact()
        app = self.application
        before = self.snapshot_files(app.library)
        with self.assertRaisesRegex(
            (ApplicationError, ColdStorageError), "prepare|Prepare|cold|Cold"
        ):
            app.play(record.profile_id, launch=lambda: None, mutation_guard=lambda: None)
        with self.assertRaises((ApplicationError, ColdStorageError, ValueError)):
            app.create_recipe_edition(record, "unknown", reuse_only=True)
        self.assertEqual(self.snapshot_files(app.library), before)

    def test_remove_cold_map_and_reimport_restores_retained_save(self):
        record = self.compact()
        app = self.application
        retained = app.remove_map(record)
        self.assertEqual((retained / "future.sav").read_bytes(), b"future" * 10000)
        rebuilt = app.import_archive(self.archive)
        app.activate(rebuilt.profile_id)
        self.assertEqual((app.profiles.live / "Saves/future.sav").read_bytes(), b"future" * 10000)

    def test_cancellation_during_prepare_does_not_switch_or_lose_cold_tree(self):
        record = self.compact()
        app = self.application
        with self.assertRaisesRegex(ColdStorageError, "cancelled"):
            app.prepare_map(record, cancelled=lambda: True)
        self.assertEqual(app.active(), ORIGINAL)
        app.prepare_map(record)
        self.assertTrue(app.profiles._profile_path(record.profile_id).is_dir())

    def test_future_import_is_cold_and_offline_duplicate_does_not_unpack_collection(self):
        record = self.compact()
        app = self.application
        before = self.snapshot_files(app.library)
        self.assertEqual(app.import_archive(self.archive), record)
        self.assertEqual(self.snapshot_files(app.library), before)
        self.archive.unlink()
        archive = app.originals / (record.archive_sha256 + ".7z")
        self.assertEqual(app.import_archive(archive), record)
        self.assertFalse(app.profiles._profile_path(record.profile_id).exists())

    def test_interrupted_compaction_resumes_exact_sealed_plan(self):
        app = self.application
        app.setup()
        record = app.import_archive(self.archive)
        plan = app.plan_compaction()

        def stop(point):
            if point == "after_rename":
                raise RuntimeError("interruption")

        app.cold_storage.fault_hook = stop
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            app.compact_library(plan)
        app.cold_storage.fault_hook = lambda point: None
        app.compact_library(plan)
        app.activate(record.profile_id)
        self.assertEqual(
            (app.profiles.live / "CustomAssets/Example.txt").read_bytes(), b"map asset"
        )

    def test_rollback_restores_all_exact_initial_trees_and_disables_policy(self):
        app = self.application
        app.setup()
        app.import_archive(self.archive)
        plan = app.plan_compaction()
        before = {tree["path"]: tree["entries"] for tree in plan["trees"]}
        app.compact_library(plan)
        app.rollback_compaction(plan)
        self.assertFalse((app.library / "compact-library.json").exists())
        for path, entries in before.items():
            self.assertEqual(app.cold_storage.inspect(app.library / path), entries)

    def test_removing_cold_map_keeps_canonical_archive_referenced_by_other_trees(self):
        record = self.compact()
        app = self.application
        canonical = app.originals / (record.archive_sha256 + ".7z")
        app.remove_map(record)
        self.assertTrue(canonical.exists())

    def test_cold_scenario_browsing_uses_small_presentation_bytes_without_streaming_archives(self):
        record = self.compact()
        app = self.application
        with patch(
            "smr_launcher.archive_references.stream_members",
            side_effect=AssertionError("no archive scan while browsing"),
        ):
            self.assertEqual(app.scenario_metadata(record)[0]["path"], record.scenarios[0])
            self.assertEqual(
                app.cold_storage.read_file(app._prepared_root(record), record.scenarios[0], 1024),
                b"<Scenario/>",
            )

    def test_cancelled_migration_rolls_back_without_losing_any_tree(self):
        app = self.application
        app.setup()
        app.import_archive(self.archive)
        plan = app.plan_compaction()
        with self.assertRaisesRegex(ColdStorageError, "cancelled"):
            app.compact_library(plan, cancelled=lambda: True)
        app.rollback_compaction(plan)
        for tree in plan["trees"]:
            self.assertEqual(app.cold_storage.inspect(app.library / tree["path"]), tree["entries"])

    def test_rollback_refuses_new_save_without_changing_any_tree(self):
        app = self.application
        app.setup()
        record = app.import_archive(self.archive)
        plan = app.plan_compaction()
        app.compact_library(plan)
        app.prepare_map(record)
        (app.profiles._profile_path(record.profile_id) / "Saves/new.sav").write_bytes(
            b"keep new save"
        )
        before = self.snapshot_files(app.library)
        with self.assertRaisesRegex(ColdStorageError, "changed"):
            app.rollback_compaction(plan)
        self.assertEqual(self.snapshot_files(app.library), before)

    def test_cold_play_uses_real_switch_with_synthetic_dispatch_and_cancellable_preparation(self):
        (self.installation.profile_root / "Settings.ini").write_bytes(b"[User Settings]\n")
        record = self.compact()
        app = self.application
        calls, progress = [], []

        def launched():
            calls.append(app.profiles._state()["active"])
            self.installation.running = True

        result = app.play(
            record.profile_id, launch=launched, progress=lambda *value: progress.append(value)
        )
        self.assertEqual(result, record.profile_id)
        self.assertEqual(calls, [record.profile_id])
        self.assertTrue(progress)
        self.assertFalse(app._prepared_root(record).exists())
        self.installation.running = False
        app.activate(ORIGINAL)
        self.assertEqual((app.profiles.live / "Saves/stock.sav").read_bytes(), b"stock save")
        calls.clear()
        with self.assertRaisesRegex(ColdStorageError, "cancelled"):
            app.play(record.profile_id, launch=launched, cancelled=lambda: True)
        self.assertEqual(calls, [])
        self.assertEqual(app.profiles._state()["active"], ORIGINAL)

    def test_explicit_unmatched_profile_inclusion_preserves_it_when_it_becomes_active(self):
        app = self.application
        app.setup()
        record = app.import_archive(self.archive)
        extra = app.profiles.register_variant("f" * 64, app._prepared_root(record))
        plan = app.plan_compaction(include_profiles=(extra,))
        app.compact_library(plan)
        app.profiles.switch(extra)
        with app._locked():
            app._cool_library()
        self.assertEqual(app.profiles._state()["active"], extra)

    def test_future_import_registers_cold_profile_without_copying_complete_prepared_tree(self):
        record = self.compact()
        app = self.application
        app.remove_map(record)
        with patch.object(
            app.profiles, "_copytree", side_effect=AssertionError("no eager profile clone")
        ):
            rebuilt = app.import_archive(self.archive)
        self.assertFalse(app.profiles._profile_path(rebuilt.profile_id).exists())
        app.activate(rebuilt.profile_id)
        self.assertEqual((app.profiles.live / "Saves/future.sav").read_bytes(), b"future" * 10000)

    def test_unknown_preservation_pin_refuses_instead_of_silently_compacting_it(self):
        app = self.application
        app.setup()
        app.import_archive(self.archive)
        before = self.snapshot_files(app.library)
        with self.assertRaisesRegex(ColdStorageError, "pin"):
            app.plan_compaction(exclude=("prepared/" + "0" * 32,))
        self.assertEqual(self.snapshot_files(app.library), before)

    def test_r3_distinct_new_archive_is_indexed_before_cold_profile_registration(self):
        from io import BytesIO
        import hashlib
        import tarfile

        self.compact()
        app = self.application
        archive = self.root / "BrandNew.tar"
        asset = b"brand new canonical asset" * 10000
        with tarfile.open(archive, "w") as stream:
            for name, data in (
                ("UserMaps/New/RRT_Scenario_New.xml", b"<Scenario/>"),
                ("CustomAssets/Unique.bin", asset),
            ):
                item = tarfile.TarInfo(name)
                item.size = len(data)
                stream.addfile(item, BytesIO(data))
        with patch.object(app.profiles, "_copytree", side_effect=AssertionError("no eager clone")):
            record = app.import_archive(archive)
        digest = hashlib.sha256(asset).hexdigest()
        self.assertIn(digest, app.cold_storage.references())
        self.assertFalse(app.cold_storage._blob(digest).exists())
        self.assertFalse(app.profiles._profile_path(record.profile_id).exists())
        app.activate(record.profile_id)
        self.assertEqual((app.profiles.live / "CustomAssets/Unique.bin").read_bytes(), asset)
        app.activate(ORIGINAL)
        self.assertTrue((app.originals / (record.archive_sha256 + ".7z")).is_file())

    def test_r4_named_recipe_creation_evicts_previous_working_set(self):
        original, rule, binding, _stock = fixture.ApplicationTests.compiled_fixture(self)
        app = self.application
        import io
        import tarfile

        other_archive = self.archive.with_name("Other.tar")
        with tarfile.open(other_archive, "w") as archive:
            for name, data in (
                ("CustomAssets/other.bin", b"other asset"),
                ("UserMaps/RRT_Scenario_Other.xml", b"<RRTScenario/>"),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        other = app.import_archive(other_archive)
        app.compact_library(app.plan_compaction())
        app.prepare_map(other)
        self.assertTrue(app._prepared_root(other).exists())
        with patch(
            "smr_launcher.recipe_service.resolve_bundled_recipe", return_value=((rule,), binding)
        ):
            edition = app.create_recipe_edition(original, binding.recipe_id)
        for cold in (other, original):
            self.assertFalse(app._prepared_root(cold).exists())
            self.assertFalse(app.profiles._profile_path(cold.profile_id).exists())
        self.assertTrue(app._prepared_root(edition).exists())
        self.assertTrue(app.profiles._profile_path(edition.profile_id).exists())

    def test_r5_named_recipe_reuse_materializes_exact_existing_edition_and_saves(self):
        original, rule, binding, _stock = fixture.ApplicationTests.compiled_fixture(self)
        app = self.application
        with patch(
            "smr_launcher.recipe_service.resolve_bundled_recipe", return_value=((rule,), binding)
        ):
            edition = app.create_recipe_edition(original, binding.recipe_id)
            self.assertEqual(app.create_recipe_edition(original, binding.recipe_id), edition)
            profile = app.profiles._profile_path(edition.profile_id)
            (profile / "Saves/edition.sav").write_bytes(b"edition save")
            expected = app.cold_storage.inspect(profile)
            app.compact_library(app.plan_compaction())
            before = self.snapshot_files(app.library)
            for arguments in ({"reuse_only": True}, {"expected_plan": {}}):
                with self.assertRaises((ApplicationError, ColdStorageError)):
                    app.create_recipe_edition(original, binding.recipe_id, **arguments)
                self.assertEqual(self.snapshot_files(app.library), before)
            reused = app.create_recipe_edition(original, binding.recipe_id)
        self.assertEqual(reused, edition)
        self.assertEqual(app.cold_storage.inspect(profile), expected)
        self.assertFalse(app._prepared_root(original).exists())
        self.assertTrue(app._prepared_root(edition).exists())
        self.assertEqual(len(app.catalogue()), 2)

    def test_r5_guarded_reuse_with_warm_original_and_cold_edition_remains_readonly(self):
        original, rule, binding, _stock = fixture.ApplicationTests.compiled_fixture(self)
        app = self.application
        with patch(
            "smr_launcher.recipe_service.resolve_bundled_recipe", return_value=((rule,), binding)
        ):
            app.create_recipe_edition(original, binding.recipe_id)
            app.compact_library(app.plan_compaction())
            app.prepare_map(original)
            before = self.snapshot_files(app.library)
            for arguments in ({"reuse_only": True}, {"expected_plan": {"synthetic": True}}):
                with patch(
                    "smr_launcher.recipe_service.plan_recipe_job", return_value={"synthetic": True}
                ):
                    with self.assertRaises((ApplicationError, ColdStorageError)):
                        app.create_recipe_edition(original, binding.recipe_id, **arguments)
                self.assertEqual(self.snapshot_files(app.library), before)
