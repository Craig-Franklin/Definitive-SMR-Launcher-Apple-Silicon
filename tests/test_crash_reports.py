"""Synthetic crash and private-filesystem risk controls; no real diagnostics."""
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from smr_launcher.crash_reports import CrashReportError, CrashStore, MAX_REPORT_BYTES, parse_crash_report

START = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
EXE = "/Applications/SyntheticGame.app/Contents/MacOS/SyntheticGame"
CONTEXT = {"game_executable_sha256": "a" * 64, "launcher_version": "1.2.3-rc.2", "archive_sha256": "b" * 64, "variant_id": "c" * 64, "scenario_key": "d" * 64, "expected_bundle_id": "example.synthetic.game"}
EXPECTED_KEYS = set("schema fingerprint report_sha256 occurred_at game_executable_sha256 launcher_version game_version macos_version architecture translated archive_sha256 variant_id scenario_key exception_type signal termination_namespace exception_codes frames diagnostics".split())


def modern(**updates):
    report = {"procPath": EXE, "pid": 123, "captureTime": "2026-01-02T12:01:00+00:00", "procLaunch": START.isoformat(),
              "bundleInfo": {"CFBundleIdentifier": "example.synthetic.game", "CFBundleShortVersionString": "1.0.0"},
              "osVersion": {"train": "15.3.1"}, "cpuType": "X86-64", "translated": True,
              "exception": {"type": "EXC_BAD_ACCESS", "signal": "SIGSEGV", "rawCodes": [1, "0x1024"]},
              "termination": {"namespace": "SIGNAL", "code": 11}, "faultingThread": 0,
              "threads": [{"triggered": True, "frames": [{"imageIndex": 0, "imageOffset": 256, "symbol": "token_SECRET_CANARY"}]}],
              "usedImages": [{"base": 4096, "size": 4096, "uuid": "12345678-1234-5678-1234-567812345678", "path": EXE}],
              "private": "/Users/PRIVATE_CANARY/secret.map"}
    report.update(updates)
    return (json.dumps({"bug_type": "309", "timestamp": "2026-01-02T12:01:00+00:00"}) + "\n" + json.dumps(report)).encode()


def legacy(base=0x1000, offset=0x100):
    return f"""Process: SyntheticGame [123]
Path: {EXE}
Identifier: example.synthetic.game
Version: 1.0.0 (10)
Code Type: X86-64 (Translated)
Date/Time: 2026-01-02 12:01:00.000 +0000
Launch Time: 2026-01-02 12:00:00.000 +0000
OS Version: macOS 15.3.1 (24E1)
Exception Type: EXC_BAD_ACCESS (SIGSEGV)
Exception Codes: 0x0000000000000001, 0x0000000000001024
Termination Reason: Namespace SIGNAL, Code 11
Crashed Thread: 0

Thread 0 Crashed:
0 SyntheticGame 0x{base + offset:x} token_SECRET_CANARY + 8

Binary Images:
0x{base:x} - 0x{base + 4095:x} SyntheticGame x86_64 <12345678-1234-5678-1234-567812345678> {EXE}
Private Text: /Users/PRIVATE_CANARY/secret.map
""".encode()


def parse(raw=None, **kwargs):
    options = dict(expected_executable=EXE, expected_pid=123, session_started_at=START, context=copy.deepcopy(CONTEXT))
    options.update(kwargs)
    return parse_crash_report(modern() if raw is None else raw, **options)


