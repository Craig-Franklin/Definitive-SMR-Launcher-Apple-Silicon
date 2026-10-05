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
        self.root = Path(temp.name)
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


if __name__ == "__main__":
    unittest.main()
