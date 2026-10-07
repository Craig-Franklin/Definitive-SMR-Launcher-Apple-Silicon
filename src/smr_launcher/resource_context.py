"""Bounded offline receipts for ordinary resources; never runtime selection proof.

Capture all three roles with ``capture_resources`` (one secure sequential walk),
then call ``resource_context`` repeatedly with explicitly requested names. Valid
supplied snapshots are historical receipts, not proof of current disk contents.
Recapture when freshness is required. Absolute provider paths bind this context,
unlike resource_identity's portable digest. No packed reader or Unix->FILETIME
conversion is implemented. All outputs can be serialized directly as JSON.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from . import resource_identity as fingerprints

SCHEMA = 1
POLICY_VERSION = "ordinary-basename-filetime-v1"
EXPECTED_ROLES = ("installed_assets", "custom_assets", "user_maps")
ROOT_NAMES = dict(zip(EXPECTED_ROLES, ("assets", "customassets", "usermaps")))
MAX_QUERIES = 1024
MAX_MANAGERS = 16
MAX_NAME_LENGTH = 1024
MAX_ENTRIES = fingerprints.DEFAULT_MAX_ENTRIES
MAX_BYTES = fingerprints.DEFAULT_MAX_TOTAL_BYTES


class ResourceContextError(ValueError):
    """Incomplete, corrupt, unsupported or over-budget context input."""


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ResourceContextError("Input must be finite JSON data") from exc


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _copy(value):
    return json.loads(_json(value))


def _require(condition, message):
    if not condition:
        raise ResourceContextError(message)


def _integer(value, *, positive=False):
    return type(value) is int and (not positive or value > 0)


def _sha(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _keys(value, expected, label):
    _require(isinstance(value, dict) and set(value) == set(expected),
             f"Invalid {label} fields")


def _path(value):
    return (isinstance(value, str) and bool(value) and "\x00" not in value
            and not value.startswith("/") and "\\" not in value
            and all(c not in ("", ".", "..") for c in value.split("/")))


def validate_snapshot(snapshot):
    """Validate exact fingerprint schema, hierarchy, limits, totals and digest.

    This verifies internal consistency, not authentication or freshness. A
    malicious producer can forge a self-consistent receipt; callers must retain
    their trusted capture provenance or recapture through the secure walker.
    """
    _keys(snapshot, ("schema", "algorithm", "identity", "roots", "totals", "limits"),
          "snapshot")
    _require(type(snapshot["schema"]) is int and snapshot["schema"] == fingerprints.SCHEMA_VERSION
             and snapshot["algorithm"] == fingerprints.ALGORITHM, "Unsupported fingerprint schema/algorithm")
    _require(_sha(snapshot["identity"]), "Invalid resource digest")
    limits = snapshot["limits"]
    _keys(limits, ("max_entries", "max_total_bytes", "max_file_bytes", "max_depth"), "limits")
    _require(all(_integer(v, positive=True) for v in limits.values()), "Invalid fingerprint limits")
    roots = snapshot["roots"]
    _require(isinstance(roots, list) and len(roots) == 3, "Complete expected roles required")
    roles = []
    totals = {"entries": 0, "files": 0, "bytes": 0}
    payload_roots = []
    for root in roots:
        _keys(root, ("role", "location", "entries"), "root")
        role = root["role"]
        _require(isinstance(role, str) and role in EXPECTED_ROLES and role not in roles,
                 "Complete unique expected roles required")
        roles.append(role)
        location = root["location"]
        _require(isinstance(location, str) and "\x00" not in location and os.path.isabs(location)
                 and os.path.normpath(location) == location
                 and location.rsplit("/", 1)[-1].casefold() == ROOT_NAMES[role], "Invalid root location")
        entries = root["entries"]
        _require(isinstance(entries, list) and 0 < len(entries) <= MAX_ENTRIES, "Invalid/over-budget entries")
        seen = {}
        for entry in entries:
            _require(isinstance(entry, dict), "Invalid entry")
            kind = entry.get("kind")
            _require(kind in ("file", "directory"), "Unsupported entry kind")
            _keys(entry, ("path", "kind", "mtime_ns") if kind == "directory" else
                  ("path", "kind", "mtime_ns", "size", "sha256"), "entry")
            path = entry["path"]
            _require((path == "." and kind == "directory") or _path(path), "Unsafe entry path")
            _require(path not in seen and _integer(entry["mtime_ns"]), "Duplicate path/invalid metadata")
            depth = 0 if path == "." else len(path.split("/")) - (kind == "file")
            _require(depth <= limits["max_depth"], "Fingerprint depth limit exceeded")
            seen[path] = kind
            totals["entries"] += 1
            if kind == "file":
                _require(_integer(entry["size"]) and 0 <= entry["size"] <= limits["max_file_bytes"]
                         and _sha(entry["sha256"]), "Invalid file metadata")
                totals["files"] += 1
                totals["bytes"] += entry["size"]
        _require(seen.get(".") == "directory", "Missing root directory entry")
        for path in seen:
            if path != ".":
                parent = path.rsplit("/", 1)[0] if "/" in path else "."
                _require(seen.get(parent) == "directory", "Missing directory parent")
        # Preserve walk order in the resource digest, as the secure walker does.
        payload_roots.append({"role": role, "entries": entries})
    _require(roles == sorted(EXPECTED_ROLES), "Noncanonical or missing root roles")
    _keys(snapshot["totals"], totals, "totals")
    _require(all(_integer(v) and v >= 0 for v in snapshot["totals"].values())
             and snapshot["totals"] == totals, "Corrupt fingerprint totals")
    _require(totals["entries"] <= min(limits["max_entries"], MAX_ENTRIES)
             and totals["bytes"] <= min(limits["max_total_bytes"], MAX_BYTES), "Fingerprint budget exceeded")
    payload = {"schema": snapshot["schema"], "algorithm": snapshot["algorithm"], "roots": payload_roots}
    _require(_digest(payload) == snapshot["identity"], "Corrupt resource digest")
    return _copy(snapshot)


def capture_resources(roots, **limits):
    """Require all roles, including existing empty directories, and capture once.

    Secure read errors propagate as ResourceFingerprintError. Cache injection is
    intentionally excluded, so returned logical read counters represent a fresh
    capture. Callers can reuse the resulting validated historical snapshot.
    """
    _require(isinstance(roots, Mapping) and set(roots) == set(EXPECTED_ROLES),
             "Complete expected roles required")
    _require(set(limits) <= {"max_entries", "max_total_bytes", "max_file_bytes", "max_depth"},
             "Unsupported capture option")
    ceilings = {"max_entries": MAX_ENTRIES, "max_total_bytes": MAX_BYTES,
                "max_file_bytes": fingerprints.DEFAULT_MAX_FILE_BYTES,
                "max_depth": fingerprints.DEFAULT_MAX_DEPTH}
    _require(all(_integer(value, positive=True) and value <= ceilings[key]
                 for key, value in limits.items()), "Capture budget exceeds bounded ceilings")
    return validate_snapshot(fingerprints.fingerprint_resources(
        roots, required_roles=EXPECTED_ROLES, **limits))


def ordinary_policy(source_evidence_hashes, *, full_path_names=False,
                    ordinary_crc_names=True):
    """Declare bounded ASCII name assumptions plus reviewed source identities.

    False full_path_names models the reviewed ordinary startup basename mode;
    None/True blocks selection. ordinary_crc_names=True explicitly excludes
    deliberate CRC collisions; None/False blocks selection. Neither declaration
    is a runtime observation. ASCII lowercasing is the only supported case rule.
    """
    return {"version": POLICY_VERSION, "source_evidence_hashes": dict(source_evidence_hashes),
            "full_path_names": full_path_names, "ordinary_crc_names": ordinary_crc_names,
            "name_rule": "ascii-lower-basename-slash-or-backslash",
            "insertion_rule": "unforced-strict-newer-stored-unsigned-filetime",
            "external_rule": "reverse-explicit-add-order-then-base"}


def _name(name):
    _require(isinstance(name, str) and 0 < len(name) <= MAX_NAME_LENGTH, "Invalid resource name")
    pieces = name.replace("\\", "/").split("/")
    _require(all(p and p not in (".", "..") for p in pieces)
             and not any(ord(c) < 32 or c in ':*?<>|"' or ord(c) == 127 for c in name),
             "Resource name outside ordinary relative path subset")
    basename = pieces[-1]
    return basename.lower() if basename.isascii() else None


def candidate_id(manager, root, entry):
    """Identity for one candidate, including path/provider/metadata/bytes."""
    return _digest({"manager": manager, "role": root["role"],
                    "location": root["location"], "entry": entry})


def resource_context(names, *, managers, policy, inventory_sha256,
                     base_manager="base", external_order=None, stored_timestamps=None,
                     enumeration=None, provenance=None):
    """Build a deterministic, bounded receipt from validated complete snapshots.

    ``managers`` maps explicit manager IDs to complete fingerprint receipts.
    ``external_order`` is None (unknown), or {managers: [IDs in add order],
    evidence_sha256: SHA}; [] explicitly means no externals. A stored timestamp
    is keyed by candidate_id and contains {filetime: uint64, evidence_sha256:
    SHA}; candidate IDs bind it to the exact captured metadata and file bytes.
    An enumeration per manager contains {candidate_ids: [all file IDs in
    insertion order], evidence_sha256: SHA, resource_identity: snapshot digest}.
    This caller evidence must establish exact comparable stored FILETIME, not
    infer it from filesystem ns. The model chooses only within these assertions.

    Logging/compiler provenance is retained outside canonical identity. Snapshot
    paths, all entries/containers, negative queries, policy and evidence DO bind
    identity. The result never claims runtime selection, even with a candidate.
    """
    _require(isinstance(names, Sequence) and not isinstance(names, (str, bytes))
             and 0 < len(names) <= MAX_QUERIES, "Bounded explicit query sequence required")
    query_names = sorted(set(names)) if all(isinstance(n, str) for n in names) else []
    _require(bool(query_names), "Invalid resource names")
    normalized = {n: _name(n) for n in query_names}
    _require(_sha(inventory_sha256), "Programme ancestry inventory SHA required")
    _keys(policy, ("version", "source_evidence_hashes", "full_path_names", "ordinary_crc_names",
                   "name_rule", "insertion_rule", "external_rule"), "policy")
    sources = policy["source_evidence_hashes"]
    _require(isinstance(sources, dict) and bool(sources)
             and all(isinstance(k, str) and k and _sha(v) for k, v in sources.items()),
             "Policy source evidence hashes required")
    expected = ordinary_policy(policy["source_evidence_hashes"],
                               full_path_names=policy["full_path_names"],
                               ordinary_crc_names=policy["ordinary_crc_names"])
    _require(policy == expected and policy["version"] == POLICY_VERSION, "Unsupported policy")
    _require(policy["full_path_names"] is None or type(policy["full_path_names"]) is bool,
             "Invalid fullpathmode")
    _require(policy["ordinary_crc_names"] is None or type(policy["ordinary_crc_names"]) is bool,
             "Invalid CRC assumption")
    _require(isinstance(managers, Mapping) and 0 < len(managers) <= MAX_MANAGERS
             and all(isinstance(k, str) and k for k in managers)
             and base_manager in managers, "Invalid manager set/base")
    snapshots = {m: validate_snapshot(managers[m]) for m in sorted(managers)}
    _require(sum(s["totals"]["entries"] for s in snapshots.values()) <= MAX_ENTRIES
             and sum(s["totals"]["bytes"] for s in snapshots.values()) <= MAX_BYTES,
             "Aggregate resource budget exceeded")
    if external_order is not None:
        _keys(external_order, ("managers", "evidence_sha256"), "external order")
        order = external_order["managers"]
        _require(isinstance(order, list) and all(isinstance(m, str) for m in order)
                 and len(order) == len(set(order))
                 and set(order) == set(snapshots) - {base_manager}
                 and _sha(external_order["evidence_sha256"]), "Incomplete/invalid external manager order")
    timestamps = {} if stored_timestamps is None else _copy(stored_timestamps)
    enumerations = {} if enumeration is None else _copy(enumeration)
    _require(isinstance(timestamps, dict) and isinstance(enumerations, dict), "Invalid selection evidence")
    all_candidates, by_manager = {}, {}
    for manager, snapshot in snapshots.items():
        files = []
        for root in snapshot["roots"]:
            for entry in root["entries"]:
                if entry["kind"] != "file":
                    continue
                cid = candidate_id(manager, root, entry)
                candidate = {"id": cid, "manager": manager, "role": root["role"],
                             "root_location": root["location"], **entry,
                             "container_members": "unknown" if "/" not in entry["path"]
                             and entry["path"].lower().endswith(".fpk") else "not-catalogued-as-root-archive"}
                all_candidates[cid] = candidate
                files.append(candidate)
        by_manager[manager] = files
    for cid, stamp in timestamps.items():
        _require(cid in all_candidates, "Timestamp evidence for unknown/stale candidate")
        _keys(stamp, ("filetime", "evidence_sha256"), "stored timestamp")
        _require(_integer(stamp["filetime"]) and 0 <= stamp["filetime"] < 2**64
                 and _sha(stamp["evidence_sha256"]), "Invalid stored timestamp")
    for manager, sequence in enumerations.items():
        _require(manager in snapshots, "Unknown enumeration manager")
        _keys(sequence, ("candidate_ids", "evidence_sha256", "resource_identity"), "enumeration")
        ids = sequence["candidate_ids"]
        _require(isinstance(ids, list) and all(isinstance(i, str) for i in ids)
                 and len(ids) == len(set(ids))
                 and set(ids) == {c["id"] for c in by_manager[manager]}
                 and sequence["resource_identity"] == snapshots[manager]["identity"]
                 and _sha(sequence["evidence_sha256"]), "Incomplete/stale enumeration evidence")
    indexes, archives, unicode_unknown = {}, {}, {}
    for manager, files in by_manager.items():
        index = defaultdict(list)
        for candidate in files:
            basename = PurePosixPath(candidate["path"]).name
            if basename.isascii():
                index[basename.lower()].append(candidate)
        indexes[manager] = index
        archives[manager] = [c for c in files if c["container_members"] == "unknown"]
        unicode_unknown[manager] = any(not PurePosixPath(c["path"]).name.isascii() for c in files)
    expanded = sum(len(indexes[m].get(key, [])) + len(archives[m])
                   for key in normalized.values() for m in snapshots)
    _require(expanded <= MAX_ENTRIES, "Query candidate expansion budget exceeded")
    queries = []
    for name, key in normalized.items():
        manager_results = {}
        for manager, files in by_manager.items():
            candidates = indexes[manager].get(key, [])
            containers = archives[manager]
            blockers = []
            if key is None or unicode_unknown[manager]:
                blockers.append("unicode-case-behavior-unknown")
            if policy["full_path_names"] is not False:
                blockers.append("fullpathmode-unsupported-or-unknown")
            if policy["ordinary_crc_names"] is not True:
                blockers.append("crc-collision-behavior-outside-subset")
            if containers:
                blockers.append("packed-members-unknown")
            selected = None
            if not blockers and candidates:
                if len(candidates) == 1:
                    selected = candidates[0]["id"]
                elif any(c["id"] not in timestamps for c in candidates):
                    blockers.append("comparable-stored-filetime-unknown-no-ns-conversion")
                else:
                    newest = max(timestamps[c["id"]]["filetime"] for c in candidates)
                    tied = [c["id"] for c in candidates if timestamps[c["id"]]["filetime"] == newest]
                    if len(tied) == 1:
                        selected = tied[0]
                    elif manager not in enumerations:
                        blockers.append("equal-filetime-enumeration-unknown")
                    else:
                        selected = next(cid for cid in enumerations[manager]["candidate_ids"] if cid in tied)
            manager_results[manager] = {
                "candidates": candidates, "potential_packed_providers": containers,
                "state": "unresolved" if blockers else "model-candidate" if selected else "absent",
                "model_candidate_id": selected, "blocking_unknowns": blockers,
                "metadata_unknowns": ["unix-ns-to-filetime-conversion-not-implemented",
                                      "packed-reader-not-implemented"]
                    + (["enumeration-not-evidenced"] if manager not in enumerations else [])
                    + (["stored-filetime-not-evidenced"] if any(c["id"] not in timestamps for c in candidates) else [])}
        chosen, state, blockers = None, "absent", []
        if external_order is None and len(snapshots) > 1:
            state, blockers = "unresolved", ["external-manager-add-order-unknown"]
        else:
            search = ([*reversed(external_order["managers"]), base_manager]
                      if external_order is not None else [base_manager])
            for manager in search:
                result = manager_results[manager]
                if result["state"] != "absent":
                    state = result["state"]
                    chosen = result["model_candidate_id"]
                    blockers = result["blocking_unknowns"]
                    break
        queries.append({"requested_name": name, "normalized_basename": key,
                        "managers": manager_results, "state": state,
                        "model_candidate_id": chosen, "blocking_unknowns": blockers,
                        "runtime_selected_provider": None})
    binding = {"schema": SCHEMA, "policy": _copy(policy), "inventory_sha256": inventory_sha256,
               "base_manager": base_manager, "manager_snapshots": snapshots,
               "external_order": _copy(external_order), "stored_timestamps": timestamps,
               "enumeration": enumerations, "queries": queries,
               "proof_level": "offline-policy-model-no-engine-challenge"}
    return {"context_id": _digest(binding), "binding": binding,
            "provenance": _copy({} if provenance is None else provenance),
            "cost": {"managers": len(snapshots), "queries": len(queries),
                     "entries_validated": sum(s["totals"]["entries"] for s in snapshots.values()),
                     "files_bound": len(all_candidates),
                     "candidate_references": expanded,
                     "index_file_visits": len(all_candidates),
                     "query_manager_evaluations": len(queries) * len(snapshots),
                     "logical_bytes_bound": sum(s["totals"]["bytes"] for s in snapshots.values()),
                     "file_reads_during_resolution": 0, "model_calls_during_resolution": 0}}
