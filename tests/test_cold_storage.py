"""Synthetic byte and metadata contracts for compact inactive trees."""

from pathlib import Path
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.cold_storage import ColdStorage, ColdStorageError, _set_xattr


class ColdStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name).resolve()
        self.store = ColdStorage(self.library)
        self.tree = self.library / "prepared" / ("a" * 32)
        (self.tree / "CustomAssets/empty").mkdir(parents=True)
        (self.tree / "Saves").mkdir()
        (self.tree / "CustomAssets/asset").write_bytes(b"map asset" * 10000)
        (self.tree / "Saves/future.sav").write_bytes(b"save" * 50000)
        (self.tree / "Settings.ini").write_bytes(b"personal settings")
        for path in sorted(self.tree.rglob("*"), reverse=True):
            os.utime(path, ns=(1_500_000_000_123456789, 1_600_000_000_987654321))
        os.utime(self.tree, ns=(1_500_000_000_123456789, 1_600_000_000_987654321))

    def test_roundtrip_preserves_bytes_empty_directories_modes_mtimes_xattrs(self):
        _set_xattr(self.tree / "Settings.ini", "user.smr-test", b"metadata")
        before = self.store.inspect(self.tree)
        self.store.pack(self.tree, expected=before)
        self.assertFalse(self.tree.exists())
        self.assertEqual(self.store.read_file(self.tree, "Settings.ini", 256), b"personal settings")
        self.assertFalse(self.tree.exists())
        self.store.materialize(self.tree)
        self.assertEqual(self.store.inspect(self.tree), before)
        self.assertFalse(self.store.is_cold(self.tree))

    def test_stale_manifest_refuses_before_any_tree_removal(self):
        before = self.store.inspect(self.tree)
        (self.tree / "Saves/new.sav").write_bytes(b"future save")
        with self.assertRaises(ColdStorageError):
            self.store.pack(self.tree, expected=before)
        self.assertTrue((self.tree / "Saves/new.sav").exists())
        self.assertFalse(self.store.is_cold(self.tree))

    def test_shared_asset_bytes_compress_once_without_merging_file_metadata(self):
        import shutil

        other = self.tree.with_name("b" * 32)
        shutil.copytree(self.tree, other)
        os.utime(other / "Settings.ini", ns=(0, 123456789))
        self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        count = len(list((self.library / "cold-storage/blobs").glob("*/*")))
        self.store.pack(other, expected=self.store.inspect(other))
        self.assertEqual(len(list((self.library / "cold-storage/blobs").glob("*/*"))), count)
        self.store.materialize(other)
        self.assertEqual((other / "Settings.ini").stat().st_mtime_ns, 123456789)

    def test_corrupt_blob_never_replaces_a_complete_tree(self):
        self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        next((self.library / "cold-storage/blobs").glob("*/*")).write_bytes(b"corrupt")
        with self.assertRaises(ColdStorageError):
            self.store.materialize(self.tree)
        self.assertFalse(self.tree.exists())
        self.assertTrue(self.store.is_cold(self.tree))

    def test_cancellation_before_commit_keeps_original_and_reports_progress(self):
        before = self.store.inspect(self.tree)
        progress = []
        with self.assertRaisesRegex(ColdStorageError, "cancelled"):
            self.store.pack(
                self.tree,
                expected=before,
                cancelled=lambda: bool(progress),
                progress=lambda *values: progress.append(values),
            )
        self.assertEqual(self.store.inspect(self.tree), before)
        self.assertFalse(self.store.is_cold(self.tree))

    def test_interruption_after_manifest_and_after_rename_recovers_exact_tree(self):
        for point in ("after_manifest", "after_rename", "after_verified", "after_materialize"):
            with self.subTest(point=point):
                before = self.store.inspect(self.tree)

                def stop(stage):
                    if stage == point:
                        raise RuntimeError("interruption")

                self.store.fault_hook = stop
                if point == "after_materialize":
                    self.store.fault_hook = lambda stage: None
                    self.store.pack(self.tree, expected=before)
                    self.store.fault_hook = stop
                    with self.assertRaisesRegex(RuntimeError, "interruption"):
                        self.store.materialize(self.tree)
                else:
                    with self.assertRaisesRegex(RuntimeError, "interruption"):
                        self.store.pack(self.tree, expected=before)
                self.store.fault_hook = lambda stage: None
                self.store.recover(self.tree)
                self.store.materialize(self.tree)
                self.assertEqual(self.store.inspect(self.tree), before)

    def test_symlinks_hardlinks_and_outside_roots_refused(self):
        outside = self.library.parent / "outside"
        with self.assertRaises(ColdStorageError):
            self.store.inspect(outside)
        link = self.tree / "link"
        link.symlink_to(self.tree / "Settings.ini")
        with self.assertRaises(RuntimeError):
            self.store.inspect(self.tree)
        link.unlink()
        os.link(self.tree / "Settings.ini", link)
        with self.assertRaises(RuntimeError):
            self.store.inspect(self.tree)

    def test_manifest_traversal_and_bounded_reads_refuse_without_writes(self):
        self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        with self.assertRaises(ColdStorageError):
            self.store.read_file(self.tree, "../Settings.ini", 1024)
        with self.assertRaises(ColdStorageError):
            self.store.read_file(self.tree, "Saves/future.sav", 10)
        self.assertFalse(self.tree.exists())

    def test_equal_save_payloads_restore_as_independent_files(self):
        first = self.tree / "Saves/future.sav"
        second = self.tree / "Saves/other.sav"
        second.write_bytes(first.read_bytes())
        self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        self.store.materialize(self.tree)
        self.assertNotEqual(first.stat().st_ino, second.stat().st_ino)
        first.write_bytes(b"changed independently")
        self.assertEqual(second.read_bytes(), b"save" * 50000)
        self.assertEqual(first.stat().st_nlink, 1)
        self.assertEqual(second.stat().st_nlink, 1)

    def test_nonempty_native_acl_survives_roundtrip(self):
        import subprocess

        if sys.platform != "darwin":
            self.skipTest("macOS ACL preservation")
        subprocess.run(
            ["/bin/chmod", "+a", "everyone allow read", str(self.tree / "Settings.ini")], check=True
        )
        before = self.store.inspect(self.tree)
        self.assertTrue(before["Settings.ini"]["acl"])
        self.store.pack(self.tree, expected=before)
        self.store.materialize(self.tree)
        self.assertEqual(self.store.inspect(self.tree), before)

    def test_partial_backup_disposal_is_recoverable(self):
        from unittest.mock import patch
        import shutil

        before = self.store.inspect(self.tree)
        original = shutil.rmtree

        def partial(path):
            if Path(path).name.startswith(".cold-backup-"):
                (Path(path) / "Settings.ini").unlink()
                raise OSError("interrupted disposal")
            return original(path)

        with patch("smr_launcher.cold_storage.shutil.rmtree", side_effect=partial):
            with self.assertRaisesRegex(OSError, "interrupted disposal"):
                self.store.pack(self.tree, expected=before)
        self.store.recover(self.tree)
        self.store.materialize(self.tree)
        self.assertEqual(self.store.inspect(self.tree), before)

    def test_interrupted_roundtrip_check_has_recorded_cleanup_and_keeps_original(self):
        from unittest.mock import patch

        before = self.store.inspect(self.tree)

        def interrupted(path, entries, **kwargs):
            path.mkdir()
            kwargs["created"](path, False)
            (path / "Settings.ini").write_bytes(b"personal")
            raise RuntimeError("killed during roundtrip")

        with (
            patch.object(self.store, "_restore", side_effect=interrupted),
            patch.object(self.store, "_discard", side_effect=RuntimeError("cleanup interrupted")),
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup interrupted"):
                self.store.pack(self.tree, expected=before)
        self.store.recover(self.tree)
        self.assertEqual(self.store.inspect(self.tree), before)
        self.assertEqual(list(self.tree.parent.glob(".cold-check-*")), [])
        self.assertFalse(self.store.is_cold(self.tree))

    def test_r1_later_backup_save_refuses_and_preserves_every_survivor(self):
        def stop(point):
            if point == "after_verified":
                raise RuntimeError("interruption")

        self.store.fault_hook = stop
        with self.assertRaises(RuntimeError):
            self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        value = self.store._load(self.tree)
        backup = self.tree.with_name(value["backup"])
        later = backup / "Saves/later.sav"
        later.write_bytes(b"new save after compact verification")
        self.store.fault_hook = lambda point: None
        with self.assertRaises(ColdStorageError):
            self.store.recover(self.tree)
        self.assertEqual(later.read_bytes(), b"new save after compact verification")

    def test_r1_changed_survivor_after_partial_disposal_is_not_deleted(self):
        from unittest.mock import patch
        import shutil

        original = shutil.rmtree

        def partial(path):
            if Path(path).name.startswith(".cold-backup-"):
                (Path(path) / "Settings.ini").unlink()
                raise OSError("partial disposal")
            return original(path)

        with patch("smr_launcher.cold_storage.shutil.rmtree", side_effect=partial):
            with self.assertRaises(OSError):
                self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        backup = self.tree.with_name(self.store._load(self.tree)["backup"])
        save = backup / "Saves/future.sav"
        save.write_bytes(b"new surviving save contents")
        with self.assertRaises(ColdStorageError):
            self.store.recover(self.tree)
        self.assertEqual(save.read_bytes(), b"new surviving save contents")

    def test_r2_uncreated_restore_scratch_remains_foreign_on_recover_and_materialize(self):
        self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        value = self.store._load(self.tree)
        scratch = self.tree.with_name(".cold-restore-" + value["backup"][-32:])
        scratch.mkdir()
        foreign = scratch / "foreign.txt"
        foreign.write_bytes(b"unowned file")
        for operation in (self.store.recover, self.store.materialize):
            with self.assertRaises(ColdStorageError):
                operation(self.tree)
            self.assertEqual(foreign.read_bytes(), b"unowned file")

    def test_r2_preexisting_check_destination_is_not_owned_by_finally(self):
        from unittest.mock import patch

        foreign = []

        def collision(path, entries, **kwargs):
            path.mkdir()
            target = path / "foreign.txt"
            target.write_bytes(b"foreign collision")
            foreign.append(target)
            raise FileExistsError("preexisting scratch")

        with patch.object(self.store, "_restore", side_effect=collision):
            with self.assertRaises((ColdStorageError, FileExistsError)):
                self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        self.assertEqual(foreign[0].read_bytes(), b"foreign collision")

    def test_r2_replaced_check_scratch_is_not_deleted_by_finally(self):
        from unittest.mock import patch

        original = self.store._restore
        foreign = []

        def replacement(path, entries, **kwargs):
            original(path, entries, **kwargs)
            path.rename(path.with_name(path.name + "-owned"))
            path.mkdir()
            target = path / "foreign.txt"
            target.write_bytes(b"foreign replacement")
            foreign.append(target)
            raise RuntimeError("replaced during roundtrip")

        with patch.object(self.store, "_restore", side_effect=replacement):
            with self.assertRaises(RuntimeError):
                self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        self.assertEqual(foreign[0].read_bytes(), b"foreign replacement")

    def test_r2_blob_temporary_collision_preserves_foreign_file(self):
        from unittest.mock import patch
        from types import SimpleNamespace

        source = self.tree / "Settings.ini"
        entry = self.store.inspect(self.tree)["Settings.ini"]
        blob = self.store._blob(entry["sha256"])
        blob.parent.mkdir(parents=True)
        temporary = blob.with_name(blob.name + ".tmp-" + "c" * 32)
        temporary.write_bytes(b"foreign temporary")
        with patch(
            "smr_launcher.cold_storage.uuid.uuid4", return_value=SimpleNamespace(hex="c" * 32)
        ):
            with self.assertRaises(FileExistsError):
                self.store._save_blob(source, entry)
        self.assertEqual(temporary.read_bytes(), b"foreign temporary")

    def test_r2_profile_marker_collision_preserves_foreign_file(self):
        from unittest.mock import patch
        from types import SimpleNamespace

        self.store.blobs.parent.mkdir(parents=True)
        temporary = self.store.blobs.parent / (".profile-marker-" + "d" * 32)
        temporary.write_bytes(b"foreign marker")
        with patch(
            "smr_launcher.cold_storage.uuid.uuid4", return_value=SimpleNamespace(hex="d" * 32)
        ):
            with self.assertRaises(FileExistsError):
                self.store.register_profile(
                    self.tree, self.tree.with_name("profile"), {"profile": "test"}
                )
        self.assertEqual(temporary.read_bytes(), b"foreign marker")

    def test_r1_identical_replacement_backup_is_not_owned(self):
        import shutil

        def stop(point):
            if point == "after_verified":
                raise RuntimeError("interrupted")

        self.store.fault_hook = stop
        with self.assertRaises(RuntimeError):
            self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        backup = self.tree.with_name(self.store._load(self.tree)["backup"])
        retained = backup.with_name(backup.name + "-original")
        backup.rename(retained)
        shutil.copytree(retained, backup)
        with self.assertRaises(ColdStorageError):
            self.store.recover(self.tree)
        self.assertTrue((backup / "Saves/future.sav").exists())
        self.assertTrue((retained / "Saves/future.sav").exists())

    def test_r2_owned_partial_scratch_with_added_content_is_retained(self):
        from unittest.mock import patch

        before = self.store.inspect(self.tree)
        scratches = []

        def interrupted(path, entries, **kwargs):
            path.mkdir()
            kwargs["created"](path, False)
            (path / "Settings.ini").write_bytes(b"personal")
            (path / "later.sav").write_bytes(b"foreign data")
            scratches.append(path)
            raise RuntimeError("interrupted")

        with patch.object(self.store, "_restore", side_effect=interrupted):
            with self.assertRaises(ColdStorageError):
                self.store.pack(self.tree, expected=before)
        with self.assertRaises(ColdStorageError):
            self.store.recover(self.tree)
        self.assertEqual((scratches[0] / "later.sav").read_bytes(), b"foreign data")
        self.assertEqual(self.store.inspect(self.tree), before)

    def test_r2_replaced_restore_scratch_is_preserved_in_finally(self):
        from unittest.mock import patch

        self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        original = self.store._restore
        foreign = []

        def replaced(path, entries, **kwargs):
            original(path, entries, **kwargs)
            path.rename(path.with_name(path.name + "-original"))
            path.mkdir()
            target = path / "foreign.txt"
            target.write_bytes(b"foreign restore replacement")
            foreign.append(target)
            raise RuntimeError("replacement")

        with patch.object(self.store, "_restore", side_effect=replaced):
            with self.assertRaises(ColdStorageError):
                self.store.materialize(self.tree)
        self.assertEqual(foreign[0].read_bytes(), b"foreign restore replacement")
        with self.assertRaises(ColdStorageError):
            self.store.recover(self.tree)

    def test_r2_safe_partial_scratch_disposal_resumes_after_interruption(self):
        from unittest.mock import patch
        import shutil

        before = self.store.inspect(self.tree)
        original = shutil.rmtree

        def partial(path):
            if Path(path).name.startswith(".cold-check-"):
                (Path(path) / "Settings.ini").unlink()
                raise OSError("partial scratch disposal")
            return original(path)

        with patch("smr_launcher.cold_storage.shutil.rmtree", side_effect=partial):
            with self.assertRaises(OSError):
                self.store.pack(self.tree, expected=before)
        self.store.recover(self.tree)
        self.assertEqual(self.store.inspect(self.tree), before)
        self.assertEqual(list(self.tree.parent.glob(".cold-check-*")), [])

    def test_r6_root_metadata_interruption_recovers_owned_check_scratch(self):
        self._root_metadata_interruption("check")

    def test_r6_root_metadata_interruption_recovers_owned_restore_scratch(self):
        self._root_metadata_interruption("restore")

    def test_r6_process_death_after_check_metadata_recovers_durable_transition(self):
        self._root_metadata_interruption("check", skip_cleanup=True)

    def test_r6_process_death_after_restore_metadata_recovers_durable_transition(self):
        self._root_metadata_interruption("restore", skip_cleanup=True)

    def _root_metadata_interruption(self, operation, skip_cleanup=False):
        from unittest.mock import patch
        from smr_launcher.cold_storage import _restore_metadata

        before = self.store.inspect(self.tree)
        if operation == "restore":
            self.store.pack(self.tree, expected=before)
        observed = []

        def interrupted(path, entry):
            _restore_metadata(path, entry)
            if path.name.startswith(".cold-" + operation + "-"):
                observed.append(path)
                raise RuntimeError("interrupted after actual root metadata")

        cleanup = self.store._clean_scratch

        def retained(*args):
            if skip_cleanup and observed:
                raise RuntimeError("interrupted after actual root metadata; cleanup not run")
            return cleanup(*args)

        with (
            patch("smr_launcher.cold_storage._restore_metadata", side_effect=interrupted),
            patch.object(self.store, "_clean_scratch", side_effect=retained),
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted after actual root metadata"):
                if operation == "check":
                    self.store.pack(self.tree, expected=before)
                else:
                    self.store.materialize(self.tree)
        self.assertEqual(len(observed), 1)
        self.store.recover(self.tree)
        if operation == "restore":
            self.store.materialize(self.tree)
        self.assertEqual(self.store.inspect(self.tree), before)
        self.assertFalse(observed[0].exists())
        self.assertFalse(self.store.is_cold(self.tree))

    def test_r6_planned_birth_transition_does_not_adopt_changed_or_replaced_scratch(self):
        import shutil
        from unittest.mock import patch
        from smr_launcher.cold_storage import _restore_metadata

        before = self.store.inspect(self.tree)
        observed = []
        cleanup = self.store._clean_scratch

        def interrupted(path, entry):
            _restore_metadata(path, entry)
            if path.name.startswith(".cold-check-"):
                observed.append(path)
                raise RuntimeError("interrupted root metadata")

        def stopped_cleanup(*args):
            if observed:
                raise RuntimeError("interrupted root metadata")
            return cleanup(*args)

        with (
            patch("smr_launcher.cold_storage._restore_metadata", side_effect=interrupted),
            patch.object(self.store, "_clean_scratch", side_effect=stopped_cleanup),
        ):
            with self.assertRaises(RuntimeError):
                self.store.pack(self.tree, expected=before)
        scratch = observed[0]
        target = scratch / "Settings.ini"
        target.write_bytes(b"later foreign content")
        with self.assertRaises(ColdStorageError):
            self.store.recover(self.tree)
        self.assertEqual(target.read_bytes(), b"later foreign content")
        target.write_bytes(b"personal settings")
        _restore_metadata(target, before["Settings.ini"])
        _restore_metadata(scratch, before["."])
        owned = scratch.with_name(scratch.name + "-retained")
        scratch.rename(owned)
        shutil.copytree(owned, scratch)
        # Even exact expected content and planned birthtime cannot adopt a different inode.
        _restore_metadata(scratch, before["."])
        with self.assertRaises(ColdStorageError):
            self.store.recover(self.tree)
        self.assertEqual(self.store.inspect(scratch), before)
        self.assertEqual(self.store.inspect(self.tree), before)
