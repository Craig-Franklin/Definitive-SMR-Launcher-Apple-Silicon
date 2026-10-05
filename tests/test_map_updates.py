"""Conservative map version discovery using synthetic package identities."""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.collection import COLLECTION_URL, RemoteMap
from smr_launcher.map_updates import find_map_updates


@dataclass(frozen=True)
class Installed:
    variant_id: str
    archive_filename: str
    source_url: str
    archive_sha1: str = ""


def installed(name, sha1="", variant="original"):
    return Installed(variant, name, COLLECTION_URL + "/" + quote(name, safe=""), sha1)


def remote(name, sha1="b" * 40):
    return RemoteMap(name, 10, sha1)


class MapUpdateTests(unittest.TestCase):
    def test_exact_family_numeric_order_latest_candidate_and_no_mutations(self):
        original = installed("Sample_v1_09.7z", "a" * 40)
        remotes = (remote("Sample_v1_10.7z"), remote("Sample_v1_09.7z", "a" * 40),
                   remote("Sample_v2_00.7z"), remote("Sample_Revision_2_v9_99.7z"),
                   remote("sample_v9_99.7z"), remote("Sample_Other_v9_99.7z"))
        candidates = find_map_updates([original], remotes)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.remote.name, "Sample_v2_00.7z")
        self.assertEqual((candidate.kind, candidate.installed_version, candidate.available_version),
                         ("new_version", "1.9.0", "2.0.0"))
        self.assertEqual(original, installed("Sample_v1_09.7z", "a" * 40))
        self.assertEqual(len(remotes), 6)

    def test_already_installed_newer_versions_and_ambiguous_versions_not_offered(self):
        old = installed("Sample_v1_00.7z", "b" * 40)
        newer = installed("Sample_v2_00.7z", "b" * 40, "newer")
        self.assertEqual(find_map_updates([old, newer], [remote("Sample_v2_00.7z")]), ())
        self.assertEqual(find_map_updates([old], [remote("Sample_v2_00.7z"), remote("Sample_v2_0.7z")]), ())
        self.assertEqual(find_map_updates([old], [remote("Sample_v2_00.7z"), remote("Sample_v2_00.7z", "c" * 40)]), ())

    def test_same_name_revision_requires_known_original_digest(self):
        latest = remote("Sample_v1_00.7z")
        self.assertEqual(find_map_updates([installed(latest.name)], [latest]), ())
        self.assertEqual(find_map_updates([installed(latest.name, "not-a-digest")], [latest]), ())
        self.assertEqual(find_map_updates([installed(latest.name, latest.sha1)], [latest]), ())
        candidate, = find_map_updates([installed(latest.name, "a" * 40)], [latest])
        self.assertEqual(candidate.kind, "revised_archive")
        self.assertEqual(candidate.remote, latest)
        self.assertEqual(find_map_updates([installed(latest.name, "a" * 40),
                                          installed(latest.name, latest.sha1, "current")], [latest]), ())

    def test_no_fuzzy_matching_unverified_source_or_noncanonical_version(self):
        names = ("Sample_v1_00.7z", "Sample_v1.00.7z", "Sample_V1_00.7z", "Sample.7z")
        remotes = [remote("Sample_v2_00.7z")]
        self.assertEqual(find_map_updates([Installed("local", names[0], "")], remotes), ())
        self.assertEqual(find_map_updates([Installed("local", names[0], "https://other.example/" + names[0])], remotes), ())
        for name in names[1:]:
            with self.subTest(name=name):
                self.assertEqual(find_map_updates([installed(name)], remotes), ())
        # A verified unchanged-name archive can be revised without a version suffix.
        candidate, = find_map_updates([installed("Sample.7z", "a" * 40)], [remote("Sample.7z")])
        self.assertEqual(candidate.kind, "revised_archive")

    def test_variant_identity_preserved_and_duplicate_input_collapsed(self):
        original = installed("Sample_v1_00.7z")
        experiment = installed("Sample_v1_00.7z", variant="experiment")
        candidates = find_map_updates([original, original, experiment], [remote("Sample_v1_01.7z")])
        self.assertEqual({item.installed_variant_id for item in candidates}, {"original", "experiment"})
        self.assertEqual(len(candidates), 2)


if __name__ == "__main__":
    unittest.main()
