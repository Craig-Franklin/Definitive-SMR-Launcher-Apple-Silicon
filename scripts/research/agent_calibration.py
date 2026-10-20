"""Reusable guarded visual lifecycle; fresh private authority remains mandatory.

The parent owns all deadlines and cleanup. Only supported provider CLI calls run
in bounded children. The accepted independent stop observer remains armed while
the parent operates. Private facts never grant a second invocation.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import uuid
import zlib

# Stop-only support ships in the same checkout; private directories supply data only.
sys.path.insert(0, str(Path(__file__).resolve().with_name("individual_stop_support")))

from collections import Counter
from image_correspondence import point_for_frame
from crop_observations import crop_text_observations

from active_job_control import ActiveJobControl, UserCancelled
from finite_operation import cancelled_outcome
from scene_contract import (
    POLICY as SCENE_POLICY,
    PURPOSE,
    HELPER_SOURCE_SHA,
    HELPER_BINARY_SHA,
    menu_target,
    text_observations,
)


class Refused(RuntimeError):
    pass


class ChildTimeout(Refused):
    pass


@dataclass(frozen=True)
class ChildCompletion:
    argv: tuple
    pid: int
    returncode: int
    stdout: bytes
    completed_at: float
    deadline: float
    reaped: bool


class ChildFailed(Refused):
    """Ordinary owned completion, not timeout/termination or delivery proof."""

    def __init__(self, label, completion):
        super().__init__("Child failed: " + label)
        self.completion = completion


# Prospective jobs must pin this exact reviewed contract. Matching official
# source/release bytes are not a reproducible-build or general no-input proof.
PREINPUT_CONTRACT = {
    "schema": 1,
    "scope": "orca-1.4.224-closed-click-stale-element-before-input-v1",
    "native_helper_path": "/Applications/Orca.app/Contents/Resources/Orca Computer Use.app/Contents/MacOS/orca-computer-use-macos",
    "native_helper_sha256": "66050a2b0806dfa677a81062149fdbb0979fcb634250ab88cc6819bec99421f2",
    "native_source_blob": "324dba707da2dfcd600636c3c6415c1cf9e81bd9",
    "native_source_sha256": "1eabe4e19f52bc6684c0b58e6cbc2c92ccf8b8d95d160205e3164432e9951601",
    "proof_sha256": "b76ce044815e512c1fa7b7d460888d96022b5ff786f7e7c73daa6797e9d721af",
    "release_match_sha256": "daac5bb38f42c7c9a5569f9406eec11c420231e92122329d7fc001a6fc7ef08c",
    "binary_reproducible_build_proven": False,
}
STALE_NEXT_STEPS = [
    "Run `orca computer get-app-state --app <app> --json` again and use an element index from the fresh tree.",
    "Do not infer valid indexes from `elementCount` or reuse indexes after navigation, scrolling, focus changes, or delays.",
]
PRE_PLAY_READINESS_CONTRACT = {
    "schema": 1,
    "matching_observations": 2,
    "maximum_reads": 3,
    "minimum_gap_seconds": 0.25,
    "reserved_calls": 10,
    "oracle_sha256": SCENE_POLICY["oracle_sha256"],
}


FILENAME_STAGES = {"NameSelectAll": "name_select_all_attempted", "NameType": "name_type_attempted"}
SAVE_DIALOG_ACTION = ("hotkey", "--key", "CmdOrCtrl+S")
COORDINATE_STAGES = {
    "SinglePlayer": ("scene_click_permit", "single_player_attempted"),
    "SetupAction": ("setup_action_permit", "setup_action_attempted"),
}
HELPER_REVIEW_DECISION = "GO_OFFLINE_IMAGE_TEXT_SOURCE_ONLY"


def verify_scene_contract(job):
    """Read-only preflight; never construct the durable operational controller."""
    binding = decode(plain(Path(job["common_root"]) / "single-player-binding.json", maximum=131072))
    keys = {
        "policy",
        "helper_binary",
        "helper_source",
        "helper_review",
        "helper_review_sha256",
        "helper_review_decision",
        "projection",
        "projection_sha256",
        "requested_strings",
        "helper_acceptance",
        "helper_acceptance_sha256",
        "projection_receipt",
        "projection_receipt_sha256",
        "coordinate_proof",
        "coordinate_proof_sha256",
    }
    need(
        job.get("purpose") == PURPOSE
        and job.get("single_player_scene") == binding
        and type(binding) is dict
        and set(binding) == keys
        and binding["policy"] == SCENE_POLICY
        and all(type(binding["policy"][k]) is type(v) for k, v in SCENE_POLICY.items())
        and HELPER_REVIEW_DECISION != "PENDING_ROOT_HELPER_GO"
        and binding["helper_review_decision"] == HELPER_REVIEW_DECISION,
        "Fresh independently bound named scene purpose required",
    )
    for path, expected in (
        (binding["helper_binary"], HELPER_BINARY_SHA),
        (binding["helper_source"], HELPER_SOURCE_SHA),
        (binding["helper_review"], binding["helper_review_sha256"]),
        (binding["projection"], binding["projection_sha256"]),
        (binding["helper_acceptance"], binding["helper_acceptance_sha256"]),
        (binding["projection_receipt"], binding["projection_receipt_sha256"]),
        (binding["coordinate_proof"], binding["coordinate_proof_sha256"]),
    ):
        need(
            type(expected) is str
            and re.fullmatch(r"[0-9a-f]{64}", expected)
            and digest(plain(path)) == expected,
            "Scene helper/review/projection pin drift",
        )
    review = decode(plain(binding["helper_review"], maximum=131072))
    need(
        review.get("decision") == HELPER_REVIEW_DECISION
        and review.get("source_sha256") == HELPER_SOURCE_SHA
        and review.get("binary_sha256") == HELPER_BINARY_SHA,
        "Independent exact image-text helper GO required",
    )
    acceptance = decode(plain(binding["helper_acceptance"], maximum=131072))
    need(
        acceptance.get("status") == "ACCEPTED_GO_OFFLINE_IMAGE_TEXT_SOURCE_ONLY"
        and acceptance.get("decision_sha256") == binding["helper_review_sha256"]
        and acceptance.get("source_sha256") == HELPER_SOURCE_SHA
        and acceptance.get("binary_sha256") == HELPER_BINARY_SHA
        and acceptance.get("root_offline_retained_image_analysis_allowed") is True
        and acceptance.get("runtime_admitted") is False,
        "Root helper acceptance required",
    )
    receipt = decode(plain(binding["projection_receipt"], maximum=131072))
    need(
        receipt.get("schema") == "smr-root-retained-menu-text/1"
        and receipt.get("status") == "ACTUAL_RETAINED_MENU_TEXT_OBSERVED"
        and type(receipt.get("returncode")) is int
        and receipt["returncode"] == 0
        and receipt.get("source_sha256") == HELPER_SOURCE_SHA
        and receipt.get("binary_sha256") == HELPER_BINARY_SHA
        and receipt.get("independent_decision_sha256") == binding["helper_review_sha256"]
        and receipt.get("helper_output_sha256") == binding["projection_sha256"]
        and receipt.get("input_identity_unchanged") is True
        and receipt.get("menu_policy_ready") is True
        and receipt.get("live_game_scene_or_receiver_authority") is False,
        "Separately pinned root policy-only OCR observation required",
    )
    labels = binding["requested_strings"]
    need(
        type(labels) is list
        and labels == ["Single Player", "Load Game"]
        and all(type(s) is str and 0 < len(s.encode("utf-8")) <= 256 for s in labels)
        and len(set(labels)) == len(labels)
        and SCENE_POLICY["target"] in labels,
        "Observed exact menu signature strings required",
    )
    projection = decode(plain(binding["projection"], maximum=131072))
    width, height = projection.get("width"), projection.get("height")
    need(
        type(width) is int
        and type(height) is int
        and 0 < width <= 8192
        and 0 < height <= 8192
        and width * height <= 16 * 1024 * 1024
        and type(projection.get("recognition_revision")) is int
        and projection["recognition_revision"] > 0
        and type(projection.get("input_sha256")) is str
        and re.fullmatch(r"[0-9a-f]{64}", projection["input_sha256"]),
        "Accepted retained-image OCR projection required",
    )
    rows = text_observations(
        projection,
        need=need,
        image_hash=projection["input_sha256"],
        width=width,
        height=height,
        revision=projection["recognition_revision"],
        labels=labels,
    )
    need(
        receipt.get("input_sha256") == projection["input_sha256"]
        and receipt.get("input_dimensions") == [width, height]
        and receipt.get("recognition_revision") == projection["recognition_revision"]
        and len(rows) == 2
        and set(labels) == {r["text"] for r in rows}
        and sum(r["text"] == SCENE_POLICY["target"] for r in rows) == 1
        and all(r["confidence"] >= SCENE_POLICY["minimum_confidence"] for r in rows),
        "Reviewed observed signature/target confidence required",
    )
    return binding, projection


def verify_setup_contract(job):
    binding = decode(plain(Path(job["common_root"]) / "setup-action-binding.json", maximum=131072))
    need(
        type(binding) is dict
        and set(binding)
        == {
            "policy",
            "reference",
            "reference_sha256",
            "rectangle",
            "offset",
            "image_dimensions",
            "window_dimensions",
            "scale",
            "source_original",
            "game_sha256",
            "helper_binary",
            "helper_binary_sha256",
            "evidence",
            "semantic_control_identity",
            "runtime_authority_from_reference",
        }
        and json.dumps(job.get("setup_action_scene"), sort_keys=True)
        == json.dumps(binding, sort_keys=True)
        and binding.get("policy")
        == {
            "purpose": PURPOSE,
            "labels": ["Scenario Setup", "Cancel"],
            "minimum_confidence": 0.95,
            "maximum_setup_action_clicks": 1,
            "effect": "measure_unknown_control_scene_only",
            "gameplay_credit": False,
            "save_reopen_credit": False,
        }
        and binding.get("source_original") == job["source_original"]
        and binding.get("game_sha256") == job["game_sha256"]
        and binding.get("semantic_control_identity") == "NOT_PROVEN"
        and binding.get("runtime_authority_from_reference") is False,
        "Fresh exact setup measurement contract required",
    )
    for path, expected in [
        (binding["reference"], binding["reference_sha256"]),
        (binding["helper_binary"], binding["helper_binary_sha256"]),
        *[(r["path"], r["sha256"]) for r in binding["evidence"]],
    ]:
        need(
            type(expected) is str
            and re.fullmatch(r"[0-9a-f]{64}", expected)
            and digest(plain(path)) == expected,
            "Setup reference/helper/evidence drift",
        )
    return binding


def verify_dialog_contract(job):
    binding = decode(plain(Path(job["common_root"]) / "save-dialog-binding.json", maximum=131072))
    need(
        type(binding) is dict
        and set(binding)
        == {
            "policy",
            "helper_source",
            "helper_source_sha256",
            "helper_binary",
            "helper_binary_sha256",
            "review",
            "review_sha256",
            "acceptance",
            "acceptance_sha256",
            "manual_reference",
            "manual_reference_sha256",
        }
        and job.get("purpose") == PURPOSE
        and json.dumps(job.get("dialog_observation"), sort_keys=True)
        == json.dumps(binding, sort_keys=True)
        and binding["policy"]
        == {
            "purpose": PURPOSE,
            "later_frames": 2,
            "minimum_sample_gap_seconds": 0.25,
            "maximum_dialog_hotkeys": 1,
            "hotkey": list(SAVE_DIALOG_ACTION),
            "requested_strings": [
                "Save game as...",
                "File name:",
                "Ok",
                "Cancel",
                "Choose save game to load",
            ],
            "required_dialog_strings": ["Save game as...", "File name:", "Cancel"],
            "minimum_confidence": 0.95,
            "save_commit_allowed": False,
            "world_credit_allowed": False,
        }
        and type(binding["policy"]["later_frames"]) is int
        and type(binding["policy"]["maximum_dialog_hotkeys"]) is int
        and type(binding["policy"]["minimum_sample_gap_seconds"]) is float
        and type(binding["policy"]["minimum_confidence"]) is float
        and binding["policy"]["save_commit_allowed"] is False
        and binding["policy"]["world_credit_allowed"] is False
        and binding["helper_source_sha256"]
        == "015471f8da1fafc981a6cd7ca43492cf85c55b4b98e24fe56f6628ab27756da9"
        and binding["helper_binary_sha256"]
        == "3056269f598448cdde003f69073b0db947eefd9a95a67be720b9bf4cdefa71f3",
        "Fresh exact dialog-observation contract required",
    )
    for key in ("helper_source", "helper_binary", "review", "acceptance", "manual_reference"):
        need(
            digest(plain(binding[key])) == binding[key + "_sha256"], "Dialog helper/proof pin drift"
        )
    review = decode(plain(binding["review"], maximum=131072))
    need(
        review.get("decision") == "GO_OFFLINE_OCR_PRECISION_SOURCE_ONLY"
        and not review.get("findings")
        and review["source"]["sha256"] == binding["helper_source_sha256"]
        and review["production_binary"]["sha256"] == binding["helper_binary_sha256"],
        "Exact source-only precision review required",
    )
    acceptance = decode(plain(binding["acceptance"], maximum=131072))
    need(
        acceptance.get("status") == "ACCEPTED_GO_OFFLINE_OCR_PRECISION_SOURCE_ONLY"
        and acceptance.get("decision_sha256") == binding["review_sha256"]
        and acceptance.get("source_sha256") == binding["helper_source_sha256"]
        and acceptance.get("binary_sha256") == binding["helper_binary_sha256"]
        and acceptance.get("root_closed_offline_owned_image_analysis_allowed") is True
        and acceptance.get("runtime_admitted") is False,
        "Component source acceptance grants no runtime authority",
    )
    manual = decode(plain(binding["manual_reference"], maximum=131072))
    need(
        manual.get("documented_chord") == "CmdOrCtrl+S"
        and manual.get("native_transport", {}).get("source_sha256")
        == PREINPUT_CONTRACT["native_source_sha256"]
        and manual.get("native_transport", {}).get("installed_helper_sha256")
        == PREINPUT_CONTRACT["native_helper_sha256"]
        and manual.get("installed_game_sha256") == job["game_sha256"]
        and manual.get("save_commit_authorized_by_this_reference") is False,
        "Documented Mac save-dialog hypothesis required",
    )
    return binding


def verify_filename_contract(job):
    binding = decode(plain(Path(job["common_root"]) / "filename-binding.json", maximum=131072))
    keys = {
        "policy",
        "token",
        "helper_source",
        "helper_source_sha256",
        "helper_binary",
        "helper_binary_sha256",
        "review",
        "review_sha256",
        "acceptance",
        "acceptance_sha256",
        "reference",
        "reference_sha256",
        "rectangle",
        "offset",
        "matcher_binary",
        "matcher_binary_sha256",
        "consumer_sha256",
    }
    expected = {
        "purpose": PURPOSE,
        "maximum_select_all": 1,
        "maximum_type_text": 1,
        "select_all": ["hotkey", "--key", "CmdOrCtrl+A"],
        "required_dialog_labels": ["File name:", "Cancel"],
        "minimum_confidence": 0.95,
        "save_commit_allowed": False,
        "field_focus_proven_before_type": False,
        "native_text_dispatch": "global_hid_after_target_window_focus_check; not PID-isolated or atomic across characters",
        "no_input_authority_from_component": True,
        "minimum_vertical_overlap_fraction": 0.5,
        "maximum_token_height_ratio": 2.0,
        "row_vertical_padding_ratio": 0.5,
        "later_read_after_each_keyboard_input": True,
        "later_read_minimum_seq_advance": 1,
        "later_read_minimum_gap_seconds": 0.25,
    }
    need(
        type(binding) is dict
        and set(binding) == keys
        and job.get("purpose") == PURPOSE
        and json.dumps(job.get("filename_observation"), sort_keys=True)
        == json.dumps(binding, sort_keys=True)
        and binding["policy"] == expected
        and all(type(binding["policy"][k]) is type(v) for k, v in expected.items())
        and type(binding["token"]) is str
        and re.fullmatch(r"Pilot[0-9]{14}", binding["token"])
        and binding["helper_source_sha256"]
        == "4b9635fb7ab418e90f270bcc2a9e30275f31f1cf3a3ce5a6a2d52f935693f9fd"
        and binding["helper_binary_sha256"]
        == "ea8893144daf0e0e9c8a151c0cf7791e9cf69ec3c081ed11391b9c7c831a8d2e"
        and binding["consumer_sha256"]
        == "aa4b25936ec0f12d488813f974ee956aad40fbab9b974083569c70a7a8616dba"
        and binding["reference_sha256"]
        == "3c143cac5c82f85d1a1f583c51b1ebc2ac1906f98f6d663dfca1b260bd09856c",
        "Exact fresh filename effect contract required",
    )
    for key in (
        "helper_source",
        "helper_binary",
        "review",
        "acceptance",
        "reference",
        "matcher_binary",
    ):
        need(
            digest(plain(binding[key])) == binding[key + "_sha256"],
            "Filename helper/reference/proof drift",
        )
    need(
        digest(plain(Path(__file__).with_name("crop_observations.py")))
        == binding["consumer_sha256"],
        "Crop consumer drift",
    )
    review = decode(plain(binding["review"], maximum=131072))
    accept = decode(plain(binding["acceptance"], maximum=131072))
    need(
        review.get("decision") == "GO_OFFLINE_EXPLICIT_CROP_SOURCE_ONLY"
        and review.get("findings") == []
        and review["exact_identity"]["source_sha256"] == binding["helper_source_sha256"]
        and review["exact_identity"]["production_binary_sha256"] == binding["helper_binary_sha256"]
        and accept.get("status") == "ACCEPTED_GO_OFFLINE_EXPLICIT_CROP_SOURCE_ONLY"
        and accept.get("decision_sha256") == binding["review_sha256"]
        and accept.get("source_sha256") == binding["helper_source_sha256"]
        and accept.get("binary_sha256") == binding["helper_binary_sha256"]
        and accept.get("runtime_admitted") is False
        and accept.get("input_authority") is False,
        "Component source acceptance supplies no operational input authority",
    )
    rectangle, offset = binding["rectangle"], binding["offset"]
    need(
        type(rectangle) is list
        and len(rectangle) == 4
        and all(type(n) is int for n in rectangle)
        and 4 <= rectangle[2] <= 128
        and 4 <= rectangle[3] <= 128
        and 0 <= rectangle[0] <= 786 - rectangle[2]
        and 0 <= rectangle[1] <= 510 - rectangle[3]
        and type(offset) is list
        and len(offset) == 2
        and offset == [rectangle[2] / 2, rectangle[3] / 2]
        and all(type(n) is float for n in offset),
        "Exact reference Cancel-region geometry required",
    )
    return binding


def filename_row_effect(rows, token):
    counts = Counter(r["text"] for r in rows)
    if any(counts[name] != 1 for name in ("File name:", "Cancel", token)):
        return False
    if not all(r["confidence"] >= 0.95 for r in rows):
        return False
    (field,) = [r for r in rows if r["text"] == "File name:"]
    (cancel,) = [r for r in rows if r["text"] == "Cancel"]
    (value,) = [r for r in rows if r["text"] == token]
    row_bottom = field["y"] - 0.5 * field["height"]
    row_top = field["y"] + 1.5 * field["height"]
    overlap = min(value["y"] + value["height"], field["y"] + field["height"]) - max(
        value["y"], field["y"]
    )
    return (
        value["x"] >= field["x"] + field["width"]
        and value["x"] + value["width"] <= cancel["x"] + cancel["width"]
        and value["y"] >= row_bottom
        and value["y"] + value["height"] <= row_top
        and value["height"] <= 2.0 * field["height"]
        and overlap >= 0.5 * min(value["height"], field["height"])
    )


def completed_preinput_refusal(failure, argv, action, runtime, deadline, window):
    """Recognize only one complete, owned, on-time, closed native error branch."""
    if (
        not isinstance(failure, ChildFailed)
        or type(failure.completion) is not ChildCompletion
        or type(action) is not list
        or len(action) != 3
        or action[:2] != ["click", "--element-index"]
        or type(action[2]) is not str
        or not re.fullmatch(r"0|[1-9][0-9]{0,8}", action[2])
        or type(window) is not int
        or window <= 0
    ):
        return False
    if (
        type(argv) is not list
        or len(argv) != 12
        or argv[1:6] != ["computer", *action, "--app"]
        or type(argv[6]) is not str
        or not re.fullmatch(r"pid:[1-9][0-9]*", argv[6])
        or argv[7:9] != ["--json", "--session"]
        or argv[10:] != ["--window-id", str(window)]
    ):
        return False
    c = failure.completion
    if (
        c.argv != tuple(argv)
        or type(c.pid) is not int
        or c.pid <= 0
        or type(c.returncode) is not int
        or c.returncode != 1
        or c.reaped is not True
        or type(c.stdout) is not bytes
        or not 0 < len(c.stdout) <= 65536
        or type(c.completed_at) not in (int, float)
        or not math.isfinite(c.completed_at)
        or type(c.deadline) not in (int, float)
        or not math.isfinite(c.deadline)
        or not c.completed_at < min(c.deadline, deadline)
        or not time.monotonic() < deadline
    ):
        return False
    try:
        receipt = decode(c.stdout)
        return (
            type(receipt) is dict
            and set(receipt) == {"id", "ok", "error", "_meta"}
            and type(receipt["id"]) is str
            and str(uuid.UUID(receipt["id"])) == receipt["id"]
            and receipt["ok"] is False
            and receipt["_meta"] == {"runtimeId": runtime}
            and receipt["error"]
            == {
                "code": "element_not_found",
                "message": f"element {action[2]} is stale; run get-app-state again and use a fresh element index",
                "data": {"nextSteps": STALE_NEXT_STEPS},
            }
        )
    except (ValueError, TypeError, KeyError, AttributeError, Refused):
        return False


def need(condition, reason):
    if not condition:
        raise Refused(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    def pairs(rows):
        value = {}
        for key, item in rows:
            need(key not in value, "Duplicate JSON key")
            value[key] = item
        return value

    return json.loads(data, object_pairs_hook=pairs)


def plain(path, maximum=32 * 1024 * 1024):
    path = Path(path)
    need(
        path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
        "Regular absolute path required",
    )
    a = path.stat()
    need(
        stat.S_ISREG(a.st_mode) and a.st_nlink == 1 and 0 < a.st_size <= maximum,
        "Plain bounded file required",
    )
    data = path.read_bytes()
    b = path.stat()
    need(
        (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns, a.st_ctime_ns)
        == (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns, b.st_ctime_ns),
        "File changed during read",
    )
    return data


def bounded_child(argv, directory, label, tick, timeout, mutable=False, *, spawn=None):
    """Poll an owned child while real sampling and deadlines continue.

    Reaping a CLI does not prove its server-side mutable operation was canceled.
    The caller must retain uncertain-input state after any mutable failure.
    """
    directory = Path(directory)
    need(re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", label), "Unique child label")
    need(
        type(timeout) in (float, int) and math.isfinite(timeout) and 0 < timeout <= 30,
        "Finite child deadline required",
    )
    deadline = time.monotonic() + timeout

    def fact(suffix, value):
        path = directory / (label + suffix + ".json")
        with path.open("x") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    with (
        (directory / (label + ".stdout")).open("x+b") as out,
        (directory / (label + ".stderr")).open("xb") as err,
    ):
        before_spawn = time.time()
        child = (subprocess.Popen if spawn is None else spawn)(argv, stdout=out, stderr=err)
        try:
            fact(
                "-child-created",
                {
                    "pid": child.pid,
                    "parent_pid": os.getpid(),
                    "creation_wall_before": before_spawn,
                    "creation_wall_after": time.time(),
                    "requested_executable": str(argv[0]),
                    "mutable": mutable,
                    "identity_basis": "direct owned unreaped Popen child; kernel executable/birth not claimed",
                    "descendant_closure": "not claimed",
                },
            )
            while child.poll() is None:
                tick()
                need(
                    out.tell() <= 32 * 1024 * 1024 and err.tell() <= 65536,
                    "Child output exceeded bound",
                )
                if time.monotonic() >= deadline:
                    raise ChildTimeout("Bounded provider/launch child timed out")
                time.sleep(0.05)
            tick()
            completed_at = time.monotonic()
        finally:
            if child.poll() is None:
                # Popen owns this unreaped child; its PID cannot be reassigned
                # while it remains our live/unreaped child. No group signals.
                child.terminate()
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
            else:
                child.wait()
            out.flush()
            err.flush()
            os.fsync(out.fileno())
            os.fsync(err.fileno())
            fact(
                "-child-exit",
                {
                    "pid": child.pid,
                    "owned_wait_returncode": child.returncode,
                    "reaped": True,
                    "mutable_delivery": "unknown unless full provider receipt returned",
                },
            )
        # Capture the same owned descriptor, not a later reopened receipt path.
        owned = os.fstat(out.fileno())
        current = (directory / (label + ".stdout")).lstat()
        need(
            (owned.st_dev, owned.st_ino) == (current.st_dev, current.st_ino)
            and owned.st_nlink == 1,
            "Owned child output replaced",
        )
        out.seek(0)
        data = out.read(32 * 1024 * 1024 + 1)
        after = os.fstat(out.fileno())
        current = (directory / (label + ".stdout")).lstat()
        need(
            (owned.st_size, owned.st_mtime_ns, owned.st_ctime_ns)
            == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            and (after.st_dev, after.st_ino) == (current.st_dev, current.st_ino)
            and after.st_nlink == 1,
            "Owned child output changed during capture",
        )
    need(len(data) <= 32 * 1024 * 1024, "Bounded child output")
    if child.returncode != 0:
        raise ChildFailed(
            label,
            ChildCompletion(
                tuple(argv), child.pid, child.returncode, data, completed_at, deadline, True
            ),
        )
    return data


def fresh_play_index(receipt, pid, *, allow_missing=False):
    """Only an actual unique actionable AX Play; never coordinates or guesses."""
    need(receipt.get("ok") is True, "Provider read failed")
    result = receipt.get("result", {})
    snapshot = result.get("snapshot", {})
    need(
        snapshot.get("app", {}).get("pid") == pid
        and snapshot.get("id")
        and snapshot.get("window", {}).get("id") is not None,
        "Current exact-PID/window snapshot required",
    )
    need(
        bool(
            (result.get("screenshot") or {}).get("path")
            or (result.get("screenshot") or {}).get("data")
        ),
        "Actual pre-Play image required",
    )
    tree = snapshot.get("treeText")
    need(type(tree) is str and len(tree) <= 65536, "Bounded actual AX tree required")
    matches = []
    for line in tree.splitlines():
        # Orca indexes come from treeText, not elementCount. Accept only the
        # observed button role, exact label and an explicitly listed AXPress.
        match = re.fullmatch(
            r"""\s*\[(\d+)\]\s+AXButton\s+["']Play["']\s+(?:enabled=true\s+)?actions=\[AXPress\]\s*""",
            line,
        )
        # The installed provider's compact AX renderer uses "21 button Play".
        # That indexed semantic button is its actionable control surface; no
        # elementCount or historical index is accepted as a substitute.
        compact = re.fullmatch(r"\s*(\d+) button Play", line)
        match = match or compact
        if match and not re.search(r"disabled|enabled[=: ]+false", line, re.I):
            matches.append(int(match.group(1)))
    if allow_missing and not matches:
        return None
    need(len(matches) == 1, "Unique fresh actionable AX Play unavailable")
    return matches[0]


