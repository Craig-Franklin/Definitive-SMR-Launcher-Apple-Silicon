"""One finite packaged-backend Play/menu trial, using the existing driver owner.

This deliberately separate catalogue path never creates or registers a profile.
Its three declared mutable files are Settings.ini and the two game preferences.
Existing saves/assets remain exact; newly created files stay in their own edition.
"""

from contextlib import contextmanager, nullcontext
from dataclasses import asdict
import os
import math
import re
from pathlib import Path
from queue import Empty, Queue
import shutil
import stat
import subprocess
import sys
from threading import Thread
import time
import uuid

from active_job_control import decode, read_plain, require, sha
from smr_launcher.activation import _assert_no_symlink_ancestor, _assert_regular_tree, _fsync_dir
from smr_launcher.packages import _digest
from smr_launcher.scenario_evidence import canonical
from unique_save_file import signature

PURPOSE = "ONE_AGENT_PACKAGED_CATALOGUE_MENU_V1"
CACHED_PURPOSE = "ONE_AGENT_PACKAGED_CACHED_MENU_V1"
LOAD_PURPOSE = "ONE_AGENT_PACKAGED_CACHED_LOAD_V1"
DIRECT_LOAD_PURPOSE = "ONE_AGENT_PACKAGED_CATALOGUE_LOAD_V1"
SAVE_PURPOSE = "ONE_AGENT_PACKAGED_CACHED_SAVE_REOPEN_V1"
GAMEPLAY_PURPOSE = "ONE_AGENT_PACKAGED_CACHED_GAMEPLAY_V1"
GUI_PURPOSE = "ONE_AGENT_NATIVE_GUI_CATALOGUE_MENU_V1"
OWNED_AUTOSAVE = "Saves/Auto0.SMRailroadsSavedGame"


class MenuIncomplete(ValueError):
    """Reserve the original quit interval instead of more menu exploration."""


def file_facts(path):
    """Content and stable file metadata, excluding access/rename-sensitive ctime."""
    path = Path(path)
    _assert_no_symlink_ancestor(path)
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 2 * 1024**3,
        "Bounded plain preservation file required",
    )
    digest = _digest(path)
    require(signature(path.lstat()) == signature(info), "Preservation file changed while hashing")
    return dict(
        sha256=digest,
        bytes=info.st_size,
        inode=info.st_ino,
        mode=stat.S_IMODE(info.st_mode),
        mtime_ns=info.st_mtime_ns,
    )


def tree_files(root):
    root = Path(root)
    _assert_regular_tree(root)
    result = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_file():
            facts = file_facts(path)
            total += facts["bytes"]
            require(len(result) < 25000 and total <= 2 * 1024**3, "Preservation bound exceeded")
            result[path.relative_to(root).as_posix()] = facts
    return result


def existing_target_unchanged(root, before):
    """Allow new files and log changes, never changed/deleted pre-existing saves."""
    actual = tree_files(root)
    require(
        all(
            actual.get(name) == facts
            for name, facts in before.items()
            if name != "Settings.ini" and not name.startswith("Logs/")
        ),
        "Existing target file changed; preserve for inspection",
    )
    return actual


def autosave_origin(job):
    """Accept the exact owned autosave from an independently settled trial."""
    config = job["catalogue_menu"]
    proof = config["owned_autosave"]
    require(
        set(proof) == {"origin_readback", "origin_sha256", "facts"},
        "Exact autosave origin required",
    )
    raw = read_plain(proof["origin_readback"])
    require(sha(raw) == proof["origin_sha256"], "Owned autosave origin changed")
    origin = decode(raw)
    service = origin["service_settled"]
    if type(service) is list:
        # A failed second-launch attempt does not invalidate the first game's
        # observed normal exit or the root's preservation/lease settlement.
        require(len(service) == 2, "Exact settled two-service origin required")
        first, second = service
        if origin["invocation"]["returncode"] == 0:
            expected = {
                key: config["selection"][key]
                for key in ("profile_id", "archive_sha256", "variant_id")
            }
            expected["game_executable_sha256"] = job["game_sha256"]
            exits = (origin.get("exact_normal_exit"), origin.get("second_observer"))
            completed = (
                type(origin["invocation"]["returncode"]) is int
                and type(origin.get("job")) is str
                and bool(origin["job"])
                and origin.get("both_normal_quits_verified") is True
                and type(origin.get("current_test_autosave")) is dict
                and all(
                    origin.get(key) is True
                    for key in (
                        "lease_released",
                        "preservation_verified",
                        "terminal_finished",
                        "game_absent",
                    )
                )
                and all(
                    type(row) is dict
                    and type(row.get("pid")) is int
                    and row["pid"] > 0
                    and row.get("commit_sent") is True
                    and row.get("eof_closed") is True
                    and row.get("reaped") is True
                    and type(row.get("returncode")) is int
                    and row["returncode"] == 0
                    and "pending" in row
                    and row["pending"] is None
                    and type(row.get("commit_response")) is dict
                    and row["commit_response"].get("ok") is True
                    and type(row["commit_response"].get("data")) is dict
                    and row["commit_response"]["data"].get("target") == expected
                    and row["commit_response"]["data"].get("launched_profile_id")
                    == config["target"]["profile_id"]
                    for row in service
                )
                and first["pid"] != second["pid"]
                and all(
                    type(receipt) is dict
                    and receipt.get("job") == origin["job"]
                    and type(receipt.get("process")) is dict
                    and type(receipt["process"].get("pid")) is int
                    and receipt["process"]["pid"] > 0
                    and type(receipt["process"].get("birth")) is str
                    and receipt["process"]["birth"].isdigit()
                    and type(receipt["process"].get("executable")) is str
                    and Path(receipt["process"]["executable"]).is_absolute()
                    and type(receipt.get("result")) is dict
                    and receipt["result"].get("state") == "observed_exited"
                    and receipt["result"].get("terminal") is True
                    and receipt["result"].get("stop_requested") is False
                    for receipt in exits
                )
                and exits[0]["process"]["pid"] != exits[1]["process"]["pid"]
                and exits[0]["process"]["executable"] == exits[1]["process"]["executable"]
                and type(origin.get("owned_absences")) is list
                and all(
                    any(
                        type(row) is dict
                        and row.get("pid") == pid
                        and row.get("role") == role
                        and row.get("positive_absence") is True
                        for row in origin["owned_absences"]
                    )
                    for pid, role in (
                        (first["pid"], "packaged_service"),
                        (second["pid"], "packaged_service"),
                        (exits[0]["process"]["pid"], "game"),
                        (exits[1]["process"]["pid"], "game"),
                    )
                )
            )
            require(
                completed, "Successful autosave origin requires exact launches and normal exits"
            )
        else:
            exit_result = origin["exact_normal_exit"]["result"]
            response = second["commit_response"]
            observed = origin.get("second_observer", {})
            pid = observed.get("process", {}).get("pid")
            second_owned_exit = (
                second["commit_sent"] is True
                and type(response) is dict
                and response.get("ok") is True
                and response["data"]["launched_profile_id"] == config["target"]["profile_id"]
                and type(origin.get("job")) is str
                and observed.get("job") == origin["job"]
                and type(pid) is int
                and pid > 0
                and pid != origin["exact_normal_exit"].get("process", {}).get("pid")
                and observed.get("result", {}).get("state") == "observed_exited"
                and observed["result"]["terminal"] is True
                and any(
                    row.get("pid") == pid
                    and row.get("role") == "game"
                    and row.get("positive_absence") is True
                    for row in origin.get("owned_absences", [])
                )
            )
            completed = (
                origin["invocation"]["returncode"] == 2
                and first["commit_sent"] is True
                and first["commit_response"]["ok"] is True
                and ((second["commit_sent"] is False and response is None) or second_owned_exit)
                and all(
                    row["reaped"] is True
                    and row["returncode"] == 0
                    and row["pending"] is None
                    and row["eof_closed"] is True
                    for row in service
                )
                and exit_result["state"] == "observed_exited"
                and exit_result["stop_requested"] is False
                and exit_result["terminal"] is True
            )
        facts = origin["current_test_autosave"]
        service = first
    else:
        completed = origin["invocation"]["returncode"] == 0
        facts = origin["new_target_files_retained"].get(OWNED_AUTOSAVE)
    require(
        origin["lease_released"] is True
        and origin["preservation_verified"] is True
        and origin["terminal_finished"] is True
        and origin["game_absent"] is True
        and service["reaped"] is True
        and completed
        and service["commit_response"]["data"]["launched_profile_id"]
        == config["target"]["profile_id"]
        and facts == proof["facts"]
        and config["preservation"]["target_files"].get(OWNED_AUTOSAVE) == proof["facts"]
        and 0 < proof["facts"]["bytes"] <= 512 * 1024**2,
        "Autosave must be the exact retained new file from a settled owned trial",
    )
    return proof


