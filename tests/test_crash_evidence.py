"""Independent synthetic comparison, coverage and publication expectations."""

import copy
import unittest

from smr_launcher.crash_evidence import summarize_crash_evidence
from smr_launcher.crash_reporting import PublicPayloadError, render_issue, validate_public_payload
from smr_launcher.crash_reports import parse_crash_report
from test_crash_details import CANARY, EXE, START, UUID, raw, source


def payload(report=None, **context):
    return parse_crash_report(
        raw(source() if report is None else report),
        expected_executable=EXE,
        expected_pid=123,
        session_started_at=START,
        context=dict(game_executable_sha256="a" * 64, launcher_version="1.2.3", **context),
    )


def summary(p):
    return summarize_crash_evidence(validate_public_payload(p))


class CrashEvidenceTests(unittest.TestCase):
    def test_known_signature_and_cross_map_aslr_stability(self):
        p = payload(archive_sha256="b" * 64, variant_id="c" * 64, scenario_key="d" * 64)
        before = copy.deepcopy(p)
        result = summary(p)
        self.assertEqual(
            result["family_fingerprint"],
            "0efbe0217960747988a1e5a68c9b47b17c5715b5758c6e5fc365d2c02218255a",
        )
        self.assertEqual(result["family_status"], "complete")
        self.assertEqual(result["fault_frames_source"], 1)
        self.assertEqual(result["fault_registers_retained"], 3)
        report = source()
        report["usedImages"][0]["base"] = 65536
        report["threads"][1]["frames"][0]["instructionAddr"] = 65792
        report["exception"]["rawCodes"] = [1, 65792]
        other = payload(report, archive_sha256="e" * 64, variant_id="f" * 64)
        self.assertNotEqual(p["fingerprint"], other["fingerprint"])
        self.assertEqual(result["family_fingerprint"], summary(other)["family_fingerprint"])
        self.assertEqual(p, before)

    def test_identity_and_stack_changes_separate_families(self):
        original = payload()
        fingerprint = summary(original)["family_fingerprint"]
        changes = [
            ("build", lambda p: p.update(game_executable_sha256="e" * 64)),
            ("translation", lambda p: p.update(translated=False)),
            ("exception", lambda p: p.update(exception_type="EXC_BAD_INSTRUCTION")),
            ("signal", lambda p: p.update(signal="SIGILL")),
            ("termination", lambda p: p.update(termination_namespace="WATCHDOG")),
            (
                "uuid",
                lambda p: p["diagnostics"]["images"]["items"][0].update(
                    uuid="ffffffff-ffff-ffff-ffff-ffffffffffff"
                ),
            ),
            (
                "offset",
                lambda p: p["diagnostics"]["threads"]["items"][1]["frames"]["items"][0].update(
                    offset=257, address=4353
                ),
            ),
        ]
        for name, change in changes:
            with self.subTest(name=name):
                p = copy.deepcopy(original)
                change(p)
                self.assertNotEqual(fingerprint, summary(p)["family_fingerprint"])
        arm = copy.deepcopy(original)
        arm["architecture"] = "arm64"
        arm["diagnostics"]["images"]["items"][0]["architecture"] = "arm64"
        self.assertNotEqual(fingerprint, summary(arm)["family_fingerprint"])

    def test_full_fault_stack_beyond_twenty_frames_and_order(self):
        report = source()
        report["threads"][1]["frames"] = [dict(imageIndex=0, imageOffset=i) for i in range(22)]
        first = payload(report)
        report["threads"][1]["frames"][21]["imageOffset"] = 30
        second = payload(report)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotEqual(
            summary(first)["family_fingerprint"], summary(second)["family_fingerprint"]
        )
        report["threads"][1]["frames"].reverse()
        self.assertNotEqual(
            summary(second)["family_fingerprint"], summary(payload(report))["family_fingerprint"]
        )
        self.assertEqual(summary(first)["supported_game_frames"], 22)

    def test_unknown_translated_pc_is_partial_without_invented_mapping(self):
        report = source()
        report["usedImages"].append(dict(uuid="00000000-0000-0000-0000-000000000000"))
        report["threads"][1]["frames"].insert(0, dict(imageIndex=2, imageOffset=123456))
        first = summary(payload(report))
        report["threads"][1]["frames"][0]["imageOffset"] = 987654
        self.assertEqual(
            first["family_fingerprint"], summary(payload(report))["family_fingerprint"]
        )
        self.assertEqual(first["family_status"], "partial")
        self.assertEqual(first["family_limits"], ["fault_pc_unresolved"])

    def test_absent_termination_still_supports_partial_comparison(self):
        report = source()
        report.pop("termination")
        result = summary(payload(report))
        self.assertIsNotNone(result["family_fingerprint"])
        self.assertEqual(result["family_status"], "partial")
        self.assertEqual(result["family_limits"], ["termination_namespace_unknown"])
        self.assertNotEqual(result["family_fingerprint"], summary(payload())["family_fingerprint"])

    def test_insufficient_coverage_refuses_a_family(self):
        original = payload()
        changes = [
            ("unknown_arch", lambda p: p.update(architecture="UNKNOWN")),
            ("unknown_translation", lambda p: p.update(translated=None)),
            ("unknown_exception", lambda p: p.update(exception_type="UNKNOWN")),
            ("missing_uuid", lambda p: p["diagnostics"]["images"]["items"][0].update(uuid=None)),
            ("missing_size", lambda p: p["diagnostics"]["images"]["items"][0].update(size=None)),
            (
                "outside_image",
                lambda p: p["diagnostics"]["threads"]["items"][1]["frames"]["items"][0].update(
                    offset=8192
                ),
            ),
        ]
        for name, change in changes:
            with self.subTest(name=name):
                p = copy.deepcopy(original)
                change(p)
                self.assertIsNone(summary(p)["family_fingerprint"])
                self.assertEqual(summary(p)["family_status"], "unknown")
        report = source()
        report.pop("faultingThread")
        for thread in report["threads"]:
            thread.pop("triggered", None)
        self.assertIn("faulting_thread_unavailable", summary(payload(report))["family_limits"])
        self.assertIsNone(summary(payload(report))["family_fingerprint"])
        report = source()
        report["threads"][1]["frames"] *= 1025
        self.assertIn("fault_frames_incomplete", summary(payload(report))["family_limits"])
        self.assertIsNone(summary(payload(report))["family_fingerprint"])
        legacy = dict(original, schema=1)
        del legacy["diagnostics"]
        del legacy["process_age_ms"]
        self.assertIsNone(summary(legacy)["family_fingerprint"])

    def test_rendered_coverage_preserves_marker_and_privacy_gate(self):
        p = payload()
        title, body = render_issue(p)
        self.assertIn("<!-- smr-crash-fingerprint:" + p["fingerprint"] + " -->", body)
        self.assertIn("Signature coverage: complete", body)
        self.assertIn("scenario_unknown", body)
        self.assertIn("operation_unobserved", body)
        self.assertIn("triggering_resource_unobserved", body)
        self.assertIn("not a common-cause or repair-applicability claim", body)
        self.assertNotIn(CANARY, title + body)
        self.assertNotIn(EXE, title + body)
        self.assertIn(UUID, body)
        p["private_context"] = CANARY
        with self.assertRaises(PublicPayloadError):
            render_issue(p)
