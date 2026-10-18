"""Synthetic updater lifecycle coverage, with no installed app or network access."""

from pathlib import Path
from unittest.mock import patch
import hashlib
import io
import json
import shutil
import tempfile
import unittest

from smr_launcher import updates, update_helper
import test_updates


class UpdateLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.payload = b"synthetic release"
        self.release = updates.Release(
            "0.4.0",
            "fixture.zip",
            "https://github.com/fixture",
            hashlib.sha256(self.payload).hexdigest(),
            len(self.payload),
            "https://github.com/fixture",
        )
        self.verify = test_updates.UpdateTests._verify_fixture

    def fixture(self):
        return test_updates.UpdateTests()._exchange_fixture(self.root)

    def test_download_cache_checks_size_hash_and_symlink_without_network(self):
        directory = self.root / "updates"
        directory.mkdir()
        target = directory / (self.release.sha256 + ".zip")
        target.write_bytes(self.payload)
        with patch.object(updates, "_opener", side_effect=AssertionError("network")):
            self.assertEqual(updates.download_release(self.release, directory), target)
            target.write_bytes(b"x" * len(self.payload))
            with self.assertRaises(updates.UpdateError):
                updates.download_release(self.release, directory)
            target.unlink()
            source = self.root / "outside"
            source.write_bytes(self.payload)
            target.symlink_to(source)
            with self.assertRaises(RuntimeError):
                updates.download_release(self.release, directory)
            self.assertEqual(source.read_bytes(), self.payload)

    def test_failed_download_leaves_no_published_or_temporary_archive(self):
        directory = self.root / "updates"
        with patch.object(updates, "_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(b"corrupt")
            with self.assertRaises(updates.UpdateError):
                updates.download_release(self.release, directory)
        self.assertEqual([p for p in directory.iterdir() if p.name != ".install.lock"], [])

    def test_prepare_verifies_copy_and_preserves_ready_transaction(self):
        target, candidate, backup, helper, pending = self.fixture()
        before = pending.read_bytes()
        with patch.object(updates, "verify_publisher", self.verify):
            with self.assertRaisesRegex(updates.UpdateError, "already ready"):
                updates.prepare_install(
                    helper, target, self.release, pending.parent.parent, "ABCDEFGHIJ", "0.3.0"
                )
        self.assertEqual(pending.read_bytes(), before)
        self.assertTrue(candidate.exists())
        self.assertFalse(backup.exists())

    def test_failed_candidate_verification_keeps_installed_app_and_cleans_candidate(self):
        target, candidate, backup, helper, pending = self.fixture()
        pending.unlink()
        shutil.rmtree(candidate.parent)

        def copy(command, **kwargs):
            shutil.copytree(Path(command[1]), Path(command[2]))
            return type("Result", (), {"returncode": 0})()

        def verify(bundle, *args, **kwargs):
            if bundle.parent.name.startswith(".smr-update-"):
                raise updates.UpdateError("bad candidate")
            return self.verify(bundle, *args, **kwargs)

        with (
            patch.object(updates, "verify_publisher", verify),
            patch.object(updates.subprocess, "run", copy),
        ):
            with self.assertRaisesRegex(updates.UpdateError, "bad candidate"):
                updates.prepare_install(
                    helper, target, self.release, pending.parent.parent, "ABCDEFGHIJ", "0.3.0"
                )
        self.assertFalse(pending.exists())
        self.assertEqual(list(target.parent.glob(".smr-update-*")), [])
        self.assertEqual((target / "Contents/version").read_text(), "0.3.0")
        self.assertTrue(helper.exists())

    def test_failed_post_exchange_verification_rolls_back_without_finalizing(self):
        target, candidate, backup, helper, pending = self.fixture()
        calls = 0

        def verify(bundle, team, version, **kwargs):
            nonlocal calls
            if bundle == target and version == "0.4.0":
                calls += 1
                raise updates.UpdateError("verification failed")
            self.verify(bundle, team, version, **kwargs)

        with (
            patch.object(update_helper, "verify_publisher", verify),
            patch.object(update_helper, "current_app_bundle", return_value=helper),
            patch.object(update_helper, "trusted_team_from_bundle", return_value="ABCDEFGHIJ"),
            patch.object(update_helper.os, "kill", side_effect=ProcessLookupError),
        ):
            with self.assertRaises(updates.UpdateError):
                update_helper.main(pending)
        self.assertEqual(calls, 2)
        self.assertEqual((target / "Contents/version").read_text(), "0.3.0")
        self.assertEqual((candidate / "Contents/version").read_text(), "0.4.0")
        self.assertEqual(json.loads(pending.read_text())["state"], "ready")
        self.assertFalse(backup.exists())

    def test_restart_after_backup_rename_is_idempotent(self):
        target, candidate, backup, helper, pending = self.fixture()
        update_helper._swap(target, candidate)
        candidate.rename(backup)
        with patch.object(update_helper, "verify_publisher", self.verify):
            self.assertTrue(update_helper.reconcile_after_exchange(pending, "0.4.0"))
            self.assertTrue(update_helper.reconcile_after_exchange(pending, "0.4.0"))
        self.assertTrue(helper.exists())
        self.assertTrue(backup.exists())
        self.assertEqual(json.loads(pending.read_text())["state"], "installed")

    def test_wrong_startup_version_never_finalizes_ready_update(self):
        target, candidate, backup, helper, pending = self.fixture()
        before = pending.read_bytes()
        with patch.object(update_helper, "verify_publisher", side_effect=AssertionError("verify")):
            self.assertFalse(update_helper.reconcile_after_exchange(pending, "0.3.0"))
        self.assertEqual(pending.read_bytes(), before)

    def test_stage_checks_zip_before_unpacking_and_removes_failed_stage(self):
        directory = self.root / "library"
        archive = self.root / "release.zip"
        archive.write_bytes(self.payload)
        with (
            patch.object(updates, "inspect_app_zip", side_effect=updates.UpdateError("unsafe zip")),
            patch.object(updates.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(updates.UpdateError, "unsafe zip"):
                updates.stage_release(archive, self.release, directory, "ABCDEFGHIJ")
            run.assert_not_called()
        with (
            patch.object(updates, "inspect_app_zip"),
            patch.object(updates.subprocess, "run") as run,
        ):
            run.return_value.returncode = 1
            with self.assertRaisesRegex(updates.UpdateError, "unpack"):
                updates.stage_release(archive, self.release, directory, "ABCDEFGHIJ")
        self.assertEqual(list((directory / "updates").glob(".app-stage-*")), [])

    def test_stage_verifies_publisher_and_preserves_only_successful_output(self):
        archive = self.root / "release.zip"
        archive.write_bytes(self.payload)
        library = self.root / "library"

        def unpack(command, **kwargs):
            bundle = Path(command[-1]) / updates.BUNDLE_NAME
            (bundle / "Contents").mkdir(parents=True)
            (bundle / "Contents/version").write_text("0.4.0")
            return type("Result", (), {"returncode": 0})()

        with (
            patch.object(updates, "inspect_app_zip"),
            patch.object(updates.subprocess, "run", unpack),
        ):
            with patch.object(
                updates, "verify_publisher", side_effect=updates.UpdateError("bad signature")
            ):
                with self.assertRaisesRegex(updates.UpdateError, "bad signature"):
                    updates.stage_release(archive, self.release, library, "ABCDEFGHIJ")
            self.assertEqual(list((library / "updates").glob(".app-stage-*")), [])
            with patch.object(updates, "verify_publisher", self.verify):
                bundle = updates.stage_release(archive, self.release, library, "ABCDEFGHIJ")
            self.assertEqual((bundle / "Contents/version").read_text(), "0.4.0")
        self.assertTrue(archive.exists())

    def test_installed_pending_record_returns_no_install_request(self):
        target, candidate, backup, helper, pending = self.fixture()
        record = json.loads(pending.read_text())
        record["state"] = "installed"
        pending.write_text(json.dumps(record))
        (target / "Contents/version").write_text("0.4.0")
        (backup / "Contents").mkdir(parents=True)
        (backup / "Contents/version").write_text("0.3.0")
        with patch.object(update_helper, "verify_publisher", self.verify):
            self.assertIsNone(updates.pending_install(pending.parent.parent, target, "0.4.0"))
        self.assertTrue(helper.exists())


class UpdateRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.library = self.root / "library"
        self.updates = self.library / "updates"
        self.updates.mkdir(parents=True)
        self.target = self.root / "Applications" / updates.BUNDLE_NAME
        self.target.mkdir(parents=True)
        (self.target / "Contents").mkdir()
        (self.target / "Contents/version").write_text("0.5.0")
        self.old = self.transaction("1" * 32, "0.3.0", "0.4.0")
        self.current = self.transaction("2" * 32, "0.4.0", "0.5.0")
        self.pending = self.updates / "pending.json"
        self.pending.write_text(json.dumps(self.current))
        self.verify = test_updates.UpdateTests._verify_fixture

    def transaction(self, token, old, new):
        helper = self.updates / (".app-stage-" + token) / updates.BUNDLE_NAME
        backup = self.target.parent / (".smr-previous-" + old + "-" + token + ".app")
        for bundle, version in ((helper, new), (backup, old)):
            (bundle / "Contents").mkdir(parents=True)
            (bundle / "Contents/version").write_text(version)
        payload = new.encode()
        digest = hashlib.sha256(payload).hexdigest()
        archive = self.updates / (digest + ".zip")
        archive.write_bytes(payload)
        (helper.parent / ".archive.json").write_text(
            json.dumps(dict(sha256=digest, size=len(payload)))
        )
        record = dict(
            schema=1,
            state="installed",
            target=str(self.target),
            candidate=str(self.target.parent / (".smr-update-" + token) / updates.BUNDLE_NAME),
            backup=str(backup),
            helper_bundle=str(helper),
            team="ABCDEFGHIJ",
            old_version=old,
            new_version=new,
            helper_pid=987654321,
            archive_sha256=digest,
            archive_size=len(payload),
        )
        history = self.updates / "completed"
        history.mkdir(exist_ok=True)
        (history / (token + ".json")).write_text(json.dumps(record))
        return record

    def prune(self):
        with (
            patch.object(update_helper, "verify_publisher", self.verify),
            patch.object(update_helper.os, "kill", side_effect=ProcessLookupError),
        ):
            return update_helper.prune_completed_updates(self.pending, self.target, "0.5.0")

    def test_startup_prunes_only_completed_superseded_artifacts_and_keeps_rollback(self):
        unknown = self.updates / "unknown.zip"
        unknown.write_bytes(b"unowned")
        self.prune()
        self.assertFalse(Path(self.old["backup"]).exists())
        self.assertFalse(Path(self.old["helper_bundle"]).parent.exists())
        self.assertFalse((self.updates / (self.old["archive_sha256"] + ".zip")).exists())
        self.assertTrue(Path(self.current["backup"]).exists())
        self.assertTrue(Path(self.current["helper_bundle"]).exists())
        self.assertTrue(unknown.exists())
        self.assertEqual(self.prune(), [])

    def test_ready_or_wrong_startup_never_prunes(self):
        for record, version in (
            (dict(self.current, state="ready"), "0.5.0"),
            (self.current, "0.4.0"),
        ):
            self.pending.write_text(json.dumps(record))
            with patch.object(update_helper, "verify_publisher", self.verify):
                self.assertEqual(
                    update_helper.prune_completed_updates(self.pending, self.target, version), []
                )
            self.assertTrue(Path(self.old["backup"]).exists())

    def test_live_helper_protects_entire_transaction(self):
        with (
            patch.object(update_helper, "verify_publisher", self.verify),
            patch.object(update_helper.os, "kill"),
        ):
            self.assertEqual(
                update_helper.prune_completed_updates(self.pending, self.target, "0.5.0"), []
            )
        self.assertTrue(Path(self.old["backup"]).exists())
        self.assertTrue(Path(self.old["helper_bundle"]).exists())

    def test_verification_failure_preserves_artifacts(self):
        Path(self.old["backup"], "Contents/version").write_text("foreign")
        self.assertTrue(self.prune())
        self.assertTrue(Path(self.old["backup"]).exists())
        self.assertTrue(Path(self.old["helper_bundle"]).exists())

    def test_symlink_archive_refused_before_any_transaction_deletion(self):
        archive = self.updates / (self.old["archive_sha256"] + ".zip")
        outside = self.root / "outside"
        archive.rename(outside)
        archive.symlink_to(outside)
        self.assertTrue(self.prune())
        self.assertTrue(Path(self.old["backup"]).exists())
        self.assertEqual(outside.read_bytes(), b"0.4.0")

    def test_cleanup_error_is_reported_and_retryable(self):
        with patch.object(update_helper.shutil, "rmtree", side_effect=PermissionError("blocked")):
            self.assertTrue(self.prune())
        self.assertTrue(Path(self.old["backup"]).exists())
        self.prune()
        self.assertFalse(Path(self.old["backup"]).exists())

    def test_missing_current_rollback_prevents_superseded_deletion(self):
        shutil.rmtree(Path(self.current["backup"]))
        with self.assertRaises(updates.UpdateError):
            self.prune()
        self.assertTrue(Path(self.old["backup"]).exists())

    def test_unknown_receipt_or_helper_contents_prevent_deletion(self):
        path = self.updates / "completed" / ("1" * 32 + ".json")
        original = path.read_bytes()
        path.write_text("malformed")
        self.assertTrue(self.prune())
        self.assertTrue(Path(self.old["backup"]).exists())
        path.write_bytes(original)
        (Path(self.old["helper_bundle"]).parent / "foreign").write_bytes(b"preserve")
        self.assertTrue(self.prune())
        self.assertTrue(Path(self.old["backup"]).exists())

    def test_partial_backup_and_helper_deletion_resume_after_restart(self):
        for artifact in ("backup", "helper_bundle"):
            with self.subTest(artifact=artifact):
                # Each subcase has its own complete synthetic transaction.
                token = ("3" if artifact == "backup" else "4") * 32
                old = self.transaction(token, "0.2.0", "0.3.0")
                damaged = Path(old[artifact])
                removal_root = damaged if artifact == "backup" else damaged.parent
                real = update_helper.shutil.rmtree

                def partial(path, *args, **kwargs):
                    if Path(path) == removal_root:
                        (damaged / "Contents/version").unlink()
                        raise SystemExit("interrupted after child deletion")
                    return real(path, *args, **kwargs)

                with patch.object(update_helper.shutil, "rmtree", partial):
                    with self.assertRaises(SystemExit):
                        self.prune()
                self.assertTrue(removal_root.exists())
                self.assertEqual(self.prune(), [])
                self.assertFalse(removal_root.exists())
                self.assertFalse((self.updates / "completed" / (token + ".json")).exists())

    def test_partial_cleanup_refuses_foreign_remaining_contents(self):
        damaged = Path(self.old["backup"])
        real = update_helper.shutil.rmtree

        def partial(path, *args, **kwargs):
            if Path(path) == damaged:
                (damaged / "Contents/version").unlink()
                raise PermissionError("interrupted after child deletion")
            return real(path, *args, **kwargs)

        with patch.object(update_helper.shutil, "rmtree", partial):
            self.assertTrue(self.prune())
        foreign = damaged / "foreign"
        foreign.write_bytes(b"must remain")
        self.assertTrue(self.prune())
        self.assertEqual(foreign.read_bytes(), b"must remain")
        self.assertTrue(Path(self.old["helper_bundle"]).exists())

    def test_incomplete_or_unsafe_receipt_blocks_every_transaction_in_either_order(self):
        path = self.updates / "completed" / ("f" * 32 + ".json")
        real = Path.iterdir
        for bad in (
            dict(schema=1, state="installed"),
            dict(self.old, candidate="/outside/unsafe"),
            dict(self.old, new_version="invalid"),
        ):
            for reverse in (False, True):
                with self.subTest(bad=bad, reverse=reverse):
                    path.write_text(json.dumps(bad))

                    def ordered(directory):
                        entries = list(real(directory))
                        return (
                            iter(sorted(entries, reverse=reverse))
                            if directory == path.parent
                            else iter(entries)
                        )

                    with patch.object(Path, "iterdir", ordered):
                        self.assertTrue(self.prune())
                    self.assertTrue(Path(self.old["backup"]).exists())
                    self.assertTrue(Path(self.old["helper_bundle"]).exists())
        path.unlink()

    def test_shared_archive_remains_retryable_after_other_receipt_finishes(self):
        self.transaction("5" * 32, "0.2.0", "0.4.0")
        damaged = Path(self.old["backup"])
        real = update_helper.shutil.rmtree

        def partial(path, *args, **kwargs):
            if Path(path) == damaged:
                (damaged / "Contents/version").unlink()
                raise PermissionError("interrupted")
            return real(path, *args, **kwargs)

        with patch.object(update_helper.shutil, "rmtree", partial):
            self.assertTrue(self.prune())
        self.assertEqual(self.prune(), [])
        self.assertFalse((self.updates / (self.old["archive_sha256"] + ".zip")).exists())

    def test_cleanup_inventory_propagates_directory_read_failure(self):
        def walk(root, **kwargs):
            if kwargs.get("onerror"):
                kwargs["onerror"](PermissionError("unreadable child"))
            return iter(())

        with patch.object(update_helper.os, "walk", walk):
            with self.assertRaises(PermissionError):
                update_helper._cleanup_manifest(Path(self.old["backup"]))
