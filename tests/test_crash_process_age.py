"""Independent timestamp, wire and immutable-history controls; invented reports only."""

import copy
from pathlib import Path
import tempfile
import unittest

from test_crash_reports import modern, legacy, parse
from smr_launcher.crash_evidence import summarize_crash_evidence
from smr_launcher.crash_reports import CrashReportError, CrashStore, _validate_payload
from smr_launcher.crash_reporting import PublicPayloadError, render_issue, validate_public_payload


class ProcessAgeControls(unittest.TestCase):
    def test_exact_elapsed_milliseconds_and_timezone_normalization(self):
        for timestamp, expected in (
            ("2026-01-02T12:00:00Z", 0),
            ("2026-01-02T12:00:00.000999Z", 0),
            ("2026-01-02T12:00:01.234999Z", 1234),
            ("2026-01-03T12:00:00.125Z", 86400125),
            ("2026-01-02T07:01:00-05:00", 60000),
        ):
            with self.subTest(timestamp=timestamp):
                payload = parse(modern(captureTime=timestamp))
                self.assertEqual(payload["schema"], 3)
                self.assertEqual(payload["process_age_ms"], expected)
                self.assertEqual(validate_public_payload(payload), payload)
                self.assertEqual(_validate_payload(payload), payload)
        self.assertEqual(parse(legacy())["process_age_ms"], 60000)

    def test_invalid_or_unbound_times_do_not_acquire_an_age(self):
        for fields in (
            dict(captureTime="2026-01-02T11:59:59Z"),
            dict(captureTime="2026-01-02T12:01:00"),
            dict(captureTime="PRIVATE_CANARY"),
            dict(procLaunch="2026-01-02T12:00:01Z"),
            dict(procLaunch=None),
        ):
            with self.subTest(fields=fields):
                self.assertIsNone(parse(modern(**fields)))

    def test_age_does_not_change_incident_or_family_grouping(self):
        first = parse()
        later = parse(modern(captureTime="2026-01-02T12:20:59.1421Z"))
        self.assertEqual(later["process_age_ms"], 1259142)
        self.assertNotEqual(first["report_sha256"], later["report_sha256"])
        self.assertEqual(first["fingerprint"], later["fingerprint"])
        self.assertEqual(summarize_crash_evidence(first), summarize_crash_evidence(later))
        title, body = render_issue(later)
        self.assertIn("Process age at report (ms): 1259142", body)
        self.assertIn("not observed loading or gameplay phase", body)
        self.assertNotIn("procLaunch", body)

    def test_independent_wire_and_store_gates_reject_invalid_age(self):
        first = parse()
        for value in (-1, True, 1.0, "60000", None, 2**64, {}, []):
            with self.subTest(value=value):
                payload = dict(first, process_age_ms=value)
                with self.assertRaises(CrashReportError):
                    _validate_payload(payload)
                with self.assertRaises(PublicPayloadError):
                    validate_public_payload(payload)
        missing = dict(first)
        missing.pop("process_age_ms")
        for payload in (
            missing,
            dict(first, schema=2),
            dict(first, schema=1),
            dict(first, schema=4),
        ):
            with self.assertRaises(CrashReportError):
                _validate_payload(payload)
            with self.assertRaises(PublicPayloadError):
                validate_public_payload(payload)
        for value in (0, 2**64 - 1):
            payload = dict(first, process_age_ms=value)
            self.assertEqual(_validate_payload(payload), payload)
            self.assertEqual(validate_public_payload(payload), payload)

    def test_old_schema_reparse_keeps_incident_and_publication_bytes(self):
        raw = modern()
        current = parse(raw)
        for version in (1, 2):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve() / "store"
                old = copy.deepcopy(current)
                old["schema"] = version
                old.pop("process_age_ms")
                if version == 1:
                    old.pop("diagnostics")
                self.assertEqual(validate_public_payload(old), old)
                self.assertIn("Process age at report (ms): unknown", render_issue(old)[1])
                with CrashStore(root) as store:
                    entry = store.collect(raw, old)
                    store.mark_publication(
                        old["fingerprint"],
                        "published",
                        "https://example.invalid/retained",
                        incident_id=entry["incident_id"],
                    )
                before = {p.name: p.read_bytes() for p in root.iterdir()}
                with CrashStore(root) as store:
                    retained = store.collect(raw, current)
                    self.assertEqual(retained["payload"], old)
                    self.assertEqual(retained["publication"]["status"], "published")
                    self.assertEqual(store.pending(), [])
                    with self.assertRaises(CrashReportError):
                        store.collect(raw, dict(current, archive_sha256="f" * 64))
                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_schema3_uses_incident_state_and_keeps_same_family_reports_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "store"
            with CrashStore(root) as store:
                first = parse()
                entry = store.collect(modern(), first)
                self.assertTrue(
                    (root / ("incident-" + entry["incident_id"] + ".publication")).exists()
                )
                store.mark_publication(
                    first["fingerprint"],
                    "published",
                    "https://example.invalid/first",
                    incident_id=entry["incident_id"],
                )
                raw = modern(captureTime="2026-01-02T12:20:00Z")
                second = store.collect(raw, parse(raw))
                self.assertEqual(second["publication"]["status"], "pending")
                self.assertNotEqual(second["incident_id"], entry["incident_id"])
                self.assertEqual(store.pending(), [second])
