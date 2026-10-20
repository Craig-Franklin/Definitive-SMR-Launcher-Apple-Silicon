"""Prospective root-only rename authority; modeled labels, real storage consumers.

Native R8/R9 controls remain same-label evidence only. These outcome controls model
an OS root-label transition, never claim to reproduce foreign-origin macOS behavior.
"""

from contextlib import contextmanager
from copy import deepcopy
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import smr_launcher.cold_storage as cold
from smr_launcher.cold_storage import ColdStorage, ColdStorageError, _identity
import test_cold_storage
import test_cold_provenance as controls
import test_compact_library

OLD = b"foreign-source-label"
TARGET = b"measured-copy-label"
PURPOSE = "cold-root-rename-provenance"


@contextmanager
def transition(old=OLD, target=TARGET, *, endpoint="target", edit=None):
    """Only label observations are modeled; native rename and storage stay real."""
    with controls.assigned_provenance(restored=target, original=old) as labels:
        publish = cold._publish_exclusive

        def renamed(source, destination):
            publish(source, destination)
            if destination.name.startswith(".cold-backup-"):
                info = destination.stat()
                labels[info.st_dev, info.st_ino] = target if endpoint == "target" else old
                if edit:
                    edit(destination, labels)

        with patch.object(cold, "_publish_exclusive", side_effect=renamed):
            yield labels


def label(path, labels, value):
    info = path.stat()
    labels[info.st_dev, info.st_ino] = value


def stopped_at(point):
    def stop(stage):
        if stage == point:
            raise RuntimeError("interrupted " + point)

    return stop