def target_files_unchanged(owner, root):
    before = owner.baseline["target_files"]
    if owner.job.get("purpose") == GAMEPLAY_PURPOSE:
        proof = gameplay_origin(owner.job)
        archives = owner.gameplay_autosave_preimages
        require(set(archives) == set(proof["autosaves"]), "Test autosave preimages incomplete")
        before = dict(before)
        for name, facts in proof["autosaves"].items():
            actual = file_facts(archives[name])
            require(
                all(actual[k] == facts[k] for k in ("sha256", "bytes", "mode", "mtime_ns")),
                "Test autosave preimage changed",
            )
            before.pop("Saves/" + name)
    archive = getattr(owner, "autosave_preimage", None)
    if owner.job.get("purpose") == SAVE_PURPOSE and archive is not None:
        proof = autosave_origin(owner.job)
        actual = file_facts(archive)
        require(
            all(actual[k] == proof["facts"][k] for k in ("sha256", "bytes", "mode", "mtime_ns")),
            "Owned autosave preimage changed; preserve for inspection",
        )
        before = {name: facts for name, facts in before.items() if name != OWNED_AUTOSAVE}
    return existing_target_unchanged(root, before)


def preserve_owned_autosave(owner, root):
    proof = autosave_origin(owner.job)
    source = root / OWNED_AUTOSAVE
    copy = owner.directory / "owned-Auto0-preimage"
    return preserve_autosave_preimage(source, copy, proof["facts"])


def preserve_autosave_preimage(source, copy, facts):
    """Retain the exact concretely threatened test autosave before launching."""
    require(not copy.exists() and not copy.is_symlink(), "Autosave preimage collision")
    require(file_facts(source) == facts, "Owned autosave changed before preservation")
    shutil.copy2(source, copy)
    with copy.open("r+b") as stream:
        os.fsync(stream.fileno())
    _fsync_dir(copy.parent)
    require(file_facts(source) == facts, "Owned autosave changed while preserving")
    copied = file_facts(copy)
    require(
        all(copied[k] == facts[k] for k in ("sha256", "bytes", "mode", "mtime_ns")),
        "Owned autosave preimage differs",
    )
    return copy


def gameplay_origin(job):
    """Bind one retained test save and its proven autosaves, never a user-save exception."""
    config = job["catalogue_menu"]
    proof = config["resume_save"]
    require(
        set(proof)
        in (
            {"token", "filename", "facts", "autosaves", "origin_readback", "origin_sha256"},
            {
                "token",
                "filename",
                "facts",
                "autosaves",
                "origin_readback",
                "origin_sha256",
                "autosave_origin",
            },
        )
        and type(proof["token"]) is str
        and re.fullmatch(r"[A-Za-z0-9]{19}", proof["token"])
        and proof["filename"] == proof["token"] + ".SMRailroadsSavedGame",
        "Exact retained test-save identity required",
    )
    raw = read_plain(proof["origin_readback"])
    require(sha(raw) == proof["origin_sha256"], "Retained test-save origin changed")
    require(
        config["preservation"]["immutable"].get(proof["origin_readback"])
        == file_facts(proof["origin_readback"]),
        "Retained test-save origin must be in the immutable baseline",
    )
    origin = decode(raw)
    services = origin["service_settled"]
    exits = (origin["exact_normal_exit"], origin["second_observer"])
    expected = dict(
        (key, config["selection"][key]) for key in ("profile_id", "archive_sha256", "variant_id")
    )
    expected["game_executable_sha256"] = job["game_sha256"]
    require(
        origin["invocation"]["returncode"] == 0
        and origin["lease_released"] is True
        and origin["preservation_verified"] is True
        and origin["terminal_finished"] is True
        and origin["game_absent"] is True
        and origin["first_normal_exit"] is True
        and origin["final_normal_exit"] is True
        and type(services) is list
        and len(services) == 2
        and all(
            row["commit_sent"] is True
            and row["eof_closed"] is True
            and row["reaped"] is True
            and row["returncode"] == 0
            and row["pending"] is None
            and row["commit_response"]["ok"] is True
            and row["commit_response"]["data"]["target"] == expected
            and row["commit_response"]["data"]["launched_profile_id"]
            == config["target"]["profile_id"]
            for row in services
        )
        and exits[0]["process"]["pid"] != exits[1]["process"]["pid"]
        and all(
            type(receipt["process"]["pid"]) is int
            and receipt["process"]["pid"] > 0
            and type(receipt["process"]["birth"]) is str
            and receipt["process"]["birth"].isdigit()
            and receipt["process"]["executable"] == exits[0]["process"]["executable"]
            and Path(receipt["process"]["executable"]).is_absolute()
            and receipt["job"] == origin["job"]
            and receipt["result"]["state"] == "observed_exited"
            and receipt["result"]["terminal"] is True
            and receipt["result"]["stop_requested"] is False
            and any(
                row["pid"] == receipt["process"]["pid"]
                and row["role"] == "game"
                and row["positive_absence"] is True
                for row in origin["owned_absences"]
            )
            for receipt in exits
        ),
        "Retained test save requires settled packaged launches and both normal exits",
    )
    old = config["preservation"]["target_files"]
    named = "Saves/" + proof["filename"]
    require(
        type(proof["facts"]) is dict
        and set(proof["facts"]) == {"sha256", "bytes", "inode", "mode", "mtime_ns"}
        and type(proof["facts"]["bytes"]) is int
        and 0 < proof["facts"]["bytes"] <= 512 * 1024**2
        and proof["facts"]
        == origin["exact_new_named_save"]
        == origin["new_target_files_retained"].get(named)
        == old.get(named),
        "Retained named save changed or was not created by that trial",
    )
    slots = {f"Auto{i}.SMRailroadsSavedGame" for i in range(5)}
    require(
        type(proof["autosaves"]) is dict
        and set(proof["autosaves"]).issubset(slots)
        and {name for name in slots if "Saves/" + name in old} == set(proof["autosaves"]),
        "Every pre-existing test autosave needs exact owned origin",
    )
    observed_autosaves = {
        name: origin["current_test_autosave"]
        if name == "Auto0.SMRailroadsSavedGame"
        else origin["new_target_files_retained"].get("Saves/" + name)
        for name in slots
    }
    if "autosave_origin" in proof:
        # A later owned gameplay run may rotate test slots. Keep the original
        # two-launch named-save proof above; bind the current slots separately.
        reference = proof["autosave_origin"]
        require(
            type(reference) is dict and set(reference) == {"path", "sha256"},
            "Exact gameplay autosave origin required",
        )
        raw = read_plain(reference["path"])
        require(
            sha(raw) == reference["sha256"]
            and config["preservation"]["immutable"].get(reference["path"])
            == file_facts(reference["path"]),
            "Gameplay autosave origin changed or missing from immutable baseline",
        )
        recent = decode(raw)
        service, exited = recent["service_settled"], recent["exact_normal_exit"]
        process = exited["process"]
        require(
            recent["job"] != origin["job"]
            and recent["invocation"]["returncode"] == 0
            and all(
                recent[key] is True
                for key in (
                    "lease_released",
                    "preservation_verified",
                    "terminal_finished",
                    "game_absent",
                    "normal_exit",
                )
            )
            and recent["exact_retained_named_save"] == proof["facts"]
            and service["commit_sent"] is True
            and service["eof_closed"] is True
            and service["reaped"] is True
            and service["returncode"] == 0
            and service["pending"] is None
            and service["commit_response"]["ok"] is True
            and service["commit_response"]["data"]["target"] == expected
            and service["commit_response"]["data"]["launched_profile_id"]
            == config["target"]["profile_id"]
            and type(process["pid"]) is int
            and process["pid"] > 0
            and type(process["birth"]) is str
            and process["birth"].isdigit()
            and process["executable"] == exits[0]["process"]["executable"]
            and exited["job"] == recent["job"]
            and exited["result"]["state"] == "observed_exited"
            and exited["result"]["terminal"] is True
            and exited["result"]["stop_requested"] is False
            and all(
                any(
                    row["pid"] == pid and row["role"] == role and row["positive_absence"] is True
                    for row in recent["owned_absences"]
                )
                for pid, role in ((process["pid"], "game"), (service["pid"], "packaged_service"))
            ),
            "Test autosaves require a settled exact owned gameplay normal exit",
        )
        observed_autosaves = {
            name: recent["current_test_autosaves"].get(
                name, recent["new_target_files_retained"].get("Saves/" + name)
            )
            for name in slots
        }
    for name, facts in proof["autosaves"].items():
        observed = observed_autosaves[name]
        require(
            facts == observed == old.get("Saves/" + name) and 0 < facts["bytes"] <= 512 * 1024**2,
            "Test autosave was not retained from the exact settled trial",
        )
    return proof


