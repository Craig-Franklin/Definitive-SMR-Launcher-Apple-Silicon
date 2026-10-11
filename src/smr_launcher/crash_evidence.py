"""Derived coverage and candidate families from validated sanitized crash payloads.

No report discovery, collection, address translation or publication occurs here.
Callers validate the payload at their own boundary. A family is a comparison lead,
never proof of a common cause or permission to apply a repair.
"""

from __future__ import annotations

import hashlib
import json

MAX_COMPARISON_REPORTS = 32
MAX_COMPARISON_FILE_BYTES = 1024 * 1024
MAX_COMPARISON_TOTAL_BYTES = 8 * 1024 * 1024


def summarize_crash_evidence(payload):
    """Describe retained evidence without changing incident or publisher identity."""
    gaps = ["operation_unobserved", "triggering_resource_unobserved"]
    if payload["scenario_key"] is None:
        gaps.append("scenario_unknown")
    result = dict(
        version=1,
        family_fingerprint=None,
        family_status="unknown",
        family_limits=[],
        supported_game_frames=0,
        fault_frames_source=None,
        fault_frames_retained=0,
        fault_registers_retained=0,
        gaps=gaps,
    )
    limits = result["family_limits"]
    if payload["schema"] not in (2, 3):
        limits.append("structured_diagnostics_unavailable")
        return result
    details = payload["diagnostics"]
    if "numeric_vm" in details["unavailable"]:
        gaps.append("numeric_vm_unavailable")
    fault = next(
        (t for t in details["threads"]["items"] if t["index"] == details["faulting_thread"]),
        None,
    )
    if fault is None:
        limits.append("faulting_thread_unavailable")
        return result
    frames = fault["frames"]
    result.update(
        fault_frames_source=frames["source_count"],
        fault_frames_retained=frames["retained_count"],
        fault_registers_retained=fault["registers"]["retained_count"],
    )
    if not result["fault_registers_retained"]:
        gaps.append("fault_registers_unavailable")
    if frames["source_count"] != frames["retained_count"]:
        limits.append("fault_frames_incomplete")
    images = {i["index"]: i for i in details["images"]["items"]}
    supported = []
    for frame in frames["items"]:
        image = images.get(frame["image_index"])
        valid = (
            image is not None
            and image["uuid"] not in (None, "00000000-0000-0000-0000-000000000000")
            and image["architecture"] == payload["architecture"] != "UNKNOWN"
            and frame["offset"] is not None
            and image["size"] is not None
            and frame["offset"] < image["size"]
        )
        if frame["index"] == 0 and not valid:
            limits.append("fault_pc_unresolved")
        if image is None:
            limits.append("frame_image_unavailable")
        elif image["role"] == "game":
            if valid:
                supported.append([image["uuid"], image["architecture"], frame["offset"]])
            else:
                limits.append("game_frame_identity_incomplete")
    result["supported_game_frames"] = len(supported)
    if not supported:
        limits.append("no_supported_game_frames")
    if payload["termination_namespace"] == "UNKNOWN":
        limits.append("termination_namespace_unknown")
    if (
        payload["architecture"] == "UNKNOWN"
        or payload["translated"] is None
        or any(payload[k] == "UNKNOWN" for k in ("exception_type", "signal"))
    ):
        limits.append("signature_identity_incomplete")
    result["family_limits"] = sorted(set(limits))
    # An unmapped translated PC can leave a useful original game call chain.
    # Never hash a truncated or incompletely identified game chain as equivalent.
    if any(
        limit not in ("fault_pc_unresolved", "termination_namespace_unknown") for limit in limits
    ):
        return result
    signature = {
        key: payload[key]
        for key in (
            "game_executable_sha256",
            "architecture",
            "translated",
            "exception_type",
            "signal",
            "termination_namespace",
        )
    }
    signature.update(family_version=1, game_frames=supported)
    result["family_fingerprint"] = hashlib.sha256(
        json.dumps(signature, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result["family_status"] = "partial" if limits else "complete"
    return result


def compare_crash_evidence(payloads):
    """Validate and group explicit sanitized inputs; never grant repair applicability."""
    from .crash_reports import CrashReportError, _validate_payload

    if not isinstance(payloads, (list, tuple)) or not 1 <= len(payloads) <= MAX_COMPARISON_REPORTS:
        raise CrashReportError("comparison input count exceeds bounds")
    unique = {}
    for payload in payloads:
        try:
            checked = _validate_payload(payload)
        except (TypeError, KeyError, RecursionError) as exc:
            raise CrashReportError("invalid comparison payload") from exc
        incident = checked["report_sha256"]
        if incident in unique and unique[incident] != checked:
            raise CrashReportError("conflicting payloads for one report")
        unique[incident] = checked
    families, unclassified = {}, []
    for incident, payload in sorted(unique.items()):
        evidence = summarize_crash_evidence(payload)
        member = {
            key: payload[key]
            for key in ("report_sha256", "archive_sha256", "variant_id", "scenario_key")
        }
        member["evidence"] = evidence
        fingerprint = evidence["family_fingerprint"]
        if fingerprint is None:
            unclassified.append(member)
        else:
            families.setdefault(fingerprint, []).append(member)
    groups = []
    for fingerprint, members in sorted(families.items()):
        archives = sorted({m["archive_sha256"] for m in members if m["archive_sha256"] is not None})
        groups.append(
            dict(
                family_fingerprint=fingerprint,
                archive_sha256s=archives,
                cross_archive_match=len(archives) >= 2,
                incidents=members,
            )
        )
    return dict(
        version=1,
        input_count=len(payloads),
        incident_count=len(unique),
        families=groups,
        unclassified=unclassified,
        limitation="Matching keys are investigation leads, not proof of a common cause or repair applicability.",
    )


def main(argv=None):
    """Compare named sanitized payloads offline; output only after all inputs pass."""
    import argparse
    import os
    import stat
    import sys

    from .crash_reports import CrashReportError, _json

    class ComparisonParser(argparse.ArgumentParser):
        def error(self, message):
            raise CrashReportError("invalid comparison arguments")

    parser = ComparisonParser(
        prog="python -m smr_launcher.crash_evidence",
        description="Compare explicit sanitized crash payload JSON files.",
    )
    parser.add_argument("payloads", nargs="+", help="Files only; no discovery or collection")
    try:
        args = parser.parse_args(argv)
        if len(args.payloads) > MAX_COMPARISON_REPORTS:
            raise CrashReportError("comparison input count exceeds bounds")
        payloads, total = [], 0
        for path in args.payloads:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                limit = min(MAX_COMPARISON_FILE_BYTES, MAX_COMPARISON_TOTAL_BYTES - total)
                if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                    raise CrashReportError("comparison input exceeds bounds or is not regular")
                data = os.read(fd, limit + 1)
                after = os.fstat(fd)
                if (
                    len(data) != before.st_size
                    or len(data) > limit
                    or any(
                        getattr(before, field) != getattr(after, field)
                        for field in (
                            "st_dev",
                            "st_ino",
                            "st_mode",
                            "st_nlink",
                            "st_size",
                            "st_mtime_ns",
                            "st_ctime_ns",
                        )
                    )
                ):
                    raise CrashReportError("comparison input changed or exceeds bounds")
            finally:
                os.close(fd)
            total += len(data)
            payloads.append(_json(data.decode("utf-8")))
        result = compare_crash_evidence(payloads)
    except (CrashReportError, OSError, UnicodeError, TypeError, KeyError, RecursionError):
        print(
            "Cannot compare crash evidence: invalid, conflicting, unreadable or excessive inputs.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
