"""Archive-backed cold bytes must match streamed canonical members exactly."""

from io import BytesIO
from pathlib import Path
import hashlib
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.cold_storage import ColdStorage, ColdStorageError
from smr_launcher.archive_references import archive_references
from smr_launcher.activation import _atomic_json


class ArchiveReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = ColdStorage(self.root)
        source = self.root / "source.tar"
        with tarfile.open(source, "w") as stream:
            for name, data in (
                ("Wrapper/UserMaps/Map/RRT_Scenario_Map.xml", b"<Scenario/>"),
                ("Wrapper/CustomAssets/asset", b"original" * 20000),
            ):
                header = tarfile.TarInfo(name)
                header.size = len(data)
                stream.addfile(header, BytesIO(data))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.archive = self.root / "original-archives" / (digest + ".7z")
        self.archive.parent.mkdir()
        source.rename(self.archive)
        self.tree = self.root / "prepared/tree"
        (self.tree / "CustomAssets").mkdir(parents=True)
        (self.tree / "CustomAssets/asset").write_bytes(b"original" * 20000)
        (self.tree / "CustomAssets/difference").write_bytes(b"edition difference")

    def test_verified_original_bytes_use_archive_and_difference_uses_fallback(self):
        references = archive_references(self.archive)
        index = self.root / "cold-storage/archive-references.json"
        index.parent.mkdir()
        _atomic_json(index, dict(schema=1, references=references))
        before = self.store.inspect(self.tree)
        self.store.pack(self.tree, expected=before)
        self.assertEqual(len(list(self.store.blobs.glob("*/*"))), 1)
        self.store.materialize(self.tree)
        self.assertEqual(self.store.inspect(self.tree), before)

    def test_changed_canonical_or_false_member_digest_refuses_before_original_disposal(self):
        references = archive_references(self.archive)
        key = hashlib.sha256(b"original" * 20000).hexdigest()
        references[key]["member"] = "Wrapper/CustomAssets/missing"
        index = self.root / "cold-storage/archive-references.json"
        index.parent.mkdir()
        _atomic_json(index, dict(schema=1, references=references))
        before = self.store.inspect(self.tree)
        with self.assertRaises(ColdStorageError):
            self.store.pack(self.tree, expected=before)
        self.assertEqual(self.store.inspect(self.tree), before)

    def test_missing_or_modified_archive_never_materializes_partial_output(self):
        references = archive_references(self.archive)
        index = self.root / "cold-storage/archive-references.json"
        index.parent.mkdir()
        _atomic_json(index, dict(schema=1, references=references))
        self.store.pack(self.tree, expected=self.store.inspect(self.tree))
        self.archive.write_bytes(b"changed archive")
        with self.assertRaises(ColdStorageError):
            self.store.materialize(self.tree)
        self.assertFalse(self.tree.exists())
        self.assertTrue(self.store.is_cold(self.tree))

    def test_noncanonical_filename_cannot_become_a_reference(self):
        wrong = self.archive.with_name("f" * 64 + ".7z")
        self.archive.rename(wrong)
        with self.assertRaises(ColdStorageError):
            archive_references(wrong)
