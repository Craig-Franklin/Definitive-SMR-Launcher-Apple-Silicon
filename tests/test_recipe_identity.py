"""Synthetic input/context changes must never silently reuse edition saves."""
from dataclasses import replace
from pathlib import Path
import hashlib
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from smr_launcher.recipe_identity import RecipePreparationBinding, map_file_context
from smr_launcher.resource_identity import ResourceFingerprintError
from smr_launcher.rules import CompatibilityRule, RuleError, TokenPatch
from smr_launcher.variants import prepare_recipe_variant, prepare_original_variant, prepare_compatibility_variant
from smr_launcher.extraction import _unfreeze_directories


def sha(data):
    return hashlib.sha256(data).hexdigest()


class RecipeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.clean, self.source = self.root / "clean", self.root / "source"
        for name in ("CustomAssets", "UserMaps", "Saves"):
            (self.clean / name).mkdir(parents=True)
        (self.clean / "Saves/keep.sav").write_bytes(b"original save")
        (self.source / "UserMaps/Test").mkdir(parents=True)
        self.path = "UserMaps/Test/map.xml"
        self.before, self.after = b"<original/>", b"<translated-copy/>"
        (self.source / self.path).write_bytes(self.before)
        (self.source / "UserMaps/Test/untouched.txt").write_bytes(b"untouched")
        old = 946684800123456789
        for file in (self.source / self.path, self.source / "UserMaps/Test/untouched.txt"):
            os.utime(file, ns=(old, old))
        self.archive, self.game = sha(b"archive"), sha(b"game")
        self.rule = CompatibilityRule("synthetic", 1, "synthetic test", "one file",
                                      self.archive, self.game,
                                      (TokenPatch(self.path, sha(self.before), sha(self.after), self.before, self.after),))
        self.binding = RecipePreparationBinding("synthetic-v1", sha(b"recipe"), sha(b"compiler"),
                                                map_file_context(self.source)["identity"], sha(b"stock"))

    def prepare(self, name, binding=None):
        return prepare_recipe_variant(self.clean, self.source, self.root / name,
                                      self.archive, self.game, (self.rule,), binding or self.binding)

    def test_file_identity_ignores_locations_directory_times_and_empty_custom_root(self):
        before = map_file_context(self.source)
        copy = self.root / "copy"
        shutil.copytree(self.source, copy)
        (copy / "CustomAssets").mkdir()
        os.utime(copy / "UserMaps", ns=(10**18, 10**18))
        self.assertEqual(before, map_file_context(copy))
        (copy / "Saves").mkdir()
        (copy / "Saves/keep.sav").write_bytes(b"private save")
        (copy / "Settings.ini").write_bytes(b"private setting")
        self.assertEqual(before, map_file_context(copy))

    def test_metadata_changes_create_new_ids_and_old_binding_rejects(self):
        first = self.prepare("first")
        file = self.source / self.path
        stat = file.stat()
        os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
        with self.assertRaisesRegex(RuleError, "source resource context"):
            self.prepare("stale")
        self.assertFalse((self.root / "stale").exists())
        updated = replace(self.binding, source_files_sha256=map_file_context(self.source)["identity"])
        second = self.prepare("second", updated)
        self.assertNotEqual(first.variant_id, second.variant_id)
        self.assertEqual(first.assets_sha256, second.assets_sha256)
        self.assertEqual(list((self.root / "second/Saves").iterdir()), [])

    def test_repeated_preparation_retains_metadata_and_independent_files(self):
        first, second = self.prepare("first"), self.prepare("second")
        self.assertEqual(first.variant_id, second.variant_id)
        self.assertEqual(first.recipe_binding, second.recipe_binding)
        output = self.root / "first" / self.path
        self.assertEqual(output.read_bytes(), self.after)
        self.assertEqual(output.stat().st_mtime_ns, (self.source / self.path).stat().st_mtime_ns)
        self.assertNotEqual(output.stat().st_ino, (self.source / self.path).stat().st_ino)
        self.assertEqual((self.source / self.path).read_bytes(), self.before)
        self.assertEqual((self.clean / "Saves/keep.sav").read_bytes(), b"original save")
        self.assertEqual(first.recipe_binding["output_files_sha256"], map_file_context(self.root / "first")["identity"])

    def test_stock_recipe_and_compiler_changes_create_distinct_ids(self):
        first = self.prepare("first")
        for field in ("stock_files_sha256", "recipe_sha256", "compiler_sha256"):
            other = self.prepare(field, replace(self.binding, **{field: sha(field.encode())}))
            self.assertNotEqual(first.variant_id, other.variant_id)
            self.assertEqual(first.assets_sha256, other.assets_sha256)

    def test_legacy_ids_stay_content_based_and_distinct(self):
        def original(name):
            return prepare_original_variant(self.clean, self.source, self.root / name, self.archive, self.game)
        def legacy(name):
            return prepare_compatibility_variant(self.clean, self.source, self.root / name, self.archive, self.game, (self.rule,))
        old, private, new = original("original"), legacy("legacy"), self.prepare("compiled")
        stat = (self.source / self.path).stat()
        os.utime(self.source / self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
        self.assertEqual(old.variant_id, original("original2").variant_id)
        self.assertEqual(private.variant_id, legacy("legacy2").variant_id)
        self.assertEqual(len({old.variant_id, private.variant_id, new.variant_id}), 3)

    def test_copy_metadata_loss_discards_stage(self):
        real_copytree = shutil.copytree

        def bad_copy(source, destination, *args, **kwargs):
            result = real_copytree(source, destination, *args, **kwargs)
            if Path(source).name == "UserMaps":
                file = Path(destination) / "Test/untouched.txt"
                st = file.stat()
                os.utime(file, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
            return result

        with patch("smr_launcher.variants.shutil.copytree", side_effect=bad_copy):
            with self.assertRaisesRegex(RuleError, "timestamps differ"):
                self.prepare("failed")
        self.assertFalse((self.root / "failed").exists())
        self.assertFalse(list(self.root.glob(".smr-variant-*")))
        self.assertEqual((self.source / self.path).read_bytes(), self.before)

    def test_unsafe_resource_types_and_missing_roots_reject(self):
        (self.source / "CustomAssets").symlink_to(self.clean / "CustomAssets")
        with self.assertRaises((ResourceFingerprintError, ValueError)):
            map_file_context(self.source)
        (self.source / "CustomAssets").unlink()
        (self.source / "UserMaps/Test/link.xml").symlink_to(self.source / self.path)
        with self.assertRaises(ResourceFingerprintError):
            map_file_context(self.source)


if __name__ == "__main__":
    unittest.main()
