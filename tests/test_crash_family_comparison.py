"""Invented cross-archive incidents and fail-closed offline command expectations."""

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smr_launcher import crash_evidence
from smr_launcher.crash_reports import CrashReportError
from test_crash_evidence import payload
from test_crash_details import CANARY


def incident(report="1", archive="a", variant="b"):
    result = payload(
        archive_sha256=archive * 64 if archive else None,
        variant_id=variant * 64,
    )
    result["report_sha256"] = report * 64
    return result


class CrashFamilyComparisonTests(unittest.TestCase):
    def test_cross_archive_lead_preserves_limits_and_inputs(self):
        first, second = incident(), incident("2", "c", "d")
        second["occurred_at"] = "2026-01-03T01:00:00Z"
        second["process_age_ms"] = 123456
        first["termination_namespace"] = second["termination_namespace"] = "UNKNOWN"
        before = copy.deepcopy([first, second])
        result = crash_evidence.compare_crash_evidence([second, first])
        self.assertEqual([first, second], before)
        self.assertEqual(result["incident_count"], 2)
        self.assertEqual(len(result["families"]), 1)
        family = result["families"][0]
        self.assertTrue(family["cross_archive_match"])
        self.assertEqual(family["archive_sha256s"], ["a" * 64, "c" * 64])
        self.assertEqual([m["report_sha256"] for m in family["incidents"]], ["1" * 64, "2" * 64])
        for member in family["incidents"]:
            self.assertEqual(member["evidence"]["family_status"], "partial")
            self.assertIn("termination_namespace_unknown", member["evidence"]["family_limits"])
            self.assertIn("operation_unobserved", member["evidence"]["gaps"])
        self.assertIn("not proof", result["limitation"])
        self.assertEqual(result, crash_evidence.compare_crash_evidence([first, second]))

    def test_variants_and_unknown_archive_do_not_inflate_cross_archive_match(self):
        result = crash_evidence.compare_crash_evidence(
            [incident(), incident("2", "a", "c"), incident("3", None, "d")]
        )
        family = result["families"][0]
        self.assertFalse(family["cross_archive_match"])
        self.assertEqual(family["archive_sha256s"], ["a" * 64])
        self.assertEqual(len(family["incidents"]), 3)

    def test_changed_build_and_stack_separate_candidates(self):
        first, build, stack = incident(), incident("2", "c"), incident("3", "d")
        build["game_executable_sha256"] = "e" * 64
        stack["diagnostics"]["threads"]["items"][1]["frames"]["items"][0].update(
            offset=300, address=4396
        )
        result = crash_evidence.compare_crash_evidence([first, build, stack])
        self.assertEqual(len(result["families"]), 3)
        self.assertEqual(result["unclassified"], [])
        self.assertFalse(any(f["cross_archive_match"] for f in result["families"]))

    def test_legacy_and_incomplete_evidence_stay_unclassified(self):
        legacy, truncated = incident(), incident("2", "c")
        legacy["schema"] = 1
        del legacy["diagnostics"], legacy["process_age_ms"]
        truncated["diagnostics"]["threads"]["items"][1]["frames"]["source_count"] = 2
        result = crash_evidence.compare_crash_evidence([legacy, truncated])
        self.assertEqual(result["families"], [])
        self.assertEqual(len(result["unclassified"]), 2)
        self.assertTrue(
            all(m["evidence"]["family_status"] == "unknown" for m in result["unclassified"])
        )

    def test_identical_duplicates_count_once_conflicting_report_refuses(self):
        first = incident()
        result = crash_evidence.compare_crash_evidence([first, copy.deepcopy(first)])
        self.assertEqual(result["input_count"], 2)
        self.assertEqual(result["incident_count"], 1)
        changed = copy.deepcopy(first)
        changed["archive_sha256"] = "c" * 64
        with self.assertRaisesRegex(CrashReportError, "conflicting"):
            crash_evidence.compare_crash_evidence([first, changed])

    def test_output_projection_contains_no_diagnostic_details_or_free_text(self):
        first = incident()
        result = crash_evidence.compare_crash_evidence([first])
        encoded = json.dumps(result)
        for forbidden in (
            CANARY,
            "occurred_at",
            "process_age_ms",
            "diagnostics",
            'registers"',
            'address"',
            "launcher_version",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(
            set(result),
            {"version", "input_count", "incident_count", "families", "unclassified", "limitation"},
        )
        family = result["families"][0]
        self.assertEqual(
            set(family),
            {"family_fingerprint", "archive_sha256s", "cross_archive_match", "incidents"},
        )
        self.assertEqual(
            set(family["incidents"][0]),
            {"report_sha256", "archive_sha256", "variant_id", "scenario_key", "evidence"},
        )

    def test_invalid_payloads_and_count_refuse(self):
        for values in ([], [incident()] * 33, [None], [dict(incident(), private=CANARY)]):
            with self.subTest(count=len(values)), self.assertRaises(CrashReportError):
                crash_evidence.compare_crash_evidence(values)


class CrashFamilyCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = self.root / "PRIVATE_CANARY.json"
        self.first.write_text(json.dumps(incident()))

    def run_command(self, paths):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = crash_evidence.main([str(p) for p in paths])
        return result, stdout.getvalue(), stderr.getvalue()

    def assert_refusal(self, paths):
        result, stdout, stderr = self.run_command(paths)
        self.assertEqual(result, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            "Cannot compare crash evidence: invalid, conflicting, unreadable or excessive inputs.\n",
        )

    def test_explicit_files_only_deduplicated_without_writes(self):
        before = self.first.read_bytes()
        result, stdout, stderr = self.run_command([self.first, self.first])
        self.assertEqual((result, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["incident_count"], 1)
        self.assertNotIn("PRIVATE_CANARY", stdout)
        self.assertEqual(self.first.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.first])

    def test_later_invalid_input_emits_no_partial_result(self):
        other = self.root / "bad.json"
        for data in (
            b'{"schema":1,"schema":2}',
            b"NaN",
            b"\xff",
            json.dumps(dict(incident(), private=CANARY)).encode(),
        ):
            other.write_bytes(data)
            self.assert_refusal([self.first, other])

    def test_nonregular_missing_and_leaf_links_refuse(self):
        link = self.root / "link.json"
        link.symlink_to(self.first)
        fifo = self.root / "fifo.json"
        os.mkfifo(fifo)
        for path in (link, fifo, self.root, self.root / "missing.json"):
            self.assert_refusal([path])

    def test_per_file_total_and_count_bounds(self):
        too_large = self.root / "large.json"
        too_large.write_bytes(b" " * (crash_evidence.MAX_COMPARISON_FILE_BYTES + 1))
        self.assert_refusal([too_large])
        with patch.object(crash_evidence, "MAX_COMPARISON_TOTAL_BYTES", self.first.stat().st_size):
            self.assert_refusal([self.first, self.first])
        self.assert_refusal([self.first] * 33)

    def test_changed_input_and_short_read_refuse(self):
        with patch.object(os, "read", return_value=b"{"):
            self.assert_refusal([self.first])
        before = self.first.stat()
        changed = os.stat_result(tuple(before)[:8] + (before.st_mtime + 1, before.st_ctime))
        with patch.object(os, "fstat", side_effect=[before, changed]):
            self.assert_refusal([self.first])

    def test_argument_errors_use_fixed_refusal_without_echoing_inputs(self):
        for args in ([], [str(self.first), "--PRIVATE_CANARY.json"], ["--PRIVATE_CANARY.json"]):
            with self.subTest(arguments=len(args)):
                self.assert_refusal(args)

    def test_help_remains_successful_without_opening_inputs(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(os, "open") as opened:
            with self.assertRaises(SystemExit) as result:
                crash_evidence.main(["--help"])
        self.assertEqual(result.exception.code, 0)
        self.assertIn("Compare explicit sanitized crash payload", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        opened.assert_not_called()