@unittest.skipUnless(sys.platform == "darwin", "macOS root rename transition")
class ColdTransitionTests(unittest.TestCase):
    setUp = test_cold_storage.ColdStorageTests.setUp

    def prepared(self):
        os.chmod(self.tree, 0o500)
        before = self.store.inspect(self.tree)
        self.store.fault_hook = stopped_at("after_manifest")
        with self.assertRaisesRegex(RuntimeError, "interrupted after_manifest"):
            self.store.pack(self.tree, expected=before)
        self.store.fault_hook = lambda stage: None
        return before, self.store._load(self.tree)

    def backup(self):
        value = self.store._load(self.tree)
        return self.tree.with_name(value["backup"]), value

    def test_exact_target_and_explicit_absence_roundtrip_keep_raw_manifest(self):
        for old, target in ((OLD, TARGET), (OLD, None), (None, TARGET), (None, None)):
            with self.subTest(old=old, target=target):
                self.setUp()
                with transition(old, target):
                    os.chmod(self.tree, 0o500)
                    before = self.store.inspect(self.tree)
                    self.store.pack(self.tree, expected=before)
                    self.assertEqual(self.store._load(self.tree)["entries"], before)
                    self.store.materialize(self.tree)
                    self.assertEqual(
                        controls.portable(self.store.inspect(self.tree)), controls.portable(before)
                    )

    def test_death_before_after_rename_and_after_observation_recovers(self):
        for point in (
            "after_manifest",
            "after_rename",
            "after_backup_observation",
            "after_verified",
        ):
            with self.subTest(point=point):
                self.setUp()
                with transition():
                    os.chmod(self.tree, 0o500)
                    before = self.store.inspect(self.tree)
                    identity = _identity(self.tree)
                    self.store.fault_hook = stopped_at(point)
                    with self.assertRaisesRegex(RuntimeError, "interrupted " + point):
                        self.store.pack(self.tree, expected=before)
                    backup, value = self.backup()
                    survivor = self.tree if point == "after_manifest" else backup
                    self.assertEqual(_identity(survivor), identity)
                    self.assertFalse(value["disposal_started"])
                    self.store = ColdStorage(self.library)
                    self.store.recover(self.tree)
                    self.store.materialize(self.tree)
                    self.assertEqual(
                        controls.portable(self.store.inspect(self.tree)), controls.portable(before)
                    )

    def test_source_must_remain_exact_even_if_changed_label_is_declared_target(self):
        with transition() as labels:
            before, _ = self.prepared()
            label(self.tree, labels, TARGET)
            with self.assertRaises(ColdStorageError):
                self.store.recover(self.tree)
            self.assertTrue(self.tree.exists())
            self.assertEqual((self.tree / "Settings.ini").read_bytes(), b"personal settings")
            self.assertNotEqual(self.store.inspect(self.tree), before)

    def test_legacy_record_does_not_gain_post_hoc_target_authority(self):
        with transition():
            _, value = self.prepared()
            legacy = {
                key: value[key]
                for key in (
                    "schema",
                    "backup_identity",
                    "check",
                    "restore",
                    "disposal_started",
                    "path",
                    "entries",
                    "sha256",
                    "phase",
                    "backup",
                )
            }
            cold._atomic_json(self.store._index(self.tree), legacy)
            backup = self.tree.with_name(legacy["backup"])
            cold._publish_exclusive(self.tree, backup)
            actual = self.store.inspect(backup)
            with self.assertRaises(ColdStorageError):
                self.store.recover(self.tree)
            self.assertEqual(self.store.inspect(backup), actual)
            self.assertFalse(self.store._load(self.tree)["disposal_started"])

    def test_unapproved_root_or_child_label_refuses_before_disposal(self):
        for scope in ("root", "child"):
            with self.subTest(scope=scope):
                self.setUp()

                def edit(backup, labels):
                    label(
                        backup if scope == "root" else backup / "Settings.ini",
                        labels,
                        b"third-label",
                    )

                with transition(edit=edit):
                    before = self.store.inspect(self.tree)
                    with self.assertRaises(ColdStorageError):
                        self.store.pack(self.tree, expected=before)
                    backup, value = self.backup()
                    self.assertFalse(value["disposal_started"])
                    self.assertEqual((backup / "Settings.ini").read_bytes(), b"personal settings")

    def test_other_metadata_and_payload_changes_are_not_authorized(self):
        for field in ("unknown", "quarantine", "acl", "mode", "mtime", "content", "topology"):
            with self.subTest(field=field):
                self.setUp()
                inspect = self.store.inspect

                def changed_inspect(path, **kwargs):
                    result = inspect(path, **kwargs)
                    if field == "acl" and path.name.startswith(".cold-backup-"):
                        result["Settings.ini"]["acl"] = "synthetic changed ACL"
                    return result

                def edit(backup, labels):
                    target = backup / "Settings.ini"
                    if field in ("unknown", "quarantine"):
                        cold._set_xattr(
                            target,
                            "user.r9-unknown" if field == "unknown" else "com.apple.quarantine",
                            b"synthetic changed attribute",
                        )
                    elif field == "mode":
                        os.chmod(target, target.stat().st_mode ^ 0o100)
                    elif field == "mtime":
                        value = target.stat().st_mtime_ns + 100
                        os.utime(target, ns=(value, value))
                    elif field == "content":
                        target.write_bytes(b"later payload")
                    elif field == "topology":
                        (backup / "added.sav").write_bytes(b"later save")

                with (
                    transition(edit=edit),
                    patch.object(self.store, "inspect", side_effect=changed_inspect),
                ):
                    before = self.store.inspect(self.tree)
                    with self.assertRaises(ColdStorageError):
                        self.store.pack(self.tree, expected=before)
                    backup, value = self.backup()
                    self.assertFalse(value["disposal_started"])
                    self.assertTrue(backup.exists())

    def test_first_observation_cannot_adopt_a_changed_second_read(self):
        with transition() as labels:
            inspect = self.store.inspect
            triggered = []

            def late(path, **kwargs):
                actual = inspect(path, **kwargs)
                if path.name.startswith(".cold-backup-") and not triggered:
                    triggered.append(True)
                    label(path, labels, OLD)
                return actual

            before = self.store.inspect(self.tree)
            with patch.object(self.store, "inspect", side_effect=late):
                with self.assertRaises(ColdStorageError):
                    self.store.pack(self.tree, expected=before)
            backup, value = self.backup()
            self.assertTrue(backup.exists())
            self.assertFalse(value["disposal_started"])

    def test_observed_endpoint_is_pinned_even_against_the_other_declared_endpoint(self):
        for endpoint in ("target", "old"):
            for point in ("after_backup_observation", "after_verified"):
                with self.subTest(endpoint=endpoint, point=point):
                    self.setUp()
                    with transition(endpoint=endpoint) as labels:
                        before = self.store.inspect(self.tree)
                        self.store.fault_hook = stopped_at(point)
                        with self.assertRaisesRegex(RuntimeError, "interrupted " + point):
                            self.store.pack(self.tree, expected=before)
                        backup, _ = self.backup()
                        label(backup, labels, OLD if endpoint == "target" else TARGET)
                        self.store.fault_hook = lambda stage: None
                        with self.assertRaises(ColdStorageError):
                            self.store.recover(self.tree)
                        self.assertTrue(backup.exists())
                        self.assertFalse(self.store._load(self.tree)["disposal_started"])

    def test_explicit_absent_observation_is_not_missing_observation(self):
        with transition(target=None) as labels:
            self.store.fault_hook = stopped_at("after_backup_observation")
            with self.assertRaisesRegex(RuntimeError, "interrupted after_backup_observation"):
                self.store.pack(self.tree, expected=self.store.inspect(self.tree))
            backup, value = self.backup()
            self.assertIn("observed", value["rename_transition"])
            self.assertIsNone(value["rename_transition"]["observed"])
            label(backup, labels, OLD)
            self.store.fault_hook = lambda stage: None
            with self.assertRaises(ColdStorageError):
                self.store.recover(self.tree)
            self.assertTrue(backup.exists())

    def test_partial_disposal_keeps_pinned_label_and_rejects_later_endpoint_edit(self):
        with transition() as labels:
            before = self.store.inspect(self.tree)
            rmtree = cold.shutil.rmtree

            def partial(path):
                if path.name.startswith(".cold-backup-"):
                    (path / "Settings.ini").unlink()
                    raise OSError("partial disposal")
                return rmtree(path)

            with patch.object(cold.shutil, "rmtree", side_effect=partial):
                with self.assertRaisesRegex(OSError, "partial disposal"):
                    self.store.pack(self.tree, expected=before)
            backup, value = self.backup()
            self.assertTrue(value["disposal_started"])
            label(backup, labels, OLD)
            with self.assertRaises(ColdStorageError):
                self.store.recover(self.tree)
            self.assertTrue(backup.exists())
            label(backup, labels, TARGET)
            self.store.recover(self.tree)
            self.store.materialize(self.tree)
            self.assertEqual(
                controls.portable(self.store.inspect(self.tree)), controls.portable(before)
            )

    def test_replaced_backup_and_destination_collision_refuse(self):
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                self.setUp()
                with transition() as labels:
                    before, value = self.prepared()
                    backup = self.tree.with_name(value["backup"])
                    if replaced:
                        cold._publish_exclusive(self.tree, backup)
                        retained = backup.with_name(backup.name + "-retained")
                        backup.rename(retained)
                        shutil.copytree(retained, backup)
                        label(backup, labels, TARGET)
                    else:
                        backup.mkdir()
                        (backup / "foreign").write_bytes(b"preserve foreign")
                    with self.assertRaises(ColdStorageError):
                        self.store.recover(self.tree)
                    self.assertTrue(backup.exists())
                    if not replaced:
                        self.assertEqual(self.store.inspect(self.tree), before)
                        self.assertEqual((backup / "foreign").read_bytes(), b"preserve foreign")

    def test_late_measurement_edit_does_not_arm_a_different_target(self):
        with transition() as labels:
            checked = self.store._scratch_matches

            def late(path, value, record):
                accepted = checked(path, value, record)
                if accepted and path.name.startswith(".cold-check-"):
                    label(path, labels, b"late-measurement")
                return accepted

            before = self.store.inspect(self.tree)
            with patch.object(self.store, "_scratch_matches", side_effect=late):
                with self.assertRaises(ColdStorageError):
                    self.store.pack(self.tree, expected=before)
            self.assertEqual(self.store.inspect(self.tree), before)

    def test_observed_backup_cannot_reappear_as_an_original_for_another_rename(self):
        with transition(endpoint="old"):
            before, value = self.prepared()
            value["rename_transition"]["observed"] = before["."]["xattrs"]["com.apple.provenance"]
            cold._atomic_json(self.store._index(self.tree), value)
            with self.assertRaises(ColdStorageError):
                self.store.recover(self.tree)
            self.assertEqual(self.store.inspect(self.tree), before)
            self.assertFalse(self.tree.with_name(value["backup"]).exists())

    def test_malformed_wrong_purpose_version_and_stale_declarations_refuse(self):
        mutations = (
            lambda r: r.update(purpose="another-purpose", measurement=None),
            lambda r: r.update(version=2, measurement=None),
            lambda r: r.update(version=True),
            lambda r: r.update(source_sha256="0" * 64),
            lambda r: r["source_identity"].update(inode=r["source_identity"]["inode"] + 1),
            lambda r: r.update(backup=".cold-backup-" + "0" * 32),
            lambda r: r.update(path="another-tree"),
            lambda r: r["measurement"].pop("provenance"),
            lambda r: r["measurement"].update(provenance="invalid base64!"),
            lambda r: r.update(observed=base64.b64encode(b"third").decode()),
            lambda r: r["measurement"]["identity"].update(birth=float("nan")),
        )
        for number, mutate in enumerate(mutations):
            with self.subTest(number=number):
                self.setUp()
                with transition():
                    captured = []
                    cleanup = self.store._clean_scratch

                    def save_measurement(root, value, key):
                        if key == "check" and value[key] and value[key]["complete"]:
                            captured.append(deepcopy(value[key]))
                        return cleanup(root, value, key)

                    with patch.object(self.store, "_clean_scratch", side_effect=save_measurement):
                        before, value = self.prepared()
                    # Independent record specification; old production has no declaration.
                    record = dict(
                        purpose=PURPOSE,
                        version=1,
                        path=value["path"],
                        backup=value["backup"],
                        source_sha256=value["sha256"],
                        source_identity=deepcopy(value["backup_identity"]),
                        measurement=dict(
                            identity=captured[0]["identity"],
                            provenance=captured[0]["provenance"].get("."),
                        ),
                    )
                    mutate(record)
                    value["rename_transition"] = record
                    cold._atomic_json(self.store._index(self.tree), value)
                    with self.assertRaises(ColdStorageError):
                        self.store.recover(self.tree)
                    self.assertEqual(self.store.inspect(self.tree), before)
                    self.assertFalse(self.tree.with_name(value["backup"]).exists())


