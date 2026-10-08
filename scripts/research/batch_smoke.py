#!/usr/bin/env python3
"""Private, sequential GUI load smoke tests. Planning is the default; --run opts in."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner_journal as journal_api
from runner_journal import RunJournal, JournalError
from smr_launcher.application import LauncherApplication
from smr_launcher.activation import (MARKER, _assert_no_symlink_ancestor,
    _assert_regular_tree, _atomic_json, _fsync_tree, _fsync_dir, _make_writable_tree)
from smr_launcher.variants import _tree_manifest
from smr_launcher.resource_identity import fingerprint_resources
from smr_launcher import resource_identity
from smr_launcher import live_run_authority as live_authority

VERSION = 2
_installed_owner = None
_installed_entrypoint = None
_installed_workflow = None
_installation_lock = threading.RLock()
_script_root = None


def authority_decision(row_or_token, *, purpose="completion", case=None, context=None):
    """All seven consumers consult only the exact root-retained live owner."""
    owner = _installed_owner
    if owner is None:
        return live_authority.INSPECTION
    with owner.lock:
        if type(row_or_token) is dict:
            return live_authority.INSPECTION
        return owner.consumer(purpose, row_or_token, case=case, context=context)


def _index_token(path, row):
    owner = _installed_owner
    if owner is None:
        return None
    with owner.lock:
        for issued in owner._registry.values():
            if (issued.row == live_authority.encoded(row)
                    and any(Path(s[0]) == Path(path).absolute() for s in issued.snapshots)):
                return issued.token
    return None


def external_settings_locations(app):
    """Derive product locations solely from the pinned installation API."""
    installation = app.installation
    if installation.bundle_id != "com.feralinteractive.railroads":
        raise SafetyError("Unsupported product settings identity")
    return (installation.support_root / "Preferences Data",
            installation.home / "Library/Preferences" / (installation.bundle_id + ".plist"))


def capture_external_settings(app):
    captured = tuple(live_authority.observed("settings.capture", live_authority.SettingsPreimage.capture, p)
                     for p in external_settings_locations(app))
    # These product resources have FILE roles. Generic profile preservation
    # still supports directories, but that does not admit a settings directory.
    if not all(p.kind in ("absent", "file") and p.supported() for p in captured):
        raise SafetyError("Settings link/type conflict; no constructor permitted")
    return captured


def _known_stopped(app, processes=None):
    app._stopped()
    pending_removal = app.library / "removal.json"
    if pending_removal.exists() or pending_removal.is_symlink():
        raise SafetyError("Pending removal; observed runner refuses unobserved recovery")
    if app.profiles.journal.exists():
        raise SafetyError("Pending activation transaction; explicit owner recovery required")
    if processes is not None and processes.games():
        raise SafetyError("Game remains running or unidentified")
    return True


def _fsync_dir(path):
    return live_authority.sync_directory(path)


def _fsync_tree(path):
    return live_authority.sync_tree(path)


def _atomic_json(path, value):
    return live_authority.atomic_json(path, value, parent_sync=_fsync_dir)


def _copytree(source, destination):
    return live_authority.observed("runner.copy_tree", live_authority.copy_tree, source, destination)


class SafetyError(RuntimeError):
    pass


class _QueueStopped(SafetyError):
    """A fully observed disposition remains administrative; no next admission."""


class DriverCleanupError(SafetyError):
    def __init__(self, group, reason):
        super().__init__("Driver cleanup failed; restoration refused: " + str(reason))
        self.group = group


def group_exists(group):
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Some macOS hosts deny signal-0 probes even after our group is gone.
        # Independently enumerate groups; permission denial itself proves nothing.
        result = subprocess.run(["/bin/ps", "-A", "-o", "pgid="],
                                capture_output=True, text=True, timeout=3, check=True)
        lines = result.stdout.split()
        if not lines or not all(value.isdigit() for value in lines):
            raise SafetyError("Cannot establish process-group absence")
        return group in {int(value) for value in lines}


def require_driver_gone(group):
    if group is None or not group_exists(group): return
    raise SafetyError("Driver group still exists; restoration refused")


def digest(path):
    # Source receipts use the same actual inode/ctime-fenced observation cache as
    # live freeze checks. They do not reread unchanged helper bytes for each job.
    if Path(path).absolute() in (Path(__file__).absolute(), Path(resource_identity.__file__).absolute()):
        return live_authority.frozen_file(path)[2]
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def manifest(root):
    _assert_no_symlink_ancestor(root)
    _assert_regular_tree(root)
    files, directories = _tree_manifest(root)
    return dict(files=files, directories=list(directories))


def stock_resources(app):
    root = app.installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets"
    return fingerprint_resources({"installed_assets": root})


def map_resources(root):
    # Stable role names allow comparison of prepared, diagnostic and live copies.
    return fingerprint_resources({"custom_assets": Path(root) / "CustomAssets",
                                  "user_maps": Path(root) / "UserMaps"})


def resource_binding(stock, custom):
    return dict(schema=1, algorithm=stock["algorithm"],
                implementation_sha256=digest(Path(resource_identity.__file__)),
                stock=stock["identity"], custom=custom["identity"])


def check_resources(app, root, expected, receipt_path=None):
    stock, custom = stock_resources(app), map_resources(root)
    if resource_binding(stock, custom) != expected:
        raise SafetyError("Resource content or timestamps changed since planning")
    if receipt_path is not None:
        _atomic_json(receipt_path, dict(stock=stock, custom=custom))


class BSDInfo(ctypes.Structure):
    _fields_ = [("prefix", ctypes.c_uint32 * 12), ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32), ("suffix", ctypes.c_uint32 * 6),
        ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]


class Usage(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in
        ("user", "system", "idle", "interrupts", "pageins", "wired", "resident",
         "footprint", "start", "exit")]


@dataclass(frozen=True)
class Process:
    pid: int
    birth_us: int
    executable: str
    footprint: int

    def same(self, other):
        return other is not None and (self.pid, self.birth_us, self.executable) == (
            other.pid, other.birth_us, other.executable)


class MacProcesses:
    """libproc identity and physical footprint; never attach a debugger."""
    def __init__(self, executable):
        if sys.platform != "darwin":
            raise SafetyError("Runtime tests require macOS")
        self.executable = str(Path(executable).resolve())
        self.lib = ctypes.CDLL(ctypes.util.find_library("proc"), use_errno=True)
        for name, args in {
            "proc_listallpids": [ctypes.c_void_p, ctypes.c_int],
            "proc_pidpath": [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32],
            "proc_name": [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32],
            "proc_pidinfo": [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int],
            "proc_pid_rusage": [ctypes.c_int, ctypes.c_int, ctypes.c_void_p],
        }.items():
            fn = getattr(self.lib, name); fn.argtypes = args; fn.restype = ctypes.c_int

    def inspect(self, pid):
        path = ctypes.create_string_buffer(4096)
        if self.lib.proc_pidpath(pid, path, len(path)) <= 0:
            return self._failed_inspection(pid, "Cannot identify a live process")
        executable = os.fsdecode(path.value)
        info = BSDInfo(); usage = Usage()
        if self.lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
            return self._failed_inspection(pid, "Cannot read process birth identity")
        if self.lib.proc_pid_rusage(pid, 0, ctypes.byref(usage)) != 0:
            return self._failed_inspection(pid, "Cannot read process physical footprint")
        return Process(pid, info.start_sec * 1000000 + info.start_usec, executable, usage.footprint)

    @staticmethod
    def _failed_inspection(pid, reason):
        # Normal exit can race the separate libproc reads. Only independent
        # evidence that the PID is gone permits treating that race as exit.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
        raise SafetyError(reason)

    def games(self):
        count = self.lib.proc_listallpids(None, 0)
        if count <= 0: raise SafetyError("Process enumeration failed")
        pids = (ctypes.c_int * max(1024, count * 2))()
        count = self.lib.proc_listallpids(pids, ctypes.sizeof(pids))
        if count <= 0 or count >= len(pids): raise SafetyError("Incomplete process enumeration")
        found = []
        for pid in pids[:count]:
            if pid <= 0: continue
            path = ctypes.create_string_buffer(4096)
            name = ctypes.create_string_buffer(1024)
            length = self.lib.proc_pidpath(pid, path, len(path))
            self.lib.proc_name(pid, name, len(name))
            if length > 0 and os.fsdecode(path.value) == self.executable:
                process = self.inspect(pid)
                if process is not None: found.append(process)
            elif name.value.startswith(b"Sid Meiers Rail"):
                raise SafetyError("Another or unidentified Railroads process is running")
        return found

    def stop(self, process):
        for sig, seconds in ((signal.SIGTERM, 2), (signal.SIGKILL, 3)):
            current = self.inspect(process.pid)
            if current is None: return
            if not process.same(current): raise SafetyError("PID identity changed; refusing to signal")
            try:
                os.kill(process.pid, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + seconds
            unreadable = None
            while time.monotonic() < deadline:
                try:
                    current = self.inspect(process.pid)
                except SafetyError as exc:
                    # During termination macOS can retain the PID briefly after
                    # its executable path disappears. Wait for independent exit
                    # evidence, without sending another signal to an unknown PID.
                    unreadable = exc
                    time.sleep(0.1)
                    continue
                if current is None: return
                if not process.same(current): raise SafetyError("PID was reused; refusing further signals")
                unreadable = None
                time.sleep(0.1)
            if unreadable is not None: raise unreadable
        raise SafetyError("Test game did not stop; profiles retained")


def validate_evidence(value, request, run_dir):
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise SafetyError("Driver result schema is invalid")
    for key in ("pid", "birth_us", "input_identity", "scenario_name"):
        if value.get(key) != request[key]: raise SafetyError("Driver result identity mismatch: " + key)
    if value.get("status") != "loaded": return False
    if request.get('mode') == 'stock':
        if (value.get('selection_mode') != 'stock'
                or value.get('resource_namespace') != 'stock-selection-v1'
                or value.get('original_scenario_identity') is not None
                or type(value.get('hud_observations')) is not int
                or value['hud_observations'] < 2
                or value.get('requested_options') != request['requested_options']
                or type(value.get('observed_options')) is not dict):
            raise SafetyError('Stock driver lacks actual bound selection/observed-options returns')
    if value.get("loaded_scene") is not True or not isinstance(value.get("assertion"), str) or not value["assertion"].strip():
        raise SafetyError("Loaded result lacks positive scene assertion")
    screenshot = Path(value.get("screenshot", ""))
    _assert_no_symlink_ancestor(screenshot)
    if not screenshot.is_absolute() or screenshot.parent.resolve() != run_dir.resolve():
        raise SafetyError("Screenshot is outside this test directory")
    if not screenshot.is_file() or screenshot.stat().st_size > 64 * 1024 * 1024:
        raise SafetyError("Screenshot is absent or too large")
    with screenshot.open("rb") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n": raise SafetyError("Screenshot is not PNG")
    if digest(screenshot) != value.get("screenshot_sha256"):
        raise SafetyError("Screenshot hash mismatch")
    return True


def watch(processes, process, driver, request, run_dir, profile_guard, memory_bytes,
          deadline, clock=time.monotonic, sleep=time.sleep):
    peak = 0
    while True:
        profile_guard()
        current = processes.inspect(process.pid)
        if current is None:
            return dict(status="crash_or_exit", cause="spontaneous_exit_unclassified",
                        reason="Game exited before positive load evidence; crash not yet established", peak_footprint=peak)
        if not process.same(current): raise SafetyError("Game PID identity changed")
        games = processes.games()
        if len(games) != 1 or not process.same(games[0]): raise SafetyError("Concurrent game process detected")
        peak = max(peak, current.footprint)
        if current.footprint >= memory_bytes:
            return dict(status="memory_limit", cause="watchdog_memory", reason="Physical footprint ceiling reached", peak_footprint=peak)
        if clock() >= deadline:
            return dict(status="timeout", cause="watchdog_timeout", reason="Load time ceiling reached", peak_footprint=peak)
        if driver.poll() is not None:
            result_file = run_dir / "driver-result.json"
            if driver.returncode != 0 or not result_file.is_file():
                return dict(status="automation_failed", reason="Driver exited without successful evidence", peak_footprint=peak)
            if result_file.stat().st_size > 1024 * 1024: raise SafetyError("Oversized driver result")
            _assert_no_symlink_ancestor(result_file)
            result = json.loads(result_file.read_text())
            passed = validate_evidence(result, request, run_dir)
            return dict(status="loaded" if passed else "automation_failed", reason=result.get("reason", ""),
                        peak_footprint=peak, evidence=result)
        sleep(0.15)


def configure(path, scenario_name):
    """Only an independent diagnostic copy is passed here."""
    raw = path.read_bytes() if path.exists() else b""
    encoding = ("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else
                "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
    text = raw.decode(encoding)
    for key, value in dict(SkipOpeningMovies="1", LastScenarioName=scenario_name.lower(),
                           LastNumAIPlayers="0", FullScreen="0", Quickstart="0").items():
        pattern = r"(?m)^" + key + r"\s*=.*$"
        replacement = key + " = " + value
        if re.search(pattern, text): text = re.sub(pattern, lambda _: replacement, text)
        else: text += "\n" + replacement + "\n"
    live_authority.write_file(path, text.encode(encoding))


def driver_identity(command):
    if not command: raise SafetyError("An external GUI driver command is required")
    executable = shutil.which(command[0])
    if not executable: raise SafetyError("GUI driver executable was not found")
    command = [str(Path(executable).resolve()), *command[1:]]
    files = {str(Path(arg).resolve()): digest(Path(arg)) for arg in command if Path(arg).is_file()}
    return command, dict(command=command, files=files)


def engine_inputs(binding):
    """Input bytes/metadata/options only; code receipts are logging provenance.

    Options here are planned diagnostic settings, not a complete E02 attestation.
    A protocol authority binds behavior separately; neither digest earns action credit.
    """
    resources = {k: binding["resources"][k] for k in ("schema", "algorithm", "stock", "custom")}
    return {k: binding[k] for k in ("variant_id", "archive_sha256", "game_sha256",
                                  "prepared", "scenario", "options")} | {"resources": resources}


def protocol_authority(path, driver_key):
    if path is None: return None
    value = journal_api.read_json(path)
    if type(value) is not dict: raise SafetyError("Protocol authority must be an object")
    try:
        if (value["schema"] != "smoke-protocol-authority-v1"
                or value["driver_receipt"] != driver_key
                or value["runner_sha256"] != digest(Path(__file__))):
            raise SafetyError("Protocol authority does not bind current runner/driver receipts")
        for key in ("protocol_id", "driver_behavior_id"):
            journal_api.sha(value[key])
        journal_api.text(value["basis"])
    except (KeyError, ValueError) as exc:
        raise SafetyError("Incomplete explicit protocol authority") from exc
    return value


def behavior_key(protocol):
    return {k: protocol[k] for k in ("protocol_id", "driver_behavior_id")}


def jobs_for(app, records, driver_key, timeout, memory, expected_title=None, protocol=None):
    jobs = []
    stock = stock_resources(app)
    for record in records:
        root = app._prepared_root(record)
        prepared = manifest(root)
        resources = resource_binding(stock, map_resources(root))
        for scenario in record.scenarios:
            relative = Path(scenario)
            if relative.is_absolute() or ".." in relative.parts: raise SafetyError("Unsafe scenario path")
            source = root / relative
            _assert_no_symlink_ancestor(source)
            if not source.is_file(): raise SafetyError("Scenario file is missing")
            title = record.name
            try:
                tree = ET.parse(source)
                scenario_root = tree.getroot()
                title = (scenario_root.findtext("./szMapName") or
                         scenario_root.findtext("./szName") or title).strip()
            except (ET.ParseError, ValueError): pass
            binding = dict(schema=VERSION, variant_id=record.variant_id, archive_sha256=record.archive_sha256,
                game_sha256=app.installation.executable_sha256, prepared=prepared, scenario=scenario,
                resources=resources,
                driver=driver_key, runner_sha256=digest(Path(__file__)), timeout=timeout, memory_bytes=memory,
                expected_title=expected_title or title,
                protocol=protocol,
                options=dict(skip_movies=True, ai_players=0, fullscreen=False, quickstart=False))
            engine_identity = identity(engine_inputs(binding))
            # Unknown behavior cannot inherit completion/calibration across code revisions.
            behavior = (behavior_key(protocol)
                        if protocol else dict(unbound_driver=driver_key, unbound_runner=binding["runner_sha256"]))
            scheduling = dict(engine_input_identity=engine_identity, behavior=behavior,
                              timeout=timeout, memory_bytes=memory, expected_title=expected_title or title)
            jobs.append(dict(input_identity=identity(scheduling), engine_input_identity=engine_identity,
                             binding=binding, name=record.name,
                             prepared_root=str(root), scenario=scenario,
                             scenario_name=relative.name, scenario_title=expected_title or title))
    return jobs


def completed(path):
    return {v["input_identity"] for v in result_rows(path)
            if authority_decision(_index_token(path, v), purpose="completion").completion}


def result_rows(path):
    if not path.exists() and not path.is_symlink(): return []
    data = journal_api.read_bytes(path, 32 * 1024 * 1024)
    if data and not data.endswith(b"\n"):
        raise JournalError("Interrupted result index; explicit inspection required")
    try:
        rows = [journal_api.decode(line) for line in data.splitlines()]
    except ValueError as exc:
        raise JournalError("Interrupted result index; explicit inspection required") from exc
    return journal_api.reduce_rows(rows)


def append_result(path, value):
    return live_authority.observed("index.complete", live_authority.write_file, path,
        journal_api.canonical(value) + b"\n", append=True, parent_sync=_fsync_dir)


def session_rows(session):
    """Require the full current session, its terminal barrier and exact index binding."""
    _assert_no_symlink_ancestor(session)
    recovery = journal_api.read_json(session / "recovery.json")
    if (recovery.get("schema") != VERSION
            or recovery.get("session") != str(session)
            or recovery.get("restoration_pending") is not False
            or recovery.get("terminal_state") != "finished"):
        raise JournalError("Unfinished or historical test session requires explicit recovery/inspection")
    indexed = {r["raw_outcome_id"]: r for r in result_rows(session.parent / "results.jsonl")}
    rows = []
    for run in session.iterdir():
        if run.name in ("original-profile", "recovery.json"): continue
        if not run.is_dir(): raise JournalError("Unexpected session evidence; inspection required")
        row = journal_api.result_row(RunJournal(run))
        journal = journal_api.validate_current_terminal(row)
        restored = journal.read("restored", True)["payload"]
        if (row["run_directory"] != str(run) or row["input_identity"] != run.name
                or recovery.get("game_sha256") != row["binding"].get("game_sha256")
                or restored.get("original_manifest") != recovery.get("original_manifest")
                or restored.get("original_resources_identity") != recovery.get("original_resources_identity")):
            raise JournalError("Session restoration/input identity mismatch")
        if indexed.get(row["raw_outcome_id"]) != row:
            raise JournalError("Unindexed current terminal disposition")
        rows.append(row)
    expected = {Path(r["run_directory"]).name: journal_api.digest(r) for r in rows}
    if (not rows or recovery.get("terminal_rows") != expected
            or recovery.get("current_run") not in expected):
        raise JournalError("Session terminal disposition binding mismatch")
    return rows


def terminal_authoritative(row):
    return authority_decision(row, purpose="session").terminal_accepted


def finish_terminal(journal, recovery):
    """Commit only after index and save work; retain a pending preimage for rollback.

    A failed final checkpoint fsync can leave complete-looking bytes. The pending
    preimage is prepared durably before that replace, so rollback needs no new
    error receipt or file creation. Immutable phase bytes are never replaced.
    """
    row = journal_api.result_row(journal)
    rollback = journal.directory / "checkpoint-rollback.json"
    _atomic_json(rollback, recovery)
    journal.append("terminal_finished", dict(disposition_identity=journal_api.digest(row),
        at_us=time.time_ns() // 1000))
    finished = dict(recovery, restoration_pending=False, terminal_state="finished",
        terminal_rows=dict(recovery.get("terminal_rows", {}),
                           **{journal.directory.name: journal_api.digest(row)}))
    checkpoint = journal.directory.parent / "recovery.json"
    try:
        live_authority.observed("checkpoint.complete", _atomic_json, checkpoint, finished)
    except BaseException:
        if _installed_owner is not None:
            _installed_owner.reporting_change()
        os.replace(rollback, checkpoint)
        # Visible pending bytes already fence same-host resume even if storage
        # continues to refuse fsync; do not hide the original failure.
        try: _fsync_dir(checkpoint.parent)
        except OSError: pass
        raise
    recovery.clear(); recovery.update(finished)


def profile_guard(app, profile_id):
    if app.profiles.journal.exists(): raise SafetyError("Activation journal appeared during test")
    _assert_no_symlink_ancestor(app.profiles.live / MARKER)
    marker = json.loads((app.profiles.live / MARKER).read_text())
    if marker.get("profile_id") != profile_id or app.profiles._state()["active"] != profile_id:
        raise SafetyError("Active profile changed during test")


def stop_driver(driver):
    """Kill the still-owned group before reaping its leader; then prove it is gone.

    SIGKILL is intentional: a GUI command must not outlive the test and click
    after restoration. An unreaped child PID cannot have been recycled. If the
    leader was already reaped, do not signal an unidentifiable/reused group.
    """
    if driver.returncode is None:
        try: os.killpg(driver.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        except PermissionError:
            # macOS can report EPERM for an already empty group. A separate
            # enumeration must prove absence; a live/unknown group still blocks
            # restoration. Do not fall back to signaling individual PIDs.
            if group_exists(driver.pid):
                raise
        driver.wait(timeout=3)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not group_exists(driver.pid): return
        time.sleep(0.05)
    raise SafetyError("Driver group still exists after leader exit; restoration refused")


def one_test(app, job, command, run_dir, timeout, memory, processes):
    journal = RunJournal(run_dir)
    source = Path(job["prepared_root"])
    if manifest(source) != job["binding"]["prepared"]:
        raise SafetyError("Prepared input changed since planning")
    check_resources(app, source, job["binding"]["resources"], run_dir / "planned-resources.json")
    diagnostic = run_dir / "diagnostic-input"
    _copytree(source, diagnostic)
    live_authority.observed("diagnostic.writable_return", app.profiles._make_writable_tree, diagnostic)
    saves = diagnostic / "Saves"
    if saves.exists():
        # Retain every copied save; a diagnostic job does not need permission to
        # discard old or future contents in order to observe a load.
        _fsync_tree(saves)
    else:
        live_authority.observed("diagnostic.saves_mkdir", saves.mkdir)
    configure(diagnostic / "Settings.ini", job["scenario_name"])
    variant = identity(dict(input=job["input_identity"], diagnostic_run=run_dir.parent.name + "/" + run_dir.name))
    profile_id = live_authority.observed("profile.register_return", app.profiles.register_variant, variant, diagnostic)
    process = None; driver = None; identity_supervisor = None
    launched_at = time.time_ns() // 1000
    deadline = time.monotonic() + timeout
    def select(found):
        nonlocal process
        process = found
        selected = journal.read("selected")
        facts = dict(pid=found.pid, birth_us=found.birth_us, executable=found.executable)
        if selected is not None:
            if selected["payload"]["process"] != facts:
                raise SafetyError("Selected process identity changed")
            return
        # These are observed inputs, not inferred complete E02 context/options.
        stock, custom = stock_resources(app), map_resources(app.profiles.live)
        observed = dict(stock=stock, custom=custom,
                        settings_sha256=digest(app.profiles.live / "Settings.ini"),
                        profile_manifest=manifest(app.profiles.live),
                        option_state="unattested", planned_input_identity=job["input_identity"])
        observed_engine_inputs = dict(game_sha256=job["binding"]["game_sha256"],
            stock_identity=stock["identity"], custom_identity=custom["identity"],
            settings_sha256=observed["settings_sha256"])
        if job.get("mode") == "stock":
            observed_engine_inputs.update(namespace="stock-selection-v1",
                stock_title=job["scenario_title"], original_scenario_identity=None)
        else:
            observed_engine_inputs.update(scenario_path=job["scenario"],
                scenario_sha256=digest(app.profiles.live / job["scenario"]))
        journal.append("selected", dict(process=facts, selected_at_us=time.time_ns() // 1000,
            launched_at_us=launched_at, footprint_at_selection=found.footprint,
            # Receipt metadata/profile marker/protocol are not engine inputs.
            observed_input_identity=identity(observed_engine_inputs),
            observed_engine_inputs=observed_engine_inputs, observed_inputs=observed))
    try:
        if processes.games(): raise SafetyError("A game is already running")
        def launch():
            nonlocal process
            check_resources(app, app.profiles.live, job["binding"]["resources"],
                            run_dir / "launched-resources.json")
            opener = subprocess.Popen(["/usr/bin/open", "-a", str(app.installation.executable.parents[2])])
            try:
                startup = min(deadline, time.monotonic() + 15)
                while time.monotonic() < startup:
                    games = processes.games()
                    if games:
                        if len(games) != 1 or games[0].birth_us < launched_at:
                            raise SafetyError("Unexpected process during launch")
                        select(games[0])
                        if process.footprint >= memory:
                            journal.outcome(dict(status="memory_limit", cause="watchdog_startup_memory",
                                                 reason="Startup memory ceiling reached", peak_footprint=process.footprint))
                            raise SafetyError("Startup memory ceiling reached")
                        return
                    if opener.poll() not in (None, 0): raise SafetyError("LaunchServices failed")
                    time.sleep(0.15)
                raise SafetyError("No identified game process before startup timeout")
            finally:
                if opener.poll() is None:
                    opener.terminate()
                    try: opener.wait(timeout=1)
                    except subprocess.TimeoutExpired: opener.kill(); opener.wait(timeout=1)
        live_authority.observed("profile.play_return", app.profiles.play,
                                profile_id, launch, startup_seconds=min(15, timeout))
        games = processes.games()
        if len(games) != 1 or games[0].birth_us < launched_at:
            raise SafetyError("Could not bind exactly one new test process")
        select(games[0])
        request = dict(schema=1, pid=process.pid, birth_us=process.birth_us,
            executable=process.executable, input_identity=job["input_identity"],
            scenario=job["scenario"], scenario_name=job["scenario_name"],
            scenario_title=job["scenario_title"], expected_title=job["scenario_title"],
            mode=job.get("mode", "custom"), resource_namespace=job.get("resource_namespace", "custom-scenario-v1"),
            original_scenario_identity=job.get("original_scenario_identity"),
            requested_options=job["binding"]["options"], output_directory=str(run_dir), deadline=time.time() + max(0, deadline-time.monotonic()))
        _atomic_json(run_dir / "driver-request.json", request)
        with live_authority.observed_context("driver.log", live_authority.output_stream(run_dir / "driver.log")) as log:
            driver = subprocess.Popen([*command, "--request", str(run_dir / "driver-request.json"),
                "--result", str(run_dir / "driver-result.json")], stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
            recovery_path = run_dir.parent / "recovery.json"
            recovery = journal_api.read_json(recovery_path)
            recovery["driver_group"] = driver.pid
            _atomic_json(recovery_path, recovery)
            def actual_runtime_guard():
                profile_guard(app, profile_id)
                owner = live_authority._observing.get()
                if getattr(owner, "runtime_freeze", None) is not None:
                    owner.runtime_freeze.check()
                return True
            owner = live_authority._observing.get()
            if getattr(owner, "runtime_freeze", None) is not None:
                identity_supervisor = live_authority.IdentitySupervisor(owner, processes,
                    process, driver, journal, deadline, memory, actual_runtime_guard, stop_driver)
            def foreground_guard():
                actual_runtime_guard()
                if identity_supervisor is not None: identity_supervisor.check()
            result = watch(processes, process, driver, request, run_dir,
                           foreground_guard, memory, deadline)
            journal.outcome(result)
            return result
    except BaseException as exc:
        if journal.read("raw_outcome") is None:
            journal.outcome(dict(status="safety_stop", reason=str(exc),
                                 error_type=type(exc).__name__), cause="harness_exception")
        raise
    finally:
        driver_failure = None
        supervision_failure = None
        if identity_supervisor is not None:
            try: identity_supervisor.close()
            except BaseException as exc: supervision_failure = exc
        if driver is not None:
            try: stop_driver(driver)
            except BaseException as exc: driver_failure = DriverCleanupError(driver.pid, exc)
        cleanup_error = None
        try:
            # Terminate the exact game even if GUI-driver cleanup failed. A runaway
            # allocator must not remain running merely because descendants linger.
            if process is None:
                games = processes.games()
                if len(games) == 1 and games[0].birth_us >= launched_at: process = games[0]
                elif games: raise SafetyError("Unidentified process remains; refusing profile restoration")
            if process is not None:
                live_authority.observed("process.stop_return", processes.stop, process)
                app.profiles._marker(app.profiles.live, profile_id)
                check_resources(app, app.profiles.live, job["binding"]["resources"],
                                run_dir / "stopped-resources.json")
        except BaseException as exc:
            cleanup_error = exc
            raise
        finally:
            # Cleanup facts cannot replace the already committed observation.
            if journal.read("raw_outcome") is not None:
                journal.append("stopped", dict(at_us=time.time_ns() // 1000,
                    game_stop_error=str(cleanup_error) if cleanup_error else None,
                    driver_stop_error=str(driver_failure) if driver_failure else None,
                    driver_group=driver.pid if driver is not None else None))
            if driver_failure is not None: raise driver_failure
            if supervision_failure is not None: raise supervision_failure


def cleanup_diagnostic(app, job, run_dir):
    """Retain sources and complete map-associated copies; uncertainty never deletes."""
    variant = identity(dict(input=job["input_identity"], diagnostic_run=run_dir.parent.name + "/" + run_dir.name))
    profile_id = "map-" + variant
    records = RunJournal(run_dir).records()
    if "raw_outcome" not in records or "restored" not in records:
        raise SafetyError("Durable raw evidence and exact restoration required")
    _fsync_tree(run_dir)
    with live_authority.observed_context("cleanup.lock", app.profiles._locked()):
        _known_stopped(app)
        if app.profiles.journal.exists() or app.profiles._state()["active"] == profile_id:
            raise SafetyError("Diagnostic profile still active or switching; retain every source")
        profile = app.profiles._profile_path(profile_id)
        if profile.exists():
            app.profiles._marker(profile, profile_id)
            preimage = live_authority.SettingsPreimage.capture(profile)
            saved = run_dir / "diagnostic-saves"
            _copytree(profile / "Saves", saved)
            if manifest(saved) != manifest(profile / "Saves"):
                raise SafetyError("Diagnostic saves failed preservation")
            _fsync_tree(saved)
            complete = run_dir / "preserved-diagnostic-profile"
            _copytree(profile, complete)
            _fsync_tree(complete)
            _atomic_json(run_dir / "diagnostic-profile-manifest.json", manifest(profile))
            if (live_authority.SettingsPreimage.capture(profile) != preimage
                    or live_authority.portable_tree(live_authority.SettingsPreimage.capture(complete))
                    != live_authority.portable_tree(preimage)
                    or manifest(saved) != manifest(profile / "Saves")):
                raise SafetyError("Save/profile copy changed; retain all sources")
            _fsync_tree(run_dir)
        # Even after complete independent copies return, retain the diagnostic
        # profile and input. No uncertain/new save or map content is deleted.


def calibration_matches(calibration, job, *, execution_mode="runtime"):
    decision = authority_decision(calibration, purpose="calibration")
    owner = _installed_owner
    return bool(owner is not None and decision.calibration
                and getattr(owner, "execution_mode", None) == execution_mode
                and owner._jobs.get(job.get("input_identity")) == live_authority.encoded(job))


def retry_authorized(row, job, authority, *, execution_mode="runtime"):
    """Diagnosis files are inspection; no accepted cause checker/executor exists."""
    authority_decision(row, purpose="retry")
    return False


def queue_state(output, jobs, *, retry=False, authority=None, execution_mode="runtime"):
    if authority is not None and type(authority) is not dict:
        raise SafetyError("Retry authority must be an object")
    rows = result_rows(output / "results.jsonl")
    indexed = {row["raw_outcome_id"] for row in rows}
    known = {}
    for session in output.glob("session-*"):
        try:
            current = session_rows(session)
        except JournalError as exc:
            # Missing/corrupt checkpoints remain explicit journal errors.
            if not (session / "recovery.json").exists(): raise
            raise SafetyError("Unfinished session requires explicit recovery/inspection: " + str(exc)) from exc
        for row in current:
            known[row["raw_outcome_id"]] = row
    if indexed != set(known):
        raise SafetyError("Result index is not bound to this output's sessions")
    by_input = {job["input_identity"]: job for job in jobs}
    done = set()
    for row in rows:
        if not authority_decision(_index_token(output / "results.jsonl", row), purpose="queue").next_job_allowed:
            raise SafetyError("Inspection-only history; cause resolution and a fresh installed invocation required")
        failed = (row["status"] not in journal_api.USABLE
                  or any(row[k] is not None for k in ("restoration_error", "harness_error", "cleanup_error"))
                  or not row["restored"])
        if failed:
            job = by_input.get(row["input_identity"])
            if job is None and authority is not None:
                # A cause fix that changes protocol behavior needs a *named*
                # fresh target, never an inferred sibling/edition substitution.
                receipt = authority.get(row["raw_outcome_id"], {})
                job = by_input.get(receipt.get("target_input_identity"))
            if job is None or not retry_authorized(row, job, authority, execution_mode=execution_mode):
                raise SafetyError("Harness/restoration failure requires documented cause resolution and fresh matching calibration")
        elif not retry:
            done.add(row["input_identity"])
    return done


def _run_batch_operations(app, jobs, command, output, timeout, memory, retry=False, *,
              retry_authority=None, execution_mode="runtime"):
    produced = []
    if (live_authority._observing.get() is not _installed_owner
            or _installed_owner is None or len(jobs) != 1):
        raise SafetyError("Only one exact installed observed job may enter operations")
    _assert_no_symlink_ancestor(output)
    for root in [app.profiles.live, app.managed, app.prepared, app.imports]:
        if output == root or root in output.parents or output in root.parents:
            raise SafetyError("Evidence output overlaps managed map storage")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    _fsync_dir(output.parent)
    # Inspect all durable evidence even with --retry, before any activation/recovery lock.
    done = queue_state(output, jobs, retry=retry, authority=retry_authority, execution_mode=execution_mode)
    selected_jobs = [job for job in jobs if job["input_identity"] not in done]
    if len({job["input_identity"] for job in selected_jobs}) != len(selected_jobs):
        raise SafetyError("Duplicate exact input jobs; refusing double execution")
    if not selected_jobs: return
    _known_stopped(app)
    external_preimages = capture_external_settings(app)
    # Fallible outer helpers may have changed frozen inputs since execute's
    # initial check. Complete the exact installed owner's admission here,
    # while it still owns the lock, before constructing any process facade.
    live_authority.observed("process.constructor_admission", _installed_owner._preflight, selected_jobs[0])
    processes = MacProcesses(app.installation.executable)
    results = output / "results.jsonl"
    if app.profiles.journal.exists(): raise SafetyError("Pending activation transaction; explicit recovery required")
    with live_authority.observed_context("application.lock", app._locked(observed_io=app.profiles.observed_io)):
        app._stopped()
        if app.profiles.journal.exists(): raise SafetyError("Pending activation transaction; explicit recovery required")
        if processes.games(): raise SafetyError("Quit Railroads before running smoke tests")
        previous = app.profiles.active_profile()
        original = manifest(app.profiles.live)
        original_preimage = live_authority.observed("profile.capture",
            live_authority.SettingsPreimage.capture, app.profiles.live)
        original_resources = map_resources(app.profiles.live)["identity"]
        session = output / ("session-" + uuid.uuid4().hex)
        session.mkdir(mode=0o700); _fsync_dir(output)
        snapshot = session / "original-profile"
        _copytree(app.profiles.live, snapshot)
        if manifest(snapshot) != original or manifest(app.profiles.live) != original:
            raise SafetyError("Original profile changed while backing up")
        if (map_resources(snapshot)["identity"] != original_resources
                or map_resources(app.profiles.live)["identity"] != original_resources):
            raise SafetyError("Original resource metadata changed while backing up")
        _fsync_tree(snapshot)
        recovery = dict(schema=VERSION, previous_profile=previous, original_manifest=original,
                        original_resources_identity=original_resources,
                        library=str(app.library), game_sha256=app.installation.executable_sha256,
                        snapshot=str(snapshot), session=str(session), restoration_pending=True)
        _atomic_json(session / "recovery.json", recovery)
        for job in selected_jobs:
            recovery.update(restoration_pending=True, terminal_state="pending", current_run=job["input_identity"],
                diagnostic_profile="map-" + identity(dict(
                input=job["input_identity"], diagnostic_run=session.name + "/" + job["input_identity"])))
            recovery.pop("driver_group", None)
            _atomic_json(session / "recovery.json", recovery)
            run_dir = session / job["input_identity"]
            run_dir.mkdir(mode=0o700); _fsync_dir(session)
            journal = RunJournal(run_dir)
            started = time.monotonic()
            journal.append("started", dict(run_id=uuid.uuid4().hex,
                input_identity=job["input_identity"], engine_input_identity=job.get("engine_input_identity"),
                name=job["name"], scenario=job["scenario"], binding=job["binding"],
                run_directory=str(run_dir), started_at_us=time.time_ns() // 1000,
                execution_mode=execution_mode, timeout=timeout, memory_bytes=memory))
            journal.append("terminal_pending", dict(at_us=time.time_ns() // 1000))
            fatal = None
            try:
                observed = live_authority.observed("process_and_driver.complete", one_test,
                    app, job, command, run_dir, timeout, memory, processes)
                if journal.read("raw_outcome") is None: journal.outcome(observed)
            except BaseException as exc:
                fatal = exc
                # Even a complete-looking file cannot cure a failed fsync/write.
                # Leave the session pending; explicit recovery is the next owner.
                if isinstance(exc, (JournalError, journal_api.EvidenceError)): raise
                if journal.read("raw_outcome") is None:
                    journal.outcome(dict(status="safety_stop", reason=str(exc), error_type=type(exc).__name__),
                                    cause="harness_exception")
                journal.append("harness_failure", dict(error=str(exc), error_type=type(exc).__name__,
                                                        at_us=time.time_ns() // 1000))
            # Read back the authoritative raw bytes before *any* restore attempt.
            journal.records()
            journal.read("raw_outcome", True)
            recovery = journal_api.read_json(session / "recovery.json")
            try:
                if isinstance(fatal, DriverCleanupError):
                    recovery["driver_group"] = fatal.group
                    _atomic_json(session / "recovery.json", recovery)
                    raise fatal
                require_driver_gone(recovery.get("driver_group"))
                if processes.games(): raise SafetyError("Game remains running; automatic restore refused")
                until = time.monotonic() + 10
                while app.installation.game_running() is not False and time.monotonic() < until:
                    time.sleep(0.2)
                app._stopped()
                if app.profiles.journal.exists():
                    raise SafetyError("Pending activation transaction; restoration requires explicit recovery")
                live_authority.observed("profile.switch_return", app.profiles.switch, previous)
                if (manifest(app.profiles.live) != original
                        or map_resources(app.profiles.live)["identity"] != original_resources):
                    raise SafetyError("Original profile failed full manifest verification; backup retained")
                if live_authority.SettingsPreimage.capture(app.profiles.live) != original_preimage:
                    raise SafetyError("Original complete bytes/modes/mtimes/resource identities changed; retained")
                stopped_guard = lambda: _known_stopped(app, processes)
                post_settings = capture_external_settings(app)
                for preimage, current in zip(external_preimages, post_settings):
                    live_authority.observed("settings.restore", preimage.restore, stopped_guard, current)
                if capture_external_settings(app) != external_preimages:
                    raise SafetyError("External settings restoration readback failed")
                live_authority.observed("restore.complete", lambda: None)
                journal.append("restored", dict(restored=True, at_us=time.time_ns() // 1000,
                    original_manifest=original, original_resources_identity=original_resources,
                    elapsed_seconds=round(time.monotonic() - started, 3), recovery=False))
            except BaseException as exc:
                journal.append("restoration_failure", dict(error=str(exc), error_type=type(exc).__name__,
                    at_us=time.time_ns() // 1000, elapsed_seconds=round(time.monotonic() - started, 3)))
                append_result(results, journal_api.result_row(journal))
                raise
            row = journal_api.result_row(journal)
            # The result index is durable before diagnostic cleanup can remove assets.
            append_result(results, row)
            if fatal is not None:
                finish_terminal(journal, recovery)
                raise fatal
            try: live_authority.observed("cleanup.complete", cleanup_diagnostic, app, job, run_dir)
            except BaseException as exc:
                journal.append("cleanup_failure", dict(error=str(exc), error_type=type(exc).__name__,
                                                        at_us=time.time_ns() // 1000))
                append_result(results, journal_api.result_row(journal))
                raise
            finish_terminal(journal, recovery)
            live_authority.observed("report.complete", print, json.dumps({k: row[k] for k in
                ("name", "scenario", "status", "restored", "elapsed_seconds")}), flush=True)
            produced.append(row)
    return produced


def run_batch(app, jobs, command, output, timeout, memory, retry=False, *,
              retry_authority=None, execution_mode="runtime", **substitutions):
    """Public calls cannot select an owner or obtain a grant from file history."""
    if substitutions or retry_authority is not None or retry:
        raise SafetyError("Caller authority/retry substitution denied before constructors")
    owner = _installed_owner
    if owner is not None and getattr(owner, "execution_mode", None) != "synthetic":
        raise SafetyError("Ordinary runtime run_batch denied; exact script workflow owns execution")
    entrypoint = _installed_entrypoint
    if entrypoint is None:
        raise SafetyError("No installed invocation; file-only history is inspection-only")
    return entrypoint(app, jobs, command, output, timeout, memory, execution_mode)


def _source_closure():
    paths = {Path(__file__).absolute(), Path(journal_api.__file__).absolute(),
             Path(live_authority.__file__).absolute()}
    for name, module in tuple(sys.modules.items()):
        if name == "smr_launcher" or name.startswith("smr_launcher."):
            source = getattr(module, "__file__", None)
            if source and source.endswith(".py"):
                paths.add(Path(source).absolute())
    return tuple(sorted(paths))


def _trusted_install(app, stock_job, jobs, command, output, timeout, memory, *,
                     execution_mode, freeze_paths=(), deadline=None, _runtime_root=None, runtime_freeze=None):
    """Trusted root creates and retains an owner; callers cannot choose one.

    Synthetic providers are expressly trusted fixtures. Runtime installation
    requires the actual script composition key and a complete current provider
    closure. Independent acceptance precedes actual use; ordinary file receipts
    and flags never replace observed stock calibration or terminal returns.
    """
    global _installed_owner, _installed_entrypoint, _installed_workflow
    with _installation_lock:
        if _installed_owner is not None:
            raise SafetyError("Invocation installation is one-shot; replacement refused")
        if execution_mode not in {"synthetic", "runtime"}:
            raise SafetyError("Unknown execution mode")
        if execution_mode == "runtime" and (stock_job.get('mode') != 'stock'
                or stock_job.get('resource_namespace') != 'stock-selection-v1'):
            raise SafetyError('Runtime stock calibration requires actual distinct stock namespace')
        if execution_mode == "runtime" and (_runtime_root is not _script_root
                or _script_root is None or runtime_freeze is None):
            raise SafetyError("Runtime requires exact script composition and full provider closure")
        if not 10 <= timeout <= 300 or not 1 <= memory <= 12 * 1024**3:
            raise SafetyError("Finite job watchdog/recovery bounds required")
        all_jobs = (stock_job, *jobs)
        if not jobs or len({j["input_identity"] for j in all_jobs}) != len(all_jobs):
            raise SafetyError("Exact distinct stock and frozen target jobs required")
        bound_jobs = tuple(json.loads(live_authority.encoded(j)) for j in all_jobs)
        bound_command = tuple(command)
        bound_output = Path(output).absolute()
        provider = MacProcesses
        process_path = app.installation.executable
        profiles = app.profiles
        initial_profile = live_authority.SettingsPreimage.capture(profiles.live)
        initial_settings = capture_external_settings(app)
        io = live_authority.ObservedIO()
        supervisor = live_authority.FiniteSupervisor(deadline if deadline is not None else
                    time.monotonic() + (timeout + 45) * len(all_jobs) + 10)
        def guard():
            if _installed_owner is not None and (_installed_owner is not owner
                    or _installed_entrypoint is not single or _installed_workflow is not workflow):
                return False
            if runtime_freeze is not None: runtime_freeze.check()
            if (MacProcesses is not provider or app.installation.executable != process_path
                    or app.profiles is not profiles
                    or live_authority.SettingsPreimage.capture(profiles.live) != initial_profile
                    or capture_external_settings(app) != initial_settings):
                return False
            stopped = _known_stopped(app)
            if runtime_freeze is not None: runtime_freeze.check()
            return stopped
        try:
            if execution_mode == "runtime":
                owner = _runtime_root
                owner._initialize((*_source_closure(), *freeze_paths), bound_jobs, supervisor, guard)
            else:
                owner = live_authority._new_owner((*_source_closure(), *freeze_paths),
                                                   bound_jobs, supervisor, guard)
            owner.execution_mode = execution_mode
            owner.runtime_freeze = runtime_freeze
            owner._preflight()
        except BaseException:
            supervisor.close()
            raise
        used = set()
        consumed = False
        def perform(job, role, destination):
            # This closure retains exact wiring; nothing from public arguments
            # supplies its owner, provider, adapter, scope, role or code freeze.
            supervisor.check(timeout + 45)
            if job["input_identity"] in used:
                raise SafetyError("Exact case already consumed")
            used.add(job["input_identity"])
            old_io = app.profiles.observed_io
            app.profiles.observed_io = io
            try:
                def execute():
                    rows = _run_batch_operations(app, [job], list(bound_command), destination,
                                                  timeout, memory, execution_mode=execution_mode)
                    if len(rows) != 1:
                        raise SafetyError("Incomplete outer runner return")
                    row = rows[0]
                    run = Path(row["run_directory"])
                    paths = tuple(sorted(run.glob("*.json"))) + (
                        run.parent / "recovery.json", destination / "results.jsonl")
                    return row, paths
                return owner.execute(job, role, execute)
            finally:
                app.profiles.observed_io = old_io
        def single(candidate_app, candidate_jobs, candidate_command, destination,
                   candidate_timeout, candidate_memory, mode):
            with owner.lock:
                owner._preflight()
                if consumed or owner._queue_stopped or supervisor._stop.is_set():
                    raise SafetyError("Installed queue consumed/stopped/finalized; zero constructors")
                if (candidate_app is not app or tuple(candidate_command) != bound_command
                        or candidate_timeout != timeout or candidate_memory != memory
                        or mode != execution_mode or len(candidate_jobs) != 1
                        or Path(destination).absolute() != bound_output):
                    raise SafetyError("Caller context substitution denied before constructor")
                candidate = candidate_jobs[0]
                if owner._jobs.get(candidate.get("input_identity")) != live_authority.encoded(candidate):
                    raise SafetyError("Exact frozen case required")
                # Legacy entrypoint remains unavailable to ordinary callers:
                # only the trusted fixture root uses this retained closure.
                role = "calibration" if candidate["input_identity"] == bound_jobs[0]["input_identity"] else "batch"
                if role == "batch":
                    token = owner.latest_calibration
                    if not owner.decide(token).calibration:
                        raise SafetyError("Fresh same-owner stock calibration required")
                token = perform(candidate, role, Path(destination).absolute())
                if role == "calibration":
                    owner.latest_calibration = token
                return token
        owner.latest_calibration = None
        def workflow(calibration):
            nonlocal consumed
            with owner.lock:
                owner._preflight()
                if consumed:
                    raise SafetyError("Installed invocation consumed")
                if calibration is not None and not owner.decide(calibration,
                        case=bound_jobs[0]["input_identity"], context=bound_jobs[0]).calibration:
                    raise SafetyError("Invalid supplied calibration; zero constructors")
                consumed = True
                try:
                    if calibration is None:
                        calibration = perform(bound_jobs[0], "calibration", bound_output / "stock")
                    owner.latest_calibration = calibration
                    if not owner.decide(calibration).calibration:
                        owner._queue_stopped = True
                        raise _QueueStopped("Stock control did not calibrate; no target constructors")
                    tokens = []
                    for number, job in enumerate(bound_jobs[1:]):
                        owner._preflight(job)
                        if not owner.decide(calibration).calibration:
                            raise SafetyError("Current calibration lost")
                        token = perform(job, "batch", bound_output / ("target-" + str(number)))
                        if not owner.decide(token).next_job_allowed:
                            owner._queue_stopped = True
                            raise _QueueStopped("Failed/unknown disposition stops generic queue")
                        tokens.append(token)
                    # Current completion remains observable; queue admission is
                    # permanently consumed by this root's one-shot closure.
                    owner._queue_stopped = True
                    return 0
                except _QueueStopped:
                    raise
                except BaseException:
                    owner.close()
                    raise
                finally:
                    try:
                        supervisor.close()
                    except BaseException:
                        # This final helper is still inside the exact owner lock.
                        # Revoke before callers can read/report any old witness.
                        owner.close()
                        raise
        _installed_owner = owner
        _installed_entrypoint = single
        _installed_workflow = workflow
        return owner


def recover_session(app, session):
    """Only an exact observed failed disposition can answer already-restored.

    Restart/file history always denies; no saved PID is signalled and no old
    clean/completed/restored receipt is promoted or rewritten.
    """
    rows = session_rows(Path(session))
    decisions = [authority_decision(_index_token(Path(session).parent / "results.jsonl", row),
                                    purpose="already_restored") for row in rows]
    if decisions and all(d.classification == "live_failed" and d.already_restored
                         for d in decisions):
        return "already_restored"
    raise SafetyError("Historical recovery is inspection-only; explicit guarded reconciliation required")


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Actually switch isolated profiles and launch games")
    parser.add_argument("--allow-ui-control", action="store_true", help="Acknowledge foreground game/mouse use by the driver")
    parser.add_argument("--batch", action="store_true", help="Run several scenarios after a recorded successful calibration")
    parser.add_argument("--calibrate-and-batch", action="store_true",
                        help="Stock control then exact targets under one installed invocation")
    parser.add_argument("--inspect", type=Path, help="Inspect historical receipts without discovery or recovery")
    parser.add_argument("--recover-session", type=Path, help="Restore a stopped interrupted session; --run applies recovery")
    parser.add_argument("--driver", help="Quoted external GUI driver command; never run through a shell")
    parser.add_argument("--output", type=Path, help="Private output directory outside managed profiles")
    parser.add_argument("--variant", action="append", help="Exact imported variant ID; repeat to select maps")
    parser.add_argument("--scenario", action="append", help="Exact relative scenario path; repeat to select scenarios")
    parser.add_argument("--expected-title", help="Known displayed scenario title (single scenario selection only)")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--memory-gib", type=float, default=6)
    parser.add_argument("--retry", action="store_true", help="Retest completed exact-input jobs")
    parser.add_argument("--protocol-authority", type=Path, help="Explicit reviewed protocol/behavior receipt; no inferred calibration")
    parser.add_argument("--retry-authority", type=Path, help="Durable cause-resolution and fresh calibration receipts keyed by failed raw outcome")
    parser.add_argument("--stock-title", help="Exact stock selector title; never a custom XML identity")
    parser.add_argument("--provider-closure", type=Path, help="Root-selected complete provider closure descriptor; never a capability")
    return parser


def main(argv=None, *, calibration=None, **substitutions):
    if substitutions:
        raise SafetyError("Caller owner/facade/default trust denied before discovery")
    parser = _parser()
    args = parser.parse_args(argv)
    if args.inspect:
        if args.run or args.calibrate_and_batch or calibration is not None:
            parser.error("Inspection cannot schedule jobs")
        print(json.dumps(dict(authority="inspection", rows=result_rows(args.inspect)), indent=2))
        return 0
    if args.batch or args.retry or args.retry_authority:
        parser.error("File-only batch/retry authority denied before discovery; use --inspect")
    if args.calibrate_and_batch:
        if not args.run or not args.allow_ui_control:
            parser.error("Installed workflow requires --run and --allow-ui-control")
        entrypoint = _installed_workflow
        if entrypoint is None:
            parser.error("No accepted root installation/supervisor; runtime remains fail-closed")
        return entrypoint(calibration)
    if calibration is not None:
        parser.error("Calibration token is valid only in the installed workflow")
    if args.recover_session:
        session = args.recover_session.expanduser().absolute()
        _assert_no_symlink_ancestor(session)
        session = session.resolve()
        if args.run: parser.error("Historical recovery denied before discovery; inspect retained evidence")
        else: print(json.dumps(dict(run=False, recovery_session=str(session))))
        return
    if not args.driver or not args.output: parser.error("--driver and --output are required for a test plan")
    if args.run and not args.allow_ui_control:
        parser.error("--run requires --allow-ui-control: the experimental GUI driver uses your desktop")
    if args.run:
        parser.error("Runtime requires an accepted installed --calibrate-and-batch invocation")
    if not 10 <= args.timeout <= 300 or not 1 <= args.memory_gib <= 12:
        parser.error("Timeout must be 10..300 seconds; memory ceiling 1..12 GiB")
    command, driver_key = driver_identity(shlex.split(args.driver))
    protocol = protocol_authority(args.protocol_authority, driver_key)
    app = LauncherApplication.discover()
    records = app.catalogue()
    if args.variant:
        requested = set(args.variant)
        if requested - {r.variant_id for r in records}: parser.error("Unknown variant ID")
        records = tuple(r for r in records if r.variant_id in requested)
    memory = int(args.memory_gib * 1024**3)
    jobs = jobs_for(app, records, driver_key, args.timeout, memory, args.expected_title, protocol)
    if args.scenario:
        selected = set(args.scenario)
        if selected - {job["scenario"] for job in jobs}: parser.error("Unknown scenario path in selected variants")
        jobs = [job for job in jobs if job["scenario"] in selected]
    if args.expected_title and len(jobs) != 1: parser.error("--expected-title requires exactly one scenario")
    output = args.output.expanduser().absolute()
    _assert_no_symlink_ancestor(output)
    output = output.resolve()
    print(json.dumps(dict(schema=VERSION, run=args.run, jobs=[{k: j[k] for k in
        ("input_identity", "name", "scenario", "scenario_title")} for j in jobs]), indent=2), flush=True)



def bounded_command(argv, *, timeout=3, limit=256 * 1024):
    """Read bounded actual subprocess returns; no shell or caller success flag."""
    deadline = time.monotonic() + timeout
    child = subprocess.Popen(list(map(str, argv)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    data = bytearray()
    try:
        os.set_blocking(child.stdout.fileno(), False)
        while True:
            if time.monotonic() >= deadline: raise SafetyError('Metadata command deadline')
            try: chunk = os.read(child.stdout.fileno(), min(65536, limit + 1 - len(data)))
            except BlockingIOError: chunk = b""
            if chunk: data.extend(chunk)
            if len(data) > limit: raise SafetyError('Oversized metadata return')
            if not chunk and child.poll() is not None: break
            if not chunk: time.sleep(.01)
        if child.wait(timeout=1) != 0: raise SafetyError('Metadata command did not return successfully')
        return bytes(data).decode('utf-8', errors='strict')
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=3)
        child.stdout.close()


class ProviderCollector:
    """Actual rooted artifact/process/platform collector, never a receipt grant.

    Static Mach-O declarations and currently observed mapped code are recorded
    separately. Apple sealed-system code is the explicit platform assumption.
    Unknown non-platform paths, text launchers, ambiguous @rpath resolutions,
    unsigned distributions, remote targets and incarnation loss refuse runtime.
    """
    PLATFORM = (Path('/System'), Path('/usr/lib'), Path('/usr/bin'), Path('/bin'), Path('/usr/sbin'), Path('/sbin'))
    TOOLS = ('/usr/bin/codesign', '/usr/bin/otool', '/usr/sbin/lsof', '/usr/bin/sw_vers')

    def __init__(self, descriptor, process_provider, command=bounded_command, *, deadline=None, platform_stat=os.stat):
        if type(descriptor) is not dict or set(descriptor) != {'schema', 'orca', 'ocr', 'toolchain'}:
            raise SafetyError('Exact provider closure selection required; no complete/trusted dictionaries')
        if descriptor['schema'] != 'root-provider-selection-v1': raise SafetyError('Unknown provider selection')
        self.descriptor = json.loads(live_authority.encoded(descriptor))
        self.process_provider, self.command = process_provider, command
        self.platform_stat = platform_stat
        self.deadline = deadline if deadline is not None else time.monotonic() + 30
        self.paths, self.roots, self.records = set(), [], {}
        self.runtime = None

    def _check_deadline(self):
        if time.monotonic() >= self.deadline: raise SafetyError('Provider collection deadline')

    def _call(self, *argv):
        self._check_deadline()
        return self.command(list(argv), timeout=min(3, self.deadline - time.monotonic()))

    def _selected(self, name):
        value = self.descriptor[name]
        if type(value) is not dict or set(value) != {'executable', 'distribution', 'external_roots'}:
            raise SafetyError('Exact executable/distribution/external roots required')
        executable, distribution = Path(value['executable']), Path(value['distribution'])
        if not executable.is_absolute() or not distribution.is_absolute(): raise SafetyError('Absolute root selections required')
        _assert_no_symlink_ancestor(executable); _assert_no_symlink_ancestor(distribution)
        if not distribution.is_dir() or distribution not in executable.parents or not executable.is_file():
            raise SafetyError('Executable is outside selected complete distribution')
        roots = (distribution, *(Path(p) for p in value['external_roots']))
        for root in roots:
            if not root.is_absolute(): raise SafetyError('Relative external provider root')
            _assert_no_symlink_ancestor(root)
            if not root.is_dir(): raise SafetyError('External dependency root absent')
            self.roots.append(root)
        return executable, roots

    def _in_boundary(self, path, roots):
        if any(path == p or p in path.parents for p in self.PLATFORM): return 'trusted-Apple-platform'
        if any(root == path or root in path.parents for root in roots): return 'selected-artifact-root'
        raise SafetyError('Unresolved non-platform dependency: ' + str(path))

    def _dependency(self, name, binary, artifacts, roots, executable):
        if name.startswith('@loader_path/'):
            path = binary.parent / name[len('@loader_path/'):]
        elif name.startswith('@executable_path/'):
            path = executable.parent / name[len('@executable_path/'):]
        elif name.startswith('@rpath/'):
            candidates = [p for p in artifacts if p.name == Path(name).name]
            if len(candidates) != 1: raise SafetyError('Unresolved/ambiguous @rpath dependency')
            path = candidates[0]
        elif name.startswith('/'):
            path = Path(name)
        else: raise SafetyError('Unknown native dependency reference')
        path = Path(os.path.normpath(path))
        if any(path == p or p in path.parents for p in self.PLATFORM):
            return {'path': str(path), 'boundary': 'trusted-Apple-platform', 'proof': 'declared dependency; available image/OS metadata collected separately'}
        path = path.resolve(strict=True)
        boundary = self._in_boundary(path, roots)
        if boundary != 'trusted-Apple-platform': self.paths.add(path)
        return {'path': str(path), 'boundary': boundary}

    def _artifacts(self, executable, roots):
        files = []
        for root in roots:
            before = live_authority.RuntimeFreeze._tree(root, allow_internal_links=True)
            for entry in before:
                self._check_deadline()
                path = Path(entry[0])
                if path.is_file():
                    if len(files) >= 65536: raise SafetyError('Provider artifact file bound exceeded')
                    path = path.resolve(strict=True)
                    live_authority.frozen_file(path); self.paths.add(path); files.append(path)
            if live_authority.RuntimeFreeze._tree(root, allow_internal_links=True) != before: raise SafetyError('Artifact namespace changed during capture')
        self._call('/usr/bin/codesign', '--verify', '--deep', '--strict', str(roots[0]))
        signature = self._call('/usr/bin/codesign', '--display', '--verbose=4', str(roots[0]))
        declarations = {}
        magic = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
        native_executable = False
        for path in files:
            with path.open('rb') as stream: header = stream.read(4)
            if header not in magic: continue
            native_executable |= path == executable
            text = self._call('/usr/bin/otool', '-L', str(path))
            refs = []
            for line in text.splitlines()[1:]:
                if not line.startswith('\t') or ' (compatibility version ' not in line: continue
                token = line.strip().split(' (compatibility version ', 1)[0]
                if not token or token == str(path): continue
                refs.append(self._dependency(token, path, files, roots, executable))
            declarations[str(path)] = refs
        if not native_executable: raise SafetyError('Text/native launcher dependency closure unresolved')
        return {'signature_metadata': signature, 'static_declared_dependencies': declarations,
                'artifact_manifest': [live_authority.frozen_file(p) for p in files]}

    def _status(self, executable):
        value = json.loads(self._call(str(executable), 'status', '--json'))
        try:
            if value.get('ok') is not True or value['result']['target']['kind'] != 'local':
                raise SafetyError('Local provider observation required')
            pid = value['result']['app']['pid']
            runtime = value['result']['runtime']
            incarnation = (runtime['runtimeId'], runtime['appVersion'], value['_meta']['runtimeId'])
            if type(pid) is not int or pid <= 0 or not all(type(v) is str and v for v in incarnation):
                raise SafetyError('Incomplete actual provider incarnation')
            process = self.process_provider.inspect(pid)
            if process is None: raise SafetyError('Provider process vanished')
            return process, incarnation
        except (KeyError, TypeError) as exc: raise SafetyError('Unsupported provider status fields') from exc

    def collect(self):
        orca, orca_roots = self._selected('orca'); ocr, ocr_roots = self._selected('ocr')
        self.records['orca'] = self._artifacts(orca, orca_roots)
        self.records['ocr'] = self._artifacts(ocr, ocr_roots)
        toolchain = self.descriptor['toolchain']
        if type(toolchain) is not dict or set(toolchain) != {'executable', 'options'}:
            raise SafetyError('Actual OCR toolchain/options selection required')
        compiler = Path(toolchain['executable'])
        if not compiler.is_absolute() or not compiler.is_file() or type(toolchain['options']) is not list:
            raise SafetyError('Toolchain selection absent')
        self._in_boundary(compiler.resolve(strict=True), (*orca_roots, *ocr_roots))
        self.paths.add(compiler.resolve(strict=True))
        self.records['toolchain'] = {'version_observed': self._call(str(compiler), '--version'),
            'options_declared': toolchain['options'], 'compiled_source_equivalence': 'not inferred from receipt'}
        self.records['platform'] = {'boundary': 'trusted Apple sealed-system runtime',
            'OS_build_observed': self._call('/usr/bin/sw_vers'),
            'system_volume_observed': self.platform_stat('/System').st_dev,
            'available_platform_code_metadata': self._call('/usr/bin/codesign', '--display', '--verbose=4', '/usr/bin/sw_vers')}
        process, incarnation = self._status(orca)
        if self._in_boundary(Path(process.executable).resolve(strict=True), orca_roots) != 'selected-artifact-root':
            raise SafetyError('Observed local host is outside complete provider distribution')
        images = self._call('/usr/sbin/lsof', '-a', '-p', str(process.pid), '-d', 'txt', '-Fn')
        observed = []
        for line in images.splitlines():
            if line.startswith('n'):
                path = Path(line[1:])
                if not path.is_absolute(): raise SafetyError('Unresolved observed provider image')
                path = path.resolve(strict=True)
                observed.append({'path': str(path), 'boundary': self._in_boundary(path, orca_roots)})
        if not observed: raise SafetyError('Actual mapped provider code unavailable')
        self.records['observed_provider_images'] = observed
        self.runtime = (process, incarnation, images)
        self.orca, self.ocr = orca, ocr
        return self

    def check(self):
        if self.runtime is None: raise SafetyError('Provider collector never completed')
        self.deadline = time.monotonic() + 7
        process, incarnation = self._status(self.orca)
        if not process.same(self.runtime[0]) or incarnation != self.runtime[1]:
            raise SafetyError('Local provider birth/incarnation/build changed')
        images = self._call('/usr/sbin/lsof', '-a', '-p', str(process.pid), '-d', 'txt', '-Fn')
        if images != self.runtime[2]: raise SafetyError('Observed provider dependency set changed')
        return True


def stock_job_for(app, driver_key, output, timeout, memory, title, protocol):
    """Prepare a real independent stock profile, without inventing scenario XML."""
    if type(title) is not str or not title.strip() or title.startswith('TAG_'):
        raise SafetyError('Exact resolved stock title required')
    _known_stopped(app)
    source = Path(output) / 'stock-source'
    _assert_no_symlink_ancestor(source)
    for root in (app.profiles.live, app.managed, app.prepared, app.imports):
        if source == root or root in source.parents or source in root.parents:
            raise SafetyError('Stock/evidence output overlaps original resources')
    live_authority.observed('stock.source_mkdir', source.mkdir, parents=True, exist_ok=False, mode=0o700)
    _fsync_dir(source.parent)
    for name in ('CustomAssets', 'UserMaps'):
        live_authority.observed('stock.empty_storage_mkdir', (source/name).mkdir)
        _fsync_dir(source)
    # Keep every original save in both the full original preimage and this copy.
    # Custom map resources are not introduced into the separate stock namespace.
    live_authority.copy_file(app.profiles.live / 'Settings.ini', source / 'Settings.ini')
    saves = app.profiles.live / 'Saves'
    if saves.exists(): _copytree(saves, source / 'Saves')
    else:
        live_authority.observed('stock.saves_mkdir', (source / 'Saves').mkdir)
        _fsync_dir(source)
    _fsync_tree(source);_fsync_dir(source.parent)
    binding = dict(schema=VERSION, namespace='stock-selection-v1',
        game_sha256=app.installation.executable_sha256, prepared=manifest(source),
        resources=resource_binding(stock_resources(app), map_resources(source)),
        stock_title=title, original_scenario_identity=None, scenario_relative_path=None,
        driver=driver_key, runner_sha256=digest(Path(__file__)), timeout=timeout,
        memory_bytes=memory, expected_title=title, protocol=protocol,
        options=dict(skip_movies=True, ai_players=0, fullscreen=False, quickstart=False))
    engine = identity({k: binding[k] for k in ('namespace', 'game_sha256', 'prepared',
        'resources', 'stock_title', 'options')})
    return dict(input_identity=identity(dict(engine=engine, driver=driver_key, protocol=protocol,
        timeout=timeout, memory=memory)), engine_input_identity=engine, binding=binding,
        name='Stock control: ' + title, prepared_root=str(source), mode='stock',
        resource_namespace='stock-selection-v1', original_scenario_identity=None,
        scenario='stock-selection:' + title, scenario_name=title, scenario_title=title)


def _runtime_wiring(app):
    # Read actual current module/class/function slots, not a cached trusted flag.
    values = [MacProcesses, app, app.installation, app.profiles, subprocess.Popen,
              subprocess.run, os.open, os.write, os.fsync, os.close, os.replace]
    for name, module in tuple(sys.modules.items()):
        if name == '__main__' or name == 'runner_journal' or name.startswith('smr_launcher'):
            for slot, value in tuple(vars(module).items()):
                if slot in {"_installed_entrypoint", "_installed_workflow"}: continue
                if callable(value):
                    values.append(getattr(value, '__func__', value))
                    if isinstance(value, type):
                        for method in tuple(vars(value).values()):
                            if isinstance(method, (staticmethod, classmethod)): method = method.__func__
                            if callable(method): values.append(method)
    return tuple(values)


def _runtime_sources():
    paths = set(_source_closure())
    for module in tuple(sys.modules.values()):
        for name in ('__file__', '__cached__'):
            value = getattr(module, name, None)
            if value:
                path = Path(value)
                if path.is_file(): paths.add(path.resolve(strict=True))
    paths.add(Path(sys.executable).resolve(strict=True))
    return paths


def _script_entry(argv):
    """The actual script composition root retains one owner before discovery.

    Imported main/public arguments cannot construct this root. Provider receipts
    select artifacts only; actual collectors, current frozen stock inputs and
    complete observed terminal returns supply the subsequent live witnesses.
    """
    global _script_root
    parser = _parser();args = parser.parse_args(argv)
    if not args.calibrate_and_batch: return main(argv)
    if __name__ != '__main__' or _script_root is not None:
        raise SafetyError('Only actual one-shot script composition may install runtime')
    if (not args.run or not args.allow_ui_control or args.inspect or args.batch
            or args.retry or args.retry_authority or args.recover_session):
        parser.error('Exact current calibrate-and-batch intent required; history/retry cannot admit')
    if (not args.output or not args.driver or not args.stock_title or not args.provider_closure
            or not args.variant or not args.scenario or not args.protocol_authority):
        parser.error('Root requires exact stock title, frozen driver/provider/protocol and exact target selections')
    if not 10 <= args.timeout <= 300 or not 1 <= args.memory_gib <= 12:
        parser.error('Finite timeout/memory bounds required')
    if sys.platform != 'darwin' or not sys.flags.isolated or not sys.dont_write_bytecode:
        parser.error('Runtime root requires isolated python -I -B on macOS')
    owner = object.__new__(live_authority.InvocationOwner)
    owner.lock = threading.RLock();owner._epoch = uuid.uuid4().hex
    _script_root = owner
    with owner.lock:
        # All discovery/provider constructors and freeze/calibration/queue checks
        # share this exact owner lock; initialization retains its object/epoch.
        app = LauncherApplication.discover();_known_stopped(app)
        before_profile = live_authority.SettingsPreimage.capture(app.profiles.live)
        before_settings = capture_external_settings(app)
        if not before_profile.supported(): raise SafetyError('Selected original profile type conflict')
        output = args.output.expanduser().absolute();_assert_no_symlink_ancestor(output)
        command, driver_key = driver_identity(shlex.split(args.driver))
        driver_path = Path(__file__).with_name('orca_load_driver.py').resolve()
        # Only the reviewed driver is used; injected -c/options/interpreters deny.
        descriptor = journal_api.read_json(args.provider_closure)
        collector = ProviderCollector(descriptor, MacProcesses(app.installation.executable)).collect()
        exact_command = [str(Path(sys.executable).resolve()), '-I', '-B', str(driver_path),
                         '--ocr', str(collector.ocr), '--orca', str(collector.orca)]
        if command != exact_command: raise SafetyError('Exact reviewed driver/interpreter/provider command required')
        protocol = protocol_authority(args.protocol_authority, driver_key)
        records = app.catalogue();wanted = set(args.variant)
        if wanted - {r.variant_id for r in records}: raise SafetyError('Unknown exact target variant')
        records = tuple(r for r in records if r.variant_id in wanted)
        memory = int(args.memory_gib * 1024**3)
        targets = jobs_for(app, records, driver_key, args.timeout, memory, args.expected_title, protocol)
        chosen = set(args.scenario)
        if chosen - {j['scenario'] for j in targets}: raise SafetyError('Unknown exact target scenario')
        targets = [j for j in targets if j['scenario'] in chosen]
        if not targets or (args.expected_title and len(targets) != 1):
            raise SafetyError('Exact nonempty target jobs required')
        stock = stock_job_for(app, driver_key, output, args.timeout, memory, args.stock_title, protocol)
        if (live_authority.SettingsPreimage.capture(app.profiles.live) != before_profile
                or capture_external_settings(app) != before_settings):
            raise SafetyError('Planning changed actual original profile/settings; no admission')
        paths = _runtime_sources() | collector.paths | {driver_path,
            driver_path.with_name('read_screen_text.swift'), args.protocol_authority.absolute(),
            args.provider_closure.absolute(), app.installation.executable.resolve()}
        paths |= {Path(tool).resolve(strict=True) for tool in collector.TOOLS}
        roots = [*collector.roots, *(Path(j['prepared_root']) for j in (stock, *targets)),
            app.installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets"]
        for root in roots:
            paths |= {p for p in root.rglob('*') if p.is_file()}
        # Actual current interpreter image observations close native dependencies
        # outside Python's module registry; future changes deny rather than infer.
        interpreter_images = collector._call('/usr/sbin/lsof', '-a', '-p', str(os.getpid()), '-d', 'txt', '-Fn')
        interpreter_roots = (*collector.roots, Path(sys.base_prefix).resolve())
        for line in interpreter_images.splitlines():
            if line.startswith('n'):
                image = Path(os.path.normpath(line[1:]))
                if collector._in_boundary(image, interpreter_roots) != 'trusted-Apple-platform':
                    paths.add(image.resolve(strict=True))
        if not any(line.startswith('n') for line in interpreter_images.splitlines()):
            raise SafetyError('Actual interpreter image dependencies unavailable')
        roots.append(Path(sys.base_prefix).resolve())
        closure = live_authority.RuntimeFreeze(paths, roots, lambda: _runtime_wiring(app),
            link_roots=(*collector.roots, Path(sys.base_prefix).resolve()))
        actual_check = closure.check
        def complete_check():
            actual_check();collector.check()
            current = bounded_command(['/usr/sbin/lsof', '-a', '-p', str(os.getpid()), '-d', 'txt', '-Fn'])
            if current != interpreter_images: raise SafetyError('Interpreter image closure changed')
            return True
        closure.check = complete_check
        _trusted_install(app, stock, targets, exact_command, output, args.timeout, memory,
            execution_mode='runtime', freeze_paths=closure.paths, _runtime_root=owner, runtime_freeze=closure)
        # No exit code is an async grant; this returns only after all work/returns.
        return _installed_workflow(None)


if __name__ == "__main__":
    raise SystemExit(_script_entry(sys.argv[1:]))
