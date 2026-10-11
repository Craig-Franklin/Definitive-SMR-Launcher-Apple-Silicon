"""Derived coverage and candidate families from validated sanitized crash payloads.

No report discovery, collection, address translation or publication occurs here.
Callers validate the payload at their own boundary. A family is a comparison lead,
never proof of a common cause or permission to apply a repair.
"""

from __future__ import annotations

import hashlib
import json


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
