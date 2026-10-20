"""R7 synthetic OS-assigned provenance controls; no installed library access."""

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import smr_launcher.cold_storage as cold
from smr_launcher.cold_storage import ColdStorageError
import test_cold_storage
import test_compact_library

PROVENANCE = "com.apple.provenance"


@contextmanager
def assigned_provenance(restored=b"os-copy", original=b"original"):
    """Model labels by inode, surviving rename; use real bytes/metadata/consumers."""
    read = cold._xattrs
    write = cold._set_xattr
    labels = {}

    def attrs(path):
        result = read(path)
        result.pop(PROVENANCE, None)
        info = path.stat()
        key = (info.st_dev, info.st_ino)
        if key not in labels:
            labels[key] = (
                restored
                if path.name.startswith(".cold-")
                or any(part.startswith(".cold-") for part in path.parts)
                else original
            )
        if labels[key] is not None:
            result[PROVENANCE] = labels[key]
        return result

    def set_attr(path, name, data):
        if name != PROVENANCE:
            write(path, name, data)

    with (
        patch.object(cold, "_xattrs", side_effect=attrs),
        patch.object(cold, "_set_xattr", side_effect=set_attr),
    ):
        yield labels


def portable(entries):
    result = deepcopy(entries)
    for entry in result.values():
        entry["xattrs"].pop(PROVENANCE, None)
    return result


