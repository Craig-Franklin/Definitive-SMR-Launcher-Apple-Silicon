"""Synthetic end-user workflow without touching a real game or downloaded map."""
from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.application import ApplicationError, LauncherApplication
from smr_launcher.activation import ORIGINAL
from smr_launcher.collection import RemoteMap
from smr_launcher.launch_preferences import LaunchPreferencesError
import smr_launcher.activation as activation_module


class FakeInstallation:
    def __init__(self, root):
        self.profile_root = root / "live"
        self.profile_root.mkdir()
        for name in ("CustomAssets", "UserMaps", "Saves"):
            (self.profile_root / name).mkdir()
        (self.profile_root / "Settings.ini").write_text("stock")
        (self.profile_root / "Saves/stock.sav").write_text("stock save")
        self.executable_sha256 = hashlib.sha256(b"test game").hexdigest()
        self.appid = "7600"
        self.steam_buildid = "synthetic-build"
        self.bundle_id = "com.feralinteractive.railroads"
        self.bundle_version = "synthetic-version"
        self.steamapps_root = root / "steamapps"
        self.running = False

    def check_current(self):
        return None

    def game_running(self):
        return self.running


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.installation = FakeInstallation(self.root)
        self.application = LauncherApplication(self.installation, self.root / "library")
        self.archive = self.root / "Example.tar"
        with tarfile.open(self.archive, "w") as stream:
            for name, data in (
                ("UserMaps/Example/RRT_Scenario_User_Example.xml", b"<Scenario/>"),
                ("CustomAssets/Example.txt", b"map asset"),
            ):
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                stream.addfile(entry, BytesIO(data))

    @staticmethod
    def snapshot_files(root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}

    def test_import_switch_and_return_keep_independent_saves_and_original(self):
        source_digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.assertEqual(self.application.setup(), ORIGINAL)
        record = self.application.import_archive(self.archive)
        self.assertEqual(record.archive_sha256, source_digest)
        self.assertEqual(record.scenarios, ("UserMaps/Example/RRT_Scenario_User_Example.xml",))
        self.assertEqual(self.application.import_archive(self.archive), record)
        self.application.activate(record.profile_id)
        self.assertEqual(list((self.installation.profile_root / "Saves").iterdir()), [])
        (self.installation.profile_root / "Saves/map.sav").write_text("map save")
        self.application.activate(ORIGINAL)
        self.assertEqual((self.installation.profile_root / "Saves/stock.sav").read_text(), "stock save")
        self.assertFalse((self.installation.profile_root / "Saves/map.sav").exists())
        self.application.activate(record.profile_id)
        self.assertEqual((self.installation.profile_root / "Saves/map.sav").read_text(), "map save")
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), source_digest)
        self.assertNotEqual((self.application.originals / (source_digest + ".7z")).stat().st_ino, self.archive.stat().st_ino)

    def test_setup_rejects_custom_content_and_running_game(self):
        (self.installation.profile_root / "UserMaps/custom.xml").write_text("custom")
        with self.assertRaises(ApplicationError):
            self.application.setup()
        self.assertFalse(self.application.profiles.state_file.exists())
        (self.installation.profile_root / "UserMaps/custom.xml").unlink()
        self.installation.running = True
        with self.assertRaises(ApplicationError):
            self.application.setup()
        self.assertFalse(self.application.profiles.state_file.exists())

    def test_gameplay_evidence_binds_stock_and_map_metadata_but_excludes_saves(self):
        import os
        import json
        from smr_launcher.verification import CHECKS
        stock = self.installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        stock.mkdir(parents=True)
        resource = stock / "rrt_goods.xml"
        resource.write_bytes(b"stock goods")
        self.application.setup()
        record = self.application.import_archive(self.archive)
        self.application.activate(record.profile_id)
        evidence = self.application.record_gameplay(record, "2026-10-05T06:00:00+00:00",
            "Synthetic player", dict.fromkeys(CHECKS, True), "Synthetic full check")
        self.assertEqual(evidence.status, "Verified")
        receipt = self.application.library / "verification-resources" / (evidence.resources_sha256 + ".json")
        self.assertEqual(json.loads(receipt.read_text())["identity"], evidence.resources_sha256)
        self.assertEqual(self.application.verification_for(record), evidence)
        (self.installation.profile_root / "Saves/future.sav").write_bytes(b"keep forever")
        self.application.activate(ORIGINAL)
        self.assertEqual(self.application.verification_for(record), evidence)
        map_asset = self.application.profiles._profile_path(record.profile_id) / "CustomAssets/Example.txt"
        for file in (resource, map_asset):
            with self.subTest(file=file):
                before = file.stat(); content = file.read_bytes()
                os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
                self.assertEqual(file.read_bytes(), content)
                self.assertIsNone(self.application.verification_for(record))
                os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
                self.assertEqual(self.application.verification_for(record), evidence)
        self.application.activate(record.profile_id)
        self.assertEqual((self.installation.profile_root / "Saves/future.sav").read_bytes(), b"keep forever")

    def test_legacy_gameplay_checks_are_retained_without_assuming_resource_metadata(self):
        from smr_launcher.verification import GameplayVerification, CHECKS
        self.application.setup()
        record = self.application.import_archive(self.archive)
        self.application.activate(record.profile_id)
        marker = self.application.profiles._marker(self.installation.profile_root, record.profile_id)
        evidence = GameplayVerification(record.archive_sha256, record.game_executable_sha256,
            record.variant_id, marker["assets_sha256"], "2026-10-05T06:00:00+00:00",
            "Historical player", dict.fromkeys(CHECKS, True), "Old success")
        self.application.verification_store.append(evidence)
        before = self.application.verification_store.path.read_bytes()
        with patch.object(self.application, "_resource_snapshot", side_effect=AssertionError("no retroactive binding")):
            observed = self.application.verification_for(record)
        self.assertEqual(observed.status, "Not verified")
        self.assertEqual(observed.checks, evidence.checks)
        self.assertEqual(self.application.verification_store.path.read_bytes(), before)

    def compatibility_rule(self, record, **changes):
        from smr_launcher.rules import CompatibilityRule, TokenPatch
        old, new = b"map asset", b"compatible map asset"
        values = dict(rule_id="synthetic-repair", version=1,
            provenance="Synthetic fixture", reason="Exercise isolated preparation",
            archive_sha256=record.archive_sha256,
            game_executable_sha256=record.game_executable_sha256,
            patches=(TokenPatch("CustomAssets/Example.txt", hashlib.sha256(old).hexdigest(),
                                hashlib.sha256(new).hexdigest(), old, new),))
        values.update(changes)
        return CompatibilityRule(**values)

    def test_compatibility_edition_preserves_original_and_separates_saves(self):
        import json
        self.application.setup()
        original = self.application.import_archive(self.archive)
        rule = self.compatibility_rule(original)
        self.application.activate(original.profile_id)
        (self.installation.profile_root / "Saves/original.sav").write_text("keep")
        before_live = self.snapshot_files(self.installation.profile_root)
        before_import = self.snapshot_files(self.application.imports / original.imported_directory)
        edition = self.application.create_compatibility_edition(original, (rule,), label="Synthetic repair v1")
        self.assertNotEqual(original.variant_id, edition.variant_id)
        self.assertIn("Experimental", edition.name)
        self.assertIsNone(self.application.verification_for(edition))
        self.assertEqual(self.snapshot_files(self.installation.profile_root), before_live)
        self.assertEqual(self.snapshot_files(self.application.imports / original.imported_directory), before_import)
        self.assertEqual(self.application.create_compatibility_edition(original, (rule,), label="Repeated"), edition)
        self.assertEqual(len(list(self.application.prepared.iterdir())), 2)
        receipt = json.loads((self.application.library / "compatibility-preparations" /
                             (edition.variant_id + ".json")).read_text())
        self.assertEqual(receipt["parent_variant"], original.variant_id)
        self.assertEqual(receipt["rules"][0]["id"], rule.rule_id)
        self.assertNotIn("old", receipt["rules"][0]["files"][0])
        self.application.activate(edition.profile_id)
        self.assertEqual(list((self.installation.profile_root / "Saves").iterdir()), [])
        self.assertEqual((self.installation.profile_root / "CustomAssets/Example.txt").read_bytes(), b"compatible map asset")
        (self.installation.profile_root / "Saves/edition.sav").write_text("independent")
        self.application.activate(original.profile_id)
        self.assertEqual((self.installation.profile_root / "Saves/original.sav").read_text(), "keep")
        self.assertFalse((self.installation.profile_root / "Saves/edition.sav").exists())
        self.assertEqual((self.installation.profile_root / "CustomAssets/Example.txt").read_bytes(), b"map asset")
        self.application.remove_map(edition)
        rebuilt = self.application.create_compatibility_edition(original, (rule,), label="Restored")
        self.assertEqual(rebuilt.variant_id, edition.variant_id)
        self.application.activate(rebuilt.profile_id)
        self.assertEqual((self.installation.profile_root / "Saves/edition.sav").read_text(), "independent")
        self.assertFalse((self.installation.profile_root / "Saves/original.sav").exists())

    def test_compatibility_preparation_rejects_mismatches_and_edition_stacking(self):
        from smr_launcher.rules import RuleError
        self.application.setup()
        original = self.application.import_archive(self.archive)
        rule = self.compatibility_rule(original)
        for bad in ((), (self.compatibility_rule(original, archive_sha256="f" * 64),)):
            with self.assertRaises((ApplicationError, RuleError)):
                self.application.create_compatibility_edition(original, bad, label="Test")
        self.installation.running = True
        with self.assertRaises(ApplicationError):
            self.application.create_compatibility_edition(original, (rule,), label="Test")
        self.installation.running = False
        edition = self.application.create_compatibility_edition(original, (rule,), label="Test")
        with self.assertRaises(ApplicationError):
            self.application.create_compatibility_edition(edition, (rule,), label="Stacked")
        source = self.application.imports / original.imported_directory / "CustomAssets/Example.txt"
        source.chmod(0o600)
        source.write_bytes(b"unexpected drift")
        with self.assertRaises(ApplicationError):
            self.application.create_compatibility_edition(original, (rule,), label="Drifted")
        self.assertEqual(len(self.application.catalogue()), 2)

    def compiled_fixture(self):
        from smr_launcher.recipe_identity import RecipePreparationBinding, map_file_context
        self.application.setup()
        original = self.application.import_archive(self.archive)
        stock = self.installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        stock.mkdir(parents=True)
        (stock / "synthetic.xml").write_bytes(b"synthetic stock")
        binding = RecipePreparationBinding("synthetic-v1", "a" * 64, "b" * 64,
            map_file_context(self.application.imports / original.imported_directory)["identity"],
            self.application._stock_file_context()["identity"])
        return original, self.compatibility_rule(original), binding, stock

    def compiled_prepare(self, original, rule, binding):
        # Exercise the one-lock integration helper with synthetic reviewed input.
        # No bundled recipe is enabled by these tests.
        with self.application._locked():
            return self.application._create_compatibility_edition_locked(original, (rule,),
                label="Synthetic compiled v1", recipe_binding=binding)

    def test_compiled_edition_save_isolation_recreation_and_receipt_binding(self):
        from dataclasses import replace
        import json
        original, rule, binding, stock = self.compiled_fixture()
        compiled = self.compiled_prepare(original, rule, binding)
        self.assertTrue(compiled.recipe_receipt_sha256)
        self.assertEqual(self.compiled_prepare(original, rule, binding), compiled)
        self.assertIsNone(self.application.verification_for(compiled))
        self.application.activate(compiled.profile_id)
        (self.installation.profile_root / "Saves/compiled.sav").write_bytes(b"keep compiled")
        self.application.activate(original.profile_id)
        self.assertFalse((self.installation.profile_root / "Saves/compiled.sav").exists())
        self.application.remove_map(compiled)
        rebuilt = self.compiled_prepare(original, rule, binding)
        self.assertEqual(compiled.variant_id, rebuilt.variant_id)
        self.application.activate(rebuilt.profile_id)
        self.assertEqual((self.installation.profile_root / "Saves/compiled.sav").read_bytes(), b"keep compiled")
        self.application.activate(ORIGINAL)
        self.assertEqual((self.installation.profile_root / "Saves/stock.sav").read_text(), "stock save")
        other = self.compiled_prepare(original, rule, replace(binding, recipe_sha256="c" * 64))
        self.assertNotEqual(other.variant_id, rebuilt.variant_id)
        self.application.activate(other.profile_id)
        self.assertEqual(list((self.installation.profile_root / "Saves").iterdir()), [])
        receipt_path = self.application.library / "compatibility-preparations" / (other.variant_id + ".json")
        receipt = json.loads(receipt_path.read_text())
        self.assertNotIn("old", receipt["rules"][0]["files"][0])
        self.assertEqual(receipt["recipe_binding"]["recipe_sha256"], "c" * 64)

    def test_compiled_map_or_stock_timestamp_drift_blocks_activation_and_play(self):
        import os
        original, rule, binding, stock = self.compiled_fixture()
        compiled = self.compiled_prepare(original, rule, binding)
        profile = self.application.profiles._profile_path(compiled.profile_id)
        for file in (profile / "CustomAssets/Example.txt", stock / "synthetic.xml"):
            st = file.stat()
            os.utime(file, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
            with self.assertRaisesRegex(ApplicationError, "revalidate"):
                self.application.activate(compiled.profile_id)
            called = []
            with self.assertRaisesRegex(ApplicationError, "revalidate"):
                self.application.play(compiled.profile_id, launch=lambda: called.append(True))
            self.assertEqual(called, [])
            self.assertEqual(self.application.active(), ORIGINAL)
            os.utime(file, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.application.activate(compiled.profile_id)

    def test_compiled_receipt_missing_modified_or_fifo_fails_closed(self):
        import os
        original, rule, binding, stock = self.compiled_fixture()
        compiled = self.compiled_prepare(original, rule, binding)
        path = self.application.library / "compatibility-preparations" / (compiled.variant_id + ".json")
        valid = path.read_bytes()
        for bad in (b'{}', b'[]', None, 'fifo'):
            path.unlink(missing_ok=True)
            if bad == 'fifo':
                os.mkfifo(path)
            elif bad is not None:
                path.write_bytes(bad)
            with self.subTest(bad=bad), self.assertRaisesRegex(ApplicationError, "revalidate"):
                self.application.activate(compiled.profile_id)
            self.assertEqual(self.application.active(), ORIGINAL)
        path.unlink()
        path.write_bytes(valid)
        self.application.activate(compiled.profile_id)

    def test_compiled_stock_mismatch_never_publishes_an_edition(self):
        from dataclasses import replace
        original, rule, binding, stock = self.compiled_fixture()
        with self.assertRaisesRegex(ApplicationError, "stock resource context"):
            self.compiled_prepare(original, rule, replace(binding, stock_files_sha256="d" * 64))
        self.assertEqual(self.application.catalogue(), (original,))

    def test_restart_with_changed_game_build_keeps_profiles_but_blocks_use(self):
        self.application.setup()
        record = self.application.import_archive(self.archive)
        self.installation.executable_sha256 = hashlib.sha256(b"updated game").hexdigest()
        reopened = LauncherApplication(self.installation, self.application.library)
        with self.assertRaises(ApplicationError):
            reopened.activate(record.profile_id)
        with self.assertRaises(ApplicationError):
            reopened.import_archive(self.archive)
        self.assertTrue(reopened.profiles._profile_path(record.profile_id).is_dir())
        self.assertEqual(len(reopened.catalogue()), 1)

    def test_setup_resumes_after_interruption_between_marker_and_state(self):
        original = activation_module._atomic_json

        def interrupted(path, value):
            if path == self.application.profiles.state_file:
                raise RuntimeError("simulated interruption")
            return original(path, value)

        with patch.object(activation_module, "_atomic_json", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.application.setup()
        self.assertTrue((self.installation.profile_root / ".smr-launcher-profile.json").exists())
        self.assertFalse(self.application.profiles.state_file.exists())
        self.assertEqual(self.application.setup(), ORIGINAL)
        self.assertEqual((self.installation.profile_root / "Saves/stock.sav").read_text(), "stock save")

    def test_concurrent_different_imports_keep_both_catalogue_records(self):
        self.application.setup()
        other = self.root / "Other.tar"
        with tarfile.open(other, "w") as stream:
            data = b"<Other/>"
            entry = tarfile.TarInfo("UserMaps/Other/RRT_Scenario_User_Other.xml")
            entry.size = len(data)
            stream.addfile(entry, BytesIO(data))
        with ThreadPoolExecutor(max_workers=2) as pool:
            records = list(pool.map(self.application.import_archive, (self.archive, other)))
        self.assertEqual({item.variant_id for item in records}, {item.variant_id for item in self.application.catalogue()})
        self.assertEqual(len(self.application.catalogue()), 2)

    def test_remote_archive_keeps_its_validated_collection_name(self):
        self.application.setup()
        downloaded = self.root / (hashlib.sha1(self.archive.read_bytes()).hexdigest() + ".7z")
        downloaded.write_bytes(self.archive.read_bytes())
        record = self.application.import_archive(
            downloaded, original_filename="Friendly_Example_v1_00.7z",
        )
        self.assertEqual(record.name, "Friendly_Example_v1_00")
        self.assertEqual(self.application.catalogue()[0].name, record.name)
        with self.assertRaises(ApplicationError):
            self.application.import_archive(downloaded, original_filename="../unsafe.7z")

    def test_source_matching_requires_archive_bytes_and_preserves_profile(self):
        self.application.setup()
        record = self.application.import_archive(self.archive)
        state = self.application.profiles.state_file.read_bytes()
        save = (self.installation.profile_root / "Saves/stock.sav").read_bytes()
        wrong = RemoteMap("Example.7z", self.archive.stat().st_size, "a" * 40)
        self.assertEqual(self.application.match_collection_sources((wrong,)), 0)
        self.assertEqual(self.application.catalogue()[0].source_url, "")
        remote = RemoteMap("Exact_Source.7z", self.archive.stat().st_size,
                           hashlib.sha1(self.archive.read_bytes()).hexdigest(), "2026-01-08")
        self.assertEqual(self.application.match_collection_sources((remote,)), 1)
        linked = self.application.catalogue()[0]
        self.assertEqual(linked.source_url, remote.source_url)
        self.assertEqual(linked.variant_id, record.variant_id)
        self.assertEqual(linked.archive_modified, "2026-01-08")
        self.assertIsNone(self.application.map_metadata(linked).created)
        self.assertEqual(self.application.profiles.state_file.read_bytes(), state)
        self.assertEqual((self.installation.profile_root / "Saves/stock.sav").read_bytes(), save)
        with self.assertRaises(ApplicationError):
            self.application.import_archive(self.archive, original_filename=wrong.name, remote=wrong)

    def test_difficulty_edition_replay_and_separate_saves(self):
        from smr_launcher import editions
        (self.installation.profile_root / 'Settings.ini').write_text('[User Settings]\nPlayerName=Test\n')
        self.application.setup()
        original = self.application.import_archive(self.archive)
        source = self.application.prepared / original.prepared_directory
        original_bytes = (source / 'CustomAssets/Example.txt').read_bytes()
        with patch.object(editions, 'SUPPORTED_GAME', self.installation.executable_sha256):
            edition = self.application.create_edition(original, editor=False, difficulty=True)
            self.assertEqual(self.application.create_edition(original, editor=False, difficulty=True), edition)
            self.installation.running = True
            with self.assertRaises(ApplicationError):
                self.application.create_edition(original, editor=False, difficulty=True)
            self.installation.running = False
        self.assertEqual(len(self.application.catalogue()), 2)
        self.assertEqual((source / 'CustomAssets/Example.txt').read_bytes(), original_bytes)
        self.application.activate(original.profile_id)
        (self.installation.profile_root / 'Saves/original.sav').write_text('original save')
        self.application.activate(edition.profile_id)
        self.assertEqual(list((self.installation.profile_root/'Saves').iterdir()), [])
        self.assertTrue((self.installation.profile_root/'CustomAssets/XML/RRT_Difficulty.xml').is_file())
        (self.installation.profile_root/'Saves/edition.sav').write_text('edition save')
        self.application.activate(original.profile_id)
        self.assertEqual((self.installation.profile_root/'Saves/original.sav').read_text(),'original save')
        self.assertFalse((self.installation.profile_root/'CustomAssets/XML/RRT_Difficulty.xml').exists())
        with self.assertRaises(ApplicationError):
            self.application.create_edition(original, editor=True, difficulty=False)

    def test_play_callback_sees_map_profile_and_configured_settings(self):
        original_settings = ("[User Settings]\nPlayerName = Craig\n"
                             "LastScenarioName = RRT_Scenario_User_Stock.xml\n"
                             "SkipOpeningMovies = 0\nQuickstart = 1\n")
        (self.installation.profile_root / "Settings.ini").write_text(original_settings)
        self.application.setup()
        record = self.application.import_archive(self.archive)
        prepared = self.application.prepared / record.prepared_directory
        frozen_before = self.snapshot_files(prepared)
        map_profile = self.application.profiles._profile_path(record.profile_id)
        (map_profile / "Saves/map-before-play.sav").write_text("map save before Play")
        callback_state = []

        def launch():
            live = self.installation.profile_root
            settings = (live / "Settings.ini").read_text()
            self.assertIn("LastScenarioName = rrt_scenario_user_example.xml", settings)
            self.assertIn("SkipOpeningMovies = 1", settings)
            self.assertIn("Quickstart = 0", settings)
            self.assertTrue((live / record.scenarios[0]).is_file())
            self.assertEqual((live / "CustomAssets/Example.txt").read_text(), "map asset")
            self.assertEqual((live / "Saves/map-before-play.sav").read_text(), "map save before Play")
            self.assertEqual(self.application.profiles._state()["active"], record.profile_id)
            callback_state.append(settings)
            self.installation.running = True

        self.assertEqual(self.application.play(record.profile_id, launch), record.profile_id)
        self.assertEqual(len(callback_state), 1)
        self.assertEqual(self.snapshot_files(prepared), frozen_before)
        self.installation.running = False
        self.application.activate(ORIGINAL)
        self.assertEqual((self.installation.profile_root / "Settings.ini").read_text(), original_settings)
        self.assertEqual((self.installation.profile_root / "Saves/stock.sav").read_text(), "stock save")
        stored_map = self.application.profiles._profile_path(record.profile_id)
        self.assertIn("SkipOpeningMovies = 1", (stored_map / "Settings.ini").read_text())
        self.assertEqual((stored_map / "Saves/map-before-play.sav").read_text(), "map save before Play")

    def test_play_settings_preflight_failure_does_not_switch_or_launch(self):
        original_settings = "[User Settings]\nPlayerName = Craig\nQuickstart = 1\n"
        (self.installation.profile_root / "Settings.ini").write_text(original_settings)
        self.application.setup()
        record = self.application.import_archive(self.archive)
        map_profile = self.application.profiles._profile_path(record.profile_id)
        invalid_settings = ("[User Settings]\nLastScenarioName = A.xml\n"
                            "LastScenarioName = B.xml\n")
        (map_profile / "Settings.ini").write_text(invalid_settings)
        prepared = self.application.prepared / record.prepared_directory
        frozen_before = self.snapshot_files(prepared)
        called = []

        with self.assertRaises(LaunchPreferencesError):
            self.application.play(record.profile_id, lambda: called.append(True))

        self.assertEqual(called, [])
        self.assertEqual(self.application.profiles._state()["active"], ORIGINAL)
        self.assertEqual((self.installation.profile_root / "Settings.ini").read_text(), original_settings)
        self.assertEqual(self.snapshot_files(prepared), frozen_before)

    def test_play_refuses_running_game_without_switching_or_launching(self):
        self.application.setup()
        record = self.application.import_archive(self.archive)
        original_settings = (self.installation.profile_root / "Settings.ini").read_bytes()
        map_profile = self.application.profiles._profile_path(record.profile_id)
        map_settings = (map_profile / "Settings.ini").read_bytes()
        called = []
        self.installation.running = True

        with self.assertRaises(ApplicationError):
            self.application.play(record.profile_id, lambda: called.append(True))

        self.assertEqual(called, [])
        self.assertEqual(self.application.profiles._state()["active"], ORIGINAL)
        self.assertEqual((self.installation.profile_root / "Settings.ini").read_bytes(), original_settings)
        self.assertEqual((map_profile / "Settings.ini").read_bytes(), map_settings)


if __name__ == "__main__":
    unittest.main()
