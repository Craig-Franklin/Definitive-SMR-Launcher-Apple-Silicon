"""Synthetic package metadata and bounded, read-only filesystem checks."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.map_metadata import (
    MAX_MAP_INFO_BYTES, MapMetadata, parse_map_metadata, read_map_metadata,
)


class MapMetadataTests(unittest.TestCase):
    def test_explicit_headers_and_description_preserve_original_text(self):
        raw = ("Map Name: Sample Map\r\nMap Version: V1.02\r\n"
               "Date Created: 2020/02/29\r\nDate Updated: 2024-05-06\r\n"
               "Author: Original Author\r\nModified by: Editor\r\nType: Single Player\r\n"
               "Unknown Detail: retained\r\n\r\nDescription with a colon: here.\r\n"
               "Date Created: 2000/01/01\r\n")
        result = parse_map_metadata(raw.encode())
        self.assertEqual((result.name, result.version), ("Sample Map", "V1.02"))
        self.assertEqual((result.created, result.updated), ("2020-02-29", "2024-05-06"))
        self.assertEqual((result.author, result.modified_by, result.map_type),
                         ("Original Author", "Editor", "Single Player"))
        self.assertEqual(result.raw_text, raw)
        self.assertIn("Unknown Detail: retained", result.description)
        self.assertIn("Date Created: 2000/01/01", result.description)

    def test_invalid_ambiguous_and_unlabelled_dates_are_unknown(self):
        for value in ("2023/02/29", "2024/13/01", "06/05/2024", "2024-05/06", "unknown", ""):
            with self.subTest(value=value):
                result = parse_map_metadata(f"Date Created: {value}\n".encode())
                self.assertIsNone(result.created)
                self.assertIn(value, result.raw_text)
        raw = "V2.00\n2021/12/20\nAuthor Name"
        result = parse_map_metadata(raw.encode())
        self.assertIsNone(result.created)
        self.assertIsNone(result.updated)
        self.assertIsNone(result.author)
        self.assertEqual(result.description, raw)
        result = parse_map_metadata(b"Date Created: 2020/01/01\nDate Created: 2021/01/01\n")
        self.assertIsNone(result.created)

    def test_only_explicit_safe_web_sources_become_links(self):
        raw = ("Source: https://example.org/map?id=3\n"
               "Source URL: https://example.org/map?id=3\n"
               "Website: http://example.org/author\n"
               "Map URL: https://user:secret@example.org/private\n"
               "Source: file:///etc/passwd\nSource: javascript:alert(1)\n"
               "Source: https://example.org:99999/map\n"
               "Source: https://example.org/with space\n"
               "Source: https://example.org\\@evil.invalid/\n"
               "\nSee https://example.org/not-a-declared-source\n"
               "Source: https://example.org/prose\n")
        result = parse_map_metadata(raw.encode())
        self.assertEqual(result.source_urls,
                         ("https://example.org/map?id=3", "http://example.org/author"))
        self.assertEqual(result.raw_text, raw)

    def test_encoding_boms_and_invalid_text(self):
        for encoding in ("utf-8-sig", "utf-16"):
            self.assertEqual(parse_map_metadata("Author: Renée".encode(encoding)).author, "Renée")
        for payload in (b"\xffinvalid", b"Map Name: A\x00B", b"x" * (MAX_MAP_INFO_BYTES + 1)):
            self.assertEqual(parse_map_metadata(payload), MapMetadata())

    def test_reader_preserves_file_and_rejects_missing_oversize_directory_and_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            file = root / "mapInfo.txt"
            payload = b"Map Name: Unchanged\nDate Created: 2024/01/02\n"
            file.write_bytes(payload)
            file.chmod(0o400)
            before = file.stat()
            self.assertEqual(read_map_metadata(file).created, "2024-01-02")
            self.assertEqual(file.read_bytes(), payload)
            after = file.stat()
            self.assertEqual((before.st_mtime_ns, before.st_mode, before.st_ino),
                             (after.st_mtime_ns, after.st_mode, after.st_ino))
            self.assertEqual(read_map_metadata(root / "missing"), MapMetadata())
            self.assertEqual(read_map_metadata(root), MapMetadata())
            huge = root / "huge"
            huge.write_bytes(b"x" * (MAX_MAP_INFO_BYTES + 1))
            self.assertEqual(read_map_metadata(huge), MapMetadata())
            leaf = root / "link"
            leaf.symlink_to(file)
            self.assertEqual(read_map_metadata(leaf), MapMetadata())
            ancestor = root / "linked-directory"
            ancestor.symlink_to(root, target_is_directory=True)
            self.assertEqual(read_map_metadata(ancestor / "mapInfo.txt"), MapMetadata())
            self.assertEqual(read_map_metadata(ancestor / ".." / "mapInfo.txt"), MapMetadata())
            fifo = root / "fifo"
            os.mkfifo(fifo)
            self.assertEqual(read_map_metadata(fifo), MapMetadata())


if __name__ == "__main__":
    unittest.main()
