"""R8 real native frozen-tree rename contracts, using synthetic local trees only."""

import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import smr_launcher.cold_storage as cold
from smr_launcher.cold_storage import ColdStorage, ColdStorageError, _identity, _set_xattr
from smr_launcher.packages import PackageError
import test_cold_storage
import test_cold_provenance


@unittest.skipUnless(sys.platform == "darwin", "native macOS exclusive rename")
class ColdRenameTests(unittest.TestCase):
    setUp = test_cold_storage.ColdStorageTests.setUp

    def freeze(self):
        _set_xattr(self.tree, "user.smr-r8", b"synthetic root attribute")
        for path in self.tree.rglob("*"):
            os.chmod(path, 0o500 if path.is_dir() else 0o400)
        os.chmod(self.tree, 0o500)
        return self.store.inspect(self.tree)

    def test_r8_real_0500_roundtrip_does_not_write_permissions_during_publication(self):
        before = self.freeze()
        with patch.object(cold.os, "fchmod", side_effect=AssertionError("fchmod forbidden")):
            self.store.pack(self.tree, expected=before)
            self.assertEqual(self.store._load(self.tree)["entries"], before)
            self.store.materialize(self.tree)
        self.assertEqual(
            test_cold_provenance.portable(self.store.inspect(self.tree)),
            test_cold_provenance.portable(before),
        )

    def test_r8_after_manifest_recovers_exact_original_without_fchmod(self):
        self._prepared_interruption("after_manifest")

    def test_r8_after_rename_recovers_exact_backup_without_fchmod(self):
        self._prepared_interruption("after_rename")

    def _prepared_interruption(self, point):
        before = self.freeze()
        identity = _identity(self.tree)

        def stopped(stage):
            if stage == point:
                raise RuntimeError("process interrupted")

        self.store.fault_hook = stopped
        with patch.object(cold.os, "fchmod", side_effect=AssertionError("fchmod forbidden")):
            with self.assertRaisesRegex(RuntimeError, "process interrupted"):
                self.store.pack(self.tree, expected=before)
            value = self.store._load(self.tree)
            self.assertEqual(value["phase"], "prepared")
            self.assertFalse(value["disposal_started"])
            survivor = (
                self.tree if point == "after_manifest" else self.tree.with_name(value["backup"])
            )
            self.assertEqual(self.store.inspect(survivor), before)
            self.assertEqual(_identity(survivor), identity)
            # New consumer: recovery depends only on the durable journal.
            self.store = ColdStorage(self.library)
            self.store.recover(self.tree)
            self.store.materialize(self.tree)
        self.assertEqual(
            test_cold_provenance.portable(self.store.inspect(self.tree)),
            test_cold_provenance.portable(before),
        )

    def test_r8_real_process_death_after_rename_retains_exact_prepared_backup(self):
        before = self.freeze()
        identity = _identity(self.tree)
        script = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from smr_launcher.cold_storage import ColdStorage
store = ColdStorage(Path(sys.argv[2]))
path = Path(sys.argv[3])
def forbidden(*args):
    raise AssertionError("fchmod forbidden")
os.fchmod = forbidden
def stop(stage):
    if stage == "after_rename":
        os._exit(73)
store.fault_hook = stop
store.pack(path, expected=store.inspect(path))
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(Path(cold.__file__).parents[1]),
                str(self.library),
                str(self.tree),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertFalse(self.tree.exists())
        value = self.store._load(self.tree)
        backup = self.tree.with_name(value["backup"])
        self.assertEqual(value["phase"], "prepared")
        self.assertFalse(value["disposal_started"])
        self.assertEqual(self.store.inspect(backup), before)
        self.assertEqual(_identity(backup), identity)
        self.store.recover(self.tree)
        self.store.materialize(self.tree)
        self.assertEqual(
            test_cold_provenance.portable(self.store.inspect(self.tree)),
            test_cold_provenance.portable(before),
        )

    def test_r8_native_destination_race_preserves_original_and_foreign_backup(self):
        before = self.freeze()
        publish = cold._publish_exclusive
        observed = []

        def collision(source, destination):
            destination.mkdir()
            (destination / "foreign").write_bytes(b"retain foreign backup")
            observed.append(destination)
            return publish(source, destination)

        with patch.object(cold, "_publish_exclusive", side_effect=collision):
            with self.assertRaises((ColdStorageError, PackageError)):
                self.store.pack(self.tree, expected=before)
        self.assertEqual(self.store.inspect(self.tree), before)
        self.assertEqual((observed[0] / "foreign").read_bytes(), b"retain foreign backup")
        with self.assertRaises(ColdStorageError):
            self.store.recover(self.tree)
        self.assertEqual(self.store.inspect(self.tree), before)
        self.assertEqual((observed[0] / "foreign").read_bytes(), b"retain foreign backup")

    def test_r8_prepared_changed_source_and_foreign_backup_remain_strict(self):
        before = self.freeze()

        def stopped(stage):
            if stage == "after_manifest":
                raise RuntimeError("prepared stop")

        self.store.fault_hook = stopped
        with self.assertRaisesRegex(RuntimeError, "prepared stop"):
            self.store.pack(self.tree, expected=before)
        value = self.store._load(self.tree)
        os.chmod(self.tree, 0o700)
        changed = self.store.inspect(self.tree)
        self.assertNotEqual(changed, before)
        with self.assertRaises(ColdStorageError):
            self.store.recover(self.tree)
        self.assertEqual(self.store.inspect(self.tree), changed)
        os.chmod(self.tree, 0o500)
        foreign = self.tree.with_name(value["backup"])
        foreign.mkdir()
        (foreign / "foreign").write_bytes(b"retain foreign backup")
        with self.assertRaises(ColdStorageError):
            self.store.recover(self.tree)
        self.assertEqual(self.store.inspect(self.tree), before)
        self.assertEqual((foreign / "foreign").read_bytes(), b"retain foreign backup")
