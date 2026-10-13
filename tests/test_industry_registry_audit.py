"""Synthetic fixtures only; no installed game or third-party map data needed."""

import hashlib
import importlib.util
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/research/check_industry_registry.py"
SPEC = importlib.util.spec_from_file_location("industry_registry_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def synthetic_binary():
    data = bytearray(512)
    struct.pack_into("<IIIIIIII", data, 0, 0xFEEDFACF, 0x01000007, 3, 2, 1, 72, 0, 0)
    struct.pack_into(
        "<II16sQQQQIIII", data, 32, 0x19, 72, b"__TEXT", 0x1000, 512, 0, 512, 7, 5, 0, 0
    )
    struct.pack_into("<QQ", data, 128, 0x10C0, 0x10D0)
    data[192:201] = b"FactoryA\0"
    data[208:217] = b"FactoryB\0"
    return bytes(data)


class RegistryMainInputTests(unittest.TestCase):
    def test_unknown_build_still_refuses_after_safe_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "invented-image"
            binary.write_bytes(synthetic_binary())
            output, errors = io.StringIO(), io.StringIO()
            with (
                patch.object(
                    audit.sys,
                    "argv",
                    ["audit", "--game-binary", str(binary), "--library", str(root)],
                ),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                self.assertEqual(audit.main(), 2)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("Unsupported executable SHA-256", errors.getvalue())

    def test_regular_executable_is_read_once_and_keeps_report_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / "invented-image"
            data = synthetic_binary()
            binary.write_bytes(data)
            (root / "catalogue.json").write_text('{"maps": []}')
            output = io.StringIO()
            with (
                patch.object(audit, "read_executable", wraps=audit.read_executable) as read,
                patch.object(audit, "extract_registry", return_value=("Invented",)) as extract,
                patch.object(
                    audit.sys,
                    "argv",
                    ["audit", "--game-binary", str(binary), "--library", str(root)],
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(audit.main(), 0)
            read.assert_called_once_with(binary)
            extract.assert_called_once_with(data)
            report = json.loads(output.getvalue())
            self.assertEqual(report["game_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(report["knowledge_use"]["game_sha256"], report["game_sha256"])

    def test_symlink_executable_refuses_before_registry_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            original, linked = root / "invented-image", root / "linked-image"
            original.write_bytes(synthetic_binary())
            linked.symlink_to(original)
            (root / "catalogue.json").write_text('{"maps": []}')
            output, errors = io.StringIO(), io.StringIO()
            with (
                patch.object(audit, "extract_registry", return_value=("Invented",)) as extract,
                patch.object(
                    audit.sys,
                    "argv",
                    ["audit", "--game-binary", str(linked), "--library", str(root)],
                ),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                result = audit.main()
            self.assertEqual(result, 2)
            extract.assert_not_called()
            self.assertEqual(output.getvalue(), "")
            self.assertIn("Industry audit failed", errors.getvalue())


class ExecutableReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "invented-image"
        self.data = b"01234567"
        self.path.write_bytes(self.data)

    def test_stable_partial_reads_preserve_bytes_and_file_facts(self):
        before = self.path.stat()
        read = audit.os.read
        observed, consumed = [], 0

        def partial(descriptor, count):
            nonlocal consumed
            self.assertLessEqual(count, len(self.data) + 1 - consumed)
            chunk = read(descriptor, min(count, 2))
            consumed += len(chunk)
            observed.append(count)
            return chunk

        with patch.object(audit.os, "read", side_effect=partial):
            self.assertEqual(audit.read_executable(self.path), self.data)
        self.assertGreater(len(observed), 1)
        after = self.path.stat()
        for name in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns"):
            self.assertEqual(getattr(before, name), getattr(after, name))

    def test_explicit_limits_and_oversized_metadata_refuse_before_read(self):
        self.assertEqual(audit.MAX_EXECUTABLE_BYTES, 256 * 1024 * 1024)
        self.assertEqual(audit.MAX_XML_BYTES, 16 * 1024 * 1024)
        with patch.object(audit, "MAX_EXECUTABLE_BYTES", len(self.data)):
            self.assertEqual(audit.read_executable(self.path), self.data)
        with (
            patch.object(audit, "MAX_EXECUTABLE_BYTES", len(self.data) - 1),
            patch.object(audit.os, "read") as read,
            self.assertRaises(audit.AuditError),
        ):
            audit.read_executable(self.path)
        read.assert_not_called()

    def test_growth_and_short_read_refuse_with_admitted_size_budget(self):
        requested = []

        def grown(descriptor, count):
            requested.append(count)
            return b"g" * count

        with patch.object(audit.os, "read", side_effect=grown), self.assertRaises(audit.AuditError):
            audit.read_executable(self.path)
        self.assertEqual(requested, [len(self.data) + 1])
        read = audit.os.read
        calls = 0

        def short(descriptor, count):
            nonlocal calls
            calls += 1
            return read(descriptor, min(count, 2)) if calls == 1 else b""

        with patch.object(audit.os, "read", side_effect=short), self.assertRaises(audit.AuditError):
            audit.read_executable(self.path)

    def test_changed_descriptor_or_path_identity_refuses(self):
        before = self.path.stat()
        fields = {
            name: getattr(before, name)
            for name in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        }
        for name in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns"):
            changed = SimpleNamespace(**dict(fields, **{name: fields[name] + 1}))
            with self.subTest(field=name, source="descriptor"):
                with (
                    patch.object(audit.os, "fstat", side_effect=(before, changed)),
                    self.assertRaises(audit.AuditError),
                ):
                    audit.read_executable(self.path)
            with self.subTest(field=name, source="path"):
                with (
                    patch.object(audit.os, "stat", return_value=changed),
                    self.assertRaises(audit.AuditError),
                ):
                    audit.read_executable(self.path)

    def test_unsafe_empty_and_missing_inputs_refuse_without_blocking(self):
        linked, fifo, empty = self.root / "linked", self.root / "fifo", self.root / "empty"
        linked.symlink_to(self.path)
        os.mkfifo(fifo)
        empty.write_bytes(b"")
        for path in (linked, fifo, empty, self.root, self.root / "missing"):
            with self.subTest(path=path.name), self.assertRaises(audit.AuditError):
                audit.read_executable(path)
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_read_failure_closes_owned_descriptor_and_unavailable_flags_refuse(self):
        close = audit.os.close
        with (
            patch.object(audit.os, "read", side_effect=OSError("invented read failure")),
            patch.object(audit.os, "close", wraps=close) as closed,
            self.assertRaises(audit.AuditError),
        ):
            audit.read_executable(self.path)
        closed.assert_called_once()
        with self.assertRaises(OSError):
            os.fstat(closed.call_args.args[0])
        for missing in ("O_NOFOLLOW", "O_NONBLOCK"):
            flags = {
                name: getattr(os, name)
                for name in ("O_RDONLY", "O_NOFOLLOW", "O_NONBLOCK")
                if name != missing
            }
            with (
                self.subTest(missing=missing),
                patch.object(audit, "os", SimpleNamespace(**flags)),
                self.assertRaises(audit.AuditError),
            ):
                audit.read_executable(self.path)


class RegistryAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.map = self.root / "map"
        self.map.mkdir()
        (self.map / "RRT_Scenario_Test.xml").write_text(
            "<Scenario><IndustriesXMLFile>industry.xml</IndustriesXMLFile></Scenario>"
        )

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
            "</Industries></RRTIndustries>"
        )
        before = {p.name: p.read_bytes() for p in self.map.iterdir()}
        result = audit.audit_map(self.map, ("FactoryA",))
        self.assertTrue(result["potential_risk"])
        self.assertTrue(result["explicit_placement_risk"])
        self.assertFalse(result["unresolved"])
        outside = result["scenarios"][0]["outside_registry"]
        self.assertEqual(
            [(x["name"], x["explicit_locations"]) for x in outside], [("Custom", 1), ("Unused", 0)]
        )
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
            "</Industries></RRTIndustries>"
        )
        result = audit.audit_map(self.map, ("FactoryA",))
        self.assertEqual(result["scenarios"][0]["duplicate_names"], ["FactoryA"])
        self.assertFalse(result["potential_risk"])
        self.assertNotIn("verified", result["scenarios"][0]["status"])

    def test_utf16_declaration_is_rejected_before_xml_parse(self):
        path = self.map / "industry.xml"
        path.write_bytes(
            '<!DOCTYPE root [<!ENTITY payload "example">]><root>&payload;</root>'.encode("utf-16")
        )
        before = path.read_bytes()
        with self.assertRaisesRegex(audit.AuditError, "DTD/entity"):
            audit.read_xml(path)
        self.assertEqual(path.read_bytes(), before)

    def test_xml_tree_traversal_error_fails_closed(self):
        def failing_walk(root, *, followlinks, onerror):
            yield str(root), [], []
            onerror(PermissionError("denied directory"))

        with patch.object(audit.os, "walk", side_effect=failing_walk):
            with self.assertRaisesRegex(audit.AuditError, "traversal failed"):
                audit.xml_files(self.map)

    def test_xml_tree_symlink_directory_and_file_fail_closed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "industry.xml").write_text("<RRTIndustries/>")
        directory_link = self.map / "linked-directory"
        try:
            directory_link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable")
        with self.assertRaisesRegex(audit.AuditError, "symlink directory"):
            audit.xml_files(self.map)
        directory_link.unlink()

        xml_link = self.map / "industry.xml"
        xml_link.symlink_to(outside / "industry.xml")
        with self.assertRaisesRegex(audit.AuditError, "symlink entry"):
            audit.xml_files(self.map)

    def test_dangling_non_xml_symlink_fails_closed(self):
        link = self.map / "unclassified-entry"
        try:
            link.symlink_to(self.root / "missing-target")
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable")
        with self.assertRaisesRegex(audit.AuditError, "symlink entry"):
            audit.xml_files(self.map)

    def test_nonregular_xml_is_rejected_without_blocking(self):
        fifo = self.map / "industry.xml"
        try:
            os.mkfifo(fifo)
        except (AttributeError, OSError, NotImplementedError):
            self.skipTest("Named pipes are unavailable")
        with self.assertRaisesRegex(audit.AuditError, "not a regular file"):
            audit.xml_files(self.map)
        with self.assertRaisesRegex(audit.AuditError, "regular file"):
            audit.read_xml(fifo)

    def test_short_xml_read_fails_even_when_file_metadata_is_stable(self):
        path = self.map / "stable.xml"
        path.write_text("<root><value>complete</value></root>")
        real_read = audit.os.read
        calls = 0

        def short_read(descriptor, count):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_read(descriptor, min(count, 4))
            return b""

        with patch.object(audit.os, "read", side_effect=short_read):
            with self.assertRaisesRegex(audit.AuditError, "short read"):
                audit.read_xml(path)


if __name__ == "__main__":
    unittest.main()
