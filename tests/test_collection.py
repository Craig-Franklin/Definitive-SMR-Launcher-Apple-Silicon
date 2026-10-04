"""Synthetic collection and download checks without network or map payloads."""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import hashlib
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.collection import CollectionError, IDENTIFIER, RemoteMap, download_map, parse_catalogue
import smr_launcher.collection as collection_module


class FakeOpener:
    def __init__(self, data: bytes):
        self.data = data

    def open(self, request, timeout):
        return BytesIO(self.data)


def catalogue(*files: dict) -> bytes:
    return json.dumps({"metadata": {"identifier": IDENTIFIER}, "files": list(files)}).encode()


class CollectionTests(unittest.TestCase):
    def test_catalogue_keeps_only_original_archives_and_sorts(self):
        document = catalogue(
            {"name": "Zulu_v1_00.7z", "source": "original", "size": "3", "sha1": "a" * 40},
            {"name": "Alpha_v1_00.7z", "source": "original", "size": "4", "sha1": "b" * 40},
            {"name": "thumb.jpg", "source": "derivative", "size": "4", "sha1": "c" * 40},
        )
        records = parse_catalogue(document)
        self.assertEqual([item.name for item in records], ["Alpha_v1_00.7z", "Zulu_v1_00.7z"])
        self.assertTrue(records[0].url.endswith("Alpha_v1_00.7z"))

    def test_invalid_identity_path_hash_and_duplicate_are_rejected(self):
        good = {"name": "Map.7z", "source": "original", "size": "3", "sha1": "a" * 40}
        for bad in (
            {"metadata": {"identifier": "other"}, "files": [good]},
            {"metadata": {"identifier": IDENTIFIER}, "files": [dict(good, name="../Map.7z")]},
            {"metadata": {"identifier": IDENTIFIER}, "files": [dict(good, sha1="bad")]},
            {"metadata": {"identifier": IDENTIFIER}, "files": [good, dict(good, name="map.7z")]},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(CollectionError):
                    parse_catalogue(json.dumps(bad).encode())
        with self.assertRaises(CollectionError):
            RemoteMap("../Map.7z", 3, "a" * 40)

    def test_download_is_verified_private_and_reused(self):
        payload = b"synthetic archive fixture"
        record = RemoteMap("Sample.7z", len(payload), hashlib.sha1(payload).hexdigest())
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        private = Path(temp.name).resolve() / "downloads"
        with patch.object(collection_module, "_opener", return_value=FakeOpener(payload)):
            path = download_map(record, private)
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(path.parent, private)
        self.assertEqual(download_map(record, private), path)
        self.assertEqual(len(list(private.glob("*.7z"))), 1)

    def test_wrong_download_cannot_publish(self):
        payload = b"wrong"
        record = RemoteMap("Sample.7z", len(payload), hashlib.sha1(b"right").hexdigest())
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        private = Path(temp.name).resolve() / "downloads"
        with patch.object(collection_module, "_opener", return_value=FakeOpener(payload)):
            with self.assertRaises(CollectionError):
                download_map(record, private)
        self.assertEqual(list(private.glob("*.7z")), [])


if __name__ == "__main__":
    unittest.main()