def sibling_saves(app, target_id, return_id):
    return {
        record.profile_id: tree_files(app.profiles._profile_path(record.profile_id) / "Saves")
        for record in app.catalogue()
        if record.profile_id not in (target_id, return_id)
    }


def verify_sibling_saves(owner):
    current = sibling_saves(
        owner.app, owner.config["target"]["profile_id"], owner.job["return_profile"]
    )
    require(
        all(
            profile in current
            and all(current[profile].get(name) == facts for name, facts in rows.items())
            for profile, rows in owner.baseline["sibling_saves"].items()
        ),
        "Existing sibling save changed; preserve for inspection",
    )


def verify_config(job):
    config = job["catalogue_menu"]
    if "window_probe" in config:
        verify_window_probe(job)
    target = config["target"]
    require(
        job["purpose"]
        in (
            PURPOSE,
            CACHED_PURPOSE,
            LOAD_PURPOSE,
            DIRECT_LOAD_PURPOSE,
            SAVE_PURPOSE,
            GAMEPLAY_PURPOSE,
            GUI_PURPOSE,
        ),
        "Closed catalogue purpose required",
    )
    cached = job["purpose"] in (CACHED_PURPOSE, LOAD_PURPOSE, SAVE_PURPOSE, GAMEPLAY_PURPOSE)
    if job["purpose"] == GAMEPLAY_PURPOSE:
        require(
            "agent_save" not in job and "owned_autosave" not in config,
            "Gameplay resumes one retained save without new-save intent",
        )
        gameplay_origin(job)
    if job["purpose"] in (LOAD_PURPOSE, DIRECT_LOAD_PURPOSE, SAVE_PURPOSE):
        title = config.get("scenario_title")
        require(
            type(title) is str
            and 0 < len(title) <= 256
            and title.strip() == title
            and all(ord(c) >= 32 and ord(c) != 127 for c in title),
            "Exact reviewed rendered scenario title required",
        )
        saves = {
            name for name in config["preservation"]["target_files"] if name.startswith("Saves/")
        }
        if job["purpose"] == SAVE_PURPOSE and saves:
            require(
                OWNED_AUTOSAVE in saves
                and not saves.intersection(
                    f"Saves/Auto{i}.SMRailroadsSavedGame" for i in range(1, 5)
                ),
                "Only the proven owned autosave may rotate; pretrial Auto1..4 refuse",
            )
            autosave_origin(job)
        elif job["purpose"] == SAVE_PURPOSE:
            require(
                "owned_autosave" not in config,
                "Empty target must not declare a prior autosave",
            )
        else:
            require(not saves, "First scenario load requires a target without pre-existing saves")
    direct_load = job["purpose"] == DIRECT_LOAD_PURPOSE
    if direct_load:
        require(
            "agent_save" not in job
            and not {"selection", "preparation", "owned_autosave", "resume_save"}.intersection(
                config
            ),
            "Direct catalogue load cannot prepare, resume or save",
        )
        subtitle = config.get("scenario_subtitle")
        require(
            type(subtitle) is str
            and 0 < len(subtitle) <= 256
            and subtitle.strip() == subtitle
            and all(ord(c) >= 32 and ord(c) != 127 for c in subtitle),
            "Exact reviewed scenario subtitle required",
        )
    require(
        set(target) == {"profile_id", "archive_sha256", "variant_id", "scenarios", "assets_sha256"},
        "Exact catalogue target fields required",
    )
    require(
        target["profile_id"] == "map-" + target["variant_id"]
        and target["profile_id"] != job["return_profile"]
        and (
            len(target["scenarios"]) in (1, 2)
            and len({Path(s).name.casefold() for s in target["scenarios"]})
            == len(target["scenarios"])
            if direct_load
            else len(target["scenarios"]) == 1
        ),
        "Distinct edition with exact scenario selection required",
    )
    require(
        config["prepare_compatibility"] is cached
        and type(config["warning"]) is str
        and (
            0 < len(config["warning"]) <= 16384
            if cached or job["purpose"] == GUI_PURPOSE
            else len(config["warning"]) <= 16384
            if direct_load
            else config["warning"] == ""
        ),
        "Exact direct or cached preparation/warning contract required",
    )
    if cached:
        selection = config["selection"]
        require(
            set(selection) == {"profile_id", "archive_sha256", "variant_id", "scenarios"}
            and selection["profile_id"] == "map-" + selection["variant_id"]
            and selection["profile_id"] not in (target["profile_id"], job["return_profile"])
            and selection["archive_sha256"] == target["archive_sha256"]
            and selection["scenarios"] == target["scenarios"]
            and config["preparation"]["cached_profile_id"] == target["profile_id"],
            "Exact original-to-cached selection required",
        )
    for key in ("archive_sha256", "variant_id", "assets_sha256"):
        require(
            len(target[key]) == 64 and all(c in "0123456789abcdef" for c in target[key]),
            "Exact target digest required",
        )
    source = job["source_original"]
    require(
        source["archive_sha256"] == target["archive_sha256"]
        and source["variant_id"] == target["variant_id"]
        and (
            source["scenario"] in target["scenarios"]
            if direct_load
            else source["scenario"] == target["scenarios"][0]
        ),
        "Journal target correspondence required",
    )
    package = config["package"]
    receipt = decode(read_plain(package["receipt_path"]))
    if package.get("artifact_mode") == "local-development":
        from native_gui_menu import verify_local_artifact

        verify_local_artifact(job, receipt)
    else:
        require(
            "artifact_mode" not in package and "artifact_mode" not in receipt,
            "Artifact mode refused",
        )
        require(
            sha(read_plain(package["receipt_path"])) == package["receipt_sha256"]
            and receipt["source_commit"] == package["source_commit"]
            and receipt["tag"] == package["tag"]
            and receipt["zip_sha256"] == package["zip_sha256"]
            and receipt["codesign_verified"] is True
            and receipt["gatekeeper_accepted"] is True,
            "Exact released artifact required",
        )
    bundle = Path(package["bundle"])
    helper = bundle / "Contents/MacOS/Definitive SMR Launcher Apple Silicon"
    require(
        str(helper) == package["executable"]
        and sha(read_plain(helper, maximum=128 * 1024**2)) == package["executable_sha256"],
        "Exact packaged backend helper required",
    )
    if job["purpose"] == GUI_PURPOSE:
        from native_gui_menu import verify_gui_config

        verify_gui_config(job)
    return config


def verify_window_probe(job):
    """Only an exact read-only window metadata helper in a load-only job."""
    probe = job["catalogue_menu"]["window_probe"]
    require(
        job["purpose"] in (LOAD_PURPOSE, DIRECT_LOAD_PURPOSE), "Window observation is load-only"
    )
    require(
        set(probe) == {"executable", "executable_sha256", "source", "source_sha256"},
        "Exact window probe binding required",
    )
    for role, name in (("executable", "WindowProbe"), ("source", "WindowProbe.swift")):
        path = Path(probe[role])
        data = read_plain(path)
        require(
            path.name == name
            and sha(data) == probe[role + "_sha256"]
            and any(
                row == dict(path=str(path), bytes=len(data), sha256=sha(data))
                for row in job["closure"]
            ),
            "Window probe closure drift",
        )
    require(os.access(probe["executable"], os.X_OK), "Window probe is not executable")
    return probe