def verify_observer_bootstrap(
    bootstrap, actual, parent_kernel, interpreter, prefix, before, after, clock_metadata
):
    """Bind the helper to the actual parent kernel, not a launcher symlink.

    Homebrew's Python launcher and the running Python.app kernel can have
    different paths. Both identities are real; resolving the launcher is not
    evidence of which executable the kernel reports for a running process.
    """
    need(
        type(actual) is dict
        and type(actual.get("pid")) is int
        and type(actual.get("birth_us")) is int
        and actual["pid"] > 0
        and actual["birth_us"] > 0
        and actual.get("executable") == parent_kernel,
        "Observer actual kernel identity mismatch",
    )
    recorded = bootstrap.get("process", {})
    need(
        all(
            type(recorded.get(k)) is type(actual[k]) and recorded.get(k) == actual[k]
            for k in ("pid", "birth_us", "executable")
        ),
        "Observer bootstrap process mismatch",
    )
    observed = bootstrap.get("monotonic")
    need(
        bootstrap.get("interpreter") == interpreter
        and bootstrap.get("prefix") == prefix
        and type(observed) in (int, float)
        and math.isfinite(observed)
        and before <= observed <= after
        and bootstrap.get("clock") == clock_metadata,
        "Observer interpreter/prefix/clock mismatch",
    )


