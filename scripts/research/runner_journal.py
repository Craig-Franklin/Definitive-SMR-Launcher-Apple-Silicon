"""Bounded immutable smoke-run facts; this is not an E02 action ledger.

Each phase is an exclusive-created file, fsynced before its parent directory.
A torn file is retained and poisons the run rather than being truncated/replaced.
The accepted E02 safe I/O primitives supply descriptor-anchored plain-file checks.
"""
from pathlib import Path
import os
import time

from smr_launcher.scenario_evidence import (
    EvidenceError, canonical, decode, digest, open_plain, read_bytes, sha, text,
)
from smr_launcher.activation import _assert_no_symlink_ancestor, _fsync_dir
from smr_launcher.live_run_authority import write_file, observed, sync_directory

SCHEMA = "bounded-smoke-journal-v1"
MAX_BYTES = 16 * 1024 * 1024
PHASES = ("started", "terminal_pending", "selected", "raw_outcome", "stopped", "harness_failure", "restoration_failure", "restored", "cleanup_failure", "terminal_finished")
USABLE = {"loaded", "crash_or_exit", "memory_limit", "timeout"}


class JournalError(RuntimeError):
    pass


def _fsync_dir(path):
    return sync_directory(path)


def read_json(path):
    try:
        return decode(read_bytes(Path(path), MAX_BYTES))
    except (EvidenceError, OSError, ValueError) as exc:
        raise JournalError("Unreadable or interrupted evidence: " + str(path)) from exc


def write_once(path, value):
    """Never replace evidence, including a partial previous write."""
    path = Path(path)
    data = canonical(value) + b"\n"
    if len(data) > MAX_BYTES:
        raise JournalError("Journal record exceeds bound")
    try:
        write_file(path, data, exclusive=True, parent_sync=_fsync_dir)
    except (EvidenceError, OSError) as exc:
        raise JournalError("Cannot durably create immutable evidence: " + str(path)) from exc


