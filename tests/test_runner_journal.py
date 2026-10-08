"""Offline durability and scheduling fixtures; no game/GUI/real calibration."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from test_batch_smoke import batch, durable_fixture


class JournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_raw_outcome_survives_restoration_failure_and_exact_recovery(self):
        journal, row = durable_fixture(self.root, restored=False)
        raw = journal.path("raw_outcome").read_bytes()
        journal.append("restoration_failure", dict(error="permission lost", elapsed_seconds=.2))
        (self.root / "results.jsonl").unlink()
        failure = batch.journal_api.result_row(journal)
        batch.append_result(self.root / "results.jsonl", failure)
        journal.append("restored", dict(restored=True, elapsed_seconds=.3))
        recovered = batch.journal_api.result_row(journal)
        batch.append_result(self.root / "results.jsonl", recovered)
        self.assertEqual(raw, journal.path("raw_outcome").read_bytes())
        self.assertEqual(recovered["status"], "loaded")
        self.assertEqual(recovered["restoration_error"], "permission lost")
        self.assertEqual(batch.result_rows(self.root / "results.jsonl"), [recovered])
        self.assertEqual(batch.completed(self.root / "results.jsonl"), set())

    def test_same_raw_incident_duplicate_is_reduced_once(self):
        _, row = durable_fixture(self.root)
        batch.append_result(self.root / "results.jsonl", row)
        self.assertEqual(batch.result_rows(self.root / "results.jsonl"), [row])
        self.assertEqual(batch.completed(self.root / "results.jsonl"), set())

    def test_conflicting_duplicate_incident_is_rejected(self):
        _, row = durable_fixture(self.root)
        batch.append_result(self.root / "results.jsonl", dict(row, status="timeout"))
        with self.assertRaises(batch.JournalError): batch.result_rows(self.root / "results.jsonl")

    def test_interrupted_phase_is_preserved_and_blocks_restore(self):
        run = self.root / "run"; run.mkdir()
        journal = batch.RunJournal(run)
        journal.path("started").write_bytes(b'{"partial":')
        before = journal.path("started").read_bytes()
        with self.assertRaises(batch.JournalError): journal.records()
        with self.assertRaises(batch.JournalError): journal.append("started", {"run_id": "another"})
        self.assertEqual(before, journal.path("started").read_bytes())

    def test_exclusive_phase_and_fsync_order(self):
        run = self.root / "run"; run.mkdir()
        journal = batch.RunJournal(run)
        events = []
        real_fsync = os.fsync
        with patch.object(batch.journal_api.os, "fsync", side_effect=lambda fd: (events.append("file"), real_fsync(fd))), \
                patch.object(batch.journal_api, "_fsync_dir", side_effect=lambda p: events.append("directory")):
            journal.append("started", {"run_id": "fixed"})
        self.assertEqual(events, ["file", "directory"])
        before = journal.path("started").read_bytes()
        with self.assertRaises(batch.JournalError): journal.append("started", {"run_id": "fixed"})
        self.assertEqual(before, journal.path("started").read_bytes())

    def test_fsync_error_is_reported_and_file_is_retained(self):
        run = self.root / "run"; run.mkdir()
        journal = batch.RunJournal(run)
        with patch.object(batch.journal_api.os, "fsync", side_effect=OSError("lost disk")):
            with self.assertRaises(batch.JournalError): journal.append("started", {"run_id": "fixed"})
        self.assertTrue(journal.path("started").exists())

    def test_symlink_and_hardlink_phases_are_rejected(self):
        run = self.root / "run"; run.mkdir()
        target = self.root / "target"; target.write_text("{}")
        journal = batch.RunJournal(run)
        for kind in ("symlink", "hardlink"):
            path = journal.path("started")
            if kind == "symlink": path.symlink_to(target)
            else: os.link(target, path)
            with self.assertRaises(batch.JournalError): journal.read("started")
            path.unlink()

    def test_nonfinite_and_duplicate_json_keys_fail_closed(self):
        run = self.root / "run"; run.mkdir()
        journal = batch.RunJournal(run)
        with self.assertRaises(ValueError): journal.append("started", {"run_id": "fixed", "time": float("nan")})
        journal.path("started").write_text('{"phase":"started","phase":"raw_outcome"}\n')
        with self.assertRaises(batch.JournalError): journal.records()

    def test_phase_chain_and_selected_facts_cannot_be_changed(self):
        journal, _ = durable_fixture(self.root)
        raw = json.loads(journal.path("raw_outcome").read_text())
        raw["payload"]["process"]["birth_us"] = 999
        body = {k:v for k,v in raw.items() if k != "id"}
        raw["id"] = batch.journal_api.digest(body)
        journal.path("raw_outcome").write_bytes(batch.journal_api.canonical(raw) + b"\n")
        with self.assertRaisesRegex(batch.JournalError, "disagree|chain"): journal.records()

    def test_restoration_without_raw_outcome_is_rejected(self):
        run = self.root / "run"; run.mkdir()
        journal = batch.RunJournal(run); journal.append("started", {"run_id": "fixed"})
        with self.assertRaisesRegex(batch.JournalError, "raw outcome"):
            journal.append("restored", {"restored": True})


class QueueTests(unittest.TestCase):
    setUp = JournalTests.setUp
    def test_resume_selects_exact_uncompleted_identity(self):
        _, row = durable_fixture(self.root)
        jobs = [dict(input_identity=key * 64, binding=row["binding"]) for key in ("a", "b")]
        with self.assertRaisesRegex(batch.SafetyError, "Inspection-only"):
            batch.queue_state(self.root, jobs)

    def test_automation_failed_never_completes_even_when_restored(self):
        _, row = durable_fixture(self.root, status="automation_failed")
        self.assertEqual(batch.completed(self.root / "results.jsonl"), set())
        for retry in (False, True):
            with self.assertRaisesRegex(batch.SafetyError, "cause resolution"):
                batch.queue_state(self.root, [dict(input_identity="a" * 64, binding=row["binding"])], retry=retry)

    def test_pending_session_blocks_before_any_runtime_probe(self):
        _, row = durable_fixture(self.root, restored=False)
        with self.assertRaisesRegex(batch.SafetyError, "explicit recovery"):
            batch.queue_state(self.root, [dict(input_identity="a" * 64, binding=row["binding"])])

    def test_orphan_session_and_unindexed_outcome_fail_closed(self):
        session = self.root / "session-orphan"; session.mkdir()
        with self.assertRaises(batch.JournalError): batch.queue_state(self.root, [])
        session.rmdir()
        durable_fixture(self.root)
        (self.root / "results.jsonl").unlink()
        with self.assertRaisesRegex(batch.SafetyError, "Unindexed"): batch.queue_state(self.root, [])

    def test_cause_resolution_and_fresh_exact_synthetic_calibration_gate(self):
        protocol = dict(protocol_id="6" * 64, driver_behavior_id="7" * 64, basis="synthetic authority")
        _, failed = durable_fixture(self.root, status="automation_failed", protocol=protocol)
        _, calibration = durable_fixture(self.root, "b", protocol=protocol, started_at=400, observed_at=500)
        job = dict(input_identity="a" * 64, engine_input_identity=failed["engine_input_identity"], binding=failed["binding"])
        receipt = dict(schema="smoke-retry-authority-v1", failed_raw_outcome_id=failed["raw_outcome_id"],
                       target_input_identity=job["input_identity"],
                       cause_resolution="Synthetic OCR cause repaired and independently checked", resolved_at_us=300,
                       execution_mode="synthetic", calibration_row=calibration)
        authority = {failed["raw_outcome_id"]: receipt}
        self.assertFalse(batch.retry_authorized(failed, job, authority, execution_mode="synthetic"))
        self.assertFalse(batch.retry_authorized(failed, job, authority))
        for changes in ({"resolved_at_us": 100}, {"resolved_at_us": 450}, {"cause_resolution": ""},
                        {"failed_raw_outcome_id": "8" * 64}, {"execution_mode": "runtime"},
                        {"target_input_identity": "9" * 64}):
            with self.subTest(changes=changes):
                self.assertFalse(batch.retry_authorized(failed, job, {failed["raw_outcome_id"]: dict(receipt, **changes)}, execution_mode="synthetic"))
        changed = dict(job, binding=dict(job["binding"], protocol=dict(protocol, protocol_id="9" * 64)))
        self.assertFalse(batch.retry_authorized(failed, changed, authority, execution_mode="synthetic"))
        with self.assertRaises(batch.SafetyError):
            batch.queue_state(self.root, [job], authority=authority, execution_mode="synthetic")
        changed_input = dict(job, engine_input_identity="0" * 64)
        self.assertFalse(batch.retry_authorized(failed, changed_input, authority, execution_mode="synthetic"))

    def test_cleanup_failure_keeps_raw_but_invalidates_baseline(self):
        journal, row = durable_fixture(self.root)
        raw = journal.path("raw_outcome").read_bytes()
        journal.append("cleanup_failure", {"error": "save copy interrupted"})
        with self.assertRaisesRegex(batch.JournalError, "unindexed"):
            batch.completed(self.root / "results.jsonl")
        batch.append_result(self.root / "results.jsonl", batch.journal_api.result_row(journal))
        self.assertEqual(batch.completed(self.root / "results.jsonl"), set())
        self.assertEqual(raw, journal.path("raw_outcome").read_bytes())
        with self.assertRaisesRegex(batch.SafetyError, "explicit recovery/inspection"):
            batch.queue_state(self.root, [dict(input_identity="a" * 64, binding=row["binding"])])

    def retry_control(self, root):
        protocol = dict(protocol_id="6" * 64, driver_behavior_id="7" * 64, basis="synthetic gate only")
        _, failed = durable_fixture(root, status="automation_failed", protocol=protocol)
        journal, clean = durable_fixture(root, "b", protocol=protocol, started_at=400, observed_at=500)
        job = dict(input_identity=failed["input_identity"], engine_input_identity=failed["engine_input_identity"],
                   binding=failed["binding"])
        receipt = dict(schema="smoke-retry-authority-v1", failed_raw_outcome_id=failed["raw_outcome_id"],
                       target_input_identity=job["input_identity"], resolved_at_us=300,
                       cause_resolution="Exact synthetic failed cause resolved", execution_mode="synthetic",
                       calibration_row=clean)
        return journal, clean, failed, job, {failed["raw_outcome_id"]: receipt}

    def test_every_later_failure_preserves_history_without_calibration_authority(self):
        for phase in ("cleanup_failure", "harness_failure", "restoration_failure"):
            with self.subTest(phase=phase):
                root = self.root / phase; root.mkdir()
                journal, clean, failed, job, authority = self.retry_control(root)
                raw = journal.path("raw_outcome").read_bytes()
                self.assertFalse(batch.retry_authorized(failed, job, authority, execution_mode="synthetic"))
                journal.append(phase, dict(error="Later synthetic failure", elapsed_seconds=.2))
                batch.append_result(root / "results.jsonl", batch.journal_api.result_row(journal))
                batch.journal_api.validate_row(clean)  # Retained history stays readable.
                self.assertEqual(journal.path("raw_outcome").read_bytes(), raw)
                self.assertFalse(batch.calibration_matches(clean, job, execution_mode="synthetic"))
                self.assertFalse(batch.retry_authorized(failed, job, authority, execution_mode="synthetic"))
                self.assertEqual(batch.completed(root / "results.jsonl"), set())

    def test_pending_corrupt_unindexed_and_mismatched_calibration_sessions(self):
        for mode in ("pending", "corrupt", "unindexed", "index_mismatch", "checkpoint_mismatch", "torn_terminal",
                     "session_path_mismatch", "current_run_mismatch", "game_mismatch", "restoration_mismatch"):
            with self.subTest(mode=mode):
                root = self.root / mode; root.mkdir()
                journal, clean, failed, job, authority = self.retry_control(root)
                checkpoint = journal.directory.parent / "recovery.json"
                if mode == "pending":
                    value = batch.journal_api.read_json(checkpoint); value["terminal_state"] = "pending"
                    batch._atomic_json(checkpoint, value)
                elif mode == "corrupt": checkpoint.write_bytes(b'{"partial":')
                elif mode == "unindexed": (root / "results.jsonl").unlink()
                elif mode == "index_mismatch":
                    (root / "results.jsonl").write_bytes(batch.journal_api.canonical(dict(clean, status="timeout")) + b"\n")
                elif mode == "checkpoint_mismatch":
                    value = batch.journal_api.read_json(checkpoint); value["terminal_rows"] = {}
                    batch._atomic_json(checkpoint, value)
                elif mode == "torn_terminal": journal.path("terminal_finished").write_bytes(b'{"partial":')
                else:
                    value = batch.journal_api.read_json(checkpoint)
                    key, bad = {"session_path_mismatch": ("session", "/synthetic/foreign/session"),
                                "current_run_mismatch": ("current_run", "0" * 64),
                                "game_mismatch": ("game_sha256", "0" * 64),
                                "restoration_mismatch": ("original_manifest", {"foreign": True})}[mode]
                    value[key] = bad; batch._atomic_json(checkpoint, value)
                self.assertFalse(batch.retry_authorized(failed, job, authority, execution_mode="synthetic"))

    def test_terminal_pending_and_finished_chain_bind_full_disposition(self):
        journal, row = durable_fixture(self.root)
        records = journal.records()
        self.assertEqual(records["terminal_pending"]["previous"], records["started"]["id"])
        self.assertEqual(records["terminal_finished"]["previous"], records["terminal_pending"]["id"])
        self.assertEqual(records["terminal_finished"]["payload"]["disposition_identity"],
                         batch.journal_api.digest(row))
        batch.journal_api.validate_current_terminal(row)
        journal.append("cleanup_failure", {"error": "later failure"})
        batch.journal_api.validate_row(row)
        with self.assertRaisesRegex(batch.JournalError, "Historical"):
            batch.journal_api.validate_current_terminal(row)

    def test_logging_only_authority_keeps_calibration_behavior_but_protocol_change_does_not(self):
        protocol = dict(protocol_id="6" * 64, driver_behavior_id="7" * 64, basis="fixed synthetic protocol")
        _, calibration = durable_fixture(self.root, protocol=protocol)
        binding = dict(calibration["binding"], runner_sha256="8" * 64,
                       driver={"files": {"synthetic-driver": "9" * 64}})
        binding["protocol"] = dict(protocol, basis="Reviewed logging-only revision; same behavior",
                                   runner_sha256=binding["runner_sha256"], driver_receipt=binding["driver"])
        job = dict(binding=binding)
        self.assertFalse(batch.calibration_matches(calibration, job, execution_mode="synthetic"))
        self.assertFalse(batch.calibration_matches(calibration, job))
        for field in ("protocol_id", "driver_behavior_id"):
            changed = dict(binding, protocol=dict(binding["protocol"], **{field: "0" * 64}))
            self.assertFalse(batch.calibration_matches(calibration, dict(binding=changed), execution_mode="synthetic"))
        changed = dict(binding, protocol=dict(binding["protocol"], runner_sha256="0" * 64))
        self.assertFalse(batch.calibration_matches(calibration, dict(binding=changed), execution_mode="synthetic"))

    def test_malformed_result_array_and_blank_line_are_not_ignored(self):
        for data in (b"[]\n", b"\n", b"{}\n", b"null\n"):
            with self.subTest(data=data):
                (self.root / "results.jsonl").write_bytes(data)
                with self.assertRaises(batch.JournalError): batch.result_rows(self.root / "results.jsonl")


if __name__ == "__main__": unittest.main()