def stage_stop_support(directory, closure):
    """Copy reviewed tracked bytes into the unchanged helper's private code boundary."""
    from individual_stop import _directory

    source = Path(__file__).resolve().with_name("individual_stop_support")
    directory = Path(directory)
    names = (
        "wrapper.py",
        "adapter.py",
        "observer.py",
        "individual_stop.py",
        "frozen_mac_processes.py",
        "proposal.json",
        "pins.json",
        "host.py",
    )
    expected = {row["path"]: row for row in closure}
    directory.mkdir(mode=0o700, exist_ok=True)
    descriptor = _directory(directory)
    os.close(descriptor)
    for name in names:
        path = source / name
        raw = plain(path)
        row = expected.get(str(path))
        need(
            row is not None and len(raw) == row["bytes"] and digest(raw) == row["sha256"],
            "Reviewed stop-support source drift",
        )
        target = directory / name
        if not target.exists() and not target.is_symlink():
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        need(plain(target) == raw, "Private stop-support copy drift")
    need(
        {path.name for path in directory.iterdir()} == set(names),
        "Unexpected private stop-support member",
    )
    descriptor = _directory(directory)
    os.fsync(descriptor)
    os.close(descriptor)
    return directory


class UKCalibration:
    """Concrete fresh job using accepted profile, observer and journal code."""

    def __init__(self, job, root, *, journal_name="UK filename effect observation"):
        self.job, self.root = job, Path(root)
        self.control = ActiveJobControl(job)
        self.directory = Path(job["runtime_directory"])
        self.directory.mkdir(mode=0o700)
        self.anchor = time.monotonic()
        self.launch_deadline = self.anchor + 120
        self.started_wall = time.time()
        self.game = None
        self.observer = None
        self.helper_packet = self.directory / "observer"
        self.helper_packet.mkdir(mode=0o700)
        self.baseline = None
        self.activated = self.open_attempted = self.uncertain_input = False
        self.seq = self.frames = 0
        self.next_sample = self.anchor
        self.creation = []
        self.phases = {"operation_started": self.anchor}
        self.last_frame_complete = None
        self.preferences = []
        self.diagnostic = "map-" + job["diagnostic_variant"]
        from smr_launcher.application import LauncherApplication
        from frozen_mac_processes import MacProcesses
        from runner_journal import RunJournal

        self.app = LauncherApplication.discover()
        self.profiles = self.app.profiles
        self.monitor = MacProcesses(str(self.app.installation.executable))
        self.controller_identity = asdict(MacProcesses(sys.executable).inspect(os.getpid()))
        self.journal = RunJournal(self.directory)
        self.journal.append(
            "started",
            {
                "run_id": job["job"],
                "input_identity": job["source_original"]["archive_sha256"],
                "engine_input_identity": job["game_sha256"],
                "binding": job["root"],
                "name": journal_name,
                "scenario": job["source_original"]["scenario"],
                "run_directory": str(self.directory),
            },
        )
        self.journal.append("terminal_pending", {"one_use": True, "automatic_successor": False})

    def write(self, name, value):
        from runner_journal import write_once, read_json

        path = self.directory / name
        write_once(path, value)
        need(read_json(path) == value, "Durable receipt readback")

    def manifest(self, path):
        from smr_launcher.activation import _assert_regular_tree, _assert_no_symlink_ancestor
        from smr_launcher.variants import _tree_manifest

        _assert_no_symlink_ancestor(path)
        _assert_regular_tree(path)
        return json.loads(json.dumps(_tree_manifest(path)))

    def metadata(self, path):
        rows = []
        total = 0
        for base, dirs, files in os.walk(path, followlinks=False):
            for name in sorted(dirs + files):
                p = Path(base) / name
                s = p.lstat()
                need(stat.S_ISREG(s.st_mode) or stat.S_ISDIR(s.st_mode), "Plain profile required")
                total += s.st_size if p.is_file() else 0
                need(len(rows) < 25000 and total <= 2 * 1024**3, "Profile preservation budget")
                rows.append(
                    {
                        "path": str(p.relative_to(path)),
                        "mode": stat.S_IMODE(s.st_mode),
                        "mtime_ns": s.st_mtime_ns,
                        "bytes": s.st_size if p.is_file() else None,
                    }
                )
        return rows

    def verify_code(self):
        for row in self.job["closure"]:
            data = plain(row["path"])
            need(len(data) == row["bytes"] and digest(data) == row["sha256"], "Frozen code changed")

    def require_stopped(self):
        need(
            self.app.installation.game_running() is False and not self.monitor.games(),
            "Current game absence not proven",
        )

    def verify_available(self):
        self.control.poll()

    def verify_cleanup_owner(self):
        # Cancellation permits exact-owned cleanup, never new input. Replacement
        # or malformed ownership refuses both stop and restoration.
        self.control.verify_owner()

    @contextmanager
    def mutation_guard(self):
        with self.control.mutation_guard():
            need(
                time.monotonic() - self.anchor < 480, "Original calibration action deadline expired"
            )
            yield

    def spawn_input(self, argv, *, deadline, before_spawn=None, **kwargs):
        # Only immediate client creation is inside the lock. The generic child
        # poll/wait remains outside, so native cancellation can still progress.
        with self.mutation_guard():
            need(
                time.monotonic() < min(deadline, self.launch_deadline),
                "Original mutable dispatch deadline expired",
            )
            if before_spawn is not None:
                before_spawn()
            return subprocess.Popen(argv, **kwargs)

    def prepare(self):
        from actor_binding import collect_actor
        from smr_launcher.activation import _fsync_tree
        from smr_launcher.resource_identity import fingerprint_resources

        self.verify_available()
        actor = collect_actor(self.job["actor_binding"])
        need(
            actor["model"] == "gpt-6.1-sol"
            and actor["effort"] == "high"
            and actor["approval_policy"] == "never"
            and actor["sandbox"] == "danger-full-access",
            "Actual MAIN provenance mismatch",
        )
        run = decode(
            subprocess.check_output(
                [
                    self.job["orca"],
                    "orchestration",
                    "run-show",
                    "--id",
                    self.job["root"]["run"],
                    "--json",
                ],
                timeout=3,
            )
        )
        need(
            type(run["result"]["run"]["consumer_generation"]) is int
            and run["result"]["run"]["consumer_generation"]
            == self.job["root"]["consumer_generation"]
            and run["result"]["run"]["coordinator_handle"] == self.job["root"]["coordinator"],
            "Current Run ownership changed",
        )
        self.verify_code()
        self.require_stopped()
        p = self.profiles
        need(not p.journal.exists() and not p.journal.is_symlink(), "Activation journal present")
        need(p._state()["active"] == self.job["return_profile"], "Current return profile changed")
        need(
            self.app.installation.executable_sha256 == self.job["game_sha256"], "Game build changed"
        )
        source = p._profile_path(self.job["source_profile"])
        p._marker(source, self.job["source_profile"])
        (record,) = [
            r
            for r in self.app.catalogue()
            if r.variant_id == self.job["source_original"]["variant_id"]
        ]
        need(
            record.archive_sha256 == self.job["source_original"]["archive_sha256"]
            and self.job["source_original"]["scenario"] in record.scenarios
            and self.app._is_original_variant(record),
            "Exact original UK selection required",
        )
        imported = self.app.imports / record.imported_directory
        prepared = self.app.prepared / record.prepared_directory
        need(
            digest(plain(self.app.originals / (record.archive_sha256 + ".7z")))
            == record.archive_sha256,
            "Archive changed",
        )
        for role, path in [
            ("import", imported),
            ("prepared", prepared),
            ("canonical", source),
            ("live", p.live),
            ("stock", p._profile_path("original-game")),
        ]:
            actual = {"manifest": self.manifest(path), "metadata": self.metadata(path)}
            need(actual == self.job["preservation"][role], "Fresh preservation changed: " + role)
        self.baseline = self.job["preservation"]
        installed_assets = (
            self.app.installation.steamapps_root
            / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        )
        need(
            fingerprint_resources({"installed_assets": installed_assets})["identity"]
            == self.job["installed_assets_identity"],
            "Installed dependency resources changed",
        )
        self.installed_assets = installed_assets
        self.original_paths = {
            "import": imported,
            "prepared": prepared,
            "canonical": source,
            "stock": p._profile_path("original-game"),
        }
        self.write(
            "preflight.json",
            {
                "game_absent": True,
                "return_profile": self.job["return_profile"],
                "baseline": self.baseline,
            },
        )
        seed = self.directory / "profile-seed"
        need(
            shutil.disk_usage(p.live).free
            >= max(
                10 * 1024**3, sum(x["bytes"] or 0 for x in self.baseline["live"]["metadata"]) * 3
            ),
            "Insufficient free space",
        )
        shutil.copytree(source, seed, copy_function=shutil.copy2)
        need(self.manifest(seed) == self.baseline["canonical"]["manifest"], "Seed copy mismatch")
        settings = seed / "Settings.ini"
        before = settings.read_bytes()
        lines = before.splitlines(keepends=True)
        section = None
        matches = []
        for index, line in enumerate(lines):
            if line.strip().startswith(b"["):
                section = line.strip()
            if section == b"[User Settings]" and re.fullmatch(
                rb"Logging[ \t]*=[ \t]*[01]\r?\n", line
            ):
                matches.append(index)
        need(len(matches) == 1, "Exact diagnostic logging setting unavailable")
        (index,) = matches
        lines[index] = re.sub(rb"(?<==)([ \t]*)[01]", rb"\g<1>1", lines[index])
        shutil.copy2(settings, self.directory / "diagnostic-settings-preimage")
        settings.write_bytes(b"".join(lines))
        logs = seed / "Logs"
        need(logs.is_dir() and not logs.is_symlink(), "Diagnostic log directory required")
        prior_logs = self.directory / "seed-prior-logs"
        prior_logs.mkdir(mode=0o700)
        for log in sorted(logs.iterdir()):
            need(log.is_file() and not log.is_symlink(), "Plain log required")
            os.rename(log, prior_logs / log.name)
        _fsync_tree(seed)
        need(not p._profile_path(self.diagnostic).exists(), "Diagnostic profile collision")
        self.verify_available()
        need(
            p.register_variant(
                self.job["diagnostic_variant"],
                seed,
                recover_pending=False,
                mutation_guard=self.mutation_guard,
            )
            == self.diagnostic,
            "Fresh diagnostic registration failed",
        )
        need(
            p._asset_hash(source)
            == p._asset_hash(p._profile_path(self.diagnostic))
            == p._asset_hash(prepared),
            "Original UK assets changed",
        )
        for name, path in [
            ("feral", self.app.installation.support_root / "Preferences Data"),
            ("global", Path.home() / "Library/Preferences/com.feralinteractive.railroads.plist"),
        ]:
            data = plain(path, 262144)
            s = path.stat()
            copy = self.directory / (name + "-preferences-preimage")
            shutil.copy2(path, copy)
            need(copy.read_bytes() == data, "Preference snapshot mismatch")
            self.preferences.append(
                {
                    "path": str(path),
                    "copy": str(copy),
                    "sha256": digest(data),
                    "mode": stat.S_IMODE(s.st_mode),
                    "mtime_ns": s.st_mtime_ns,
                }
            )
        self.write("preferences-preimages.json", self.preferences)
        self.phases["preparation_complete"] = time.monotonic()

    def publish(self, name, value):
        from wrapper import publish, parent_stamp

        publish(self.helper_packet, name, value, parent_stamp(self.helper_packet))

    def launch(self, *, navigation_deadline=None, record_selection=True):
        self.launch_deadline = (
            self.anchor + 120 if navigation_deadline is None else navigation_deadline
        )
        need(
            time.monotonic() < self.launch_deadline <= self.anchor + 480,
            "Fixed launch deadline required",
        )
        from frozen_mac_processes import MacProcesses
        from wrapper import bounded

        self.require_stopped()
        self.verify_available()
        self.verify_code()
        helper = stage_stop_support(self.directory / "stop-support", self.job["closure"])
        self.code = digest((helper / "pins.json").read_bytes())
        self.nonce = uuid.uuid4().hex
        admission = dict(
            job=self.job["job"],
            nonce=self.nonce,
            code=self.code,
            anchor=self.anchor,
            expected_executable=str(self.app.installation.executable),
            interpreter=sys.executable,
            clock_domain="root-venv-time.monotonic-v1",
        )
        approval = dict(
            issue=127,
            status="approved",
            proposal_sha256=decode((helper / "proposal.json").read_bytes())["sha256"],
            job=admission["job"],
            nonce=self.nonce,
            code=self.code,
            anchor=self.anchor,
        )
        self.publish(
            "admission.json",
            dict(
                admission=admission,
                approval=approval,
                source_acceptance=dict(
                    issue=129,
                    status="accepted",
                    observer_sha256="8683b919f8d3bd0b68675e6c6731839144a7c68555a75efafab8309ca18b19b3",
                    component_sha256="c7a4a0eee7e2ceb2cf900eeabcc93689119c75e8f61fe96bac933c5247079848",
                ),
            ),
        )
        self.helper_log = (self.directory / "stop-observer.log").open("xb")
        before_helper = time.monotonic()
        self.observer = subprocess.Popen(
            [sys.executable, "-B", str(helper / "host.py"), str(self.helper_packet)],
            stdout=self.helper_log,
            stderr=subprocess.STDOUT,
        )
        limit = min(time.monotonic() + 5, self.launch_deadline)
        while not (self.helper_packet / "ready.json").exists():
            need(
                self.observer.poll() is None and time.monotonic() < limit,
                "Stop observer failed to arm",
            )
            self.verify_available()
            time.sleep(0.05)
        bootstrap = decode(bounded(self.helper_packet / "bootstrap.json"))
        own = MacProcesses(sys.executable).inspect(self.observer.pid)
        need(
            own is not None and own.pid == self.observer.pid, "Owned observer identity unavailable"
        )
        self.creation.append(dict(role="observer", **asdict(own)))
        self.write(
            "helper-prebind-identity.json",
            {
                "actual": asdict(own),
                "parent_kernel": self.controller_identity["executable"],
                "interpreter_launcher": sys.executable,
                "bootstrap": bootstrap,
            },
        )
        verify_observer_bootstrap(
            bootstrap,
            asdict(own),
            self.controller_identity["executable"],
            sys.executable,
            sys.prefix,
            before_helper,
            time.monotonic(),
            vars(time.get_clock_info("monotonic")),
        )
        self.write("helper-clock-binding.json", bootstrap)
        self.write(
            "consumed.json",
            {
                "job": self.job["job"],
                "one_launch_consumed": True,
                "anchor": self.anchor,
                "automatic_successor": False,
            },
        )

        def open_once():
            self.verify_available()
            self.activated = True
            self.request = time.monotonic()
            self.phases["launch_request"] = self.request
            self.request_wall_us = time.time_ns() // 1000
            self.publish(
                "request.json",
                dict(
                    version=1,
                    job=self.job["job"],
                    nonce=self.nonce,
                    code=self.code,
                    anchor=self.anchor,
                    request=self.request,
                ),
            )
            self.write(
                "open-request-attempted.json",
                {
                    "wall_us": self.request_wall_us,
                    "qualification": "LaunchServices may be asynchronous; absence is not cancellation",
                },
            )
            self.verify_available()

            def mark_launch_attempted():
                self.open_attempted = True

            bounded_child(
                ["/usr/bin/open", "-a", str(self.app.installation.executable.parents[2])],
                self.directory,
                "Launch",
                self.tick,
                15,
                spawn=lambda argv, **kwargs: self.spawn_input(
                    argv,
                    deadline=min(self.launch_deadline, self.request + 15),
                    before_spawn=mark_launch_attempted,
                    **kwargs,
                ),
            )
            limit = min(self.request + 20, self.launch_deadline)
            while self.game is None:
                self.tick()
                need(time.monotonic() < limit, "Launch identity unresolved; no stopped rollback")
                time.sleep(0.05)
            self.sample(force=True)
            process = dict(
                pid=self.game.pid, birth=str(self.game.birth_us), executable=self.game.executable
            )
            from sample_fence import _wait_initial_sample_tick

            ready = decode(bounded(self.helper_packet / "ready.json"))
            _wait_initial_sample_tick(
                self.helper_packet, admission, process, ready, limit, self.verify_available
            )
            self.publish(
                "descriptor.json",
                dict(
                    version=1,
                    job=self.job["job"],
                    nonce=self.nonce,
                    code=self.code,
                    anchor=self.anchor,
                    request=self.request,
                    process=process,
                ),
            )
            while not (self.helper_packet / "bound.json").exists():
                # Keep the initial sample immutable while the accepted
                # observer validates its fingerprint twice during binding.
                self.verify_available()
                actual = self.monitor.inspect(self.game.pid)
                need(
                    actual is not None
                    and self.game.same(actual)
                    and actual.footprint <= 6 * 1024**3
                    and self.observer.poll() is None,
                    "Binding process/observer changed",
                )
                need(time.monotonic() < limit, "Observer binding unresolved")
                time.sleep(0.05)
            bound = decode(bounded(self.helper_packet / "bound.json"))
            need(
                bound["state"]["bound"] is True and bound["state"]["process"] == process,
                "Exact observer binding mismatch",
            )

        # The accepted transactional play method holds its launcher lock across
        # exchange and launch. There is exactly one LaunchServices request.
        self.verify_available()
        self.app.play(
            self.job["source_profile"],
            launch=open_once,
            profile_copy=self.diagnostic,
            startup_seconds=1,
            mutation_guard=self.mutation_guard,
        )
        self.write(
            "creation-ledger.json",
            {
                "processes": self.creation,
                "controller": self.controller_identity,
                "scope": "Game and observer kernel identities; short-lived CLI children have per-call parent-creation/wait receipts. Controller returns after terminalization. No server/descendant closure claim.",
            },
        )
        engine = dict(
            game_sha256=self.job["game_sha256"], source_original=self.job["source_original"]
        )
        selection = dict(
            process={k: asdict(self.game)[k] for k in ("pid", "birth_us", "executable")},
            selected_at_us=time.time_ns() // 1000,
            observed_inputs=self.job["source_original"],
            observed_engine_inputs=engine,
            observed_input_identity=digest(
                json.dumps(engine, sort_keys=True, separators=(",", ":")).encode()
            ),
        )
        if record_selection:
            self.journal.append("selected", selection)
        else:
            self.write("relaunch-selection.json", selection)

    def tick(self):
        self.verify_available()
        need(time.monotonic() - self.anchor < 480, "Original calibration action deadline expired")
        if self.observer is not None:
            need(self.observer.poll() is None, "Independent stop observer exited during operation")
        self.sample()

    def sample(self, force=False):
        now = time.monotonic()
        if not force and now < self.next_sample:
            return
        self.next_sample = now + 1
        games = self.monitor.games()
        need(len(games) <= 1, "Ambiguous game process")
        if self.game is None and self.open_attempted and games:
            (actual,) = games
            need(
                actual.birth_us >= self.request_wall_us
                and actual.executable == str(self.app.installation.executable),
                "Unowned launch identity",
            )
            self.game = actual
            self.creation.append(dict(role="game", **asdict(actual)))
        if self.game is None:
            need(not games, "Game appeared before owned launch request")
            return
        need(len(games) == 1 and self.game.same(games[0]), "Game exited or identity changed")
        need(games[0].footprint <= 6 * 1024**3, "Physical footprint exceeded 6 GiB")
        logs = self.profiles.live / "Logs"
        need(logs.is_dir() and not logs.is_symlink(), "Diagnostic logs unavailable")
        total = 0
        for log in logs.iterdir():
            need(log.is_file() and not log.is_symlink(), "Nonregular diagnostic log")
            total += log.stat().st_size
        need(total <= 8 * 1024 * 1024, "Diagnostic log resource budget exceeded")
        self.seq += 1
        process = dict(
            pid=self.game.pid, birth=str(self.game.birth_us), executable=self.game.executable
        )
        self.publish(
            "sample.json",
            dict(
                version=1,
                job=self.job["job"],
                nonce=self.nonce,
                code=self.code,
                anchor=self.anchor,
                process=process,
                seq=self.seq,
                status="success",
                start=now,
                complete=time.monotonic(),
            ),
        )
        self.phases.setdefault("first_real_sample_complete", time.monotonic())
        self.last_sample_start = now

    def verify_provider(self):
        from frozen_mac_processes import MacProcesses

        for expected in self.job["provider_processes"]:
            actual = MacProcesses(expected["executable"]).inspect(expected["pid"])
            need(
                actual is not None
                and all(
                    asdict(actual)[k] == expected[k] for k in ("pid", "birth_us", "executable")
                ),
                "Selected provider process changed",
            )
        status = decode(subprocess.check_output([self.job["orca"], "status", "--json"], timeout=3))
        need(
            status.get("_meta", {}).get("runtimeId")
            == self.job["actor_binding"]["provider_runtime_id"],
            "Selected provider runtime changed",
        )

    def verify_preinput_contract(self):
        """A new job explicitly pins this known build/branch before mutable input."""
        contract = self.job.get("preinput_refusal_contract")
        need(
            type(contract) is dict
            and set(contract) == set(PREINPUT_CONTRACT) | {"proof_path", "release_match_path"}
            and all(
                type(contract[k]) is type(v) and contract[k] == v
                for k, v in PREINPUT_CONTRACT.items()
            )
            and all(
                type(contract[k]) is str and Path(contract[k]).is_absolute()
                for k in ("proof_path", "release_match_path")
            ),
            "Exact prospective native pre-input contract required",
        )
        for path, expected in (
            (PREINPUT_CONTRACT["native_helper_path"], PREINPUT_CONTRACT["native_helper_sha256"]),
            (contract["proof_path"], PREINPUT_CONTRACT["proof_sha256"]),
            (contract["release_match_path"], PREINPUT_CONTRACT["release_match_sha256"]),
        ):
            need(digest(plain(Path(path))) == expected, "Native pre-input build/proof changed")

    def provider(self, label, action, mutable=False, window=None, deadline=None):
        self.tick()
        self.sample(force=True)
        self.verify_code()
        self.verify_provider()
        if deadline is not None:
            need(time.monotonic() < deadline, "Original provider transition deadline expired")
        if mutable:
            self.verify_available()
            self.verify_preinput_contract()
            need(
                self.last_frame_complete is not None
                and 0 <= time.monotonic() - self.last_frame_complete < 20,
                "Observed actionable frame expired",
            )
        need(
            self.game is not None and self.frames < 24 and time.monotonic() - self.anchor < 120,
            "Provider/navigation budget expired",
        )
        self.frames += 1
        self.write(
            label + "-attempt.json",
            {
                "mutable": mutable,
                "anchor": self.anchor,
                "call": self.frames,
                "elapsed": time.monotonic() - self.anchor,
            },
        )
        need(
            type(action) is list
            and action
            and action[0] in ("list-windows", "get-app-state", "click", "hotkey", "type-text"),
            "Unsupported provider command routing",
        )
        command = action[0]
        need(
            type(mutable) is bool and mutable == (command in ("click", "hotkey", "type-text")),
            "Inconsistent mutable command",
        )
        need(
            command in ("click", "hotkey", "type-text") or action == [command],
            "Unsupported observation arguments",
        )
        need(
            command not in ("hotkey", "type-text")
            or label == "SaveDialog"
            or label in FILENAME_STAGES,
            "Unnamed keyboard input refused",
        )
        if label in FILENAME_STAGES:
            self.filename_input_guard(label, action, window)
        if label == "SaveDialog":
            self.dialog_hotkey_guard(action, window)
        need(
            not any(
                flag in action[1:]
                for flag in ("--app", "--session", "--window-id", "--window-index", "--worktree")
            ),
            "Provider target selector override refused",
        )
        need(
            window is None or (type(window) is int and window > 0),
            "Invalid explicit window target",
        )
        need(command != "list-windows" or window is None, "Window list has no window selector")
        argv = [
            self.job["orca"],
            "computer",
            *action,
            "--app",
            "pid:" + str(self.game.pid),
            "--json",
        ]
        if command != "list-windows":
            argv.extend(["--session", self.job["root"]["session"]])
        if window is not None:
            argv.extend(["--window-id", str(window)])
        if (
            any(type(flag) is str and flag.startswith(("--x", "--y")) for flag in action[1:])
            or label in COORDINATE_STAGES
        ):
            need(label in COORDINATE_STAGES, "Coordinate input outside named scene purpose")
            self.scene_click_guard(action, window, label=label)
        dispatch_attempted = False

        def mark_dispatch_attempted():
            nonlocal dispatch_attempted
            need(
                time.monotonic() < dispatch_deadline,
                "Original provider deadline expired before dispatch",
            )
            if label in COORDINATE_STAGES:
                self.scene_click_guard(action, window, label=label)
                setattr(self, COORDINATE_STAGES[label][1], True)
            if label == "SaveDialog":
                self.dialog_hotkey_guard(action, window)
                self.save_dialog_attempted = True
            if label in FILENAME_STAGES:
                self.filename_input_guard(label, action, window)
                setattr(self, FILENAME_STAGES[label], True)
            dispatch_attempted = True

        try:
            # Availability and owner reads consume the original child budget.
            dispatch_deadline = min(time.monotonic() + 10, self.anchor + 120)
            if deadline is not None:
                dispatch_deadline = min(dispatch_deadline, deadline)
            if mutable:
                # Fallible proof IO precedes the final lock-aware ingress drain
                # in spawn_input; only the deadline/attempt flag remain inside.
                self.verify_preinput_contract()
                if label in COORDINATE_STAGES:
                    self.verify_scene_contract()
                if label == "SetupAction":
                    self.verify_setup_contract()
                if label == "SaveDialog":
                    self.verify_dialog_contract()
                if label in FILENAME_STAGES:
                    self.verify_filename_contract()
            self.verify_available()
            if mutable:
                need(
                    time.monotonic() < dispatch_deadline,
                    "Original provider deadline expired before dispatch",
                )
            timeout = dispatch_deadline - time.monotonic()

            def provider_tick():
                self.tick()
                need(
                    time.monotonic() < dispatch_deadline,
                    "Original provider child deadline expired",
                )

            data = bounded_child(
                argv,
                self.directory,
                label,
                provider_tick,
                timeout,
                mutable,
                spawn=(
                    lambda argv, **kwargs: self.spawn_input(
                        argv,
                        deadline=dispatch_deadline,
                        before_spawn=mark_dispatch_attempted,
                        **kwargs,
                    )
                )
                if mutable
                else None,
            )
            value = decode(data)
            need(value.get("ok") is True, "Provider failed or unverified delivery")
            if label in COORDINATE_STAGES or label == "SaveDialog" or label in FILENAME_STAGES:
                need(
                    value.get("_meta", {}).get("runtimeId")
                    == self.job["actor_binding"]["provider_runtime_id"]
                    and type(value.get("result")) is dict
                    and value["result"].get("snapshot", {}).get("app", {}).get("pid")
                    == self.game.pid
                    and value["result"].get("snapshot", {}).get("window", {}).get("id") == window,
                    "Unbound coordinate delivery receipt",
                )
            self.sample(force=True)
            self.verify_provider()
            if value.get("result", {}).get("snapshot"):
                self.last_frame_complete = time.monotonic()
        except BaseException as failure:
            qualified_refusal = False
            try:
                if mutable and dispatch_attempted and isinstance(failure, ChildFailed):
                    self.verify_available()
                    self.verify_provider()
                    self.verify_preinput_contract()
                    qualified_refusal = completed_preinput_refusal(
                        failure,
                        argv,
                        action,
                        self.job["actor_binding"]["provider_runtime_id"],
                        dispatch_deadline,
                        window,
                    )
                    if qualified_refusal:
                        # Preserve the failed operation/raw child facts; this is
                        # no retry or gameplay result, only a narrow no-input basis.
                        qualified_refusal = False
                        self.write(
                            label + "-preinput-refusal.json",
                            {
                                "classification": "known_completed_preinput_refusal",
                                "child_pid": failure.completion.pid,
                                "stdout_sha256": digest(failure.completion.stdout),
                                "returncode": failure.completion.returncode,
                                "contract": self.job["preinput_refusal_contract"],
                                "receipt_match_only": True,
                                "acceptance_requires_current_guards_and_on_time_validation": True,
                                "operation_failed": True,
                                "retry_allowed": False,
                                "binary_reproducible_build_proven": False,
                            },
                        )
                        # Persistence/ownership reads consume the original clock.
                        self.verify_available()
                        self.verify_provider()
                        self.verify_preinput_contract()
                        need(
                            time.monotonic() < dispatch_deadline,
                            "Original provider deadline expired during refusal validation",
                        )
                        qualified_refusal = True
            finally:
                if mutable and dispatch_attempted and not qualified_refusal:
                    self.uncertain_input = True
            # Never clear an uncertainty latch from an earlier operation.
            raise
        return value

    def retain_frame(self, receipt, label):
        result = receipt.get("result", {})
        snap = result.get("snapshot", {})
        shot = result.get("screenshot") or {}
        need(
            snap.get("app", {}).get("pid") == self.game.pid
            and snap.get("window", {}).get("id") is not None,
            "Exact game frame/window required",
        )
        if shot.get("path"):
            image_path = Path(shot["path"])
            # Orca uses macOS's system /var alias for temporary screenshots.
            # Normalize that one system alias; arbitrary link paths still fail.
            if image_path.parts[:2] == ("/", "var"):
                image_path = Path("/private") / image_path.relative_to("/")
            data = plain(image_path)
        elif shot.get("data"):
            need(len(shot["data"]) <= 44 * 1024 * 1024, "Inline image exceeded bound")
            data = base64.b64decode(shot["data"], validate=True)
        else:
            raise Refused("No actual provider image")
        need(
            data.startswith(b"\x89PNG\r\n\x1a\n")
            and 32 <= len(data) <= 32 * 1024 * 1024
            and data[12:16] == b"IHDR",
            "Nonempty bounded PNG frame required",
        )
        offset, has_pixels, ended = 8, False, False
        image_data = []
        while offset < len(data):
            need(offset + 12 <= len(data), "Truncated PNG chunk")
            size = struct.unpack(">I", data[offset : offset + 4])[0]
            end = offset + 12 + size
            need(end <= len(data), "Truncated PNG payload")
            kind = data[offset + 4 : offset + 8]
            payload = data[offset + 8 : offset + 8 + size]
            checksum = struct.unpack(">I", data[end - 4 : end])[0]
            need(zlib.crc32(kind + payload) & 0xFFFFFFFF == checksum, "PNG checksum mismatch")
            if kind == b"IDAT" and size:
                has_pixels = True
                image_data.append(payload)
            if kind == b"IEND":
                need(size == 0 and end == len(data), "Unexpected PNG tail")
                ended = True
            offset = end
        need(has_pixels and ended, "PNG pixel payload/end missing")
        width, height = struct.unpack(">II", data[16:24])
        need(struct.unpack(">I", data[8:12])[0] == 13, "PNG IHDR length mismatch")
        depth, colour, compression, filtering, interlace = data[24:29]
        need(
            depth == 8
            and colour in (0, 2, 4, 6)
            and (compression, filtering, interlace) == (0, 0, 0),
            "Unsupported provider PNG encoding",
        )
        channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
        row_bytes = 1 + width * channels
        expected_bytes = row_bytes * height
        need(0 < expected_bytes <= 64 * 1024 * 1024, "Decoded image resource bound")
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(b"".join(image_data), expected_bytes + 1)
        need(
            decoder.eof
            and not decoder.unused_data
            and not decoder.unconsumed_tail
            and len(pixels) == expected_bytes
            and all(pixels[offset] <= 4 for offset in range(0, len(pixels), row_bytes)),
            "Invalid or incomplete provider PNG compression/rows",
        )
        scale = shot.get("scale")
        window = snap["window"]
        need(
            type(scale) in (int, float)
            and math.isfinite(scale)
            and 0 < scale <= 4
            and 0 < width <= 8192
            and 0 < height <= 8192
            and abs(width - window.get("width", 0) * scale) <= 2
            and abs(height - window.get("height", 0) * scale) <= 2,
            "Actual frame geometry/scale mismatch",
        )
        from smr_launcher.live_run_authority import write_file

        write_file(self.directory / (label + ".png"), data, exclusive=True)
        frame = {
            "label": label,
            "sha256": digest(data),
            "bytes": len(data),
            "snapshot": snap["id"],
            "window": snap["window"],
            "scale": shot.get("scale"),
            "treeText": snap.get("treeText"),
            "world_loaded": "unknown",
            "gameplay_credit": False,
        }
        self.write(label + "-frame.json", frame)
        self.phases["frame_" + label] = time.monotonic()
        return frame

    def startup_window(self, deadline):
        """Observe readiness within original launch clocks; never focus or restore."""
        # Preserve readiness3, Play1, Windows1, transition3, scene-click1 and scene-read1.
        startup_calls = 24 - self.frames - SCENE_POLICY["reserved_after_startup"]
        need(startup_calls > 0, "Startup observation capacity unavailable")
        next_read = time.monotonic()
        need(next_read < deadline, "Original startup window deadline expired")
        cadence = (deadline - next_read) / startup_calls
        for attempt in range(startup_calls):
            while time.monotonic() < next_read:
                self.tick()
                now = time.monotonic()
                need(now < deadline, "Original startup window deadline expired during wait")
                pause = min(0.05, next_read - now, deadline - now)
                if pause > 0:
                    time.sleep(pause)
            need(time.monotonic() < deadline, "Original startup window deadline expired")
            observation_start = time.monotonic()
            receipt = self.provider(
                "StartupWindows" + str(attempt + 1), ["list-windows"], deadline=deadline
            )
            need(time.monotonic() < deadline, "Original startup window deadline expired after read")
            result = receipt.get("result")
            need(
                type(result) is dict
                and type(result.get("app")) is dict
                and result["app"].get("pid") == self.game.pid
                and type(result.get("windows")) is list,
                "Startup window list identity or shape invalid",
            )
            choices, seen = [], set()
            for window in result["windows"]:
                need(
                    type(window) is dict
                    and type(window.get("app")) is dict
                    and window["app"].get("pid") == self.game.pid
                    and type(window.get("id")) is int
                    and window["id"] > 0
                    and window["id"] not in seen
                    and type(window.get("isOffscreen")) is bool
                    and type(window.get("isMinimized")) is bool,
                    "Startup window identity or visibility invalid",
                )
                seen.add(window["id"])
                if not window["isOffscreen"] and not window["isMinimized"]:
                    choices.append(window["id"])
            need(len(choices) <= 1, "Ambiguous actual startup windows")
            if choices:
                return choices[0]
            next_read = min(deadline, observation_start + cadence)
        raise Refused("Original startup window observation budget exhausted")

    def pre_play_ready(self, window, deadline):
        """Two matching fresh observations, never reselection after an input."""
        previous, validated, snapshots = None, None, set()
        self.scene_snapshots = snapshots
        for attempt in range(3):
            if validated is not None:
                ready_at = validated + 0.25
                while time.monotonic() < ready_at:
                    self.tick()
                    now = time.monotonic()
                    need(now < deadline, "Original startup readiness deadline expired during wait")
                    pause = min(0.05, ready_at - now, deadline - now)
                    if pause > 0:
                        time.sleep(pause)
            need(time.monotonic() < deadline, "Original startup readiness deadline expired")
            label = "Pregame" if attempt == 0 else "Pregame" + str(attempt + 1)
            receipt = self.provider(label, ["get-app-state"], window=window, deadline=deadline)
            result = receipt.get("result")
            need(
                type(result) is dict and type(result.get("snapshot")) is dict,
                "Malformed readiness snapshot",
            )
            snapshot = result["snapshot"]
            need(
                type(snapshot.get("app")) is dict and type(snapshot.get("window")) is dict,
                "Malformed readiness app/window",
            )
            target = snapshot.get("window", {})
            need(
                type(snapshot.get("app", {}).get("pid")) is int
                and snapshot["app"]["pid"] == self.game.pid
                and type(target.get("id")) is int
                and target["id"] == window,
                "Readiness snapshot differs from observed PID/window",
            )
            sid = snapshot.get("id")
            need(type(sid) is str, "Genuinely fresh native snapshot required")
            snapshot_id = uuid.UUID(sid)
            need(
                str(snapshot_id) == sid.lower() and snapshot_id not in snapshots,
                "Genuinely fresh native snapshot required",
            )
            snapshots.add(snapshot_id)
            geometry = tuple(target.get(k) for k in ("x", "y", "width", "height"))
            need(
                all(type(v) in (int, float) and math.isfinite(v) for v in geometry)
                and geometry[2] > 0
                and geometry[3] > 0,
                "Malformed readiness window geometry",
            )
            tree = snapshot.get("treeText")
            need(
                type(tree) is str and len(tree.encode("utf-8")) <= 65536,
                "Bounded readiness tree required",
            )
            self.retain_frame(receipt, label)
            index = fresh_play_index(receipt, self.game.pid, allow_missing=True)
            key = (self.game.pid, window, geometry, tree.encode("utf-8"), index)
            validated = time.monotonic()
            need(
                validated < deadline, "Original startup readiness deadline expired after validation"
            )
            if index is not None and previous == key:
                return receipt, index
            previous = key if index is not None else None
        raise Refused("Three readiness observations did not establish a matching Play pair")

    def observe(self):
        self.verify_scene_contract()
        contract = self.job.get("pre_play_readiness")
        need(
            type(contract) is dict
            and contract == PRE_PLAY_READINESS_CONTRACT
            and all(type(contract[k]) is type(v) for k, v in PRE_PLAY_READINESS_CONTRACT.items()),
            "Fresh job must pin the pre-Play observation policy",
        )
        startup_deadline = min(self.request + 20, self.anchor + 120)
        window = self.startup_window(startup_deadline)
        first, index = self.pre_play_ready(window, startup_deadline)
        need(time.monotonic() - self.anchor < 120, "Fresh Play deadline")
        # One actual current actionable control. Never coordinates or key guesses.
        self.sample(force=True)
        need(time.monotonic() < startup_deadline, "Original startup deadline expired before Play")
        transition_anchor = self.last_sample_start
        played = self.provider(
            "Play",
            ["click", "--element-index", str(index)],
            mutable=True,
            window=first["result"]["snapshot"]["window"]["id"],
            deadline=transition_anchor + 30,
        )
        result = played.get("result", {})
        need(time.monotonic() - transition_anchor < 30, "Original Play transition clock expired")
        if (result.get("screenshot") or {}).get("path") or (result.get("screenshot") or {}).get(
            "data"
        ):
            frame = self.retain_frame(played, "AfterPlay")
            return self.measure_single_player(
                played, frame, played["result"]["snapshot"]["window"]["id"]
            )
        need(
            result.get("screenshotStatus", {}).get("code") == "screenshot_failed"
            and result.get("snapshot", {}).get("app", {}).get("pid") == self.game.pid,
            "Missing image outside established successful generic-AX transition",
        )
        tree = result.get("snapshot", {}).get("treeText", "")
        need(
            type(tree) is str
            and not re.search(
                r"^\s*\d+ (?:button|text field|checkbox|HTML content|menu)(?: |$)", tree, re.M
            ),
            "Post-Play AX is not the established generic game tree",
        )
        before_window = first["result"]["snapshot"]["window"]
        after_window = result["snapshot"]["window"]
        need(
            any(
                after_window.get(k) != before_window.get(k)
                for k in ("id", "title", "width", "height")
            ),
            "No actual post-Play window transition observed",
        )
        need(time.monotonic() - transition_anchor < 30, "Original transition clock expired")
        windows = self.provider("Windows", ["list-windows"], deadline=transition_anchor + 30)[
            "result"
        ]
        need(windows.get("app", {}).get("pid") == self.game.pid, "Window list identity mismatch")
        choices = [
            w
            for w in windows.get("windows", [])
            if w.get("app", {}).get("pid") == self.game.pid
            and type(w.get("id")) is int
            and not w.get("isOffscreen")
            and not w.get("isMinimized")
        ]
        need(len(choices) == 1, "Unique actual visible game window unavailable")
        for attempt in range(3):
            need(time.monotonic() - transition_anchor < 30, "Original transition clock expired")
            current = self.provider(
                "WindowRead" + str(attempt + 1),
                ["get-app-state"],
                window=choices[0]["id"],
                deadline=transition_anchor + 30,
            )
            need(
                time.monotonic() - transition_anchor < 30,
                "Original transition clock expired after read",
            )
            if current.get("result", {}).get("screenshot"):
                frame = self.retain_frame(current, "WindowRead" + str(attempt + 1))
                return self.measure_single_player(current, frame, choices[0]["id"])
            need(
                current.get("result", {}).get("screenshotStatus", {}).get("code")
                == "screenshot_failed",
                "Unexpected provider observation failure",
            )
        raise Refused("Three permitted transition observations produced no image")

    def verify_scene_contract(self):
        return verify_scene_contract(self.job)

    def scene_click_guard(self, action, window, *, label="SinglePlayer"):
        need(label in COORDINATE_STAGES, "Unknown coordinate stage")
        permit_name, attempted_name = COORDINATE_STAGES[label]
        permit = getattr(self, permit_name, None)
        need(
            self.job.get("purpose") == PURPOSE
            and permit is not None
            and not getattr(self, attempted_name, False)
            and (label != "SetupAction" or getattr(self, "single_player_attempted", False))
            and tuple(action) == permit["action"]
            and window == permit["window"]
            and 0 <= time.monotonic() - permit["captured_at"] < 20
            and time.monotonic() < self.anchor + 120,
            "One fresh exact named-purpose scene click required",
        )

    def scene_frame(self, receipt, frame, window, *, fresh=True):
        snapshot = receipt.get("result", {}).get("snapshot", {})
        need(
            receipt.get("ok") is True
            and receipt.get("_meta", {}).get("runtimeId")
            == self.job["actor_binding"]["provider_runtime_id"]
            and type(snapshot.get("app", {}).get("pid")) is int
            and snapshot["app"]["pid"] == self.game.pid
            and type(window) is int
            and window > 0
            and type(snapshot.get("window", {}).get("id")) is int
            and snapshot["window"]["id"] == window
            and frame["window"] == snapshot["window"]
            and frame["snapshot"] == snapshot.get("id"),
            "Current owned scene receipt/runtime binding required",
        )
        sid = snapshot.get("id")
        need(type(sid) is str, "Fresh scene snapshot required")
        identity = uuid.UUID(sid)
        seen = getattr(self, "scene_snapshots", set())
        need(
            str(identity) == sid.lower() and (not fresh or identity not in seen),
            "Scene snapshot replay",
        )
        path = self.directory / (frame["label"] + ".png")
        data = plain(path)
        width, height = struct.unpack(">II", data[16:24])
        scale = frame["scale"]
        geometry = frame["window"]
        need(
            digest(data) == frame["sha256"]
            and len(data) == frame["bytes"]
            and all(
                type(geometry.get(k)) in (int, float) and math.isfinite(geometry[k])
                for k in ("x", "y", "width", "height")
            )
            and type(scale) in (int, float)
            and math.isfinite(scale)
            and 0 < scale <= 4
            and geometry["width"] > 0
            and geometry["height"] > 0
            and abs(width - geometry["width"] * scale) <= 2
            and abs(height - geometry["height"] * scale) <= 2,
            "Actual PNG/geometry/scale binding drift",
        )
        if fresh:
            seen.add(identity)
            self.scene_snapshots = seen
        return dict(frame, pixel_width=width, pixel_height=height), path

    def measure_single_player(self, receipt, frame, window):
        binding, projection = self.verify_scene_contract()
        source_label = "Play" if frame["label"] == "AfterPlay" else frame["label"]
        attempt = decode(plain(self.directory / (source_label + "-attempt.json"), maximum=65536))
        need(
            attempt.get("anchor") == self.anchor
            and type(attempt.get("elapsed")) in (int, float)
            and math.isfinite(attempt["elapsed"])
            and attempt["elapsed"] >= 0,
            "Original provider observation clock required",
        )
        # Request start conservatively predates capture; persistence/OCR never renew frame age.
        captured_at = self.anchor + attempt["elapsed"]
        need(
            type(captured_at) in (int, float) and 0 <= time.monotonic() - captured_at < 20,
            "Current menu image expired",
        )
        bound, image_path = self.scene_frame(receipt, frame, window)
        ocr_deadline = min(time.monotonic() + 10, self.anchor + 120, captured_at + 20)
        self.verify_available()
        self.sample(force=True)

        def ocr_tick():
            self.tick()
            need(time.monotonic() < ocr_deadline, "Original image-text child deadline expired")

        def spawn_ocr(argv, **kwargs):
            ocr_tick()
            return subprocess.Popen(argv, **kwargs)

        raw = bounded_child(
            [
                binding["helper_binary"],
                str(image_path),
                bound["sha256"],
                json.dumps(binding["requested_strings"], ensure_ascii=False),
            ],
            self.directory,
            "MenuText",
            ocr_tick,
            ocr_deadline - time.monotonic(),
            spawn=spawn_ocr,
        )
        need(0 < len(raw) <= 131072, "Image-text output byte bound")
        value = decode(raw)
        x, y = menu_target(value, bound, projection, binding["requested_strings"], need=need)
        self.write(
            "menu-text-bound.json",
            {
                "observation": value,
                "snapshot": frame["snapshot"],
                "window": window,
                "captured_at": captured_at,
                "projection_sha256": binding["projection_sha256"],
                "local_center": {"x": x, "y": y},
                "no_authority_from_OCR_alone": True,
            },
        )
        # All fallible image/build/proof/OCR work precedes provider's final native drain.
        self.scene_frame(receipt, frame, window, fresh=False)
        self.verify_scene_contract()
        need(time.monotonic() < ocr_deadline, "Original image-text validation deadline expired")
        action = ["click", "--x", str(x), "--y", str(y)]
        self.scene_click_permit = {
            "action": tuple(action),
            "window": window,
            "captured_at": captured_at,
        }
        clicked = self.provider(
            "SinglePlayer", action, mutable=True, window=window, deadline=self.anchor + 120
        )
        result = clicked.get("result", {})
        source_label = "SinglePlayer"
        if not result.get("screenshot"):
            source_label = "SceneRead"
            clicked = self.provider(
                "SceneRead", ["get-app-state"], window=window, deadline=self.anchor + 120
            )
        final = self.retain_frame(clicked, "SinglePlayerScene")
        self.scene_frame(clicked, final, window)
        need(
            time.monotonic() < self.anchor + 120,
            "Original scene navigation deadline expired after validation",
        )
        return self.measure_setup_action(clicked, final, window, source_label)

    def verify_setup_contract(self):
        return verify_setup_contract(self.job)

    def measure_setup_action(self, receipt, frame, window, source_label):
        binding = self.verify_setup_contract()
        text_binding, projection = self.verify_scene_contract()
        attempt = decode(plain(self.directory / (source_label + "-attempt.json"), maximum=65536))
        need(
            attempt.get("anchor") == self.anchor
            and type(attempt.get("elapsed")) in (int, float)
            and math.isfinite(attempt["elapsed"])
            and attempt["elapsed"] >= 0,
            "Original setup capture clock required",
        )
        captured_at = self.anchor + attempt["elapsed"]
        bound, image_path = self.scene_frame(receipt, frame, window, fresh=False)
        need(
            [bound["pixel_width"], bound["pixel_height"]] == binding["image_dimensions"]
            and [bound["window"]["width"], bound["window"]["height"]]
            == binding["window_dimensions"]
            and bound["scale"] == binding["scale"],
            "Reference/current setup geometry changed",
        )
        deadline = min(time.monotonic() + 10, self.anchor + 120, captured_at + 20)

        def tick():
            self.tick()
            need(time.monotonic() < deadline, "Original setup image deadline expired")

        def spawn(argv, **kwargs):
            tick()
            return subprocess.Popen(argv, **kwargs)

        self.sample(force=True)
        raw = bounded_child(
            [
                text_binding["helper_binary"],
                str(image_path),
                bound["sha256"],
                json.dumps(binding["policy"]["labels"]),
            ],
            self.directory,
            "SetupText",
            tick,
            deadline - time.monotonic(),
            spawn=spawn,
        )
        need(0 < len(raw) <= 131072, "Setup text output byte bound")
        rows = text_observations(
            decode(raw),
            need=need,
            image_hash=bound["sha256"],
            width=bound["pixel_width"],
            height=bound["pixel_height"],
            revision=projection["recognition_revision"],
            labels=binding["policy"]["labels"],
        )
        need(
            Counter(r["text"] for r in rows) == Counter(binding["policy"]["labels"])
            and all(r["confidence"] >= binding["policy"]["minimum_confidence"] for r in rows),
            "Unique high-confidence setup signature required",
        )
        tick()
        raw = bounded_child(
            [
                binding["helper_binary"],
                binding["reference"],
                binding["reference_sha256"],
                str(image_path),
                bound["sha256"],
                *map(str, binding["rectangle"]),
                *map(str, binding["offset"]),
            ],
            self.directory,
            "SetupMatch",
            tick,
            deadline - time.monotonic(),
            spawn=spawn,
        )
        need(0 < len(raw) <= 131072, "Setup match output byte bound")
        match = decode(raw)
        x, y = point_for_frame(
            match,
            reference_sha256=binding["reference_sha256"],
            frame=bound,
            rectangle=binding["rectangle"],
            offset=binding["offset"],
        )
        self.write(
            "setup-action-bound.json",
            {
                "match": match,
                "signature": rows,
                "snapshot": frame["snapshot"],
                "window": window,
                "captured_at": captured_at,
                "local_point": [x, y],
                "semantic_control_identity": "NOT_PROVEN",
                "no_authority_from_reference_alone": True,
            },
        )
        self.scene_frame(receipt, frame, window, fresh=False)
        self.verify_setup_contract()
        tick()
        action = ["click", "--x", str(x), "--y", str(y)]
        self.setup_action_permit = {
            "action": tuple(action),
            "window": window,
            "captured_at": captured_at,
        }
        clicked = self.provider(
            "SetupAction", action, mutable=True, window=window, deadline=deadline
        )
        if not clicked.get("result", {}).get("screenshot"):
            clicked = self.provider(
                "SetupEffectRead", ["get-app-state"], window=window, deadline=self.anchor + 120
            )
        final = self.retain_frame(clicked, "SetupEffectScene")
        self.scene_frame(clicked, final, window)
        need(time.monotonic() < self.anchor + 120, "Original setup observation deadline expired")
        return self.measure_save_dialog(clicked, final, window)

    def verify_dialog_contract(self):
        return verify_dialog_contract(self.job)

    def dialog_hotkey_guard(self, action, window):
        permit = getattr(self, "save_dialog_permit", None)
        need(
            self.job.get("purpose") == PURPOSE
            and permit is not None
            and tuple(action) == SAVE_DIALOG_ACTION == permit["action"]
            and window == permit["window"]
            and self.game.pid == permit["pid"]
            and getattr(self, "setup_action_attempted", False)
            and getattr(self, "later_frame_count", 0) == 2
            and not getattr(self, "save_dialog_attempted", False)
            and permit["snapshot"] == getattr(self, "latest_later_snapshot", None)
            and permit["identity"] in getattr(self, "scene_snapshots", set())
            and permit["focused_game_window"] is True
            and 0 <= time.monotonic() - permit["captured_at"] < 20
            and time.monotonic() < self.anchor + 120,
            "One fresh exact save-dialog hotkey observation required",
        )

    def wait_later_sample(self):
        previous, started = self.seq, time.monotonic()
        while self.seq <= previous or time.monotonic() - started < 0.25:
            self.tick()
            need(time.monotonic() < self.anchor + 120, "Original later-scene deadline expired")
            time.sleep(0.05)

    def dialog_text(self, receipt, frame, window, label, requested):
        binding = self.verify_dialog_contract()
        bound, image = self.scene_frame(receipt, frame, window, fresh=False)
        attempt = decode(plain(self.directory / (label + "-attempt.json"), maximum=65536))
        need(
            attempt.get("anchor") == self.anchor
            and type(attempt.get("elapsed")) in (int, float)
            and math.isfinite(attempt["elapsed"])
            and attempt["elapsed"] >= 0,
            "Original dialog capture clock required",
        )
        captured = self.anchor + attempt["elapsed"]
        deadline = min(time.monotonic() + 10, captured + 20, self.anchor + 120)

        def tick():
            self.tick()
            need(time.monotonic() < deadline, "Original dialog text deadline expired")

        def spawn(argv, **kwargs):
            tick()
            return subprocess.Popen(argv, **kwargs)

        raw = bounded_child(
            [binding["helper_binary"], str(image), bound["sha256"], json.dumps(requested)],
            self.directory,
            label + "Text",
            tick,
            deadline - time.monotonic(),
            spawn=spawn,
        )
        need(0 < len(raw) <= 131072, "Dialog text output byte bound")
        value = decode(raw)
        rows = text_observations(
            value,
            need=need,
            image_hash=bound["sha256"],
            width=bound["pixel_width"],
            height=bound["pixel_height"],
            revision=3,
            labels=requested,
        )
        tick()
        self.write(
            label + "-text-bound.json",
            {
                "projection": value,
                "snapshot": frame["snapshot"],
                "captured_at": captured,
                "input_authority_from_text": False,
            },
        )
        return rows, captured

    def measure_save_dialog(self, receipt, frame, window):
        self.verify_dialog_contract()
        self.later_frame_count = 0
        known_nonworld = ["Single Player", "Load Game", "Scenario Setup", "Cancel"]
        for number in (1, 2):
            self.wait_later_sample()
            label = "LaterScene" + str(number)
            receipt = self.provider(
                label, ["get-app-state"], window=window, deadline=self.anchor + 120
            )
            frame = self.retain_frame(receipt, label)
            self.scene_frame(receipt, frame, window)
            rows, captured = self.dialog_text(receipt, frame, window, label, known_nonworld)
            if rows:
                return dict(
                    status="safety_stop",
                    cause="known_nonworld_or_modal_scene_after_setup",
                    measurement_status="partial",
                    frame=frame,
                    world_loaded="unknown",
                    single_player_attempts=1,
                    setup_action_attempts=1,
                    save_dialog_attempts=0,
                    gameplay_credit=False,
                    save_reopen_credit=False,
                )
            self.later_frame_count += 1
            self.latest_later_snapshot = frame["snapshot"]
        title = frame["window"].get("title")
        focused = (
            type(title) is str
            and title
            and frame["treeText"]
            .rstrip()
            .endswith("The focused UI element is 0 standard window " + title + ".")
        )
        need(focused is True, "Current focused owned game window required for hotkey")
        before = self.manifest(self.profiles.live / "Saves")
        self.scene_frame(receipt, frame, window, fresh=False)
        self.verify_dialog_contract()
        self.save_dialog_permit = {
            "action": SAVE_DIALOG_ACTION,
            "window": window,
            "pid": self.game.pid,
            "snapshot": frame["snapshot"],
            "identity": uuid.UUID(frame["snapshot"]),
            "captured_at": captured,
            "focused_game_window": True,
        }
        clicked = self.provider(
            "SaveDialog",
            list(SAVE_DIALOG_ACTION),
            mutable=True,
            window=window,
            deadline=min(captured + 20, self.anchor + 120),
        )
        source = "SaveDialog"
        if not clicked.get("result", {}).get("screenshot"):
            source = "SaveDialogRead"
            clicked = self.provider(
                source, ["get-app-state"], window=window, deadline=self.anchor + 120
            )
        final = self.retain_frame(clicked, "SaveDialogScene")
        self.scene_frame(clicked, final, window)
        # The transport's immediate screenshot is evidence, not settled input effect.
        clicked, final, source = self.later_input_frame("SaveDialog", clicked, window)
        return self.measure_filename(clicked, final, window, source, before)

    def verify_filename_contract(self):
        return verify_filename_contract(self.job)

    def filename_input_guard(self, label, action, window):
        permit = getattr(self, "filename_permit", None)
        expected = {
            "NameSelectAll": ("hotkey", "--key", "CmdOrCtrl+A"),
            "NameType": ("type-text", "--text", self.job["filename_observation"]["token"]),
        }
        need(
            label in expected
            and permit is not None
            and self.job.get("purpose") == PURPOSE
            and permit["stage"] == label
            and tuple(action) == expected[label] == permit["action"]
            and window == permit["window"]
            and self.game.pid == permit["pid"]
            and getattr(self, "save_dialog_attempted", False)
            and getattr(self, "filename_dialog_seen", False)
            and not getattr(self, FILENAME_STAGES[label], False)
            and (label == "NameSelectAll" or getattr(self, "name_select_all_attempted", False))
            and permit["snapshot"] == getattr(self, "latest_filename_snapshot", None)
            and permit["identity"] in getattr(self, "scene_snapshots", set())
            and permit["focused_game_window"] is True
            and 0 <= time.monotonic() - permit["captured_at"] < 20
            and time.monotonic() < self.anchor + 120,
            "One fresh named filename observation input required",
        )

    def filename_text(self, receipt, frame, window, label, requested):
        binding = self.verify_filename_contract()
        bound, image = self.scene_frame(receipt, frame, window, fresh=False)
        attempt = decode(plain(self.directory / (label + "-attempt.json"), maximum=65536))
        need(
            attempt.get("anchor") == self.anchor
            and type(attempt.get("elapsed")) in (int, float)
            and math.isfinite(attempt["elapsed"])
            and attempt["elapsed"] >= 0,
            "Original filename capture clock required",
        )
        captured = self.anchor + attempt["elapsed"]
        deadline = min(time.monotonic() + 10, captured + 20, self.anchor + 120)

        def tick():
            self.tick()
            need(time.monotonic() < deadline, "Original filename observation deadline expired")

        def spawn(argv, **kwargs):
            tick()
            return subprocess.Popen(argv, **kwargs)

        raw = bounded_child(
            [binding["helper_binary"], str(image), bound["sha256"], json.dumps(requested)],
            self.directory,
            label + "CropText",
            tick,
            deadline - time.monotonic(),
            spawn=spawn,
        )
        need(0 < len(raw) <= 131072, "Filename crop output byte bound")
        value = decode(raw)
        rows = crop_text_observations(
            value,
            need=need,
            image_hash=bound["sha256"],
            width=bound["pixel_width"],
            height=bound["pixel_height"],
            revision=3,
            labels=requested,
        )
        tick()
        raw = bounded_child(
            [
                binding["matcher_binary"],
                binding["reference"],
                binding["reference_sha256"],
                str(image),
                bound["sha256"],
                *map(str, binding["rectangle"]),
                *map(str, binding["offset"]),
            ],
            self.directory,
            label + "DialogMatch",
            tick,
            deadline - time.monotonic(),
            spawn=spawn,
        )
        need(0 < len(raw) <= 131072, "Filename correspondence output byte bound")
        match = decode(raw)
        point_for_frame(
            match,
            reference_sha256=binding["reference_sha256"],
            frame=bound,
            rectangle=binding["rectangle"],
            offset=binding["offset"],
        )
        counts = Counter(r["text"] for r in rows)
        need(
            all(counts[name] == 1 for name in binding["policy"]["required_dialog_labels"])
            and counts["Choose save game to load"] == 0
            and all(r["confidence"] >= 0.95 for r in rows),
            "Current filename dialog candidate signature required",
        )
        (cancel,) = [r for r in rows if r["text"] == "Cancel"]
        px, py = match["point"]
        w, h = bound["pixel_width"], bound["pixel_height"]
        need(
            cancel["x"] * w <= px <= (cancel["x"] + cancel["width"]) * w
            and (1 - cancel["y"] - cancel["height"]) * h <= py <= (1 - cancel["y"]) * h,
            "Unique exact correspondence must join the current Cancel observation",
        )
        self.write(
            label + "-filename-observation.json",
            {
                "crop_projection": value,
                "source_rows": rows,
                "correspondence": match,
                "snapshot": frame["snapshot"],
                "captured_at": captured,
                "field_focus_proved": False,
                "input_authority_from_component": False,
            },
        )
        self.scene_frame(receipt, frame, window, fresh=False)
        tick()
        return rows, captured

    def later_input_frame(self, label, receipt, window):
        flags = {"SaveDialog": "save_dialog_attempted", **FILENAME_STAGES}
        need(
            label in flags
            and self.job.get("purpose") == PURPOSE
            and getattr(self, flags[label], False),
            "One completed named keyboard attempt required",
        )
        need(
            receipt.get("_meta", {}).get("runtimeId")
            == self.job["actor_binding"]["provider_runtime_id"]
            and receipt.get("result", {}).get("snapshot", {}).get("app", {}).get("pid")
            == self.game.pid
            and receipt.get("result", {}).get("snapshot", {}).get("window", {}).get("id") == window,
            "Current owned input receipt required before later observation",
        )
        used = getattr(self, "post_input_reads", set())
        need(label not in used, "Later keyboard observation already consumed")
        self.post_input_reads = used | {label}
        previous, started = self.seq, time.monotonic()
        self.wait_later_sample()
        need(
            self.seq > previous
            and time.monotonic() - started >= 0.25
            and time.monotonic() < self.anchor + 120,
            "Real later sample and original observation clock required",
        )
        after_wait = self.seq
        read_label = label + "Later"
        value = self.provider(
            read_label, ["get-app-state"], window=window, deadline=self.anchor + 120
        )
        frame = self.retain_frame(value, read_label + "Scene")
        self.scene_frame(value, frame, window)
        self.write(
            read_label + "-sample-binding.json",
            {
                "input_stage": label,
                "previous_seq": previous,
                "after_wait_seq": after_wait,
                "captured_seq": self.seq,
                "anchor": self.anchor,
                "elapsed": time.monotonic() - self.anchor,
                "snapshot": frame["snapshot"],
                "ui_effect_authority_from_sample": False,
                "input_repeated": False,
            },
        )
        return value, frame, read_label

    def filename_permit_for(self, label, action, receipt, frame, window, captured):
        self.scene_frame(receipt, frame, window, fresh=False)
        title = frame["window"].get("title")
        focused = (
            type(title) is str
            and title
            and frame["treeText"]
            .rstrip()
            .endswith("The focused UI element is 0 standard window " + title + ".")
        )
        need(
            focused is True,
            "Current owned window focus required; text field focus remains empirical",
        )
        self.latest_filename_snapshot = frame["snapshot"]
        self.filename_permit = {
            "stage": label,
            "action": tuple(action),
            "window": window,
            "pid": self.game.pid,
            "snapshot": frame["snapshot"],
            "identity": uuid.UUID(frame["snapshot"]),
            "captured_at": captured,
            "focused_game_window": True,
        }

    def measure_filename(self, receipt, frame, window, source, before):
        binding = self.verify_filename_contract()
        token = binding["token"]
        labels = ["File name:", "Cancel", "Choose save game to load", token]
        rows, captured = self.filename_text(receipt, frame, window, source, labels)
        need(not any(r["text"] == token for r in rows), "New filename token already visible")
        self.filename_dialog_seen = True
        for label, action in [
            ("NameSelectAll", ["hotkey", "--key", "CmdOrCtrl+A"]),
            ("NameType", ["type-text", "--text", token]),
        ]:
            self.verify_filename_contract()
            self.filename_permit_for(label, action, receipt, frame, window, captured)
            clicked = self.provider(
                label,
                action,
                mutable=True,
                window=window,
                deadline=min(captured + 20, self.anchor + 120),
            )
            capture_label = label
            if not clicked.get("result", {}).get("screenshot"):
                capture_label = label + "Read"
                clicked = self.provider(
                    capture_label, ["get-app-state"], window=window, deadline=self.anchor + 120
                )
            frame = self.retain_frame(clicked, label + "Scene")
            self.scene_frame(clicked, frame, window)
            clicked, frame, capture_label = self.later_input_frame(label, clicked, window)
            receipt = clicked
            rows, captured = self.filename_text(receipt, frame, window, capture_label, labels)
            if label == "NameSelectAll":
                need(
                    not any(r["text"] == token for r in rows),
                    "Token appeared before the one named type input",
                )
        observed = filename_row_effect(rows, token)
        return dict(
            status="safety_stop",
            measurement_status="partial",
            cause="filename_token_in_declared_row_observed"
            if observed
            else "filename_effect_unclassified",
            frame=frame,
            filename_token_observed_in_declared_row=observed,
            single_player_attempts=1,
            setup_action_attempts=1,
            save_dialog_attempts=1,
            select_all_attempts=1,
            type_text_attempts=1,
            save_commit_attempted=False,
            save_namespace_unchanged=before == self.manifest(self.profiles.live / "Saves"),
            field_focus="empirical_effect_only" if observed else "unknown",
            scene_identity="filename_dialog_candidate",
            world_loaded="unknown",
            gameplay_credit=False,
            save_reopen_credit=False,
        )

    def persist_outcome(self, outcome):
        # Close the observation-to-outcome gap without losing unconditional stop
        # when state persistence or validation fails.
        try:
            self.verify_available()
        except UserCancelled:
            outcome.clear()
            outcome.update(cancelled_outcome())
        self.journal.outcome(outcome)
        need(
            self.journal.read("raw_outcome", required=True)["payload"]["cause"] == outcome["cause"],
            "Raw outcome readback mismatch",
        )

    def stop(self):
        try:
            self.verify_cleanup_owner()
            return self.stop_owned()
        finally:
            # Even ownership refusal must settle our direct observer child.
            # Never leave it armed to signal after a failed ownership fence.
            if self.observer is not None:
                if self.observer.poll() is None:
                    self.observer.terminate()
                    try:
                        self.observer.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.observer.kill()
                        self.observer.wait(timeout=2)
                if not self.helper_log.closed:
                    self.helper_log.flush()
                    os.fsync(self.helper_log.fileno())
                    self.helper_log.close()

    def stop_owned(self):
        # Do not run GUI, relaunch or wait for a model decision here.
        self.phases["stop_started"] = time.monotonic()
        if self.game is not None:
            current = self.monitor.inspect(self.game.pid)
            if current is not None:
                need(self.game.same(current), "Exact game identity changed; stop refused")
                self.verify_cleanup_owner()
                with self.control.cleanup_guard():
                    self.monitor.stop(self.game)
            need(self.monitor.inspect(self.game.pid) is None, "Exact game absence not proven")
        elif self.open_attempted:
            # There is no admitted game identity to signal. Settle our own
            # prebinding helper, preserve the diagnostic profile and refuse
            # rollback; current absence does not cancel LaunchServices.
            if self.observer is not None and self.observer.poll() is None:
                self.observer.terminate()
                self.observer.wait(timeout=3)
            raise Refused("Asynchronous launch unbound; absence is not cancellation")
        self.require_stopped()
        if self.observer is not None:
            if self.game is not None:
                try:
                    self.observer.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Exact game exit already settles the owned launch. Only
                    # our remaining direct helper child is terminated here.
                    self.observer.terminate()
                    self.observer.wait(timeout=3)
            elif self.observer.poll() is None:
                self.observer.terminate()
                self.observer.wait(timeout=3)
            need(self.observer.poll() is not None, "Observer unsettled")
            if not self.helper_log.closed:
                self.helper_log.flush()
                os.fsync(self.helper_log.fileno())
                self.helper_log.close()
        exits = []
        from frozen_mac_processes import MacProcesses

        for row in self.creation:
            need(
                MacProcesses(row["executable"]).inspect(row["pid"]) is None,
                "Owned child still present",
            )
            exits.append({**row, "positive_exit": True, "method": "fresh_exact_pid_absence"})
        self.write(
            "positive-exits.json",
            {
                "children": exits,
                "game_absent": True,
                "uncertain_input": self.uncertain_input,
                "elapsed": time.monotonic() - self.anchor,
            },
        )
        self.phases["stop_complete"] = time.monotonic()
        if self.journal.read("raw_outcome") is not None:
            self.journal.append(
                "stopped", {"game_absent": True, "elapsed_seconds": time.monotonic() - self.anchor}
            )
        return True

    def require_settled_input(self):
        need(not self.uncertain_input, "Unsettled mutable provider operation; restoration refused")

    def restore(self):
        from smr_launcher.activation import _fsync_dir
        from smr_launcher.resource_identity import fingerprint_resources

        self.verify_cleanup_owner()
        self.require_settled_input()
        self.phases["restoration_started"] = time.monotonic()
        need(time.monotonic() - self.anchor < 900, "Original restoration deadline exceeded")
        self.require_stopped()
        need(not self.profiles.journal.exists(), "Activation uncertainty; no restoration replay")
        need(self.baseline is not None, "No complete preservation baseline; restoration refused")
        p = self.profiles
        current = p._state()["active"]
        if current == self.diagnostic:
            stored = p._profile_path(self.job["return_profile"])
            need(
                self.manifest(stored) == self.baseline["live"]["manifest"]
                and self.metadata(stored) == self.baseline["live"]["metadata"],
                "Return profile changed",
            )
            # Keep every post-test save/log in the diagnostic profile. Nothing
            # is restored over its saves, and no save is merged or deleted.
            self.verify_cleanup_owner()
            p.switch(self.job["return_profile"], mutation_guard=self.control.cleanup_guard)
        else:
            need(
                current == self.job["return_profile"] and not self.open_attempted,
                "Unexpected active profile; restoration refused",
            )
        need(
            self.manifest(p.live) == self.baseline["live"]["manifest"]
            and self.metadata(p.live) == self.baseline["live"]["metadata"],
            "Return profile readback mismatch",
        )
        for index, row in enumerate(self.preferences):
            path, copy = Path(row["path"]), Path(row["copy"])
            before = plain(path, 262144)
            old = plain(copy, 262144)
            need(digest(old) == row["sha256"], "Preference preimage changed")
            shutil.copy2(path, self.directory / ("preference-post-" + str(index)))
            if before != old:
                need(path.name == "Preferences Data", "Unexpected global preference bytes changed")
            if (
                before != old
                or stat.S_IMODE(path.stat().st_mode) != row["mode"]
                or path.stat().st_mtime_ns != row["mtime_ns"]
            ):
                self.require_stopped()
                tmp = path.with_name(path.name + ".finite-calibration-" + self.job["job"])
                need(not tmp.exists(), "Preference restoration collision")
                shutil.copy2(copy, tmp)
                with tmp.open("rb") as stream:
                    os.fsync(stream.fileno())
                need(path.read_bytes() == before, "Preference changed during restoration")
                self.verify_cleanup_owner()
                with self.control.cleanup_guard():
                    os.replace(tmp, path)
                _fsync_dir(path.parent)
            need(
                digest(path.read_bytes()) == row["sha256"]
                and stat.S_IMODE(path.stat().st_mode) == row["mode"]
                and path.stat().st_mtime_ns == row["mtime_ns"],
                "Preference readback failed",
            )
        canonical = p._profile_path(self.job["source_profile"])
        need(
            self.manifest(canonical) == self.baseline["canonical"]["manifest"]
            and self.metadata(canonical) == self.baseline["canonical"]["metadata"],
            "Canonical profile changed",
        )
        for role, path in self.original_paths.items():
            need(
                self.manifest(path) == self.baseline[role]["manifest"]
                and self.metadata(path) == self.baseline[role]["metadata"],
                "Protected input changed: " + role,
            )
        need(
            fingerprint_resources({"installed_assets": self.installed_assets})["identity"]
            == self.job["installed_assets_identity"],
            "Installed dependency preservation changed",
        )
        self.require_stopped()
        self.verify_code()
        self.write(
            "restoration-readback.json",
            {
                "restored": True,
                "return_profile": self.job["return_profile"],
                "game_absent": True,
                "journal_absent": not p.journal.exists(),
                "all_new_saves_retained_in": self.diagnostic,
                "elapsed": time.monotonic() - self.anchor,
            },
        )
        self.journal.append(
            "restored", {"restored": True, "elapsed_seconds": time.monotonic() - self.anchor}
        )
        self.phases["restoration_complete"] = time.monotonic()
        return True

    def finish(self, outcome, stopped, restored):
        from runner_journal import result_row
        from smr_launcher.scenario_evidence import digest as object_digest

        # A late command may change terminal disposition, but a failed drain
        # must leave the final barrier pending rather than rewrite raw facts.
        with self.control.finalization_guard():
            if self.control.cancelled:
                # Preserve immutable raw observation facts; terminal disposition is
                # user-requested even if the command arrived during cleanup.
                outcome.clear()
                outcome.update(cancelled_outcome())
            row = result_row(self.journal)
            self.write("result-index.json", row)
            self.write(
                "phase-times.json",
                {
                    "anchor": self.anchor,
                    "events_monotonic": self.phases,
                    "elapsed_seconds": time.monotonic() - self.anchor,
                },
            )
            self.write(
                "completion-ready.json",
                {
                    "children_stopped_restored_indexed": True,
                    "terminal_finished": "pending final durable journal append",
                    "result": row,
                    "terminal_outcome": outcome,
                    "cancelled": self.control.cancelled,
                    "calibration_frames_observed": outcome.get("cause")
                    == "provider_calibration_frame_observed",
                    "world_loaded": "unknown",
                    "gameplay_credit": False,
                    "save_reopen_credit": False,
                },
            )
            # This is the final fallible durable operation. No later required write
            # may leave a success-shaped terminal barrier after a failed index.
            self.journal.append(
                "terminal_finished",
                {"disposition_identity": object_digest(row), "no_successor": True},
            )
