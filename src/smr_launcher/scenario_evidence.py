"""Bounded private immutable scenario facts. No engine or discovery operations.

Raw facts, protocols, applicability and interpretations are separate records.
The ledger order selects the latest interpretation; it never edits an observation.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import fcntl
import hashlib
import json
import os
import stat
import uuid
import math
import re

from .activation import _assert_no_symlink_ancestor, _fsync_dir
from .resource_identity import (SCHEMA_VERSION as RESOURCE_SCHEMA, ALGORITHM as RESOURCE_ALGORITHM,
                                _open_directory_path, _verify_directory_path, ResourceFingerprintError)

LEGACY_SCHEMA = "scenario-action-evidence-v1"
SCHEMA = "scenario-action-evidence-v2"
CONTEXT_SCHEMA = "scenario-context-v2"
OPTIONS_SCHEMA = "scenario-options-v1"
PROVIDER_ROLES = ("custom_assets", "installed_assets", "user_maps")
MAX_RECORD_BYTES = 1024 * 1024
MAX_STORE_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 10000
MAX_RECEIPT_BYTES = 16 * 1024 * 1024
FEATURES = ("industries", "production", "transport", "geometry", "materials",
            "animations", "placements", "terrain", "goals", "events", "eras", "difficulty")
REQUIRED_ACTIONS = ("load", "camera", "track_construction", "station_construction",
                    "train_purchase_routing", "cargo_transport", "bridge_construction",
                    "annex_construction", "tunnel_construction",
                    "industry_construction_production", "goals_events", "era_transition",
                    "difficulty", "ai_competition", "autosave", "manual_save", "reload",
                    "continue_after_reload", "extended_play")
OUTCOMES = ("pass", "failure", "partial", "unknown", "historical_limited")


class EvidenceError(ValueError):
    """Evidence is incomplete, corrupt, unsafe or inconsistent."""


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise EvidenceError("Evidence must contain finite JSON values") from exc


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise EvidenceError("Exact SHA-256 identity required")
    return value


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise EvidenceError("Nonempty bounded assertion required")
    return value


def relative(value):
    text(value)
    p = PurePosixPath(value)
    if p.is_absolute() or str(p) != value or any(x in (".", "..") for x in p.parts) or "\\" in value or "\x00" in value:
        raise EvidenceError("Unsafe relative evidence path")
    return value


def safe_path(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise EvidenceError("Evidence path must be absolute and plain")
    try:
        _assert_no_symlink_ancestor(path)
        if path.exists() and (not stat.S_ISREG(path.stat().st_mode) or path.stat().st_nlink != 1):
            raise EvidenceError("Evidence must be a plain unlinked regular file")
    except RuntimeError as exc:
        raise EvidenceError("Unsafe evidence path") from exc
    return path


def decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvidenceError("Duplicate JSON field")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


@contextmanager
def parent_handle(path):
    """Anchor file operations to a descriptor opened without following ancestors."""
    try:
        fd, identity = _open_directory_path(Path(path).parent, "evidence")
        try:
            yield fd
            _verify_directory_path(Path(path).parent, "evidence", identity)
        finally:
            os.close(fd)
    except ResourceFingerprintError as exc:
        raise EvidenceError("Evidence parent changed or is unsafe") from exc


def open_plain(path, flags, mode=0o600):
    safe_path(path)
    fd = None
    try:
        with parent_handle(path) as parent_fd:
            fd = os.open(path.name, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), mode, dir_fd=parent_fd)
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise EvidenceError("Evidence must be a plain regular file")
        return fd
    except Exception:
        if fd is not None:
            os.close(fd)
        raise


def read_bytes(path, limit):
    path = safe_path(path)
    try:
        fd = open_plain(path, os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise EvidenceError("Evidence file exceeds bounds or is not regular")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            current = path.stat()
            if len(data) > limit or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino):
                raise EvidenceError("Evidence changed while reading")
            safe_path(path)
            return data
    except OSError as exc:
        raise EvidenceError("Evidence is missing or unreadable") from exc


def receipt(root, name):
    relative(name)
    data = read_bytes(Path(root) / name, MAX_RECEIPT_BYTES)
    return {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


@dataclass(frozen=True)
class EvidenceRecord:
    """Canonical bytes prevent caller mutation of nested facts."""
    encoded: bytes

    @classmethod
    def create(cls, kind, payload, refs=(), claimed_id=None):
        body = {"schema": SCHEMA, "kind": kind, "payload": payload, "refs": list(refs)}
        identity = digest(body)
        if claimed_id is not None and sha(claimed_id) != identity:
            raise EvidenceError("Claimed record identity conflicts with facts")
        result = cls(canonical(dict(body, id=identity)))
        result.validate()
        return result

    @property
    def value(self):
        return decode(self.encoded)

    @property
    def id(self):
        return self.value["id"]

    @property
    def observed_input_identity(self):
        if self.kind != "observation":
            raise EvidenceError("Only observations have observed input identities")
        p = self.payload
        return digest({k:p[k] for k in ("scenario", "build", "contract", "action", "protocol", "actual_context")})

    @property
    def kind(self):
        return self.value["kind"]

    @property
    def payload(self):
        return self.value["payload"]

    def validate(self):
        if len(self.encoded) > MAX_RECORD_BYTES:
            raise EvidenceError("Record bound exceeded")
        try:
            v = self.value
            if self.encoded != canonical(v):
                raise EvidenceError("Immutable records require canonical bytes")
            if set(v) != {"id", "schema", "kind", "payload", "refs"} or v["schema"] not in (SCHEMA, LEGACY_SCHEMA):
                raise EvidenceError("Unexpected evidence schema")
            if sha(v["id"]) != digest({k: v[k] for k in v if k != "id"}):
                raise EvidenceError("Record identity mismatch")
            if not isinstance(v["refs"], list) or len(v["refs"]) > 1000 or len(set(v["refs"])) != len(v["refs"]):
                raise EvidenceError("Invalid record references")
            for ref in v["refs"]:
                sha(ref)
            p = v["payload"]
            if not isinstance(p, dict):
                raise EvidenceError("Invalid facts")
            validate_payload(v["kind"], p, legacy=v["schema"] == LEGACY_SCHEMA)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
            raise EvidenceError("Invalid immutable evidence record") from exc


def scenario_key(archive, edition, path, scenario_sha256):
    return {"archive_sha256": sha(archive), "edition_id": sha(edition),
            "scenario_path": relative(path), "scenario_sha256": sha(scenario_sha256)}


def validate_scenario(s):
    if not isinstance(s, dict) or s != scenario_key(s["archive_sha256"], s["edition_id"], s["scenario_path"], s["scenario_sha256"]):
        raise EvidenceError("Incomplete exact scenario identity")


def _known_option_value(value, depth=0):
    """No normalization: JSON bool/int/float and nested types stay distinct."""
    if depth > 20:
        raise EvidenceError("Options nesting bound exceeded")
    if type(value) in (bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is str and value.strip() and len(value) <= 4000 and value.casefold() not in ("unknown", "unresolved", "unattested"):
        return
    if type(value) is dict and value and len(value) <= 200:
        for key, item in value.items():
            text(key); _known_option_value(item, depth + 1)
        return
    if type(value) is list and value and len(value) <= 200:
        for item in value:
            _known_option_value(item, depth + 1)
        return
    raise EvidenceError("Known options require resolved finite typed values")


def attest_options(values, *, required_fields, contract_version, basis):
    """Explicit caller attestation, not a collector or inference from equality.

    The caller owns the reviewed option-field contract; real field collection is
    future work. Every declared field must be present/resolved. Raw dictionaries
    are deliberately untrusted. Synthetic controls may explicitly attest theirs.
    """
    result = {"schema": OPTIONS_SCHEMA, "state": "known", "values": values,
              "required_fields": sorted(required_fields), "contract_version": contract_version,
              "basis": basis}
    validate_options(result)
    return decode(canonical(result))


def validate_options(value):
    if type(value) is not dict or set(value) != {"schema", "state", "values", "required_fields", "contract_version", "basis"} or value["schema"] != OPTIONS_SCHEMA:
        raise EvidenceError("Versioned option attestation required")
    if value["state"] not in ("known", "unknown") or type(value["values"]) is not dict:
        raise EvidenceError("Unknown options attestation state")
    text(value["basis"])
    fields = value["required_fields"]
    if type(fields) is not list or len(fields) > 200 or any(type(k) is not str for k in fields) or fields != sorted(set(fields)):
        raise EvidenceError("Explicit unique bounded option fields required")
    if value["state"] == "known":
        text(value["contract_version"])
        if not fields or set(fields) != set(value["values"]):
            raise EvidenceError("Complete option contract fields required")
        _known_option_value(value["values"])
    elif fields or value["contract_version"]:
        raise EvidenceError("Unknown options cannot claim reviewed field completeness")
    if len(canonical(value)) > 65536:
        raise EvidenceError("Option attestation bound exceeded")


def option_attestation(value=None):
    if type(value) is dict and value.get("schema") == OPTIONS_SCHEMA:
        validate_options(value)
        return decode(canonical(value))
    if value is not None and type(value) is not dict:
        raise EvidenceError("Options must be an attestation or unresolved dictionary")
    return {"schema": OPTIONS_SCHEMA, "state": "unknown", "values": value or {},
            "required_fields": [], "contract_version": "", "basis": "No exact option attestation supplied"}


def engine_context(game_sha256, prepared_output_sha256, resources, options=None):
    result = {"schema": CONTEXT_SCHEMA, "game_sha256": game_sha256,
              "prepared_output_sha256": prepared_output_sha256,
              "resources_identity": resources["identity"],
              "provider_roles": [r["role"] for r in resources["roots"]],
              "options": option_attestation(options)}
    validate_context(result)
    return result


def validate_context(p, *, legacy=False):
    sha(p["game_sha256"]); sha(p["resources_identity"]); sha(p["prepared_output_sha256"])
    if legacy:
        if not isinstance(p["options"], dict):
            raise EvidenceError("Legacy exact options object required")
        return
    if set(p) != {"schema", "game_sha256", "resources_identity", "prepared_output_sha256", "provider_roles", "options"} or p["schema"] != CONTEXT_SCHEMA:
        raise EvidenceError("Versioned exact context required")
    if p["provider_roles"] != list(PROVIDER_ROLES):
        raise EvidenceError("All three actual resource providers are required")
    validate_options(p["options"])


def context_identity(context, *, require_complete=True):
    """Invalid/legacy/incomplete contexts are unverifiable, never equal passes."""
    try:
        validate_context(context)
        if require_complete and context["options"]["state"] != "known":
            return None
        return digest(context)
    except (EvidenceError, KeyError, TypeError, AttributeError):
        return None


def validate_payload(kind, p, *, legacy=False):
    if kind == "inventory":
        sha(p["sha256"]); text(p["schema"])
        if not isinstance(p["scenarios"], list) or len(p["scenarios"]) > 2000:
            raise EvidenceError("Inventory bound exceeded")
        for s in p["scenarios"]:
            validate_scenario(s)
        for e in p.get("unbound_editions", []):
            sha(e["archive_sha256"]); sha(e["edition_id"]); text(e["edition_kind"])
            for name in e["scenario_paths"]:
                relative(name)
    elif kind == "build":
        sha(p["inventory"]); sha(p["archive_sha256"]); sha(p["edition_id"])
        validate_context(p["context"], legacy=legacy); sha(p["prepared_output_sha256"])
        if p["context"]["prepared_output_sha256"] != p["prepared_output_sha256"]:
            raise EvidenceError("Prepared output context mismatch")
        r = p["resource_receipt"]
        if r["identity"] != p["context"]["resources_identity"] or r["identity"] != digest({k:r[k] for k in ("schema", "algorithm", "roots")}):
            raise EvidenceError("Resource receipt identity mismatch")
        if type(r["schema"]) is not int or r["schema"] != RESOURCE_SCHEMA or r["algorithm"] != RESOURCE_ALGORITHM:
            raise EvidenceError("Unsupported resource receipt schema")
        roles = [x["role"] for x in r["roots"]]
        if not roles or roles != sorted(set(roles)) or set(roles) - {"installed_assets", "custom_assets", "user_maps"}:
            raise EvidenceError("Complete unique provider resource receipt required")
        if not legacy and roles != list(PROVIDER_ROLES):
            raise EvidenceError("Omitted provider is unknown, not absent")
        if not legacy and roles != p["context"]["provider_roles"]:
            raise EvidenceError("Provider context differs from receipt")
        for root in r["roots"]:
            entries = root["entries"]
            if not isinstance(entries, list) or not entries or len(entries) > 10000:
                raise EvidenceError("Bounded complete resource entries required")
            paths = []
            for entry in entries:
                name = entry["path"]
                if name != ".":
                    relative(name)
                paths.append(name)
                if type(entry["mtime_ns"]) is not int or entry["mtime_ns"] < 0:
                    raise EvidenceError("Exact resource timestamps required")
                if entry["kind"] == "file":
                    sha(entry["sha256"])
                    if type(entry["size"]) is not int or entry["size"] < 0:
                        raise EvidenceError("Exact resource sizes required")
                elif entry["kind"] != "directory":
                    raise EvidenceError("Unknown resource provider entry")
            if len(set(paths)) != len(paths) or entries[0]["path"] != "." or entries[0]["kind"] != "directory":
                raise EvidenceError("Incomplete or duplicate provider entries")
    elif kind == "contract":
        validate_scenario(p["scenario"]); sha(p["build"]); text(p["version"])
        if set(p["actions"]) != set(REQUIRED_ACTIONS) or set(p["features"]) != set(FEATURES):
            raise EvidenceError("Full versioned action/feature contract required")
        for a in p["actions"].values():
            if a["applicability"] not in ("applicable", "not_applicable", "unknown"):
                raise EvidenceError("Unknown applicability disposition")
            text(a["basis"])
            for k in ("setup", "trigger", "expected", "expectation_basis"):
                text(a["protocol"][k])
        cargo = p["actions"]["cargo_transport"]["protocol"].get("required_assertions", {})
        if set(cargo) != {"goods", "car_attachment", "delivery"}:
            raise EvidenceError("Cargo requires distinct goods, car attachment and delivery assertions")
        for assertion in cargo.values():
            text(assertion)
        for name, f in p["features"].items():
            if not isinstance(f["actions"], list) or not f["actions"] or len(set(f["actions"])) != len(f["actions"]) or set(f["actions"]) - set(REQUIRED_ACTIONS):
                raise EvidenceError("Feature-specific action coverage must be explicit")
            for action in f["actions"]:
                if p["actions"][action]["protocol"].get("feature_assertions", {}).get(name) != f["expected_loaded"]:
                    raise EvidenceError("Feature coverage needs an independently fixed loaded assertion")
            if f["state"] not in ("reviewed", "unknown"):
                raise EvidenceError("Unresolved feature expectation")
            for k in ("authored_intent", "expected_loaded", "basis"):
                text(f[k])
    elif kind == "observation":
        validate_scenario(p["scenario"]); sha(p["build"]); sha(p["contract"])
        validate_context(p["actual_context"], legacy=legacy)
        if p["action"] not in REQUIRED_ACTIONS or p["outcome"] not in OUTCOMES:
            raise EvidenceError("Unknown action or outcome")
        if not isinstance(p["actual_assertions"], dict):
            raise EvidenceError("Actual assertions required")
        for k in ("setup", "trigger", "expected", "expectation_basis"):
            text(p["protocol"][k])
        text(p["observed_at"])
        from datetime import datetime
        if datetime.fromisoformat(p["observed_at"]).tzinfo is None:
            raise EvidenceError("Observation requires a dated time zone")
        if not isinstance(p["evidence"], list) or len(p["evidence"]) > 100:
            raise EvidenceError("Bounded evidence references required")
        if p["outcome"] == "pass" and not p["evidence"]:
            raise EvidenceError("Pass needs readable evidence")
        for r in p["evidence"]:
            relative(r["path"]); sha(r["sha256"])
            if type(r["bytes"]) is not int or not 0 <= r["bytes"] <= MAX_RECEIPT_BYTES:
                raise EvidenceError("Evidence size bound exceeded")
    elif kind == "interpretation":
        sha(p["observation"]); text(p["analysis_version"]); text(p["contract_version"])
        if p["outcome"] not in OUTCOMES:
            raise EvidenceError("Unknown interpretation outcome")
        text(p["reason"])
    elif kind == "finding":
        validate_scenario(p["scenario"]); sha(p["build"]); text(p["analyzer_version"]); text(p["finding"])
    elif kind == "provenance":
        sha(p["observation"]); sha(p["driver_sha256"]); text(p["driver_version"])
    elif kind == "report_target":
        relative(p["path"]); relative(p["ledger_name"])
        if "/" in p["path"] or "/" in p["ledger_name"]:
            raise EvidenceError("Report ownership must be a plain ledger sibling")
    elif kind == "report_publication":
        relative(p["path"]); sha(p["target"]); sha(p["sha256"])
    elif kind == "legacy":
        from .verification import GameplayVerification
        GameplayVerification.from_json(p["record"])
        if p["scope"] != "unscoped_historical_limited":
            raise EvidenceError("Legacy facts must remain unscoped")
    else:
        raise EvidenceError("Unknown evidence kind")


def bind_inventory(path, expected_sha256):
    """Consume only immutable identity rows; never open referenced private sources."""
    data = read_bytes(path, MAX_RECEIPT_BYTES)
    if hashlib.sha256(data).hexdigest() != sha(expected_sha256):
        raise EvidenceError("Accepted inventory identity changed")
    try:
        v = decode(data)
        if v["schema"] != "smr-e01-inventory-v1":
            raise EvidenceError("Unexpected inventory schema")
        scenarios, unbound = [], []
        if not isinstance(v["editions"], list) or len(v["editions"]) > 2000:
            raise EvidenceError("Bounded inventory editions required")
        for edition in v["editions"]:
            if "scenarios" not in edition:
                if edition["edition_kind"] not in ("prepared", "unclassified"):
                    raise EvidenceError("Original edition lacks exact scenario identity rows")
                unbound.append(dict(archive_sha256=edition["archive_sha256"], edition_id=edition["edition_id"],
                                    edition_kind=edition["edition_kind"], scenario_paths=edition["scenario_relative_paths"]))
                continue
            for s in edition["scenarios"]:
                scenarios.append(scenario_key(edition["archive_sha256"], edition["edition_id"],
                                              s["scenario_relative_path"], s["scenario_sha256"]))
        return EvidenceRecord.create("inventory", {"sha256": expected_sha256,
            "schema": v["schema"], "scenarios": sorted(scenarios, key=canonical),
            "unbound_editions": sorted(unbound, key=canonical)})
    except (KeyError, ValueError, TypeError) as exc:
        raise EvidenceError("Inventory identity rows need inspection") from exc


def build_record(inventory, archive, edition, prepared_output_sha256, resources, game_sha256, options):
    # Locations and compiler/logging provenance do not select engine input bytes.
    r = {k:resources[k] for k in ("schema", "algorithm", "identity")}
    r["roots"] = [{"role":x["role"], "entries":x["entries"]} for x in resources["roots"]]
    return EvidenceRecord.create("build", {"inventory":inventory.id, "archive_sha256":archive,
        "edition_id":edition, "prepared_output_sha256":prepared_output_sha256,
        "resource_receipt":r, "context":engine_context(game_sha256, prepared_output_sha256, resources, options)}, (inventory.id,))


class EvidenceStore:
    """Append-only JSONL with a commit marker on every transaction.

    A partial/interrupted transaction leaves an invalid suffix. Reads and later
    appends refuse it, including otherwise complete records before the suffix.
    """
    def __init__(self, path, *, max_bytes=MAX_STORE_BYTES, max_records=MAX_RECORDS):
        self.path = safe_path(path)
        self.root = self.path.parent
        self.max_bytes = min(max_bytes, MAX_STORE_BYTES)
        self.max_records = min(max_records, MAX_RECORDS)
        if self.max_bytes <= 0 or self.max_records <= 0:
            raise EvidenceError("Positive store bounds required")

    @contextmanager
    def _writer(self):
        safe_path(self.path)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = safe_path(self.path.with_name(self.path.name + ".lock"))
        fd = open_plain(lock, os.O_CREAT | os.O_RDWR)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
                raise EvidenceError("Unsafe writer guard")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise EvidenceError("Evidence writer already active") from exc
            yield
        finally:
            os.close(fd)

    def _validate_references(self, record, existing):
        v, p = record.value, record.payload
        for ref in v["refs"]:
            if ref not in existing:
                raise EvidenceError("Missing referenced record")
        def require(name, kind):
            ref = p[name]
            if ref not in v["refs"] or ref not in existing or existing[ref].kind != kind:
                raise EvidenceError("Missing or wrong typed record reference")
            return existing[ref].payload
        if record.kind == "build":
            inv = require("inventory", "inventory")
            if not any(s["archive_sha256"] == p["archive_sha256"] and s["edition_id"] == p["edition_id"] for s in inv["scenarios"]):
                raise EvidenceError("Build edition absent from accepted inventory")
        if record.kind in ("contract", "observation", "finding"):
            b = require("build", "build")
            inv = existing[b["inventory"]].payload
            if p["scenario"] not in inv["scenarios"] or (p["scenario"]["archive_sha256"],p["scenario"]["edition_id"]) != (b["archive_sha256"],b["edition_id"]):
                raise EvidenceError("Scenario absent from exact inventory/build")
        if record.kind == "observation":
            c = require("contract", "contract")
            if c["build"] != p["build"] or c["scenario"] != p["scenario"] or canonical(c["actions"][p["action"]]["protocol"]) != canonical(p["protocol"]):
                raise EvidenceError("Observation differs from independently fixed protocol")
            targets = {r.payload["path"] for r in existing.values() if r.kind == "report_target"}
            if record.value["schema"] == SCHEMA and any(e["path"] in targets or ".report-" in e["path"] for e in p["evidence"]):
                raise EvidenceError("Mutable report snapshots cannot be raw evidence")
            if p["outcome"] != "historical_limited":
                for r in p["evidence"]:
                    if receipt(self.root, r["path"]) != r:
                        raise EvidenceError("Referenced evidence integrity changed")
        if record.kind == "report_target":
            if p["ledger_name"] != self.path.name or not self._is_report_path(self.root / p["path"]):
                raise EvidenceError("Report target belongs to another ledger")
            if any(p["path"] == evidence["path"] for r in existing.values() if r.kind == "observation" for evidence in r.payload["evidence"]):
                raise EvidenceError("Referenced evidence cannot become a report target")
        if record.kind == "report_publication":
            target = require("target", "report_target")
            if p["path"] != target["path"]:
                raise EvidenceError("Report publication target mismatch")
        if record.kind in ("interpretation", "provenance"):
            o = require("observation", "observation")
            if record.kind == "interpretation":
                c = existing[o["contract"]].payload
                if c["version"] != p["contract_version"]:
                    raise EvidenceError("Interpretation contract version mismatch")

    def records(self):
        safe_path(self.path)
        if not self.path.exists():
            return ()
        data = read_bytes(self.path, self.max_bytes)
        if data and not data.endswith(b"\n"):
            raise EvidenceError("Truncated evidence suffix")
        existing, pending = {}, []
        try:
            for line in data.splitlines():
                if len(line) > MAX_RECORD_BYTES:
                    raise EvidenceError("Record bound exceeded")
                v = decode(line)
                if v.get("commit") is not None:
                    if set(v) != {"commit", "count"} or type(v["count"]) is not int or v["count"] != len(pending) or not pending or v["commit"] != digest([r.id for r in pending]):
                        raise EvidenceError("Invalid transaction commit")
                    for r in pending:
                        if r.id in existing:
                            raise EvidenceError("Duplicate ledger record")
                        self._validate_references(r, existing)
                        existing[r.id] = r
                        if len(existing) > self.max_records:
                            raise EvidenceError("Store entry bound exceeded")
                    pending = []
                else:
                    r = EvidenceRecord(canonical(v)); r.validate(); pending.append(r)
            if pending:
                raise EvidenceError("Interrupted uncommitted evidence suffix")
            return tuple(existing.values())
        except (ValueError, TypeError, AttributeError, RecursionError) as exc:
            raise EvidenceError("Evidence ledger needs inspection") from exc

    def _append_locked(self, record, existing):
        record.validate()
        if record.id in existing:
            if existing[record.id] != record:
                raise EvidenceError("Conflicting immutable identity")
            return record.id
        self._validate_references(record, existing)
        if len(existing) >= self.max_records:
            raise EvidenceError("Store entry bound exceeded")
        data = record.encoded + b"\n" + canonical({"commit":digest([record.id]), "count":1}) + b"\n"
        if (self.path.stat().st_size if self.path.exists() else 0) + len(data) > self.max_bytes:
            raise EvidenceError("Store byte bound exceeded")
        safe_path(self.path)
        fd = open_plain(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            if os.write(fd, data) != len(data):
                raise EvidenceError("Interrupted evidence append")
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(self.root)
        existing[record.id] = record
        return record.id

    def append(self, record):
        if record.kind in ("report_target", "report_publication"):
            raise EvidenceError("Report ownership/publication is managed only by snapshot")
        record.validate()
        with self._writer():
            return self._append_locked(record, {r.id:r for r in self.records()})

    def report_path(self, name):
        """A report namespace owned by this ledger, never an arbitrary sibling."""
        if type(name) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", name):
            raise EvidenceError("Bounded plain report name required")
        return self.root / (self.path.name + ".report-" + name + ".json")

    def _is_report_path(self, path):
        prefix = self.path.name + ".report-"
        if path.parent != self.root or not path.name.startswith(prefix) or not path.name.endswith(".json"):
            return False
        name = path.name[len(prefix):-5]
        return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", name))

    def snapshot(self, path, report):
        with self._writer():
            records = self.records()
            existing = {r.id:r for r in records}
            path = safe_path(path)
            if not self._is_report_path(path):
                raise EvidenceError("Snapshot target must belong to this ledger's report namespace")
            if any(path.name == e["path"] for r in records if r.kind == "observation" for e in r.payload["evidence"]):
                raise EvidenceError("Referenced immutable evidence cannot be replaced")
            target = next((r for r in records if r.kind == "report_target" and r.payload["path"] == path.name), None)
            prior = [r for r in records if r.kind == "report_publication" and r.payload["path"] == path.name]
            before = None
            if path.exists():
                if target is None or not prior:
                    raise EvidenceError("Existing artifact has no supported report ownership/publication")
                before = read_bytes(path, MAX_RECORD_BYTES)
                if hashlib.sha256(before).hexdigest() != prior[-1].payload["sha256"]:
                    raise EvidenceError("Owned report bytes differ from the committed publication")
                document = decode(before)
                if document.get("owner") != target.id or document.get("ledger_name") != self.path.name or document.get("schema") != SCHEMA:
                    raise EvidenceError("Existing report ownership is invalid")
            elif prior:
                raise EvidenceError("Published report is missing; explicit recovery required")
            if target is None:
                target = EvidenceRecord.create("report_target", {"path":path.name, "ledger_name":self.path.name})
            result = {"schema":SCHEMA, "owner":target.id, "ledger_name":self.path.name,
                      "ledger_identity":digest([r.id for r in records]), "report":report}
            encoded = canonical(result) + b"\n"
            if len(encoded) > MAX_RECORD_BYTES:
                raise EvidenceError("Snapshot bound exceeded")
            identity = hashlib.sha256(encoded).hexdigest()
            publication = EvidenceRecord.create("report_publication", {"target":target.id, "path":path.name, "sha256":identity}, (target.id,))
            additions = [r for r in (target, publication) if r.id not in existing]
            append_bytes = sum(len(r.encoded) + 1 + len(canonical({"commit":digest([r.id]), "count":1})) + 1 for r in additions)
            if len(existing) + len(additions) > self.max_records or (self.path.stat().st_size if self.path.exists() else 0) + append_bytes > self.max_bytes:
                raise EvidenceError("Snapshot publication would exceed store bounds")
            self._append_locked(target, existing)
            temporary = ".snapshot-" + uuid.uuid4().hex
            with parent_handle(path) as parent_fd:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                    safe_path(path)
                    # Cooperating writers are serialized. Detect changed/appeared
                    # artifacts before replacing even an owned report target.
                    if before is None:
                        if path.exists():
                            raise EvidenceError("Artifact appeared at reserved report target")
                        os.link(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
                        os.unlink(temporary, dir_fd=parent_fd)
                    else:
                        if read_bytes(path, MAX_RECORD_BYTES) != before:
                            raise EvidenceError("Owned report changed before publication")
                        os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    os.fsync(parent_fd)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass
            # An interruption here leaves an unreceipted report which cannot be
            # silently adopted or replaced. It requires explicit recovery.
            self._append_locked(publication, existing)
            return identity