class PackagedService:
    """Closed stdio transport for this single Play, with owned EOF/reap evidence."""

    def __init__(self, owner):
        self.owner = owner
        self.pending = None
        self.commit_sent = False
        self.commit_response = None
        self.closed = False
        self.eof = False
        self.messages = Queue(maxsize=128)
        self.reader_error = None
        self.stderr = (owner.directory / "service-stderr.txt").open("xb")
        self.stdout = (owner.directory / "service-stdout.jsonl").open("xb")
        env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME"):
            env.pop(key, None)
        with owner.mutation_guard():
            self.child = subprocess.Popen(
                [owner.config["package"]["executable"], "--native-service"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.stderr,
                env=env,
                cwd=owner.directory,
                start_new_session=True,
                bufsize=0,
            )
        from frozen_mac_processes import MacProcesses

        self.monitor = MacProcesses(owner.config["package"]["executable"])
        self.identity = self.monitor.inspect(self.child.pid)
        if self.identity is None:
            # No request has been sent. Settle our direct child on failed setup.
            self.child.stdin.close()
            self.child.terminate()
            self.child.wait(timeout=3)
            self.stdout.close()
            self.stderr.close()
            raise ValueError("Packaged service identity unavailable")
        owner.creation.append(dict(role="packaged_service", **asdict(self.identity)))
        os.set_blocking(self.child.stdin.fileno(), False)
        self.thread = Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        total = 0
        try:
            while True:
                line = self.child.stdout.readline(96 * 1024**2 + 1)
                if not line:
                    self.eof = True
                    break
                total += len(line)
                require(
                    len(line) <= 96 * 1024**2 and total <= 128 * 1024**2,
                    "Packaged response size bound",
                )
                self.stdout.write(line)
                self.stdout.flush()
                self.messages.put_nowait(decode(line))
        except BaseException as exc:
            self.reader_error = str(exc)

    def send(self, ident, method, params, *, commit=False):
        require(
            self.pending is None and not self.closed and self.child.poll() is None,
            "Packaged request pending or service closed",
        )
        require(not commit or not self.commit_sent, "One packaged Play commit maximum")
        raw = canonical(dict(id=ident, method=method, params=params)) + b"\n"
        require(len(raw) <= 4096, "Bounded packaged request required")
        with self.owner.mutation_guard():
            self.owner.control._poll_locked()
            validator = getattr(self.owner, "delivery_validation", None)
            if commit and validator is not None:
                validator()
            require(not self.owner.control.cancelled, "Cancelled before packaged request")
            require(time.monotonic() < self.owner.launch_deadline, "Packaged launch cutoff expired")
            # This private pipe has one writer. Record uncertainty before its
            # single nonblocking write; an incomplete write is never replayed.
            self.pending = ident
            if commit:
                self.commit_sent = True
                self.owner.open_attempted = True
            require(
                os.write(self.child.stdin.fileno(), raw) == len(raw),
                "Incomplete packaged request; do not replay",
            )

    def receive(self, *, event=None, deadline):
        while time.monotonic() < deadline:
            self.owner.tick()
            require(self.reader_error is None, "Packaged reader failed: " + str(self.reader_error))
            try:
                value = self.messages.get(timeout=0.05)
            except Empty:
                require(not self.eof, "Packaged EOF before response")
                continue
            if value.get("event") == "closed":
                self.closed = True
            if event is not None and value.get("event") == event:
                return value
            if self.pending is not None and value.get("id") == self.pending:
                if self.commit_sent and self.pending == "play-commit":
                    self.commit_response = value
                self.pending = None
                return value
        raise ValueError("Original packaged request deadline expired")

    def settle(self):
        """EOF fences any undispatched work. Reap before releasing observer authority."""
        if not self.child.stdin.closed:
            self.child.stdin.close()
        limit = min(self.owner.anchor + 900, time.monotonic() + 20)
        while self.child.poll() is None and time.monotonic() < limit:
            self.owner.verify_cleanup_owner()
            time.sleep(0.05)
        if self.child.poll() is None:
            with self.owner.control.cleanup_guard():
                current = self.monitor.inspect(self.child.pid)
                require(self.identity.same(current), "Packaged service identity changed")
                self.child.terminate()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with self.owner.control.cleanup_guard():
                    require(
                        self.identity.same(self.monitor.inspect(self.child.pid)),
                        "Packaged service identity changed before kill",
                    )
                    self.child.kill()
                self.child.wait(timeout=2)
        self.child.wait()
        self.thread.join(timeout=2)
        require(not self.thread.is_alive(), "Packaged reader unsettled")
        self.child.stdout.close()
        while not self.messages.empty():
            value = self.messages.get_nowait()
            if value.get("event") == "closed":
                self.closed = True
            if self.pending is not None and value.get("id") == self.pending:
                if self.pending == "play-commit":
                    self.commit_response = value
                self.pending = None
        require(self.monitor.inspect(self.child.pid) is None, "Packaged service absence unproven")
        for stream in (self.stdout, self.stderr):
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        self.owner.write(
            "service-settled.json",
            dict(
                pid=self.child.pid,
                returncode=self.child.returncode,
                reaped=True,
                eof_closed=self.closed,
                pending=self.pending,
                commit_sent=self.commit_sent,
                commit_response=self.commit_response,
            ),
        )


def arm_observer(owner, common):
    """Reuse the frozen observer's admission, clock, process and sample contracts."""
    from frozen_mac_processes import MacProcesses
    from wrapper import bounded

    helper = owner.root / "stop-helper"
    owner.code = sha(read_plain(helper / "pins.json"))
    owner.nonce = uuid.uuid4().hex
    admission = dict(
        job=owner.job["job"],
        nonce=owner.nonce,
        code=owner.code,
        anchor=owner.anchor,
        expected_executable=str(owner.app.installation.executable),
        interpreter=sys.executable,
        clock_domain="root-venv-time.monotonic-v1",
    )
    owner.publish(
        "admission.json",
        dict(
            admission=admission,
            approval=dict(
                issue=127,
                status="approved",
                proposal_sha256=decode(read_plain(helper / "proposal.json"))["sha256"],
                job=owner.job["job"],
                nonce=owner.nonce,
                code=owner.code,
                anchor=owner.anchor,
            ),
            source_acceptance=dict(
                issue=129,
                status="accepted",
                observer_sha256="8683b919f8d3bd0b68675e6c6731839144a7c68555a75efafab8309ca18b19b3",
                component_sha256="c7a4a0eee7e2ceb2cf900eeabcc93689119c75e8f61fe96bac933c5247079848",
            ),
        ),
    )
    owner.helper_log = (owner.directory / "stop-observer.log").open("xb")
    before = time.monotonic()
    with owner.mutation_guard():
        owner.observer = subprocess.Popen(
            [sys.executable, "-B", str(helper / "agent_host.py"), str(owner.helper_packet)],
            stdout=owner.helper_log,
            stderr=subprocess.STDOUT,
        )
    limit = min(before + 5, owner.launch_deadline)
    while not (owner.helper_packet / "ready.json").exists():
        owner.tick()
        require(time.monotonic() < limit, "Stop observer failed to arm")
        time.sleep(0.05)
    identity = MacProcesses(sys.executable).inspect(owner.observer.pid)
    require(identity is not None, "Observer identity unavailable")
    owner.creation.append(dict(role="observer", **asdict(identity)))
    bootstrap = decode(bounded(owner.helper_packet / "bootstrap.json"))
    common.verify_observer_bootstrap(
        bootstrap,
        asdict(identity),
        owner.controller_identity["executable"],
        sys.executable,
        sys.prefix,
        before,
        time.monotonic(),
        vars(time.get_clock_info("monotonic")),
    )
    owner.write("helper-clock-binding.json", bootstrap)
    return admission


def bind_observer(owner, admission):
    from sample_fence import _wait_initial_sample_tick
    from wrapper import bounded

    limit = min(owner.request + 20, owner.launch_deadline)
    while owner.game is None:
        owner.tick()
        require(time.monotonic() < limit, "Packaged launch identity unresolved")
        time.sleep(0.05)
    owner.sample(force=True)
    process = dict(
        pid=owner.game.pid, birth=str(owner.game.birth_us), executable=owner.game.executable
    )
    ready = decode(bounded(owner.helper_packet / "ready.json"))
    _wait_initial_sample_tick(
        owner.helper_packet, admission, process, ready, limit, owner.verify_available
    )
    owner.publish(
        "descriptor.json",
        dict(
            version=1,
            job=owner.job["job"],
            nonce=owner.nonce,
            code=owner.code,
            anchor=owner.anchor,
            request=owner.request,
            process=process,
        ),
    )
    while not (owner.helper_packet / "bound.json").exists():
        owner.verify_available()
        require(
            owner.game.same(owner.monitor.inspect(owner.game.pid))
            and owner.observer.poll() is None
            and time.monotonic() < limit,
            "Observer binding unresolved or process changed",
        )
        time.sleep(0.05)
    bound = decode(bounded(owner.helper_packet / "bound.json"))
    require(
        bound["state"]["bound"] is True and bound["state"]["process"] == process,
        "Exact observer binding mismatch",
    )


@contextmanager
def restoration_guard(owner):
    with owner.control.cleanup_guard():
        require(time.monotonic() < owner.anchor + 900, "Original restoration cutoff expired")
        if owner.job.get("purpose") == GUI_PURPOSE:
            owner.require_gui_closed()
        require(
            owner.service is None or owner.service.child.poll() is not None,
            "Packaged service still live",
        )
        owner.require_stopped()
        require(
            not owner.profiles.journal.exists() and not owner.profiles.journal.is_symlink(),
            "Activation journal hold",
        )
        yield


def restore_catalogue(owner):
    owner.verify_cleanup_owner()
    owner.require_settled_input()
    require(owner.dispatch_settled, "Unsettled packaged dispatch; restoration refused")
    require(owner.baseline is not None, "Complete catalogue baseline required")
    p = owner.profiles
    target_id = owner.config["target"]["profile_id"]
    return_id = owner.job["return_profile"]
    with owner.app._locked(nonblocking=True, recover_removal=False), restoration_guard(owner):
        active = p._state()["active"]
        require(active in (target_id, return_id), "Foreign active profile; restoration refused")
        require(
            active == target_id or not owner.open_attempted,
            "Unexpected return activation after dispatch",
        )
        target = p.live if active == target_id else p._profile_path(target_id)
        returned = p._profile_path(return_id) if active == target_id else p.live
        require(tree_files(returned) == owner.baseline["return_files"], "Return profile changed")
        target_files_unchanged(owner, target)
        verify_sibling_saves(owner)
        p._marker(target, target_id)
        require(
            p._asset_hash(target) == owner.config["target"]["assets_sha256"],
            "Target assets changed",
        )
        for path, facts in owner.baseline["immutable"].items():
            require(file_facts(path) == facts, "Protected catalogue/input changed")
        for row in owner.preimages:
            require(
                sha(read_plain(row["copy"], maximum=262144)) == row["sha256"],
                "Declared preimage changed",
            )
        target_post = tree_files(target)
        if active == target_id:
            # The outer restoration guard already holds the same STATE lock.
            # Keep that fence through the maintained exchange without reentry.
            p.switch(return_id, mutation_guard=nullcontext)
        target = p._profile_path(target_id)
        for index, row in enumerate(owner.preimages):
            path = target / "Settings.ini" if row["role"] == "settings" else Path(row["path"])
            before = read_plain(row["copy"], maximum=262144)
            require(sha(before) == row["sha256"], "Declared preimage changed")
            post = read_plain(path, maximum=262144)
            post_info = file_facts(path)
            post_path = owner.directory / ("mutable-post-" + str(index))
            with post_path.open("xb") as stream:
                stream.write(post)
            temporary = path.with_name(path.name + ".menu-restore-" + uuid.uuid4().hex)
            try:
                with temporary.open("xb") as stream:
                    stream.write(before)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, row["mode"])
                os.utime(temporary, ns=(row["mtime_ns"], row["mtime_ns"]))
                require(file_facts(path) == post_info, "Mutable file changed during restoration")
                os.replace(temporary, path)
                _fsync_dir(path.parent)
            finally:
                if temporary.exists():
                    temporary.unlink()
            actual = file_facts(path)
            require(
                actual["sha256"] == row["sha256"]
                and actual["mode"] == row["mode"]
                and actual["mtime_ns"] == row["mtime_ns"],
                "Declared preimage readback failed",
            )
        require(
            tree_files(p.live) == owner.baseline["return_files"], "Return profile readback differs"
        )
        after = target_files_unchanged(owner, target)
        require(
            all(
                after.get(name) == facts
                for name, facts in target_post.items()
                if name != "Settings.ini"
            ),
            "Target postimages/new files lost",
        )
        for path, facts in owner.baseline["immutable"].items():
            require(file_facts(path) == facts, "Protected input readback differs")
        verify_sibling_saves(owner)
    owner.write(
        "restoration-readback.json",
        dict(
            restored=True,
            return_profile=return_id,
            target_profile=target_id,
            game_absent=True,
            journal_absent=True,
            all_new_files_retained=True,
            **(
                {"owned_autosave_preimage": str(owner.autosave_preimage)}
                if getattr(owner, "autosave_preimage", None)
                else {}
            ),
            elapsed=time.monotonic() - owner.anchor,
        ),
    )
    owner.journal.append(
        "restored", dict(restored=True, elapsed_seconds=time.monotonic() - owner.anchor)
    )
    return True


