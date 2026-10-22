"""Exercise the unchanged real import consumer before inspection integration."""

from io import BytesIO
import hashlib
import json
import os
import tarfile
import unittest
from unittest.mock import Mock, patch

import test_application as fixtures
from smr_launcher import map_inspection
from smr_launcher.gui import _checks_text
from smr_launcher.native_backend import NativeBackendWindow


class InspectionImportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ApplicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.application
        self.exe = self.fixture.root / "installation/Game.app/Contents/MacOS/Sid Meiers Railroads"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"test game")
        self.fixture.installation.executable = self.exe
        self.fixture.installation.executable_sha256 = hashlib.sha256(
            self.exe.read_bytes()
        ).hexdigest()
        self.stock = self.exe.parents[3] / "SMRailroadsData/assets"
        self.stock.mkdir(parents=True)
        (self.stock / "goods.xml").write_text("<RRTGoods/>")
        with tarfile.open(self.fixture.archive, "w") as stream:
            for name, data in (
                (
                    "UserMaps/Example/RRT_Scenario_User_Example.xml",
                    b"<RRTScenario><GoodsXMLFile>goods.xml</GoodsXMLFile></RRTScenario>",
                ),
                ("UserMaps/Example/goods.xml", b"<RRTGoods/>"),
            ):
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                stream.addfile(entry, BytesIO(data))
        self.app.setup()
        self.record = self.app.import_archive(self.fixture.archive)

    def test_import_uses_shared_report_and_preserves_candidate_uncertainty(self):
        report = self.app.import_checks(self.record)
        self.assertEqual(report["schema"], 2)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["analyzer"], map_inspection.analyzer_identity())
        self.assertNotIn("warnings", report)
        self.assertEqual(
            report["resource_resolution"]["explicit_reference_counts"][
                "ambiguous_loose_candidates"
            ],
            1,
        )
        self.assertEqual(report["input"]["scopes"], ["CustomAssets", "UserMaps"])
        self.assertEqual(report["runtime_acceptance"], "not_tested")
        self.assertIsNone(self.app.verification_for(self.record))
        text = _checks_text(report)
        self.assertIn("ambiguous_loose_candidates", text)
        self.assertIn("not_tested", text)
        self.assertIn("unimplemented_check", text)

    def test_stock_byte_change_invalidates_same_name_same_mtime(self):
        before = self.app.import_checks(self.record)
        stock = self.stock / "goods.xml"
        info = stock.stat()
        stock.write_text("<RRTGoods><Good/></RRTGoods>")
        os.utime(stock, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertIsNone(self.app.import_checks(self.record))
        refreshed = self.app.import_checks(self.record, refresh=True)
        self.assertNotEqual(before["input_binding"], refreshed["input_binding"])

    def test_executable_byte_change_withholds_cached_report(self):
        self.assertIsNotNone(self.app.import_checks(self.record))
        self.exe.write_bytes(b"changed executable")
        self.assertIsNone(self.app.import_checks(self.record))

    def test_unrelated_save_changes_do_not_enter_or_invalidate_report(self):
        before = self.app.import_checks(self.record)
        root = self.app._prepared_root(self.record)
        # Only this disposable fixture is writable; production preparation stays frozen.
        (root / "Saves").chmod(0o700)
        (root / "Saves/future.sav").write_bytes(b"synthetic future save")
        self.assertEqual(self.app.import_checks(self.record), before)
        self.assertFalse(any(row["path"].startswith("Saves/") for row in before["input"]["files"]))

    def test_legacy_report_is_not_current_shared_inspection_evidence(self):
        path = self.app.library / "import-checks" / (self.record.variant_id + ".json")
        report = json.loads(path.read_text())
        report["schema"] = 1
        path.write_text(json.dumps(report))
        before = path.read_bytes()
        self.assertIsNone(self.app.import_checks(self.record))
        self.assertEqual(path.read_bytes(), before)

    def test_real_native_import_completion_accepts_shared_report(self):
        window = NativeBackendWindow.__new__(NativeBackendWindow)
        window.app = self.app
        window.close_when_idle = window.closed = False
        window.status = Mock()
        window._show = Mock()

        def immediate(message, operation, callback, **kwargs):
            callback(operation())

        window._submit = immediate
        window._imported(self.record)
        self.assertEqual(window.selected_profile, self.record.profile_id)
        window._show.assert_called_once_with("maps")
        self.assertIn("static findings", window.status.set.call_args.args[0])

    def test_cached_admission_refuses_map_change_during_stock_inventory(self):
        acquisition = map_inspection.acquisition
        original = acquisition.inventory
        target = self.app._prepared_root(self.record) / "UserMaps/Example/goods.xml"
        changed = False

        def inventory_then_change(path, **kwargs):
            nonlocal changed
            snapshot = original(path, **kwargs)
            if path == self.stock and not changed:
                target.chmod(0o600)  # Only the disposable test fixture.
                target.write_bytes(b"<RRTGoods><Good/></RRTGoods>")
                changed = True
            return snapshot

        before = (
            self.app.library / "import-checks" / (self.record.variant_id + ".json")
        ).read_bytes()
        with patch.object(acquisition, "inventory", side_effect=inventory_then_change):
            with self.assertRaisesRegex(acquisition.InspectionError, "changed during"):
                self.app.import_checks(self.record)
        self.assertTrue(changed)
        self.assertIsNone(self.app.import_checks(self.record))
        self.assertEqual(
            (self.app.library / "import-checks" / (self.record.variant_id + ".json")).read_bytes(),
            before,
        )


if __name__ == "__main__":
    unittest.main()
