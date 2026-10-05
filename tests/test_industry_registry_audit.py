"""Synthetic fixtures only; no installed game or third-party map data needed."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/research/check_industry_registry.py"
SPEC = importlib.util.spec_from_file_location("industry_registry_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def synthetic_binary():
    data = bytearray(512)
    struct.pack_into("<IIIIIIII", data, 0, 0xFEEDFACF, 0x01000007, 3, 2, 1, 72, 0, 0)
    struct.pack_into("<II16sQQQQIIII", data, 32, 0x19, 72, b"__TEXT", 0x1000, 512, 0, 512, 7, 5, 0, 0)
    struct.pack_into("<QQ", data, 128, 0x10C0, 0x10D0)
    data[192:201] = b"FactoryA\0"
    data[208:217] = b"FactoryB\0"
    return bytes(data)


class RegistryAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.map = self.root / "map"
        self.map.mkdir()
        (self.map / "RRT_Scenario_Test.xml").write_text(
            "<Scenario><IndustriesXMLFile>industry.xml</IndustriesXMLFile></Scenario>")

    def test_extracts_only_file_backed_hash_matched_registry(self):
        data = synthetic_binary()
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(audit.extract_registry(data, digest, 0x1080, 2), ("FactoryA", "FactoryB"))
        with self.assertRaisesRegex(audit.AuditError, "SHA-256"):
            audit.extract_registry(data)
        with self.assertRaisesRegex(audit.AuditError, "file-backed"):
            audit.extract_registry(data, digest, 0x11FC, 2)

    def test_reports_placed_and_unused_custom_definitions_without_mutation(self):
        (self.map / "industry.xml").write_text(
            "<RRTIndustries><Industries>"
            "<RRTIndustry><szName>FactoryA</szName></RRTIndustry>"
            "<RRTIndustry><szName>Custom</szName><Locations><Location><InCity>CityA</InCity>"
            "</Location></Locations></RRTIndustry>"
            "<RRTIndustry><szName>Unused</szName></RRTIndustry>"
            "</Industries></RRTIndustries>")
        before = {p.name: p.read_bytes() for p in self.map.iterdir()}
        result = audit.audit_map(self.map, ("FactoryA",))
        self.assertTrue(result["potential_risk"])
        self.assertTrue(result["explicit_placement_risk"])
        self.assertFalse(result["unresolved"])
        outside = result["scenarios"][0]["outside_registry"]
        self.assertEqual([(x["name"], x["explicit_locations"]) for x in outside], [("Custom", 1), ("Unused", 0)])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.map.iterdir()})

    def test_filename_collision_is_unresolved_instead_of_choosing(self):
        for folder in ("one", "two"):
            (self.map / folder).mkdir()
            (self.map / folder / "industry.xml").write_text("<RRTIndustries/>")
        result = audit.audit_map(self.map, ("FactoryA",))
        self.assertTrue(result["unresolved"])
        self.assertIn("Ambiguous", result["scenarios"][0]["error"])
        self.assertFalse(result["potential_risk"])

    def test_malformed_xml_is_unresolved_and_escape_rejected(self):
        (self.map / "industry.xml").write_text("<broken>")
        result = audit.audit_map(self.map, ("FactoryA",))
        self.assertTrue(result["unresolved"])
        with self.assertRaisesRegex(audit.AuditError, "escapes"):
            audit.audit_map(self.map, ("FactoryA",), ["../outside.xml"])

    def test_duplicate_definition_names_remain_visible(self):
        (self.map / "industry.xml").write_text(
            "<RRTIndustries><Industries>"
            "<RRTIndustry><szName>FactoryA</szName></RRTIndustry>"
            "<RRTIndustry><szName>FactoryA</szName></RRTIndustry>"
            "</Industries></RRTIndustries>")
        result = audit.audit_map(self.map, ("FactoryA",))
        self.assertEqual(result["scenarios"][0]["duplicate_names"], ["FactoryA"])
        self.assertFalse(result["potential_risk"])
        self.assertNotIn("verified", result["scenarios"][0]["status"])

    def test_utf16_declaration_is_rejected_before_xml_parse(self):
        path = self.map / "industry.xml"
        path.write_bytes('<!DOCTYPE root [<!ENTITY payload "example">]><root>&payload;</root>'.encode("utf-16"))
        before = path.read_bytes()
        with self.assertRaisesRegex(audit.AuditError, "DTD/entity"):
            audit.read_xml(path)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