def pilot_class(common, agent_class):
    class CatalogueMenuPilot(agent_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.config = verify_config(self.job)
            self.service = None
            self.dispatch_settled = False
            self.preimages = []
            self.autosave_preimage = None
            self.gameplay_autosave_preimages = {}

        def spawn_input(self, argv, *, deadline, before_spawn=None, **kwargs):
            def final_menu_guard():
                if (
                    getattr(self, "menu_work_active", False)
                    and time.monotonic() >= self.anchor + 390
                ):
                    raise MenuIncomplete("Menu work cutoff before input; normal quit next")
                if before_spawn is not None:
                    before_spawn()

            return super().spawn_input(
                argv, deadline=deadline, before_spawn=final_menu_guard, **kwargs
            )

        def observe_missing_window(self, label, deadline):
            if not ("window_probe" in self.config and getattr(self, "menu_work_active", False)):
                return None
            first_scene = not hasattr(self, "last_agent_window")
            if first_scene and time.monotonic() < self.launch_deadline - 10:
                return None
            require(
                not getattr(self, "window_loss_observed", False), "Window loss already observed"
            )
            self.window_loss_observed = True
            limit = min(deadline, self.anchor + 390, time.monotonic() + 10)
            if first_scene:
                limit = min(limit, self.launch_deadline)

            def guard():
                require(time.monotonic() < limit, "Lost-window observation cap expired")
                try:
                    self.guard_running()
                except MenuIncomplete:
                    require(False, "Lost-window observation cap expired")
                process = self.monitor.inspect(self.game.pid)
                require(self.game.same(process), "Probe process drift")
                require(time.monotonic() < limit, "Lost-window observation cap expired")
                return process

            def spawn(argv, **kwargs):
                guard()
                return subprocess.Popen(argv, **kwargs)

            if first_scene:
                # One persistent-empty sample inside the original launch interval.
                probe = verify_window_probe(self.job)
                provider = self.last_window_enumeration
                before = asdict(guard())
                require(provider["pid"] == self.game.pid, "Probe/provider receiver drift")
                started = time.monotonic()
                require(
                    self.anchor <= provider["started"] <= provider["finished"] <= started,
                    "Provider observation clock drift",
                )
                child_label = label + "FirstWindowMetadata"
                argv = [probe["executable"], "enumerate-pid", str(self.game.pid)]
                code = 0
                try:
                    raw = common.bounded_child(
                        argv,
                        self.directory,
                        child_label,
                        guard,
                        min(2, limit - started),
                        False,
                        spawn=spawn,
                    )
                except common.ChildFailed as failure:
                    done = failure.completion
                    require(
                        done.argv == tuple(argv)
                        and done.returncode == 2
                        and done.reaped
                        and done.completed_at <= limit,
                        "Unresolved first-window metadata completion",
                    )
                    raw, code = done.stdout, done.returncode
                after = asdict(guard())
                finished = time.monotonic()
                metadata = decode(raw)
                require(
                    type(metadata) is dict
                    and set(metadata)
                    == {
                        "status",
                        "pid",
                        "started_unix_seconds",
                        "finished_unix_seconds",
                        "screen_recording_authorized",
                        "windows",
                    }
                    and type(metadata["pid"]) is int
                    and metadata["pid"] == self.game.pid
                    and type(metadata["screen_recording_authorized"]) is bool
                    and all(
                        type(metadata[k]) in (int, float) and math.isfinite(metadata[k])
                        for k in ("started_unix_seconds", "finished_unix_seconds")
                    ),
                    "First-window metadata shape or identity drift",
                )
                if code == 2:
                    require(
                        metadata["status"] == "enumeration_error" and metadata["windows"] is None,
                        "First-window enumeration error required",
                    )
                else:
                    expected = (
                        "ok" if metadata["screen_recording_authorized"] else "permission_limited"
                    )
                    require(
                        metadata["status"] == expected and type(metadata["windows"]) is list,
                        "First-window enumeration status drift",
                    )
                    for row in metadata["windows"]:
                        require(
                            type(row) is dict
                            and set(row)
                            == {"window", "pid", "bounds", "layer", "on_screen", "alpha", "sharing"}
                            and type(row["pid"]) is int
                            and row["pid"] == self.game.pid,
                            "First-window metadata row identity drift",
                        )
                self.write(
                    child_label + "Observation.json",
                    dict(
                        process_before=before,
                        process_after=after,
                        provider=provider,
                        metadata_started=started,
                        metadata_finished=finished,
                        gap_after_provider_seconds=started - provider["finished"],
                        total_observation_span_seconds=finished - provider["started"],
                        first_empty_provider=self.first_empty_window_enumeration,
                        empty_provider_reads=self.empty_window_enumeration_count,
                        observed_empty_interval_seconds=(
                            provider["finished"] - self.first_empty_window_enumeration["started"]
                        ),
                        concurrent=False,
                        metadata=metadata,
                        cutoff=limit,
                        classification="uninterpreted; API metadata does not prove window usability or OS/AX absence",
                    ),
                )
                require(False, "First-scene metadata recorded; owned cleanup required")

            for attempt in (1, 2):
                probe = verify_window_probe(self.job)
                before = asdict(guard())
                child_label = label + f"LostWindowProbe{attempt}"
                argv = [probe["executable"], "inspect", str(self.last_agent_window)]
                try:
                    raw = common.bounded_child(
                        argv,
                        self.directory,
                        child_label,
                        guard,
                        min(2, limit - time.monotonic()),
                        False,
                        spawn=spawn,
                    )
                except common.ChildFailed as failure:
                    done = failure.completion
                    require(
                        done.argv == tuple(argv)
                        and done.returncode == 3
                        and done.reaped
                        and done.stdout == b""
                        and done.completed_at <= limit
                        and read_plain(self.directory / (child_label + ".stderr"), maximum=4096)
                        == b"Exact window absent\n",
                        "Unresolved window probe completion",
                    )
                    metadata = None
                else:
                    metadata = decode(raw)
                    require(
                        type(metadata) is dict
                        and set(metadata) == {"window", "pid", "bounds", "layer"}
                        and type(metadata["window"]) is int
                        and type(metadata["pid"]) is int
                        and metadata["window"] == self.last_agent_window
                        and metadata["pid"] == self.game.pid
                        and type(metadata["layer"]) is int
                        and type(metadata["bounds"]) is dict
                        and set(metadata["bounds"]) == {"x", "y", "width", "height"}
                        and all(
                            type(v) in (int, float) and math.isfinite(v)
                            for v in metadata["bounds"].values()
                        ),
                        "Exact window probe metadata required",
                    )
                after = asdict(guard())
                self.write(
                    child_label + "Observation.json",
                    dict(
                        process_before=before,
                        process_after=after,
                        last_window=self.last_agent_window,
                        metadata=metadata,
                        elapsed=time.monotonic() - self.anchor,
                        cutoff=limit,
                        limit="One old window only; replacement windows and visibility/alpha/sharing unknown",
                    ),
                )
                if attempt == 2:
                    break
                followup = min(limit, time.monotonic() + 2)
                while time.monotonic() < followup:
                    guard()
                    time.sleep(0.05)
                guard()
                try:
                    result = self.native(
                        label + f"LostWindowFollowup{attempt}", ["list-windows"], None, limit
                    )["result"]
                except MenuIncomplete:
                    require(False, "Lost-window observation cap expired")
                guard()
                require(
                    type(result.get("app", {}).get("pid")) is int
                    and result["app"]["pid"] == self.game.pid
                    and type(result.get("windows")) is list
                    and all(type(row) is dict for row in result["windows"]),
                    "Lost-window followup correspondence",
                )
                if result["windows"]:
                    self.write(
                        label + "WindowRecovered.json",
                        dict(
                            elapsed=time.monotonic() - self.anchor,
                            cutoff=limit,
                            automatic_focus=False,
                            expanded_scope=False,
                        ),
                    )
                    return result["windows"]
            require(False, "Lost game window unresolved; bounded observation ended")

        def prepare(self):
            from agent_actor_binding import collect_actor

            self.verify_available()
            actor = collect_actor(self.job["actor_binding"])
            require(
                (actor["model"], actor["effort"], actor["approval_policy"], actor["sandbox"])
                == ("gpt-6.1-sol", "high", "never", "danger-full-access"),
                "Actual driver provenance mismatch",
            )
            self.verify_cleanup_owner()
            self.verify_code()
            self.require_stopped()
            # Signed packages legitimately contain framework/resource symlinks.
            # Verify their maintained signature rather than treating the bundle
            # as a plain mutable profile tree.
            bundle = self.config["package"]["bundle"]
            if self.config["package"].get("artifact_mode") == "local-development":
                verify_config(self.job)
            subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", bundle],
                check=True,
                capture_output=True,
                timeout=10,
            )
            if self.config["package"].get("artifact_mode") == "local-development":
                from native_gui_menu import verify_adhoc_signature

                verify_adhoc_signature(bundle)
            else:
                subprocess.run(
                    ["/usr/sbin/spctl", "--assess", "--type", "execute", bundle],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
            p = self.profiles
            require(
                not p.journal.exists() and not p.journal.is_symlink(), "Activation journal hold"
            )
            require(p._state()["active"] == self.job["return_profile"], "Current return changed")
            p._marker(p.live, self.job["return_profile"])
            stored_original = p._profile_path("original-game")
            try:
                stored_original.lstat()
            except FileNotFoundError:
                require(
                    self.job["return_profile"] == "original-game",
                    "Nonactive Original stored profile missing",
                )
                original_roots = ()
            else:
                require(
                    self.job["return_profile"] != "original-game",
                    "Active Original also has stored profile",
                )
                p._marker(stored_original, "original-game")
                original_roots = (stored_original,)
            require(
                self.app.installation.executable_sha256 == self.job["game_sha256"],
                "Game build changed",
            )
            target = self.config["target"]
            records = [r for r in self.app.catalogue() if r.profile_id == target["profile_id"]]
            require(len(records) == 1, "Exact existing catalogue edition required")
            record = records[0]
            require(
                record.archive_sha256 == target["archive_sha256"]
                and record.variant_id == target["variant_id"]
                and list(record.scenarios) == target["scenarios"],
                "Catalogue target drift",
            )
            root = p._profile_path(record.profile_id)
            p._marker(root, record.profile_id)
            require(
                (root / "Logs").is_dir() and not (root / "Logs").is_symlink(),
                "Existing plain target Logs directory required",
            )
            require(p._asset_hash(root) == target["assets_sha256"], "Target asset drift")
            baseline = dict(
                return_files=tree_files(p.live), target_files=tree_files(root), immutable={}
            )
            baseline["sibling_saves"] = sibling_saves(
                self.app, target["profile_id"], self.job["return_profile"]
            )
            paths = [self.app.catalogue_file, self.app.originals / (record.archive_sha256 + ".7z")]
            if self.job["purpose"] == GAMEPLAY_PURPOSE:
                proof = gameplay_origin(self.job)
                paths.append(Path(proof["origin_readback"]))
                if "autosave_origin" in proof:
                    paths.append(Path(proof["autosave_origin"]["path"]))
            if self.job["purpose"] == SAVE_PURPOSE and "owned_autosave" in self.config:
                paths.append(Path(autosave_origin(self.job)["origin_readback"]))
            if self.config["prepare_compatibility"]:
                selection = self.config["selection"]
                selected = [
                    r for r in self.app.catalogue() if r.profile_id == selection["profile_id"]
                ]
                require(
                    len(selected) == 1
                    and self.app._is_original_variant(selected[0])
                    and selected[0].archive_sha256 == selection["archive_sha256"]
                    and selected[0].variant_id == selection["variant_id"]
                    and list(selected[0].scenarios) == selection["scenarios"],
                    "Current original selection drift",
                )
                original_root = p._profile_path(selection["profile_id"])
                p._marker(original_root, selection["profile_id"])
                for base in (original_root, self.app.prepared / selected[0].prepared_directory):
                    paths.extend(base / name for name in tree_files(base))
            for base in (
                self.app.imports / record.imported_directory,
                self.app.prepared / record.prepared_directory,
                *original_roots,
            ):
                paths.extend(base / name for name in tree_files(base))
            baseline["immutable"] = {str(path): file_facts(path) for path in paths}
            require(baseline == self.config["preservation"], "Fresh catalogue baseline drift")
            require(
                sha(read_plain(paths[1], maximum=2 * 1024**3)) == record.archive_sha256,
                "Archive drift",
            )
            self.baseline = baseline
            if self.job["purpose"] == GAMEPLAY_PURPOSE:
                proof = gameplay_origin(self.job)
                self.gameplay_autosave_preimages = {
                    name: preserve_autosave_preimage(
                        root / "Saves" / name,
                        self.directory / ("owned-" + name + "-preimage"),
                        facts,
                    )
                    for name, facts in proof["autosaves"].items()
                }
            if self.job["purpose"] == SAVE_PURPOSE and "owned_autosave" in self.config:
                self.autosave_preimage = preserve_owned_autosave(self, root)
                target_files_unchanged(self, root)
            for role, path in (
                ("settings", root / "Settings.ini"),
                ("feral", self.app.installation.support_root / "Preferences Data"),
                (
                    "global",
                    Path.home() / "Library/Preferences/com.feralinteractive.railroads.plist",
                ),
            ):
                data = read_plain(path, maximum=262144)
                facts = file_facts(path)
                require(
                    facts == self.config["mutable_preimages"][role], "Fresh mutable preimage drift"
                )
                copy = self.directory / (role + "-preimage")
                shutil.copy2(path, copy)
                require(read_plain(copy) == data, "Preimage readback failed")
                self.preimages.append(dict(role=role, path=str(path), copy=str(copy), **facts))
            self.write(
                "preflight.json",
                dict(
                    return_profile=self.job["return_profile"],
                    target=target,
                    baseline=baseline,
                    mutable_preimages=self.preimages,
                    **(
                        {
                            "owned_test_autosave_preimages": {
                                name: str(path)
                                for name, path in self.gameplay_autosave_preimages.items()
                            }
                        }
                        if self.gameplay_autosave_preimages
                        else {}
                    ),
                    **(
                        {"owned_autosave_preimage": str(self.autosave_preimage)}
                        if self.autosave_preimage
                        else {}
                    ),
                ),
            )
            self.phases["preparation_complete"] = time.monotonic()

        def launch(self):
            if self.job.get("purpose") == SAVE_PURPOSE:
                number = self.next_launch()
                with self.launch_files(number):
                    self.launches = number
                    self.service = None
                    self.dispatch_settled = False
                    self.helper_packet = self.directory / "observer"
                    self.helper_packet.mkdir(mode=0o700)
                    self.launch_deadline = self.anchor + (120 if number == 1 else 390)
                    self._launch_packaged()
            else:
                require(self.launches == 0, "One packaged launch maximum")
                self.launches = 1
                self._launch_packaged()

        def _launch_packaged(self):
            self.require_stopped()
            self.service = PackagedService(self)
            ready_deadline = (
                self.anchor + 60
                if self.launches == 1
                else min(self.anchor + 390, time.monotonic() + 10)
            )
            self.service.receive(event="ready", deadline=min(ready_deadline, self.launch_deadline))
            target = self.config["target"]
            prepare = self.config["prepare_compatibility"]
            selection = self.config["selection"] if prepare else target
            scope = dict(
                profile_id=selection["profile_id"],
                archive_sha256=selection["archive_sha256"],
                variant_id=selection["variant_id"],
                game_executable_sha256=self.job["game_sha256"],
                prepare_compatibility=prepare,
                launch_deadline=self.launch_deadline,
            )
            self.service.send("play-review", "maptools.play_preflight", scope)
            reply = self.service.receive(deadline=self.launch_deadline)
            require(reply.get("ok") is True, "Packaged preflight refused")
            review = reply["data"]
            expected = {
                k: scope[k]
                for k in ("profile_id", "archive_sha256", "variant_id", "game_executable_sha256")
            }
            require(
                review["target"] == expected
                and review["warning"] == self.config["warning"]
                and review["preparation"] == (self.config["preparation"] if prepare else None)
                and review["prepare_compatibility"] is prepare
                and review["launch_deadline"] == self.launch_deadline,
                "Packaged review scope drift",
            )
            self.observer_admission = arm_observer(self, common)
            self.request = time.monotonic()
            self.request_wall_us = time.time_ns() // 1000
            self.phases["launch_request"] = self.request
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
                "packaged-request-interval.json",
                dict(
                    start=self.request,
                    wall_us=self.request_wall_us,
                    deadline=self.launch_deadline,
                    service=asdict(self.service.identity),
                    target=target,
                    one_commit=True,
                ),
            )
            self.service.send(
                "play-commit",
                "maptools.play_commit",
                dict(
                    scope,
                    confirmed=True,
                    ticket=review["ticket"],
                    fingerprint=review["fingerprint"],
                ),
                commit=True,
            )
            reply = self.service.receive(deadline=min(self.request + 25, self.launch_deadline))
            self.write("packaged-play-response.json", reply)
            data = reply.get("data", {})
            if reply.get("ok") is True and data.get("launch_dispatched") is False:
                self.dispatch_settled = True
                self.open_attempted = False
                raise ValueError("Packaged launch cancelled before dispatch")
            require(
                reply.get("ok") is True
                and data.get("target") == expected
                and data.get("launched_profile_id") == target["profile_id"],
                "Packaged Play outcome unknown",
            )
            bind_observer(self, self.observer_admission)
            self.dispatch_settled = True
            self.service.settle()
            self.write(
                "creation-ledger.json",
                dict(
                    processes=self.creation,
                    controller=self.controller_identity,
                    scope="Exact direct service, observer and game identities; no descendant closure claim",
                ),
            )
            engine = dict(
                game_sha256=self.job["game_sha256"],
                catalogue_target=target,
                selected_catalogue_edition=selection,
                prepare_compatibility=prepare,
            )
            selection_receipt = dict(
                process={k: asdict(self.game)[k] for k in ("pid", "birth_us", "executable")},
                selected_at_us=time.time_ns() // 1000,
                observed_inputs=self.job["source_original"],
                observed_engine_inputs=engine,
                observed_input_identity=sha(canonical(engine)),
            )
            if self.launches == 1:
                self.journal.append("selected", selection_receipt)
            else:
                self.write("relaunch-selection.json", selection_receipt)

        def guard_running(self, *, allow_exit=False):
            self.verify_available()
            self.admitted()
            self.verify_code()
            require(time.monotonic() < self.anchor + 480, "Fixed catalogue menu deadline expired")
            if getattr(self, "menu_work_active", False) and time.monotonic() >= self.anchor + 390:
                raise MenuIncomplete("Menu discovery cutoff reached; normal quit next")
            require(
                self.profiles._state()["active"] == self.config["target"]["profile_id"]
                and not self.profiles.journal.exists(),
                "Catalogue target ownership drift",
            )
            games = self.monitor.games()
            require(len(games) <= 1, "Ambiguous game process")
            if allow_exit and not games:
                require(
                    self.game is not None and self.monitor.inspect(self.game.pid) is None,
                    "Unresolved quit identity",
                )
                return
            require(
                len(games) == 1 and self.game is not None and self.game.same(games[0]),
                "Owned catalogue game identity drift",
            )
            require(self.observer.poll() is None, "Independent observer exited")
            self.sample()

        def verify_gameplay_save(self):
            proof = gameplay_origin(self.job)
            require(
                file_facts(self.profiles.live / "Saves" / proof["filename"]) == proof["facts"],
                "Exact retained gameplay save changed",
            )

        def observe(self):
            if self.job["purpose"] == GAMEPLAY_PURPOSE:
                return self.observe_gameplay()
            if self.job["purpose"] == SAVE_PURPOSE:
                return self.observe_save_reopen()
            observations = {}
            partial = None
            loading = self.job["purpose"] in (LOAD_PURPOSE, DIRECT_LOAD_PURPOSE)
            instructions = (
                (
                    "launcher",
                    "Wait for the Feral Play control. Activate only its fresh unique AX Play once; observe the game main menu.",
                ),
                (
                    "menu",
                    "From the current game main menu open visible Credits or Options once. No Single Player, Load, scenario, settings changes or save actions. Observe that reversible submenu.",
                ),
                (
                    "returned",
                    "Return once using visible Back or Escape; observe the main menu. No gameplay, settings changes, loading or saves.",
                ),
            )
            if loading:
                instructions = (
                    instructions[0],
                    (
                        "world",
                        "Use visible Single Player and verify the selected scenario title is exactly "
                        + repr(self.config["scenario_title"])
                        + (
                            " and subtitle "
                            + repr(self.config["scenario_subtitle"])
                            + ". Select that scenario from this edition's visible list; do not assume its default selection matches"
                            if self.job["purpose"] == DIRECT_LOAD_PURPOSE
                            else ""
                        )
                        + " before starting once. Observe its playable world, HUD funds/year and actual city; pause using the visible control. No construction, purchases, Load, saves or settings changes. If the exact scenario cannot be verified or loading cannot finish promptly, return partial with no input; normal quit follows.",
                    ),
                )
            self.menu_work_active = True
            try:
                for stage, instruction in instructions:
                    if time.monotonic() >= self.anchor + 390:
                        raise MenuIncomplete("Menu discovery cutoff reached")
                    observations[stage] = self.decisions.stage(stage, instruction)
            except MenuIncomplete as exc:
                partial = str(exc)
            finally:
                self.menu_work_active = False
            self.normal_quit("finished", None)
            return dict(
                status="safety_stop",
                cause=(
                    "packaged_direct_catalogue_scenario_load_requires_review"
                    if self.job["purpose"] == DIRECT_LOAD_PURPOSE
                    else "packaged_cached_scenario_load_requires_review"
                    if loading
                    else "packaged_catalogue_menu_requires_review"
                ),
                world_loaded="agent_observed" if "world" in observations else "unknown",
                gameplay_credit=False,
                save_reopen_credit=False,
                evidence_kind="agent_visual_observations",
                human_confirmation=False,
                partial_reason=partial,
                **observations,
            )

        def observe_gameplay(self):
            """One packaged launch, exact owned test save, relevant play, normal quit."""
            observations = {}
            partial = None
            proof = gameplay_origin(self.job)
            self.menu_work_active = True
            try:
                self.verify_gameplay_save()
                observations["reopened"] = self.decisions.stage(
                    "reopened",
                    "From the main menu use Load Game to open exactly "
                    + repr(proof["token"])
                    + " once. Verify the exact named row, matching saved world/HUD and a fresh paused banner. "
                    "No new scenario, Start, Save, settings or overwrite. If uncertain, return partial with no input.",
                    validate=self.verify_gameplay_save,
                )
                observations["action"] = self.decisions.stage(
                    "action",
                    "Use only the current paused world and the concrete granted acceptance task. For a "
                    "station/annex construction-only task, require the actual selected location, visible owned "
                    "track/endpoint and a valid placement/connection cue before building only the planned "
                    "structure and necessary short rail. Stock is not a construction prerequisite. Distinguish "
                    "the observed station from an industry annex; never claim one as the other. For transport "
                    "tasks, verify actual source supply and destination demand before construction; build the "
                    "short connection/required station, purchase an available engine and matching relevant "
                    "cars only when current observations support them. Record actual purchase/route and "
                    "rendered car appearance; an unpurchased configuration proves none of these. Never search "
                    "for an alternative site or exceed the grant's discovery bound; partial with no input if "
                    "a prerequisite fails.",
                    validate=self.verify_gameplay_save,
                )
                observations["continued"] = self.decisions.stage(
                    "continued",
                    "Follow the same granted task within the original bound. For construction-only, verify "
                    "the visibly committed new structure, its actual type and supported connection, plus a "
                    "corroborating transaction/state change; preview, hover or funds alone are insufficient. "
                    "Then finish without train purchase or delivery work; normal quit follows. For transport, "
                    "run the established route and separately observe matching cars loaded, arrival and "
                    "unloading/delivery. Movement or income alone does not prove cargo. No saving, alternative "
                    "site/new route search or extended play; partial with no input if evidence is unavailable.",
                    validate=self.verify_gameplay_save,
                )
            except MenuIncomplete as exc:
                partial = str(exc)
            finally:
                self.menu_work_active = False
            self.normal_quit("finished", None)
            return dict(
                status="safety_stop",
                cause="packaged_retained_test_world_gameplay_requires_review",
                evidence_kind="agent_visual_observations",
                human_confirmation=False,
                gameplay_credit=False,
                save_reopen_credit=False,
                cargo_credit=False,
                partial_reason=partial,
                **observations,
            )

        def observe_save_reopen(self):
            from save_cycle import observe_save_cycle
            from unique_save_file import SaveIntent, SaveScope

            source = self.job["source_original"]
            self.scope = SaveScope(
                self.job["job"],
                source["archive_sha256"],
                source["variant_id"],
                source["scenario"],
                self.job["game_sha256"],
                self.config["target"]["profile_id"],
                str(self.profiles.live),
            )
            self.menu_work_active = True
            try:
                world = self.decisions.stage(
                    "world",
                    "Use visible Single Player and verify selected scenario title exactly "
                    + repr(self.config["scenario_title"])
                    + " before one Start. Observe playable world/HUD and pause with visible control. No construction, purchases or transport. Once the paused world is observed, you may pair that observation with CmdS once to open Save As. If the exact title/world is unavailable, return partial with no input.",
                )
                mapping = self.decisions.stage(
                    "filename",
                    "Open Save As if not already open. Enter exactly "
                    + self.job["agent_save"]["token"]
                    + "; observe the filename field/caret without saving. An empty observed reply authorizes the owner to submit Return once after its filesystem guards. Refuse overwrite. Return partial with no input if the field cannot be identified.",
                )
                intent = SaveIntent(
                    str(self.profiles.live / "Saves"),
                    self.job["agent_save"]["filename"],
                    sha(canonical(mapping)),
                )
                outcome = observe_save_cycle(
                    self,
                    self.scope,
                    intent,
                    minimum_stable_seconds=1,
                    deadline=self.anchor + 480,
                    scratch_autosaves=tuple(f"Auto{i}.SMRailroadsSavedGame" for i in range(5)),
                )
                outcome.update(
                    cause="packaged_cached_save_exact_reopen_requires_review",
                    world_loaded="agent_observed",
                    world=world,
                    evidence_kind="agent_visual_observations",
                    human_confirmation=False,
                )
                return outcome
            except MenuIncomplete as exc:
                self.menu_work_active = False
                self.normal_quit("finished", None)
                return dict(
                    status="safety_stop",
                    cause="packaged_cached_save_prerequisite_incomplete",
                    partial_reason=str(exc),
                    world_loaded="unknown",
                    gameplay_credit=False,
                    save_reopen_credit=False,
                    evidence_kind="agent_visual_observations",
                    human_confirmation=False,
                )
            finally:
                self.menu_work_active = False

        def normal_quit(self, stage, validate):
            work = getattr(self, "menu_work_active", False)
            if self.job.get("purpose") == SAVE_PURPOSE:
                self.menu_work_active = False
            try:
                return super().normal_quit(stage, validate)
            finally:
                self.menu_work_active = work

        def observe_reopened_world_and_continued_action(self, *, validate_delivery, deadline):
            if self.job["purpose"] != SAVE_PURPOSE:
                return super().observe_reopened_world_and_continued_action(
                    validate_delivery=validate_delivery, deadline=deadline
                )
            require(deadline == self.anchor + 480, "Original cycle deadline required")
            validate_delivery()
            self.normal_quit("finished", validate_delivery)
            return dict(scope="exact_reopened_world_only", continued_action=False)

        def stop(self):
            self.verify_cleanup_owner()
            if self.service is not None and not self.service.stdout.closed:
                self.service.settle()
            if self.open_attempted and self.game is None:
                # EOF/death fences future service dispatch, but never cancels an
                # already asynchronous unknown LaunchServices request.
                raise ValueError("Packaged launch unbound; observer retained, restoration held")
            if self.service is not None and not self.service.commit_sent:
                self.dispatch_settled = True
            if self.service is None:
                self.dispatch_settled = True
            return super().stop()

        def restore(self):
            return restore_catalogue(self)

    return CatalogueMenuPilot
