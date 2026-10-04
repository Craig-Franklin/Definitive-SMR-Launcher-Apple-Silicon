"""Synthetic collection and download checks without network or map payloads."""
from io import BytesIO
from pathlib import Path
from threading import Event, Lock
from unittest.mock import patch
import hashlib
import json
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.collection import CollectionError, IDENTIFIER, RemoteMap, download_all_maps, download_map, parse_catalogue
import smr_launcher.collection as collection_module


class FakeOpener:
    def __init__(self, data: bytes):
        self.data = data

    def open(self, request, timeout):
        return BytesIO(self.data)


def catalogue(*files: dict) -> bytes:
    return json.dumps({"metadata": {"identifier": IDENTIFIER}, "files": list(files)}).encode()


class CollectionTests(unittest.TestCase):
    def test_archive_modification_is_separate_from_creation_date(self):
        record, = parse_catalogue(catalogue({"name": "Map.7z", "source": "original", "size": "3",
                                             "sha1": "a" * 40, "mtime": "1767841069"}))
        self.assertEqual(record.archive_modified, "2026-01-08")
        self.assertIn("/details/", record.source_url)
        self.assertFalse(hasattr(record, "created"))

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
        progress = []
        with patch.object(collection_module, "_opener", return_value=FakeOpener(payload)):
            path = download_map(record, private, progress=lambda count, total: progress.append((count, total)))
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(path.parent, private)
        self.assertEqual(progress, [(0, len(payload)), (len(payload), len(payload))])
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

    def test_cancel_discards_partial_archive(self):
        payload = b"synthetic archive fixture"
        record = RemoteMap("Sample.7z", len(payload), hashlib.sha1(payload).hexdigest())
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        private = Path(temp.name).resolve() / "downloads"
        stop = Event()
        with patch.object(collection_module, "_opener", return_value=FakeOpener(payload)):
            with self.assertRaisesRegex(CollectionError, "canceled"):
                download_map(record, private,
                             progress=lambda count, total: stop.set() if count else None,
                             cancelled=stop.is_set)
        self.assertEqual(list(private.iterdir()), [])

    def test_download_all_bounds_parallelism_and_keeps_per_map_failures(self):
        records = tuple(RemoteMap(f"Map{i}.7z", 10, f"{i:040x}") for i in range(7))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        events = []
        lock = Lock()
        active = 0
        peak = 0
        imported = []

        def fake_download(record, private_directory, *, progress, cancelled):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            progress(5, 10)
            time.sleep(0.03)
            with lock:
                active -= 1
            if record.name == "Map3.7z":
                raise CollectionError("synthetic failure")
            progress(10, 10)
            return Path(private_directory) / record.name

        with patch.object(collection_module, "download_map", side_effect=fake_download):
            result = download_all_maps(records, Path(temp.name),
                                       lambda record, state, count, total, detail: events.append(
                                           (record.name, state, count, detail)), Event(),
                                       importer=lambda record, path: (
                                           imported.append(record.name),
                                           (_ for _ in ()).throw(CollectionError("bad package"))
                                           if record.name == "Map4.7z" else None))
        self.assertEqual(result, (5, 2, 0))
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 4)
        self.assertIn(("Map3.7z", "Failed", 0, "synthetic failure"), events)
        self.assertIn(("Map4.7z", "Failed", 10, "bad package"), events)
        self.assertEqual(len([event for event in events if event[1] == "Imported"]), 5)
        self.assertEqual(len(imported), 6)


if __name__ == "__main__":
    unittest.main()
