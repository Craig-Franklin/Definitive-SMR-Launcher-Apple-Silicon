"""Invented contradictory frame identities must not produce shared families."""

import copy
import unittest

from smr_launcher.crash_evidence import compare_crash_evidence
from smr_launcher.crash_reporting import render_issue
from test_crash_details import CANARY, EXE, source
from test_crash_evidence import payload, summary


GOLDEN = "0efbe0217960747988a1e5a68c9b47b17c5715b5758c6e5fc365d2c02218255a"


class CrashFrameBindingTests(unittest.TestCase):
    def test_conflicting_game_address_is_unknown_without_changing_payload(self):
        report = source()
        report["threads"][1]["frames"][0]["instructionAddr"] = 4353
        checked = payload(report)
        before = copy.deepcopy(checked)
        result = summary(checked)
        self.assertEqual(checked, before)
        self.assertIsNone(result["family_fingerprint"])
        self.assertEqual(result["family_status"], "unknown")
        self.assertEqual(result["supported_game_frames"], 0)
        self.assertIn("game_frame_address_conflict", result["family_limits"])

    def test_consistent_and_optional_absolute_fields_keep_original_family(self):
        checked = payload()
        self.assertEqual(summary(checked)["family_fingerprint"], GOLDEN)
        frame = checked["diagnostics"]["threads"]["items"][1]["frames"]["items"][0]
        frame["address"] = None
        self.assertEqual(summary(checked)["family_fingerprint"], GOLDEN)
        checked["diagnostics"]["images"]["items"][0]["base"] = None
        self.assertEqual(summary(checked)["family_fingerprint"], GOLDEN)
        frame["address"] = 4352
        self.assertEqual(summary(checked)["family_fingerprint"], GOLDEN)

    def test_uint64_overflow_is_unknown_without_wrapping_or_absolute_address(self):
        report = source()
        report["usedImages"][0]["base"] = (1 << 64) - 128
        report["threads"][1]["frames"][0].pop("instructionAddr")
        result = summary(payload(report))
        self.assertIsNone(result["family_fingerprint"])
        self.assertEqual(result["family_status"], "unknown")
        self.assertIn("game_frame_address_conflict", result["family_limits"])

    def test_non_game_fault_conflict_preserves_only_partial_game_chain(self):
        report = source()
        report["usedImages"][1]["arch"] = "x86_64"
        report["threads"][1]["frames"].insert(
            0, dict(imageIndex=1, imageOffset=32, instructionAddr=32801)
        )
        result = summary(payload(report))
        self.assertEqual(result["family_fingerprint"], GOLDEN)
        self.assertEqual(result["family_status"], "partial")
        self.assertEqual(result["supported_game_frames"], 1)
        self.assertIn("fault_pc_unresolved", result["family_limits"])
        self.assertIn("non_game_frame_address_conflict", result["gaps"])

    def test_contradiction_cannot_nominate_cross_archive_family(self):
        first = payload(archive_sha256="b" * 64, variant_id="c" * 64)
        second = copy.deepcopy(first)
        second.update(report_sha256="2" * 64, archive_sha256="e" * 64)
        second["diagnostics"]["threads"]["items"][1]["frames"]["items"][0]["address"] = 4353
        result = compare_crash_evidence([first, second])
        self.assertEqual(result["incident_count"], 2)
        self.assertEqual(len(result["families"]), 1)
        self.assertFalse(result["families"][0]["cross_archive_match"])
        self.assertEqual(len(result["unclassified"]), 1)
        self.assertEqual(result["unclassified"][0]["report_sha256"], "2" * 64)

    def test_rendered_conflict_keeps_incident_identity_and_privacy(self):
        report = source()
        report["threads"][1]["frames"][0]["instructionAddr"] = 4353
        checked = payload(report)
        title, body = render_issue(checked)
        self.assertIn("<!-- smr-crash-fingerprint:" + checked["fingerprint"] + " -->", body)
        self.assertIn("Candidate family: unknown", body)
        self.assertIn("Signature coverage: unknown", body)
        self.assertIn("game_frame_address_conflict", body)
        self.assertNotIn(CANARY, title + body)
        self.assertNotIn(EXE, title + body)
