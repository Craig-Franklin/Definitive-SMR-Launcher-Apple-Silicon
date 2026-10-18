"""First upgrades from legacy schema-1 records use only synthetic app bundles."""

from pathlib import Path
from unittest.mock import patch
import hashlib
import json
import tempfile
import unittest

from smr_launcher import update_helper, updates
import test_updates


class LegacyUpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.library = self.root / "library"
        self.directory = self.library / "updates"
        self.directory.mkdir(parents=True)
        self.pending = self.directory / "pending.json"
        self.target = self.root / "Applications" / updates.BUNDLE_NAME
        self.verify = test_updates.UpdateTests._verify_fixture

    def transaction(self, token, old, new, *, tracked):
        candidate = self.target.parent / (".smr-update-" + token) / updates.BUNDLE_NAME
        backup = self.target.parent / (".smr-previous-" + old + "-" + token + ".app")
        helper = self.directory / (".app-stage-" + token) / updates.BUNDLE_NAME
        for path, version in ((self.target, old), (candidate, new), (helper, new)):
            (path / "Contents").mkdir(parents=True, exist_ok=True)
            (path / "Contents/version").write_text(version)
        payload = ("synthetic archive " + new).encode()
        digest = hashlib.sha256(payload).hexdigest()
        archive = self.directory / (digest + ".zip")
        archive.write_bytes(payload)
        record = dict(
            schema=1,
            state="ready",
            target=str(self.target),
            candidate=str(candidate),
            backup=str(backup),
            helper_bundle=str(helper),
            team="ABCDEFGHIJ",
            old_version=old,
            new_version=new,
            pid=987654321,
            manual_install=False,
        )
        if tracked:
            record.update(archive_sha256=digest, archive_size=len(payload))
            (helper.parent / ".archive.json").write_text(
                json.dumps(dict(sha256=digest, size=len(payload)))
            )
        self.pending.write_text(json.dumps(record))
        return record, archive

    def complete_helper(self, record):
        with (
            patch.object(update_helper, "verify_publisher", self.verify),
            patch.object(
                update_helper, "current_app_bundle", return_value=Path(record["helper_bundle"])
            ),
            patch.object(update_helper, "trusted_team_from_bundle", return_value=record["team"]),
            patch.object(update_helper.os, "kill", side_effect=ProcessLookupError),
        ):
            update_helper.main(self.pending)

    def startup(self, version):
        with (
            patch.object(update_helper, "verify_publisher", self.verify),
            patch.object(update_helper.os, "kill", side_effect=ProcessLookupError),
        ):
            self.assertIsNone(updates.pending_install(self.library, self.target, version))

    def receipt(self, record):
        token = Path(record["candidate"]).parent.name[len(".smr-update-") :]
        return self.directory / "completed" / (token + ".json")

    def assert_installed_and_preserved(self, record, archive):
        self.assertEqual(json.loads(self.pending.read_text())["state"], "installed")
        self.assertEqual((self.target / "Contents/version").read_text(), record["new_version"])
        self.assertEqual(
            (Path(record["backup"]) / "Contents/version").read_text(), record["old_version"]
        )
        self.assertEqual(
            (Path(record["helper_bundle"]) / "Contents/version").read_text(), record["new_version"]
        )
        self.assertTrue(archive.is_file())

    @staticmethod
    def snapshot(roots):
        return {
            str(path): (
                path.read_bytes(),
                path.stat().st_ino,
                path.stat().st_mode,
                path.stat().st_mtime_ns,
            )
            for root in roots
            for path in ([root] if root.is_file() else root.rglob("*"))
            if path.is_file()
        }

    def test_tracked_real_token_helper_still_records_verified_history(self):
        record, archive = self.transaction("a" * 32, "0.3.0", "0.4.0", tracked=True)
        self.complete_helper(record)
        self.startup("0.4.0")
        self.assert_installed_and_preserved(record, archive)
        self.assertEqual(
            json.loads(self.receipt(record).read_text())["archive_sha256"], record["archive_sha256"]
        )

    def test_legacy_real_token_helper_finishes_without_admitting_history(self):
        record, archive = self.transaction("b" * 32, "0.3.0", "0.4.0", tracked=False)
        self.complete_helper(record)
        self.startup("0.4.0")
        self.assert_installed_and_preserved(record, archive)
        self.assertFalse(self.receipt(record).exists())
        self.assertFalse((Path(record["helper_bundle"]).parent / ".archive.json").exists())

    def test_legacy_startup_reconciles_both_interrupted_exchange_boundaries(self):
        for index, backup_renamed in enumerate((False, True)):
            with self.subTest(backup_renamed=backup_renamed):
                record, archive = self.transaction(
                    str(index + 1) * 32, "0.3.0", "0.4.0", tracked=False
                )
                update_helper._swap(self.target, Path(record["candidate"]))
                if backup_renamed:
                    Path(record["candidate"]).rename(record["backup"])
                self.startup("0.4.0")
                self.startup("0.4.0")
                self.assert_installed_and_preserved(record, archive)
                self.assertFalse(self.receipt(record).exists())

    def test_subsequent_tracked_upgrades_prune_tracked_history_and_preserve_legacy(self):
        legacy, archive = self.transaction("c" * 32, "0.3.0", "0.4.0", tracked=False)
        self.complete_helper(legacy)
        self.startup("0.4.0")
        roots = [Path(legacy["backup"]), Path(legacy["helper_bundle"]).parent, archive]
        before = self.snapshot(roots)
        first, _ = self.transaction("d" * 32, "0.4.0", "0.5.0", tracked=True)
        self.complete_helper(first)
        self.startup("0.5.0")
        self.assertTrue(self.receipt(first).exists())
        second, _ = self.transaction("e" * 32, "0.5.0", "0.6.0", tracked=True)
        self.complete_helper(second)
        self.startup("0.6.0")
        self.assertFalse(self.receipt(first).exists())
        self.assertFalse(Path(first["backup"]).exists())
        self.assertFalse(Path(first["helper_bundle"]).parent.exists())
        self.assertTrue(self.receipt(second).exists())
        self.assertTrue(Path(second["backup"]).exists())
        self.assertFalse(self.receipt(legacy).exists())
        self.assertEqual(self.snapshot(roots), before)

    def test_archive_fields_without_staging_provenance_stay_untracked(self):
        record, archive = self.transaction("f" * 32, "0.3.0", "0.4.0", tracked=True)
        (Path(record["helper_bundle"]).parent / ".archive.json").unlink()
        self.complete_helper(record)
        self.startup("0.4.0")
        self.assert_installed_and_preserved(record, archive)
        self.assertFalse(self.receipt(record).exists())