@unittest.skipUnless(sys.platform == "darwin", "macOS root rename transition")
class TransitionApplicationTests(unittest.TestCase):
    setUp = test_compact_library.CompactLibraryTests.setUp

    def test_real_apply_resume_and_rollback_for_prepared_and_observed_death(self):
        for point in ("after_manifest", "after_rename", "after_backup_observation"):
            with self.subTest(point=point):
                self.setUp()
                with transition():
                    app = self.application
                    app.setup()
                    app.import_archive(self.archive)
                    candidate = app.plan_compaction()
                    app.cold_storage.fault_hook = stopped_at(point)
                    with self.assertRaisesRegex(RuntimeError, "interrupted " + point):
                        app.compact_library(candidate)
                    app.cold_storage = ColdStorage(app.library)
                    app.compact_library(candidate)
                    app.rollback_compaction(candidate)
                    for tree in candidate["trees"]:
                        self.assertEqual(
                            controls.portable(app.cold_storage.inspect(app.library / tree["path"])),
                            controls.portable(tree["entries"]),
                        )
                    self.assertFalse((app.library / "compact-library.json").exists())

    def test_real_mixed_new_transaction_resume_then_rollback(self):
        with transition():
            app = self.application
            app.setup()
            app.import_archive(self.archive)
            candidate = app.plan_compaction()
            renames = []

            def stopped(stage):
                if stage == "after_rename":
                    renames.append(True)
                    if len(renames) == 2:
                        raise RuntimeError("second tree interruption")

            app.cold_storage.fault_hook = stopped
            with self.assertRaisesRegex(RuntimeError, "second tree interruption"):
                app.compact_library(candidate)
            pending = json.loads((app.library / "compact-migration.json").read_bytes())
            self.assertEqual(len(pending["completed"]), 1)
            app.cold_storage = ColdStorage(app.library)
            app.compact_library(candidate)
            app.rollback_compaction(candidate)
            for tree in candidate["trees"]:
                self.assertEqual(
                    controls.portable(app.cold_storage.inspect(app.library / tree["path"])),
                    controls.portable(tree["entries"]),
                )
