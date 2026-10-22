"""Current diagnostics require current inputs; all assets are invented."""

from io import BytesIO
import json
import os
import tarfile
import unittest
from unittest.mock import patch

import test_application as fixtures
from smr_launcher import map_inspection


class ImportCheckFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ApplicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.application
        executable = self.fixture.root / "game/Game.app/Contents/MacOS/Sid Meiers Railroads"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"test game")
        self.fixture.installation.executable = executable
        self.stock = executable.parents[3] / "SMRailroadsData/assets"
        self.stock.mkdir(parents=True)
        with tarfile.open(self.fixture.archive, "w") as stream:
            for name, data in (
                (
                    "UserMaps/Example/RRT_Scenario_User_Example.xml",
                    b"<RRTScenario><GoodsXMLFile>invented-stock.xml</GoodsXMLFile></RRTScenario>",
                ),
                ("CustomAssets/Example.txt", b"map asset"),
            ):
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                stream.addfile(entry, BytesIO(data))
        self.app.setup()
        self.record = self.app.import_archive(self.fixture.archive)
        self.report_path = self.app.library / "import-checks" / (self.record.variant_id + ".json")

    def test_map_timestamp_change_withholds_cache_until_explicit_full_recheck(self):
        before = self.report_path.read_bytes()
        report = self.app.import_checks(self.record)
        self.assertIsNotNone(report)
        root = self.app.prepared / self.record.prepared_directory
        asset = root / "CustomAssets/Example.txt"
        info = asset.stat()
        os.utime(asset, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
        with patch("smr_launcher.application.inspect_map") as scanner:
            self.assertIsNone(self.app.import_checks(self.record))
            scanner.assert_not_called()
        self.assertEqual(self.report_path.read_bytes(), before)
        refreshed = self.app.import_checks(self.record, refresh=True)
        self.assertNotEqual(report["input_binding"], refreshed["input_binding"])
        self.assertEqual(self.app.import_checks(self.record), refreshed)
        self.assertIsNone(self.app.verification_for(self.record))

    def test_stock_name_change_invalidates_then_recheck_updates_warning(self):
        initial = self.app.import_checks(self.record)
        self.assertEqual(
            initial["scenarios"][0]["reference_fields"][0]["reason"], "no_loose_candidate"
        )
        stock = self.stock / "invented-stock.xml"
        stock.write_bytes(b"<InventedStock/>")
        self.assertIsNone(self.app.import_checks(self.record))
        current = self.app.import_checks(self.record, refresh=True)
        self.assertEqual(
            current["scenarios"][0]["reference_fields"][0]["reason"], "unique_loose_candidate"
        )
        stock.unlink()
        self.assertIsNone(self.app.import_checks(self.record))
        final = self.app.import_checks(self.record, refresh=True)
        self.assertEqual(
            final["scenarios"][0]["reference_fields"][0]["reason"], "no_loose_candidate"
        )

    def test_missing_or_changed_contract_binding_is_not_current(self):
        original = self.report_path.read_bytes()
        legacy = json.loads(original)
        legacy.pop("input_binding", None)
        self.report_path.write_text(json.dumps(legacy))
        before = self.report_path.read_bytes()
        self.assertIsNone(self.app.import_checks(self.record))
        self.assertEqual(self.report_path.read_bytes(), before)
        self.report_path.write_bytes(original)
        with patch.object(map_inspection, "VERSION", "different-contract"):
            self.assertIsNone(self.app.import_checks(self.record))
        self.assertEqual(self.report_path.read_bytes(), original)

    def test_changed_inputs_during_scan_refuse_report_publication(self):
        before = self.report_path.read_bytes()
        profile = self.app.profiles._profile_path(self.record.profile_id)
        (profile / "Saves/current.sav").write_bytes(b"invented current save")
        (profile / "Saves/future.sav").write_bytes(b"invented future save")
        preserved = self.fixture.snapshot_files(profile)
        scanner = map_inspection.inspect_map

        def change_stock_after_scan(*args, **kwargs):
            result = scanner(*args, **kwargs)
            (self.stock / "appeared.xml").write_bytes(b"<Invented/>")
            return result

        with patch("smr_launcher.application.inspect_map", side_effect=change_stock_after_scan):
            with self.assertRaisesRegex(fixtures.ApplicationError, "changed during"):
                self.app.import_checks(self.record, refresh=True)
        self.assertEqual(self.report_path.read_bytes(), before)
        self.assertEqual(self.fixture.snapshot_files(profile), preserved)

    def test_directory_metadata_and_membership_bind_scoped_inventory_budget(self):
        custom = self.app._prepared_root(self.record) / "CustomAssets"
        custom.chmod(0o700)
        limits = dict(
            max_entries=5, max_total_bytes=1024 * 1024, max_file_bytes=1024 * 1024, max_depth=64
        )
        with patch.object(map_inspection, "inventory_limits", return_value=limits):
            initial = self.app.import_checks(self.record, refresh=True)
            info = custom.stat()
            os.utime(custom, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            self.assertIsNone(self.app.import_checks(self.record))
            current = self.app.import_checks(self.record, refresh=True)
            self.assertNotEqual(initial["input_binding"], current["input_binding"])
            before = self.report_path.read_bytes()
            empty = custom / "empty-directory"
            empty.mkdir()
            with self.assertRaisesRegex(RuntimeError, "entry limit"):
                self.app.import_checks(self.record, refresh=True)
            self.assertEqual(self.report_path.read_bytes(), before)
            empty.rmdir()
            restored = self.app.import_checks(self.record, refresh=True)
            self.assertEqual(restored["status"], "completed")

    def test_directory_added_during_scan_preserves_prior_report(self):
        custom = self.app._prepared_root(self.record) / "CustomAssets"
        custom.chmod(0o700)
        before = self.report_path.read_bytes()
        scanner = map_inspection.inspect_map

        def add_directory_after_scan(*args, **kwargs):
            result = scanner(*args, **kwargs)
            (custom / "appeared-during-scan").mkdir()
            return result

        with patch("smr_launcher.application.inspect_map", side_effect=add_directory_after_scan):
            with self.assertRaisesRegex(fixtures.ApplicationError, "changed during"):
                self.app.import_checks(self.record, refresh=True)
        self.assertEqual(self.report_path.read_bytes(), before)
