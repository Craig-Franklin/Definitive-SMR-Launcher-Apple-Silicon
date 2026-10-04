"""Installation safety with synthetic app trees; no real apps or game data."""
from contextlib import ExitStack
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher import installation
from smr_launcher.updates import BUNDLE_NAME, UpdateError


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.source = self.root / "download" / BUNDLE_NAME
        resources = self.source / "Contents/Resources"
        resources.mkdir(parents=True)
        (resources / "payload").write_bytes(b"signed fixture payload")
        (resources / "alias").symlink_to("payload")
        self.destination = self.home / "Applications" / BUNDLE_NAME
        self.sentinel = self.home / "private-map-save"
        self.sentinel.write_bytes(b"preserve save")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(installation, "current_app_bundle", return_value=self.source))
        self.stack.enter_context(patch.object(installation, "trusted_team_from_bundle", return_value="ABCDEFGHIJ"))
        self.verify = self.stack.enter_context(patch.object(installation, "verify_publisher"))
        self.copy = self.stack.enter_context(patch.object(installation.subprocess, "run", side_effect=self.copy_app))

    def copy_app(self, command, **kwargs):
        self.assertEqual(command[0], "/usr/bin/ditto")
        shutil.copytree(command[1], command[2], symlinks=True)
        return subprocess.CompletedProcess(command, 0)

    def assert_preserved(self):
        self.assertEqual(self.sentinel.read_bytes(), b"preserve save")
        self.assertEqual((self.source / "Contents/Resources/payload").read_bytes(), b"signed fixture payload")
        self.assertEqual(list((self.home / "Applications").glob(".smr-install-*")), [])

    @unittest.skipUnless(sys.platform == "darwin", "Exclusive installation uses macOS renamex_np")
    def test_install_publishes_independent_exact_copy_without_touching_source_or_saves(self):
        result = installation.install_to_user_applications(self.source, self.home)
        self.assertEqual(result, self.destination)
        copied = result / "Contents/Resources/payload"
        self.assertEqual(copied.read_bytes(), b"signed fixture payload")
        self.assertNotEqual(copied.stat().st_ino, (self.source / "Contents/Resources/payload").stat().st_ino)
        self.assertEqual((result / "Contents/Resources/alias").readlink(), Path("payload"))
        self.assertEqual(self.verify.call_count, 2)
        self.assert_preserved()

    def test_existing_destination_is_never_replaced(self):
        self.destination.mkdir(parents=True)
        (self.destination / "keep").write_bytes(b"old app")
        with self.assertRaises(installation.InstallationError):
            installation.install_to_user_applications(self.source, self.home)
        self.assertEqual((self.destination / "keep").read_bytes(), b"old app")
        self.copy.assert_not_called()
        self.assert_preserved()

    def test_invalid_signature_fails_before_copying(self):
        self.verify.side_effect = UpdateError("Invalid signature")
        with self.assertRaises(installation.InstallationError):
            installation.install_to_user_applications(self.source, self.home)
        self.copy.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_changed_copy_is_not_published(self):
        def corrupt(command, **kwargs):
            result = self.copy_app(command, **kwargs)
            (Path(command[2]) / "Contents/Resources/payload").write_bytes(b"different")
            return result
        self.copy.side_effect = corrupt
        with self.assertRaises(installation.InstallationError):
            installation.install_to_user_applications(self.source, self.home)
        self.assertFalse(self.destination.exists())
        self.assert_preserved()

    def test_linked_applications_and_escaping_bundle_link_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.home / "Applications").symlink_to(outside)
        with self.assertRaises(installation.InstallationError):
            installation.install_to_user_applications(self.source, self.home)
        (self.home / "Applications").unlink()
        (self.source / "Contents/Resources/escape").symlink_to(self.sentinel)
        with self.assertRaises(installation.InstallationError):
            installation.install_to_user_applications(self.source, self.home)
        self.copy.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(sys.platform == "darwin", "Exclusive installation uses macOS renamex_np")
    def test_destination_created_during_publication_is_preserved(self):
        publish = installation._publish_exclusive
        def racing_publish(source, destination):
            destination.mkdir()
            (destination / "keep").write_bytes(b"another installation")
            publish(source, destination)
        with patch.object(installation, "_publish_exclusive", side_effect=racing_publish):
            with self.assertRaises(installation.InstallationError):
                installation.install_to_user_applications(self.source, self.home)
        self.assertEqual((self.destination / "keep").read_bytes(), b"another installation")
        self.assert_preserved()

    def test_installation_location_and_exact_running_source(self):
        self.assertTrue(installation.installation_needed(self.source, self.home))
        self.assertFalse(installation.installation_needed(self.destination, self.home))
        self.assertFalse(installation.installation_needed(Path("/Applications") / BUNDLE_NAME, self.home))
        with self.assertRaises(installation.InstallationError):
            installation.install_to_user_applications(self.root / BUNDLE_NAME, self.home)
        self.copy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