class ParserControls(unittest.TestCase):
    def test_modern_exact_payload_and_useful_frame(self):
        payload = parse()
        self.assertEqual(set(payload), EXPECTED_KEYS)
        self.assertEqual(payload["exception_type"], "EXC_BAD_ACCESS")
        self.assertEqual(payload["architecture"], "x86_64")
        self.assertEqual(payload["exception_codes"], [1, 0x1024])
        self.assertEqual(payload["frames"], [{"index": 0, "image_role": "game", "image_uuid": "12345678-1234-5678-1234-567812345678", "offset": 256}])
        self.assertEqual(payload["report_sha256"], hashlib.sha256(modern()).hexdigest())

    def test_legacy_equivalent_signature_and_aslr(self):
        self.assertEqual(parse(legacy())["occurred_at"], "2026-01-02T12:01:00+00:00")
        self.assertEqual(parse(legacy())["fingerprint"], parse()["fingerprint"])
        self.assertEqual(parse(legacy())["fingerprint"], parse(legacy(base=0x700000))["fingerprint"])
        self.assertNotEqual(parse(legacy())["report_sha256"], parse(legacy(base=0x700000))["report_sha256"])

    def test_changed_game_map_edition_scenario_and_frame_distinct(self):
        original = parse()["fingerprint"]
        for key in ("game_executable_sha256", "archive_sha256", "variant_id", "scenario_key"):
            with self.subTest(key=key):
                context = dict(CONTEXT, **{key: "e" * 64})
                self.assertNotEqual(parse(context=context)["fingerprint"], original)
        self.assertNotEqual(parse(legacy(offset=257))["fingerprint"], original)

    def test_wrong_identity_stale_birth_and_normal_exit(self):
        cases = [modern(procPath=EXE + "Other"), modern(pid=124), modern(pid=True),
                 modern(captureTime="2026-01-02T11:59:00Z"), modern(procLaunch="2026-01-02T11:00:00Z"),
                 modern(procLaunch=None), modern(procPath="/Users/USER/SyntheticGame"),
                 modern(bundleInfo={"CFBundleIdentifier": "other.app"}),
                 modern(exception={}, termination={"namespace": "SIGNAL", "code": 0}),
                 modern(exception={}, termination={"namespace": "UNKNOWN", "code": 44})]
        for raw in cases:
            with self.subTest(raw_sha=hashlib.sha256(raw).hexdigest()):
                self.assertIsNone(parse(raw))

    def test_missing_session_qualifiers_and_timezone(self):
        for overrides in ({"expected_pid": None}, {"session_started_at": None}, {"session_started_at": START.replace(tzinfo=None)}):
            self.assertIsNone(parse(**overrides))
        self.assertIsNone(parse(modern(captureTime="2026-01-02T12:01:00")))
        self.assertIsNone(parse(context=dict(CONTEXT, session_ended_at=START + timedelta(seconds=10))))

    def test_attached_session_requires_pinned_birth(self):
        later = START + timedelta(seconds=30)
        self.assertIsNone(parse(session_started_at=later))
        self.assertIsNotNone(parse(session_started_at=later, context=dict(CONTEXT, process_started_at=START)))
        self.assertIsNone(parse(context=dict(CONTEXT, process_started_at=START + timedelta(seconds=1))))

    def test_malformed_duplicate_keys_and_oversize(self):
        cases = [b"{broken", b"\xff", b"nonsense", b"x" * (MAX_REPORT_BYTES + 1), b"", b'{"pid":1,"pid":2}',
                 b'{"bug_type":"309"}\n{"exception":{"type":NaN}}', modern(threads="invalid")]
        for raw in cases:
            with self.subTest(size=len(raw)):
                with self.assertRaises(CrashReportError):
                    parse(raw)
        with self.assertRaises(CrashReportError):
            parse(legacy() + b"Path: /other\n")

    def test_unknown_fields_are_honest_and_no_private_text(self):
        raw = modern(bundleInfo={"CFBundleIdentifier": "example.synthetic.game", "CFBundleShortVersionString": "token_SECRET_CANARY"},
                     osVersion={"train": "/Users/PRIVATE_CANARY/secret.map"}, cpuType="private architecture", translated="yes",
                     exception={"type": "EXC_BAD_ACCESS", "signal": "SECRET", "rawCodes": [True, -1, 2**64, "SECRET", 0]},
                     usedImages=[{"path": "/Users/PRIVATE_CANARY/secret.map", "uuid": "token_SECRET_CANARY"}])
        payload = parse(raw)
        self.assertEqual(payload["game_version"], "UNKNOWN")
        self.assertEqual(payload["macos_version"], "UNKNOWN")
        self.assertIsNone(payload["translated"])
        self.assertEqual(payload["exception_codes"], [0])
        self.assertEqual(payload["frames"][0]["image_role"], "other")
        encoded = json.dumps(payload)
        for canary in ("PRIVATE_CANARY", "SECRET_CANARY", "SyntheticGame", '"symbol":', "/Users/"):
            self.assertNotIn(canary, encoded)

    def test_official_versions_only(self):
        for version in ("1.2.3", "1.2.3-alpha1", "1.2.3-beta.2", "1.2.3-rc9"):
            self.assertEqual(parse(context=dict(CONTEXT, launcher_version=version))["launcher_version"], version)
        for version in ("1.2.3-arbitrary-private-prerelease", "v1.2.3", "1.2", "1.2.3+secret", "1.2.3-rc", "1.2.3\nsecret"):
            with self.assertRaises(CrashReportError):
                parse(context=dict(CONTEXT, launcher_version=version))

    def test_frame_bounds_and_role(self):
        payload = parse(modern(threads=[{"triggered": True, "frames": [{"imageIndex": 0, "imageOffset": 7}] * 30}],
                               usedImages=[{"path": "/usr/lib/synthetic.dylib", "uuid": "12345678-1234-5678-1234-567812345678"}]))
        self.assertEqual(len(payload["frames"]), 20)
        self.assertEqual(payload["frames"][0]["image_role"], "system")
        self.assertEqual(parse(modern(threads=[]))["frames"], [])


class StoreControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ.get("CRASH_TEST_TEMP_ROOT"))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "incidents"
        self.store = CrashStore(self.root)
        self.addCleanup(self.store.close)
        self.raw = modern()
        self.payload = parse(self.raw)
        self.payload.pop("diagnostics")
        self.payload["schema"] = 1  # Existing immutable-store contract stays covered.

    def test_private_modes_raw_retention_and_restart(self):
        entry = self.store.collect(self.raw, self.payload)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(set(entry), {"incident_id", "fingerprint", "report_sha256", "raw_file", "payload", "publication"})
        raw_path = self.root / entry["raw_file"]
        self.assertEqual(raw_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(raw_path.read_bytes(), self.raw)
        with CrashStore(self.root) as restarted:
            self.assertEqual(restarted.pending(), [entry])

    def test_duplicate_dedup_and_each_equivalent_incident_retained(self):
        first = self.store.collect(self.raw, self.payload)
        self.assertEqual(first, self.store.collect(self.raw, self.payload))
        other_raw = modern(captureTime="2026-01-02T12:02:00Z")
        other_payload = parse(other_raw)
        other_payload.pop("diagnostics")
        other_payload["schema"] = 1
        second = self.store.collect(other_raw, other_payload)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotEqual(first["incident_id"], second["incident_id"])
        self.assertEqual(len(self.store.pending()), 2)
        self.store.mark_publication(first["fingerprint"], "published", "https://example.invalid/issues/1")
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(len(list(self.root.glob("*.raw"))), 2)

    def test_sending_restart_is_held_unknown_and_reconciliation_explicit(self):
        entry = self.store.collect(self.raw, self.payload)
        self.store.mark_publication(entry["fingerprint"], "sending")
        with CrashStore(self.root) as restarted:
            self.assertEqual(restarted.pending()[0]["publication"]["status"], "unknown")
            for status in ("pending", "sending"):
                with self.assertRaises(CrashReportError):
                    restarted.mark_publication(entry["fingerprint"], status)
            restarted.reconcile_publication(entry["fingerprint"], "pending", detail="synthetic proven no mutation")
            self.assertEqual(restarted.pending()[0]["publication"]["status"], "pending")
            restarted.mark_publication(entry["fingerprint"], "unknown")
            restarted.mark_publication(entry["fingerprint"], "published", "https://example.invalid/issues/1")
            self.assertEqual(restarted.pending(), [])

    def test_missing_publication_is_unknown(self):
        entry = self.store.collect(self.raw, self.payload)
        (self.root / (entry["fingerprint"] + ".publication")).unlink()
        self.assertEqual(self.store.pending()[0]["publication"]["status"], "unknown")

    def test_context_collision_and_digest_mismatch_refused(self):
        self.store.collect(self.raw, self.payload)
        changed = dict(self.payload, launcher_version="1.2.4")
        with self.assertRaises(CrashReportError):
            self.store.collect(self.raw, changed)
        with self.assertRaises(CrashReportError):
            self.store.collect(self.raw + b"x", self.payload)

    def test_preexisting_raw_not_overwritten(self):
        path = self.root / (self.payload["report_sha256"] + ".raw")
        path.write_bytes(b"preserve existing")
        path.chmod(0o600)
        with self.assertRaises(CrashReportError):
            self.store.collect(self.raw, self.payload)
        self.assertEqual(path.read_bytes(), b"preserve existing")

    def test_symlink_raw_and_hardlink_refused(self):
        entry = self.store.collect(self.raw, self.payload)
        raw_path = self.root / entry["raw_file"]
        raw_path.unlink()
        target = self.root / "private-target"
        target.write_bytes(self.raw)
        target.chmod(0o600)
        raw_path.symlink_to(target)
        with self.assertRaises(CrashReportError):
            self.store.pending()
        raw_path.unlink()
        os.link(target, raw_path)
        with self.assertRaises(CrashReportError):
            self.store.pending()
        self.assertEqual(target.read_bytes(), self.raw)

    def test_store_symlink_and_insecure_mode_refused(self):
        link = self.root.parent / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(CrashReportError):
            CrashStore(link)
        self.root.chmod(0o755)
        with self.assertRaises(CrashReportError):
            CrashStore(self.root)

    def test_payload_injection_refused_without_raw_write(self):
        for payload in (dict(self.payload, secret="token_SECRET_CANARY"), dict(self.payload, frames=[{"index": 0, "image_role": "game", "image_uuid": None, "offset": 0, "symbol": "secret"}])):
            with self.assertRaises(CrashReportError):
                self.store.collect(self.raw, payload)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_publication_symlink_is_held_and_never_overwrites_target(self):
        entry = self.store.collect(self.raw, self.payload)
        state = self.root / (entry["fingerprint"] + ".publication")
        state.unlink()
        target = self.root / "unrelated-private"
        target.write_bytes(b"preserve")
        target.chmod(0o600)
        state.symlink_to(target)
        self.assertEqual(self.store.pending()[0]["publication"]["status"], "unknown")
        with self.assertRaises(CrashReportError):
            self.store.mark_publication(entry["fingerprint"], "published")
        self.assertTrue(state.is_symlink())
        self.assertEqual(target.read_bytes(), b"preserve")

    def test_corruption_retained_and_bounded(self):
        entry = self.store.collect(self.raw, self.payload)
        raw_path = self.root / entry["raw_file"]
        raw_path.write_bytes(b"corrupt")
        with self.assertRaises(CrashReportError):
            self.store.pending()
        self.assertEqual(raw_path.read_bytes(), b"corrupt")


if __name__ == "__main__":
    unittest.main()

class LegacyVersionControls(unittest.TestCase):
    def test_new_legacy_payload_has_explicit_unavailable_details(self):
        p=parse(legacy());d=p['diagnostics']
        self.assertEqual(p['schema'],2)
        self.assertEqual(d['format'],'legacy_crash')
        self.assertEqual(d['threads'],dict(source_count=None,retained_count=0,items=[]))
        self.assertEqual(set(d['unavailable']),{'faulting_thread','threads','registers','binary_images','numeric_vm','exception_codes'})
        self.assertEqual(p['frames'][0]['offset'],256)
