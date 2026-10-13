"""Synthetic bounded-scan coverage, independent of native game behavior."""

from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smr_launcher import import_checks
from smr_launcher.gui import _checks_text


class ImportCheckCoverageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.maps = self.root / "UserMaps"
        self.maps.mkdir()

    def write(self, name, data):
        path = self.maps / name
        path.write_bytes(data)
        return path

    def scan(self, **limits):
        before = {
            p.name: (p.read_bytes(), p.stat().st_size, p.stat().st_mtime_ns)
            for p in self.maps.iterdir()
        }
        with patch.multiple(import_checks, **limits) if limits else nullcontext():
            report = import_checks.inspect_map(self.root)
        self.assertEqual(
            before,
            {
                p.name: (p.read_bytes(), p.stat().st_size, p.stat().st_mtime_ns)
                for p in self.maps.iterdir()
            },
        )
        return report

    def assert_coverage(self, report, complete, discovered, examined, reasons=()):
        coverage = report["xml_scan"]
        self.assertIs(coverage["complete"], complete)
        self.assertEqual(coverage["files_discovered"], discovered)
        self.assertEqual(coverage["files_examined"], examined)
        self.assertEqual(set(coverage["incomplete_reasons"]), set(reasons))

    def test_empty_and_findings_can_be_complete(self):
        self.assert_coverage(self.scan(), True, 0, 0)
        self.write("scenario.XML", b"<a><b>missing.xml</b></a>")
        report = self.scan()
        self.assert_coverage(report, True, 1, 1)
        self.assertIn("may be packed or unavailable", report["warnings"][0])
        self.assertEqual(report["xml_files_checked"], 1)

    def test_size_limit_boundary(self):
        self.write("a.xml", b"<a/>")
        self.assert_coverage(self.scan(MAX_XML_BYTES=4), True, 1, 1)
        report = self.scan(MAX_XML_BYTES=3)
        self.assert_coverage(report, False, 1, 0, ["xml_size_limit"])
        self.assertEqual(report["xml_files_checked"], 0)
        self.assertIn("exceeds the scan size limit", report["warnings"][0])

    def test_file_and_total_budget_boundaries(self):
        self.write("a.xml", b"<a/>")
        self.write("b.xml", b"<b/>")
        self.assert_coverage(self.scan(MAX_XML_FILES=2, MAX_TOTAL_XML=8), True, 2, 2)
        for limits, reason in [
            ({"MAX_XML_FILES": 1}, "xml_file_limit"),
            ({"MAX_TOTAL_XML": 7}, "xml_total_bytes_limit"),
            ({"MAX_XML_FILES": 1, "MAX_TOTAL_XML": 7}, "xml_file_limit"),
        ]:
            with self.subTest(limits=limits):
                report = self.scan(**limits)
                reasons = [reason]
                if len(limits) == 2:
                    reasons.append("xml_total_bytes_limit")
                self.assert_coverage(report, False, 2, 1, reasons)
                self.assertEqual(report["xml_files_checked"], 1)
                self.assertEqual(
                    report["warnings"],
                    ["XML scan limit reached; remaining files were not checked."],
                )

    def test_declaration_and_parse_refusals_keep_historical_counter(self):
        for data, reason, warning in [
            (
                b'<!DOCTYPE a [<!ENTITY x "x">]><a>&x;</a>',
                "xml_declaration_refused",
                "declarations require manual",
            ),
            (
                b"<\0!\0E\0N\0T\0I\0T\0Y\0 a>",
                "xml_declaration_refused",
                "declarations require manual",
            ),
            (b"<broken>", "xml_parse_failed", "could not be parsed"),
        ]:
            with self.subTest(data=data):
                self.write("a.xml", data)
                report = self.scan()
                self.assert_coverage(report, False, 1, 0, [reason])
                self.assertEqual(report["xml_files_checked"], 1)
                self.assertIn(warning, report["warnings"][0])
        self.write("a.xml", b"<a/>")
        self.assert_coverage(self.scan(), True, 1, 1)

    def test_finding_limit_boundary(self):
        for count in (199, 200, 201):
            with self.subTest(count=count):
                self.write(
                    "a.xml",
                    (
                        "<a>" + "".join(f"<b>missing{i}.xml</b>" for i in range(count)) + "</a>"
                    ).encode(),
                )
                report = self.scan()
                self.assert_coverage(
                    report,
                    count < 200,
                    1,
                    int(count < 200),
                    [] if count < 200 else ["xml_finding_limit"],
                )
                self.assertEqual(report["xml_files_checked"], 1)
                self.assertEqual(len(report["warnings"]), min(count, 200) + int(count >= 200))
                if count >= 200:
                    self.assertIn("Further findings omitted", report["warnings"][-1])

    def test_model_incomplete_is_independent(self):
        self.write("a.xml", b"<a/>")
        models = dict(complete=False, files_checked=0, findings=[])
        with patch.object(import_checks, "inspect_models", return_value=models):
            report = self.scan()
        self.assert_coverage(report, True, 1, 1)
        self.assertEqual(report["model_headers"], models)
        self.assertIn("Model scan incomplete", report["warnings"][-1])
        text = _checks_text(dict(report, checked_at="synthetic"))
        self.assertIn("Loose XML scan coverage: complete", text)
        self.assertIn("Model header coverage: incomplete", text)

    def test_filesystem_errors_still_propagate(self):
        self.write("a.xml", b"<a/>")
        with patch.object(Path, "open", side_effect=OSError("synthetic read denial")):
            with self.assertRaisesRegex(OSError, "synthetic read denial"):
                import_checks.inspect_map(self.root)

    def test_shared_text_current_legacy_and_missing_reports(self):
        legacy = dict(
            checked_at="synthetic",
            xml_files_checked=2,
            warnings=[],
            limitations="Synthetic limitation.",
        )
        text = _checks_text(legacy)
        self.assertIn("Loose XML scan coverage: unknown", text)
        self.assertIn("Model header coverage: unknown", text)
        self.assertIn("Native dependency resolution: unknown", text)
        self.assertIn("compatibility and gameplay remain unverified", text)
        self.assertNotIn("No problems found", text)
        self.write("bad.xml", b"<bad>")
        current = dict(
            self.scan(), checked_at="synthetic", input_scope="Synthetic prepared inputs only."
        )
        text = _checks_text(current)
        self.assertIn("Loose XML scan coverage: incomplete", text)
        self.assertIn("xml_parse_failed", text)
        self.assertIn(current["input_scope"], text)
        self.assertIn("Model header coverage: complete", text)
        self.assertIn("Gameplay remains unverified", _checks_text(None))

    def test_version_invalidates_previous_binding(self):
        self.assertNotEqual(import_checks.IMPORT_CHECKS_VERSION, "loose-xml-and-model-headers-v2")


if __name__ == "__main__":
    unittest.main()
