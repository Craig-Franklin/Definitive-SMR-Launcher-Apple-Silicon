"""Synthetic optional community index validation and cache preservation tests."""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher import community


def entry(**changes):
    return dict({"map_name": "Sample Map", "url": "42", "is_stable": "y", "multiplayer": "n"}, **changes)


def payload(document=None):
    return json.dumps({"Sample_v1_00": entry()} if document is None else document).encode()


class Response(BytesIO):
    def geturl(self):
        return community.COMMUNITY_INDEX_URL


class FakeOpener:
    def __init__(self, data):
        self.data = data

    def open(self, request, timeout):
        assert request.full_url == community.COMMUNITY_INDEX_URL
        assert timeout == 15
        return Response(self.data)


class CommunityTests(unittest.TestCase):
    def test_flags_exact_filenames_template_and_canonical_discussions(self):
        records = community.parse_community_index(payload({
            "Sample_v1_00": entry(),
            "Other_v1_01": entry(is_stable="n", multiplayer="y"),
            "Unknown_v1_00": entry(is_stable="", multiplayer=None),
            "blank": entry(map_name="", multiplayer=""),
        }))
        self.assertEqual(set(records), {"Sample_v1_00.7z", "Other_v1_01.7z", "Unknown_v1_00.7z"})
        self.assertEqual(records["Sample_v1_00.7z"].discussion_url, community.DISCUSSION_ROOT + "42")
        self.assertEqual(records["Sample_v1_00.7z"].stability, "Reported stable")
        self.assertFalse(records["Sample_v1_00.7z"].multiplayer)
        self.assertEqual(records["Other_v1_01.7z"].stability, "Reported unstable")
        self.assertTrue(records["Other_v1_01.7z"].multiplayer)
        self.assertEqual(records["Unknown_v1_00.7z"].stability, "Not reported")
        self.assertIsNone(records["Unknown_v1_00.7z"].multiplayer)

    def test_malicious_names_discussion_ids_types_and_duplicates_rejected(self):
        for key in ("../escape", "/absolute", "back\\slash", "scheme:name", "hidden\x00file", ".hidden", " padded "):
            with self.subTest(key=key), self.assertRaises(community.CommunityError):
                community.parse_community_index(payload({key: entry()}))
        for changes in ({"url": "https://evil.example/"}, {"url": "../42"}, {"url": "0"},
                        {"url": 42}, {"url": "1?redirect=evil"}, {"is_stable": True},
                        {"multiplayer": []}, {"map_name": 3}, {"map_name": ""}):
            with self.subTest(changes=changes), self.assertRaises(community.CommunityError):
                community.parse_community_index(payload({"Sample": entry(**changes)}))
        for data in (b'{"same":{},"same":{}}', payload({"Sample": entry(), "sample": entry()}), b"[]"):
            with self.assertRaises(community.CommunityError):
                community.parse_community_index(data)

    def test_limits_and_redirects(self):
        for data in (b" " * (community.MAX_INDEX_BYTES + 1),
                     payload({str(i): entry() for i in range(community.MAX_COMMUNITY_MAPS + 1)})):
            with self.assertRaises(community.CommunityError):
                community.parse_community_index(data)
        with self.assertRaises(community.CommunityError):
            community._NoRedirects().redirect_request(None, None, 302, "", {}, "http://evil.example/")

    def test_fetch_cache_round_trip_private_atomic_and_failed_refresh_keeps_previous(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp).resolve() / "library/community-index.json"
            with patch.object(community, "_opener", return_value=FakeOpener(payload())):
                records = community.fetch_community_index(cache)
            self.assertEqual(community.read_cached_community_index(cache), records)
            document = json.loads(cache.read_bytes())
            self.assertEqual(document["source"], community.COMMUNITY_INDEX_URL)
            self.assertIn("+00:00", document["fetched_at"])
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
            before = cache.read_bytes()
            with patch.object(community, "_opener", return_value=FakeOpener(b"invalid")):
                with self.assertRaises(community.CommunityError):
                    community.fetch_community_index(cache)
            self.assertEqual(cache.read_bytes(), before)
            self.assertEqual(list(cache.parent.iterdir()), [cache])
            with patch.object(community.os, "replace", side_effect=OSError("synthetic failure")):
                with patch.object(community, "_opener", return_value=FakeOpener(payload())):
                    with self.assertRaises(community.CommunityError):
                        community.fetch_community_index(cache)
            self.assertEqual(cache.read_bytes(), before)
            self.assertEqual(list(cache.parent.iterdir()), [cache])

    def test_missing_corrupt_and_symlink_cache_graceful_without_network_or_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            cache = root / "community-index.json"
            with patch.object(community, "_opener", side_effect=AssertionError("no network")):
                self.assertEqual(community.read_cached_community_index(cache), {})
                cache.write_text("broken")
                self.assertEqual(community.read_cached_community_index(cache), {})
                cache.write_bytes(b"x" * (community.MAX_INDEX_BYTES + 1025))
                self.assertEqual(community.read_cached_community_index(cache), {})
            target = root / "sentinel"
            target.write_text("preserve me")
            cache.unlink()
            cache.symlink_to(target)
            self.assertEqual(community.read_cached_community_index(cache), {})
            with patch.object(community, "_opener", return_value=FakeOpener(payload())):
                with self.assertRaises(community.CommunityError):
                    community.fetch_community_index(cache)
            self.assertEqual(target.read_text(), "preserve me")
            linked = root / "linked"
            linked.symlink_to(root, target_is_directory=True)
            self.assertEqual(community.read_cached_community_index(linked / "community-index.json"), {})
            with patch.object(community, "_opener", return_value=FakeOpener(payload())):
                with self.assertRaises(community.CommunityError):
                    community.fetch_community_index(linked / "other.json")
            self.assertFalse((root / "other.json").exists())
            fifo = root / "fifo"
            os.mkfifo(fifo)
            self.assertEqual(community.read_cached_community_index(fifo), {})


if __name__ == "__main__":
    unittest.main()
