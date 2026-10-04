"""Synthetic update trust and ZIP checks; no GitHub release or game files."""
from pathlib import Path
from unittest.mock import patch
import io
import json
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.updates import (BUNDLE_NAME, REPOSITORY, SLUG, UpdateError,
                                  UpdatePreferences, inspect_app_zip, parse_release,
                                  pending_install, pending_install_manual, version_tuple)
from smr_launcher import update_helper


def release_payload(version="0.3.0", *, digest="a" * 64, repo=SLUG, prerelease=False):
    name = f"{REPOSITORY}-v{version}-arm64.zip"
    return json.dumps({
        "tag_name": "v" + version, "draft": False, "prerelease": prerelease,
        "html_url": f"https://github.com/{repo}/releases/tag/v{version}",
        "assets": [{"name": name, "browser_download_url":
                    f"https://github.com/{repo}/releases/download/v{version}/{name}",
                    "state": "uploaded", "digest": "sha256:" + digest, "size": 1234}],
    }).encode()


def archive_with(entries):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as output:
        for name, value, mode in entries:
            item = zipfile.ZipInfo(name)
            item.external_attr = mode << 16
            output.writestr(item, value)
    return payload.getvalue()


class UpdateTests(unittest.TestCase):
    def _exchange_fixture(self, root: Path):
        install = root / "install"
        install.mkdir()
        target = install / BUNDLE_NAME
        candidate = install / ".smr-update-fixture" / BUNDLE_NAME
        backup = install / ".smr-previous-0.3.0-fixture.app"
        helper = root / "private" / BUNDLE_NAME
        for bundle, version in ((target, "0.3.0"), (candidate, "0.4.0"),
                                (helper, "0.4.0")):
            (bundle / "Contents").mkdir(parents=True)
            (bundle / "Contents/version").write_text(version)
        private = root / "private"
        (private / "map-save.sentinel").write_bytes(b"private map save")
        pending = private / "updates/pending.json"
        pending.parent.mkdir()
        pending.write_text(json.dumps(dict(
            schema=1, state="ready", target=str(target), candidate=str(candidate),
            backup=str(backup), helper_bundle=str(helper), team="ABCDEFGHIJ",
            old_version="0.3.0", new_version="0.4.0", pid=987654321,
            manual_install=False)))
        return target, candidate, backup, helper, pending

    @staticmethod
    def _verify_fixture(bundle, team, version, *, require_name=True):
        if (team != "ABCDEFGHIJ" or not bundle.is_dir()
                or (require_name and bundle.name != BUNDLE_NAME)
                or (bundle / "Contents/version").read_text() != version):
            raise UpdateError("Synthetic publisher or version mismatch")

    def test_atomic_exchange_preserves_old_app_and_private_map_save(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            target, candidate, backup, helper, pending = self._exchange_fixture(root)
            self.assertFalse(pending_install_manual(pending))
            with (patch.object(update_helper, "verify_publisher", self._verify_fixture),
                  patch.object(update_helper, "current_app_bundle", return_value=helper),
                  patch.object(update_helper, "trusted_team_from_bundle", return_value="ABCDEFGHIJ"),
                  patch.object(update_helper.os, "kill", side_effect=ProcessLookupError)):
                update_helper.main(pending)
            self.assertEqual((target / "Contents/version").read_text(), "0.4.0")
            self.assertEqual((backup / "Contents/version").read_text(), "0.3.0")
            self.assertFalse(candidate.exists())
            self.assertEqual((root / "private/map-save.sentinel").read_bytes(), b"private map save")
            self.assertEqual(json.loads(pending.read_text())["state"], "installed")

    def test_restart_finishes_exchange_interrupted_before_backup_rename(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            target, candidate, backup, helper, pending = self._exchange_fixture(root)
            update_helper._swap(target, candidate)
            self.assertEqual(json.loads(pending.read_text())["state"], "ready")
            with patch.object(update_helper, "verify_publisher", self._verify_fixture):
                self.assertIsNone(pending_install(root / "private", target, "0.4.0"))
            self.assertEqual((target / "Contents/version").read_text(), "0.4.0")
            self.assertEqual((backup / "Contents/version").read_text(), "0.3.0")
            self.assertFalse(candidate.exists())
            self.assertEqual((root / "private/map-save.sentinel").read_bytes(), b"private map save")

    def test_release_identity_version_and_digest_are_exact(self):
        item = parse_release(release_payload(), "0.2.0")
        self.assertEqual(item.version, "0.3.0")
        self.assertEqual(item.sha256, "a" * 64)
        self.assertIsNone(parse_release(release_payload(), "0.3.0"))
        self.assertIsNone(parse_release(release_payload(), "0.4.0"))
        for payload in (release_payload(repo="some-org/other"),
                        release_payload(digest="broken"),
                        release_payload(prerelease=True)):
            with self.assertRaises(UpdateError):
                parse_release(payload, "0.2.0")
        with self.assertRaises(UpdateError):
            version_tuple("0.3.0-rc1")

    def test_update_preference_stays_private_and_defaults_off(self):
        with tempfile.TemporaryDirectory() as root:
            store = UpdatePreferences(Path(root).resolve() / "private")
            self.assertFalse(store.automatic())
            store.set_automatic(True)
            self.assertTrue(store.automatic())
            store.set_automatic(False)
            self.assertFalse(store.automatic())

    def test_zip_accepts_internal_symlink_but_rejects_escape_and_traversal(self):
        prefix = BUNDLE_NAME + "/Contents/"
        good = archive_with([
            (prefix + "MacOS/launcher", b"fixture", stat.S_IFREG | 0o755),
            (prefix + "Resources/framework", b"../Frameworks/framework", stat.S_IFLNK | 0o777),
        ])
        bad = [
            archive_with([(BUNDLE_NAME + "/../outside", b"bad", stat.S_IFREG | 0o644)]),
            archive_with([(prefix + "Resources/bad", b"../../../outside", stat.S_IFLNK | 0o777)]),
            archive_with([(prefix + "Resources/link", b"../Frameworks", stat.S_IFLNK | 0o777),
                          (prefix + "Resources/link/payload", b"bad", stat.S_IFREG | 0o644)]),
            archive_with([(prefix + "Resources/first", b"..", stat.S_IFLNK | 0o777),
                          (prefix + "Resources/second", b"first/../../outside", stat.S_IFLNK | 0o777)]),
            archive_with([(prefix + "Resources/\u00e9", b"..", stat.S_IFLNK | 0o777),
                          (prefix + "Resources/second", "e\u0301/../../outside".encode(), stat.S_IFLNK | 0o777)]),
        ]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "app.zip"
            path.write_bytes(good)
            inspect_app_zip(path)
            for payload in bad:
                path.write_bytes(payload)
                with self.assertRaises(UpdateError):
                    inspect_app_zip(path)


if __name__ == "__main__":
    unittest.main()