@unittest.skipUnless(sys.platform == "darwin", "macOS creation-owned provenance boundary")
class ProvenanceTests(unittest.TestCase):
    setUp = test_cold_storage.ColdStorageTests.setUp

    def test_r7_changed_missing_added_copy_provenance_roundtrips(self):
        for original, restored in ((b"original", b"copy"), (b"original", None), (None, b"copy")):
            with (
                self.subTest(original=original, restored=restored),
                assigned_provenance(restored, original),
            ):
                before = self.store.inspect(self.tree)
                self.store.pack(self.tree, expected=before)
                self.assertEqual(self.store._load(self.tree)["entries"], before)
                self.store.materialize(self.tree)
                self.assertEqual(portable(self.store.inspect(self.tree)), portable(before))
                self.assertFalse(self.store.is_cold(self.tree))

    def test_r7_source_and_backup_provenance_changes_remain_strict(self):
        with assigned_provenance(restored=b"original") as labels:
            before = self.store.inspect(self.tree)
            info = self.tree.stat()
            labels[info.st_dev, info.st_ino] = b"later-source"
            with self.assertRaises(ColdStorageError):
                self.store.pack(self.tree, expected=before)
            self.assertTrue(self.tree.exists())
            labels[info.st_dev, info.st_ino] = b"original"
            self.store.fault_hook = lambda stage: (_ for _ in ()).throw(RuntimeError("stop"))
            with self.assertRaisesRegex(RuntimeError, "stop"):
                self.store.pack(self.tree, expected=before)
            value = self.store._load(self.tree)
            self.tree.rename(self.tree.with_name(value["backup"]))
            labels[info.st_dev, info.st_ino] = b"later-backup"
            self.store.fault_hook = lambda stage: None
            with self.assertRaises(ColdStorageError):
                self.store.recover(self.tree)
            self.assertTrue(self.tree.with_name(value["backup"]).exists())

    def test_r7_source_and_verified_backup_other_metadata_remain_strict(self):
        with assigned_provenance(restored=b"original"):
            expected = self.store.inspect(self.tree)
            inspect = self.store.inspect
            for location in ("source", "backup"):
                if location == "backup":

                    def stop(stage):
                        if stage == "after_verified":
                            raise RuntimeError("verified stop")

                    self.store.fault_hook = stop
                    with self.assertRaisesRegex(RuntimeError, "verified stop"):
                        self.store.pack(self.tree, expected=expected)
                    self.store.fault_hook = lambda stage: None
                    value = self.store._load(self.tree)
                    target = self.tree.with_name(value["backup"])
                else:
                    target = self.tree
                for field in ("quarantine", "custom", "acl", "mode", "mtime", "content"):
                    with self.subTest(location=location, field=field):
                        changed = deepcopy(expected)
                        entry = changed["Settings.ini"]
                        if field in ("quarantine", "custom"):
                            entry["xattrs"][
                                "com.apple.quarantine"
                                if field == "quarantine"
                                else "unknown.attribute"
                            ] = "Y2hhbmdlZA=="
                        elif field == "acl":
                            entry["acl"] = "changed ACL"
                        elif field == "mode":
                            entry["mode"] ^= 0o100
                        elif field == "mtime":
                            entry["mtime_ns"] += 100
                        else:
                            entry["sha256"] = "0" * 64
                        with patch.object(self.store, "inspect", return_value=changed):
                            with self.assertRaises(ColdStorageError):
                                if location == "source":
                                    self.store.pack(self.tree, expected=expected)
                                else:
                                    self.store.recover(self.tree)
                        self.assertEqual(inspect(target), expected)

    def test_r7_checking_legacy_complete_scratch_recovers_without_original_change(self):
        with assigned_provenance():
            before = self.store.inspect(self.tree)
            cleanup = self.store._clean_scratch
            with patch.object(self.store, "_clean_scratch", side_effect=RuntimeError("death")):
                with self.assertRaises(RuntimeError):
                    self.store.pack(self.tree, expected=before)
            value = self.store._load(self.tree)
            self.assertEqual(value["phase"], "checking")
            self.assertTrue(value["check"]["complete"])
            # First installed failure predates restored readback fields.
            value["check"].pop("provenance", None)
            self.store._index(self.tree).write_text(json.dumps(value))
            cleanup(self.tree, value, "check")
            self.store.recover(self.tree)
            self.assertEqual(self.store.inspect(self.tree), before)
            self.assertFalse(self.store.is_cold(self.tree))

    def test_r7_restored_other_metadata_changes_refuse_and_keep_original(self):
        for field in ("quarantine", "custom", "acl", "mode", "mtime", "content"):
            with self.subTest(field=field), assigned_provenance():
                before = self.store.inspect(self.tree)
                restore = cold._restore_metadata
                inspect = self.store.inspect

                def altered(path, entry):
                    restore(path, entry)
                    if path.name == "Settings.ini":
                        if field in ("quarantine", "custom"):
                            cold._set_xattr(
                                path,
                                "com.apple.quarantine" if field == "quarantine" else "user.smr-r7",
                                b"changed",
                            )
                        elif field == "mode":
                            os.chmod(path, entry["mode"] ^ 0o100)
                        elif field == "mtime":
                            os.utime(path, ns=(entry["mtime_ns"] + 100, entry["mtime_ns"] + 100))
                        elif field == "content":
                            path.write_bytes(b"changed")

                def inspected(path, **kwargs):
                    result = inspect(path, **kwargs)
                    if field == "acl" and path.name.startswith(".cold-"):
                        result["Settings.ini"]["acl"] = "synthetic changed ACL"
                    return result

                with (
                    patch.object(cold, "_restore_metadata", side_effect=altered),
                    patch.object(self.store, "inspect", side_effect=inspected),
                ):
                    with self.assertRaises(ColdStorageError):
                        self.store.pack(self.tree, expected=before)
                self.assertEqual(inspect(self.tree), before)
                # Each refusal is a distinct retained synthetic transaction.
                value = self.store._load(self.tree)
                scratch = self.tree.with_name(".cold-check-" + value["backup"][-32:])
                if scratch.exists():
                    import shutil

                    shutil.rmtree(scratch)
                self.store._index(self.tree).unlink()

    def test_r7_death_before_complete_readback_recovers_only_owned_portable_tree(self):
        for operation in ("check", "restore"):
            with self.subTest(operation=operation), assigned_provenance():
                before = self.store.inspect(self.tree)
                if operation == "restore":
                    self.store.pack(self.tree, expected=before)
                restore = cold._restore_metadata

                def interrupted(path, entry):
                    restore(path, entry)
                    if path.name.startswith(".cold-" + operation + "-"):
                        raise RuntimeError("death after root metadata")

                with (
                    patch.object(cold, "_restore_metadata", side_effect=interrupted),
                    patch.object(self.store, "_clean_scratch", side_effect=RuntimeError("death")),
                ):
                    with self.assertRaisesRegex(RuntimeError, "death"):
                        if operation == "check":
                            self.store.pack(self.tree, expected=before)
                        else:
                            self.store.materialize(self.tree)
                self.store.recover(self.tree)
                self.store.materialize(self.tree)
                self.assertEqual(portable(self.store.inspect(self.tree)), portable(before))

    def test_r7_completed_copy_later_provenance_change_is_retained(self):
        with assigned_provenance() as labels:
            before = self.store.inspect(self.tree)
            with patch.object(self.store, "_clean_scratch", side_effect=RuntimeError("death")):
                with self.assertRaisesRegex(RuntimeError, "death"):
                    self.store.pack(self.tree, expected=before)
            value = self.store._load(self.tree)
            scratch = self.tree.with_name(".cold-check-" + value["backup"][-32:])
            info = scratch.stat()
            labels[info.st_dev, info.st_ino] = b"later-provenance"
            with self.assertRaises(ColdStorageError):
                self.store.recover(self.tree)
            self.assertTrue(scratch.exists())
            self.assertEqual(self.store.inspect(self.tree), before)

    def test_r7_r1_late_label_before_receipt_refuses_publication_and_keeps_scratch(self):
        with assigned_provenance() as labels:
            before = self.store.inspect(self.tree)
            self.store.pack(self.tree, expected=before)
            checked = self.store._scratch_matches

            def later_edit(path, value, record):
                accepted = checked(path, value, record)
                if accepted and path.name.startswith(".cold-restore-"):
                    info = path.stat()
                    labels[info.st_dev, info.st_ino] = b"late-receipt-edit"
                return accepted

            with patch.object(self.store, "_scratch_matches", side_effect=later_edit):
                with self.assertRaises(ColdStorageError):
                    self.store.materialize(self.tree)
            self.assertFalse(self.tree.exists())
            self.assertFalse(self.store._restored_index(self.tree).exists())
            value = self.store._load(self.tree)
            scratch = self.tree.with_name(".cold-restore-" + value["backup"][-32:])
            self.assertTrue(scratch.exists())
            self.assertEqual((scratch / "Settings.ini").read_bytes(), b"personal settings")

    def test_r7_r1_late_label_before_cleaning_refuses_and_keeps_original(self):
        with assigned_provenance() as labels:
            before = self.store.inspect(self.tree)
            checked = self.store._scratch_matches

            def later_edit(path, value, record):
                accepted = checked(path, value, record)
                if accepted and path.name.startswith(".cold-check-"):
                    info = path.stat()
                    labels[info.st_dev, info.st_ino] = b"late-cleaning-edit"
                return accepted

            with patch.object(self.store, "_scratch_matches", side_effect=later_edit):
                with self.assertRaises(ColdStorageError):
                    self.store.pack(self.tree, expected=before)
            self.assertEqual(self.store.inspect(self.tree), before)
            value = self.store._load(self.tree)
            scratch = self.tree.with_name(".cold-check-" + value["backup"][-32:])
            self.assertTrue(scratch.exists())
            self.assertFalse(value["check"]["cleaning"])

    def test_r7_r1_late_label_at_disposal_readback_keeps_original_and_scratch(self):
        with assigned_provenance() as labels:
            before = self.store.inspect(self.tree)
            discard = self.store._discard

            def later_edit(path, **kwargs):
                if path.name.startswith(".cold-check-"):
                    info = path.stat()
                    labels[info.st_dev, info.st_ino] = b"late-disposal-edit"
                return discard(path, **kwargs)

            with patch.object(self.store, "_discard", side_effect=later_edit):
                with self.assertRaises(ColdStorageError):
                    self.store.pack(self.tree, expected=before)
            self.assertEqual(self.store.inspect(self.tree), before)
            value = self.store._load(self.tree)
            scratch = self.tree.with_name(".cold-check-" + value["backup"][-32:])
            self.assertTrue(scratch.exists())

    def test_r7_repeated_index_unlink_interruption_retains_creation_record(self):
        with assigned_provenance():
            before = self.store.inspect(self.tree)
            self.store.pack(self.tree, expected=before)
            unlink = Path.unlink
            index = self.store._index(self.tree)

            def interrupted(path, *args, **kwargs):
                if path == index:
                    raise OSError("index unlink interrupted")
                return unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", interrupted):
                with self.assertRaisesRegex(OSError, "index unlink interrupted"):
                    self.store.materialize(self.tree)
                with self.assertRaisesRegex(OSError, "index unlink interrupted"):
                    self.store.recover(self.tree)
                self.assertTrue(self.store._load(self.tree)["restore"]["complete"])
            self.store.recover(self.tree)
            self.assertFalse(self.store.is_cold(self.tree))
            self.assertEqual(portable(self.store.inspect(self.tree)), portable(before))

    def test_r7_materialization_process_interruption_recovers(self):
        with assigned_provenance():
            before = self.store.inspect(self.tree)
            self.store.pack(self.tree, expected=before)
            self.store.fault_hook = lambda stage: (_ for _ in ()).throw(RuntimeError("death"))
            with self.assertRaises(RuntimeError):
                self.store.materialize(self.tree)
            self.store.fault_hook = lambda stage: None
            self.store.recover(self.tree)
            self.assertEqual(portable(self.store.inspect(self.tree)), portable(before))
            self.assertFalse(self.store.is_cold(self.tree))