class RunJournal:
    def __init__(self, directory):
        self.directory = Path(directory)
        _assert_no_symlink_ancestor(self.directory)

    def path(self, phase):
        if phase not in PHASES:
            raise JournalError("Unknown journal phase")
        return self.directory / (phase + ".json")

    def read(self, phase, required=False):
        path = self.path(phase)
        if not path.exists() and not path.is_symlink():
            if required:
                raise JournalError("Missing phase " + phase)
            return None
        value = read_json(path)
        if read_bytes(path, MAX_BYTES) != canonical(value) + b"\n":
            raise JournalError("Noncanonical or interrupted phase bytes")
        try:
            body = {k: value[k] for k in ("schema", "phase", "run_id", "previous", "payload")}
            if (set(value) != set(body) | {"id"} or body["schema"] != SCHEMA
                    or body["phase"] != phase or sha(value["id"]) != digest(body)):
                raise JournalError("Journal phase identity mismatch")
            text(value["run_id"])
            if type(value["payload"]) is not dict:
                raise JournalError("Invalid journal facts")
        except (KeyError, TypeError, EvidenceError) as exc:
            raise JournalError("Invalid journal record") from exc
        return value

    def append(self, phase, payload):
        records = self.records()
        if phase in records:
            raise JournalError("Immutable phase already exists: " + phase)
        if phase == "started":
            if records:
                raise JournalError("Started must be first")
            run_id, previous = payload["run_id"], None
        else:
            started = records.get("started")
            if started is None:
                raise JournalError("Started phase required")
            run_id = started["run_id"]
            if phase == "terminal_pending":
                previous = started["id"]
            elif phase == "terminal_finished":
                pending = records.get("terminal_pending")
                if pending is None or "restored" not in records:
                    raise JournalError("Terminal completion requires pending and restored proof")
                if payload.get("disposition_identity") != digest(result_row(self)):
                    raise JournalError("Terminal completion differs from current disposition")
                previous = pending["id"]
            elif phase == "selected":
                if "raw_outcome" in records:
                    raise JournalError("Cannot select after outcome")
                previous = started["id"]
            elif phase == "raw_outcome":
                previous = records.get("selected", started)["id"]
            else:
                raw = records.get("raw_outcome")
                if raw is None:
                    raise JournalError("Durable raw outcome required before restoration")
                previous = raw["id"]
                if phase == "restored" and payload.get("restored") is not True:
                    raise JournalError("Restored phase requires exact positive proof")
        body = dict(schema=SCHEMA, phase=phase, run_id=run_id, previous=previous, payload=payload)
        value = dict(body, id=digest(body))
        observed("journal." + phase, write_once, self.path(phase), value)
        return value

    def records(self):
        result = {}
        for phase in PHASES:
            value = self.read(phase)
            if value is not None:
                result[phase] = value
        if not result:
            return result
        started = result.get("started")
        if started is None:
            raise JournalError("Orphan journal phases")
        for phase, record in result.items():
            if record["run_id"] != started["run_id"]:
                raise JournalError("Mixed run identities")
            if phase == "started":
                previous = None
            elif phase == "terminal_pending":
                previous = started["id"]
            elif phase == "terminal_finished":
                if "terminal_pending" not in result or "restored" not in result:
                    raise JournalError("Orphan terminal completion")
                previous = result["terminal_pending"]["id"]
            elif phase == "selected":
                previous = started["id"]
            elif phase == "raw_outcome":
                previous = result.get("selected", started)["id"]
            else:
                if "raw_outcome" not in result:
                    raise JournalError("Restoration without durable outcome")
                previous = result["raw_outcome"]["id"]
            if record["previous"] != previous:
                raise JournalError("Broken phase chain")
        if "raw_outcome" in result:
            raw = result["raw_outcome"]["payload"]
            selected = result.get("selected", {}).get("payload")
            if (raw.get("process") != (selected or {}).get("process")
                    or any(raw.get(k) != (selected or {}).get(k) for k in
                           ("observed_inputs", "observed_engine_inputs", "observed_input_identity"))):
                raise JournalError("Raw facts disagree with selected process/inputs")
            if raw.get("status") not in USABLE | {"automation_failed", "safety_stop"}:
                raise JournalError("Unknown raw outcome")
            if raw["status"] in USABLE and selected is None:
                raise JournalError("Usable smoke outcome requires an exact selected process")
            if not isinstance(raw.get("cause"), str) or not raw["cause"]:
                raise JournalError("Raw outcome cause required")
        if "restored" in result and result["restored"]["payload"].get("restored") is not True:
            raise JournalError("Invalid restored proof")
        if "selected" in result:
            selected = result["selected"]["payload"]
            try:
                process = selected["process"]
                if (set(process) != {"pid", "birth_us", "executable"}
                        or any(type(process[k]) is not int or process[k] <= 0 for k in ("pid", "birth_us"))
                        or not Path(process["executable"]).is_absolute()
                        or type(selected["selected_at_us"]) is not int or selected["selected_at_us"] <= 0
                        or type(selected["observed_engine_inputs"]) is not dict
                        or selected["observed_input_identity"] != digest(selected["observed_engine_inputs"])):
                    raise JournalError("Incomplete exact selected process/input facts")
            except (KeyError, TypeError) as exc:
                raise JournalError("Incomplete selected process/input facts") from exc
        return result

    def outcome(self, result, *, cause=None):
        records = self.records()
        selected = records.get("selected", {}).get("payload", {})
        payload = dict(result)
        payload.update(cause=cause or result.get("cause") or result["status"],
            process=selected.get("process"), observed_inputs=selected.get("observed_inputs"),
            observed_input_identity=selected.get("observed_input_identity"),
            observed_engine_inputs=selected.get("observed_engine_inputs"),
            observed_at_us=time.time_ns() // 1000, observation_credit="unbound_smoke_only")
        return self.append("raw_outcome", payload)


def result_row(journal, *, include_restored=True, include_cleanup=True, include_harness=True,
               include_failure=True):
    """A historical projection; files never confer live invocation authority."""
    records = journal.records()
    started = records["started"]["payload"]
    raw = records["raw_outcome"]
    restored = records.get("restored") if include_restored else None
    failure = records.get("restoration_failure") if include_failure else None
    harness = records.get("harness_failure") if include_harness else None
    cleanup = records.get("cleanup_failure") if include_cleanup else None
    return dict(schema=2, **{k: started[k] for k in (
        "input_identity", "engine_input_identity", "binding", "name", "scenario", "run_directory")},
        run_id=records["started"]["run_id"], raw_outcome_id=raw["id"],
        raw_outcome=raw["payload"], status=raw["payload"]["status"],
        restored=restored is not None, restoration_id=restored["id"] if restored else None,
        restoration_error=failure["payload"]["error"] if failure else None,
        harness_error=harness["payload"]["error"] if harness else None,
        cleanup_error=cleanup["payload"]["error"] if cleanup else None,
        cleanup_id=cleanup["id"] if cleanup else None,
        elapsed_seconds=(restored or failure or raw)["payload"].get("elapsed_seconds"))


def validate_row(row):
    try:
        if type(row) is not dict:
            raise JournalError("Result index must contain objects")
        if row.get("schema") != 2:
            raise JournalError("Historical smoke receipts require explicit reconciliation")
        journal = RunJournal(row["run_directory"])
        actual = result_row(journal, include_restored=row.get("restored") is True,
                            include_cleanup=row.get("cleanup_id") is not None,
                            include_harness=row.get("harness_error") is not None,
                            include_failure=row.get("restoration_error") is not None)
        if actual != row:
            raise JournalError("Result index differs from immutable phases")
        return journal
    except (KeyError, TypeError) as exc:
        raise JournalError("Incomplete result index") from exc


def validate_current_terminal(row):
    """Validate file integrity for inspection, without granting any purpose."""
    journal = validate_row(row)
    if row != result_row(journal):
        raise JournalError("Historical disposition cannot confer current authority")
    records = journal.records()
    finished = records.get("terminal_finished")
    if (finished is None
            or finished["payload"].get("disposition_identity") != digest(row)):
        raise JournalError("Unfinished or stale terminal disposition")
    return journal


def reduce_rows(rows):
    """Repeated index receipts of one raw incident never count twice."""
    incidents = {}
    for row in rows:
        validate_row(row)
        key = row["raw_outcome_id"]
        if key in incidents and incidents[key] != row:
            # Explicit recovery may append a later restoration receipt for the
            # same raw incident. It changes disposition, never the raw facts.
            old = incidents[key]
            lifecycle = {"restored", "restoration_id", "elapsed_seconds", "cleanup_id", "cleanup_error",
                         "harness_error", "restoration_error"}
            if {k:v for k,v in old.items() if k not in lifecycle} != {k:v for k,v in row.items() if k not in lifecycle}:
                raise JournalError("Conflicting duplicate raw incident")
        # Pick only a receipt for the current immutable disposition. Older
        # lifecycle receipts remain valid history and cannot mask later errors.
        current = result_row(RunJournal(row["run_directory"]))
        if row == current or key not in incidents: incidents[key] = row
    for row in incidents.values():
        if row != result_row(RunJournal(row["run_directory"])):
            raise JournalError("Latest durable disposition is unindexed; explicit reconciliation required")
    return list(incidents.values())
