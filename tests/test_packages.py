"""Synthetic archives only; no commercial files or downloaded maps are fixtures."""
from io import BytesIO
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.packages import PackageError, inspect_package
from smr_launcher.extraction import extract_package


class PackageInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "map.tar"

    def archive(self, files, links=()):
        with tarfile.open(self.path, "w") as out:
            for name, data in files:
                item = tarfile.TarInfo(name)
                item.size = len(data)
                out.addfile(item, BytesIO(data))
            for name, destination in links:
                item = tarfile.TarInfo(name)
                item.type = tarfile.SYMTYPE
                item.linkname = destination
                out.addfile(item)

    def test_one_wrapper_is_removed_and_scenario_is_found(self):
        self.archive([
            ("package/UserMaps/Example/RRT_Scenario_User_Example.xml", b"<Scenario/>"),
            ("package/CustomAssets/shared.xml", b"<Assets/>"),
        ])
        result = inspect_package(self.path)
        self.assertEqual(result.wrapper, "package")
        self.assertEqual(result.scenarios, ("UserMaps/Example/RRT_Scenario_User_Example.xml",))
        self.assertEqual(len(result.entries), 2)

    def test_traversal_or_unsafe_windows_path_is_rejected(self):
        for bad in ("../escape", "UserMaps/../../escape", "C:/escape", "UserMaps\\escape"):
            with self.subTest(bad=bad):
                self.archive([
                    ("UserMaps/Example/RRT_Scenario_User_Example.xml", b"<Scenario/>"),
                    (bad, b"unsafe"),
                ])
                with self.assertRaises(PackageError):
                    inspect_package(self.path)

    def test_link_and_case_collision_are_rejected(self):
        scenario = ("UserMaps/Example/RRT_Scenario_User_Example.xml", b"<Scenario/>")
        self.archive([scenario], links=[("UserMaps/Example/linked", "/outside")])
        with self.assertRaises(PackageError):
            inspect_package(self.path)
        self.archive([scenario, ("usermaps/example/rrt_scenario_user_example.XML", b"second")])
        with self.assertRaises(PackageError):
            inspect_package(self.path)

    def test_missing_scenario_and_empty_archive_are_rejected(self):
        self.archive([("UserMaps/Example/readme.txt", b"no scenario")])
        with self.assertRaises(PackageError):
            inspect_package(self.path)

    def test_checked_package_extracts_to_an_independent_new_directory(self):
        scenario = b"<Scenario/>"
        self.archive([
            ("package/UserMaps/Example/RRT_Scenario_User_Example.xml", scenario),
            ("package/CustomAssets/shared.xml", b"<Assets/>"),
        ])
        inspection = inspect_package(self.path)
        destination = Path(self.temp.name) / "prepared-import"
        result = extract_package(self.path, destination, inspection.source_sha256)
        self.assertEqual(result.inspection.source_sha256, inspection.source_sha256)
        self.assertEqual((destination / "UserMaps/Example/RRT_Scenario_User_Example.xml").read_bytes(), scenario)
        self.assertEqual((destination / "CustomAssets/shared.xml").read_bytes(), b"<Assets/>")
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o500)
        self.assertEqual(stat.S_IMODE((destination / "CustomAssets/shared.xml").stat().st_mode), 0o400)
        with self.assertRaises(PackageError):
            extract_package(self.path, destination)
        self.path.write_bytes(b"")
        with self.assertRaises(PackageError):
            inspect_package(self.path)


if __name__ == "__main__":
    unittest.main()