@unittest.skipUnless(sys.platform == "darwin", "macOS creation-owned provenance boundary")
class ProvenanceApplicationTests(unittest.TestCase):
    setUp = test_compact_library.CompactLibraryTests.setUp

    def prepare(self):
        app = self.application
        app.setup()
        app.import_archive(self.archive)
        return app, app.plan_compaction()

    def test_r7_apply_resume_and_rollback_resume_use_observed_copy_metadata(self):
        with assigned_provenance():
            app, plan = self.prepare()
            app.cold_storage.fault_hook = lambda stage: (_ for _ in ()).throw(RuntimeError("death"))
            with self.assertRaises(RuntimeError):
                app.compact_library(plan)
            app.cold_storage.fault_hook = lambda stage: None
            app.compact_library(plan)
            app.cold_storage.fault_hook = lambda stage: (_ for _ in ()).throw(RuntimeError("death"))
            with self.assertRaises(RuntimeError):
                app.rollback_compaction(plan)
            app.cold_storage.fault_hook = lambda stage: None
            app.rollback_compaction(plan)
            for tree in plan["trees"]:
                self.assertEqual(
                    portable(app.cold_storage.inspect(app.library / tree["path"])),
                    portable(tree["entries"]),
                )
            self.assertFalse((app.library / "compact-library.json").exists())

    def test_r7_normal_materialization_allows_rollback_but_later_provenance_edit_refuses(self):
        with assigned_provenance() as labels:
            app, plan = self.prepare()
            app.compact_library(plan)
            path = app.library / plan["trees"][0]["path"]
            app.cold_storage.materialize(path)
            info = path.stat()
            key = (info.st_dev, info.st_ino)
            previous = labels[key]
            labels[key] = b"later-edit"
            with self.assertRaises(ColdStorageError):
                app.rollback_compaction(plan)
            # Even an edit back to the original label must match the saved
            # restored readback, not bypass it through original equality.
            copied = [path, *path.rglob("*")]
            saved = {}
            for copied_path in copied:
                info = copied_path.stat()
                copied_key = (info.st_dev, info.st_ino)
                saved[copied_key] = labels[copied_key]
                labels[copied_key] = b"original"
            with self.assertRaises(ColdStorageError):
                app.rollback_compaction(plan)
            labels.update(saved)
            labels[key] = previous
            app.rollback_compaction(plan)

    def test_r7_restored_receipt_cannot_adopt_replacement_or_later_other_metadata(self):
        import shutil

        with assigned_provenance():
            app, plan = self.prepare()
            app.compact_library(plan)
            path = app.library / plan["trees"][0]["path"]
            app.cold_storage.materialize(path)
            expected = plan["trees"][0]["entries"]
            self.assertTrue(app.cold_storage.matches_restored(path, expected))
            before = app.cold_storage.inspect(path)
            for field in ("quarantine", "custom", "acl", "mode", "mtime", "content"):
                with self.subTest(field=field):
                    actual = deepcopy(before)
                    target = next(e for e in actual.values() if e["kind"] == "file")
                    if field in ("quarantine", "custom"):
                        target["xattrs"][
                            "com.apple.quarantine" if field == "quarantine" else "unknown.attribute"
                        ] = "Y2hhbmdlZA=="
                    elif field == "acl":
                        target["acl"] = "changed ACL"
                    elif field == "mode":
                        target["mode"] ^= 0o100
                    elif field == "mtime":
                        target["mtime_ns"] += 100
                    else:
                        target["sha256"] = "0" * 64
                    with patch.object(app.cold_storage, "inspect", return_value=actual):
                        with self.assertRaises(ColdStorageError):
                            app.rollback_compaction(plan)
            retained = path.with_name(path.name + "-retained")
            path.rename(retained)
            shutil.copytree(retained, path)
            # Hold the observed metadata equal: inode identity alone must refuse.
            with patch.object(app.cold_storage, "inspect", return_value=before):
                self.assertFalse(app.cold_storage.matches_restored(path, expected))
