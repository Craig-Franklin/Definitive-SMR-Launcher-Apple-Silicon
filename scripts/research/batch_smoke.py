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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from smr_launcher.application import LauncherApplication
from smr_launcher.activation import (MARKER, _assert_no_symlink_ancestor,
    _assert_regular_tree, _atomic_json, _fsync_tree, _fsync_dir, _make_writable_tree)
from smr_launcher.variants import _tree_manifest
from smr_launcher.resource_identity import fingerprint_resources
from smr_launcher import resource_identity

VERSION = 1


class SafetyError(RuntimeError):
    pass


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
            return dict(status="crash_or_exit", reason="Game exited before positive load evidence", peak_footprint=peak)
        if not process.same(current): raise SafetyError("Game PID identity changed")
        games = processes.games()
        if len(games) != 1 or not process.same(games[0]): raise SafetyError("Concurrent game process detected")
        peak = max(peak, current.footprint)
        if current.footprint >= memory_bytes:
            return dict(status="memory_limit", reason="Physical footprint ceiling reached", peak_footprint=peak)
        if clock() >= deadline:
            return dict(status="timeout", reason="Load time ceiling reached", peak_footprint=peak)
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
    path.write_bytes(text.encode(encoding))


def driver_identity(command):
    if not command: raise SafetyError("An external GUI driver command is required")
    executable = shutil.which(command[0])
    if not executable: raise SafetyError("GUI driver executable was not found")
    command = [str(Path(executable).resolve()), *command[1:]]
    files = {str(Path(arg).resolve()): digest(Path(arg)) for arg in command if Path(arg).is_file()}
    return command, dict(command=command, files=files)


def jobs_for(app, records, driver_key, timeout, memory, expected_title=None):
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
                options=dict(skip_movies=True, ai_players=0, fullscreen=False, quickstart=False))
            jobs.append(dict(input_identity=identity(binding), binding=binding, name=record.name,
                             prepared_root=str(root), scenario=scenario,
                             scenario_name=relative.name, scenario_title=expected_title or title))
    return jobs


def completed(path):
    if not path.exists(): return set()
    _assert_no_symlink_ancestor(path)
    # A torn final line is preserved and requires inspection rather than silent truncation.
    values = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {v["input_identity"] for v in values if v.get("schema") == VERSION
            and v.get("restored") is True and v.get("status") in
            ("loaded", "crash_or_exit", "memory_limit", "timeout", "automation_failed")}


def append_result(path, value):
    _assert_no_symlink_ancestor(path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n"); stream.flush(); os.fsync(stream.fileno())


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
    source = Path(job["prepared_root"])
    if manifest(source) != job["binding"]["prepared"]:
        raise SafetyError("Prepared input changed since planning")
    check_resources(app, source, job["binding"]["resources"], run_dir / "planned-resources.json")
    diagnostic = run_dir / "diagnostic-input"
    shutil.copytree(source, diagnostic, copy_function=shutil.copy2)
    _make_writable_tree(diagnostic)
    saves = diagnostic / "Saves"
    if saves.exists(): shutil.rmtree(saves)
    saves.mkdir()
    configure(diagnostic / "Settings.ini", job["scenario_name"])
    variant = identity(dict(input=job["input_identity"], diagnostic_run=run_dir.parent.name + "/" + run_dir.name))
    profile_id = app.profiles.register_variant(variant, diagnostic)
    process = None; driver = None
    launched_at = time.time_ns() // 1000
    deadline = time.monotonic() + timeout
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
                        process = games[0]
                        if process.footprint >= memory: raise SafetyError("Startup memory ceiling reached")
                        return
                    if opener.poll() not in (None, 0): raise SafetyError("LaunchServices failed")
                    time.sleep(0.15)
                raise SafetyError("No identified game process before startup timeout")
            finally:
                if opener.poll() is None:
                    opener.terminate()
                    try: opener.wait(timeout=1)
                    except subprocess.TimeoutExpired: opener.kill(); opener.wait(timeout=1)
        app.profiles.play(profile_id, launch, startup_seconds=min(15, timeout))
        games = processes.games()
        if len(games) != 1 or games[0].birth_us < launched_at:
            raise SafetyError("Could not bind exactly one new test process")
        process = games[0]
        request = dict(schema=1, pid=process.pid, birth_us=process.birth_us,
            executable=process.executable, input_identity=job["input_identity"],
            scenario=job["scenario"], scenario_name=job["scenario_name"],
            scenario_title=job["scenario_title"], expected_title=job["scenario_title"],
            mode="custom", output_directory=str(run_dir), deadline=time.time() + max(0, deadline-time.monotonic()))
        _atomic_json(run_dir / "driver-request.json", request)
        with (run_dir / "driver.log").open("xb") as log:
            driver = subprocess.Popen([*command, "--request", str(run_dir / "driver-request.json"),
                "--result", str(run_dir / "driver-result.json")], stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
            return watch(processes, process, driver, request, run_dir,
                lambda: profile_guard(app, profile_id), memory, deadline)
    finally:
        driver_failure = None
        if driver is not None:
            try: stop_driver(driver)
            except BaseException as exc: driver_failure = DriverCleanupError(driver.pid, exc)
        try:
            # Terminate the exact game even if GUI-driver cleanup failed. A runaway
            # allocator must not remain running merely because descendants linger.
            if process is None:
                games = processes.games()
                if len(games) == 1 and games[0].birth_us >= launched_at: process = games[0]
                elif games: raise SafetyError("Unidentified process remains; refusing profile restoration")
            if process is not None:
                processes.stop(process)
                app.profiles._marker(app.profiles.live, profile_id)
                check_resources(app, app.profiles.live, job["binding"]["resources"],
                                run_dir / "stopped-resources.json")
        finally:
            if driver_failure is not None: raise driver_failure


def cleanup_diagnostic(app, job, run_dir):
    """After restoration, retain diagnostic saves/evidence but discard duplicate assets."""
    variant = identity(dict(input=job["input_identity"], diagnostic_run=run_dir.parent.name + "/" + run_dir.name))
    profile_id = "map-" + variant
    with app.profiles._locked():
        app.profiles._require_stopped()
        if app.profiles.journal.exists() or app.profiles._state()["active"] == profile_id:
            raise SafetyError("Diagnostic profile is still active or switching")
        profile = app.profiles._profile_path(profile_id)
        if profile.exists():
            app.profiles._marker(profile, profile_id)
            expected = manifest(profile)
            saved = run_dir / "diagnostic-saves"
            shutil.copytree(profile / "Saves", saved, copy_function=shutil.copy2)
            if manifest(saved) != manifest(profile / "Saves"):
                raise SafetyError("Diagnostic saves failed preservation")
            _fsync_tree(saved)
            _atomic_json(run_dir / "diagnostic-profile-manifest.json", expected)
            shutil.rmtree(profile); _fsync_dir(profile.parent)
        copied = run_dir / "diagnostic-input"
        if copied.exists():
            manifest(copied)
            shutil.rmtree(copied); _fsync_dir(copied.parent)


def run_batch(app, jobs, command, output, timeout, memory, retry=False):
    _assert_no_symlink_ancestor(output)
    # Do not nest artifacts inside any profile or prepared source.
    for root in [app.profiles.live, app.managed, app.prepared, app.imports]:
        if output == root or root in output.parents or output in root.parents:
            raise SafetyError("Evidence output overlaps managed map storage")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    processes = MacProcesses(app.installation.executable)
    results = output / "results.jsonl"
    done = set() if retry else completed(results)
    with app._locked():
        app._stopped()
        if processes.games(): raise SafetyError("Quit Railroads before running smoke tests")
        for pending in output.glob("session-*/recovery.json"):
            _assert_no_symlink_ancestor(pending)
            if json.loads(pending.read_text()).get("restoration_pending") is not False:
                raise SafetyError("Unfinished test session requires recovery: " + str(pending))
        previous = app.profiles.active_profile()
        original = manifest(app.profiles.live)
        original_resources = map_resources(app.profiles.live)["identity"]
        session = output / ("session-" + uuid.uuid4().hex)
        session.mkdir(mode=0o700)
        snapshot = session / "original-profile"
        shutil.copytree(app.profiles.live, snapshot, copy_function=shutil.copy2)
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
        for job in jobs:
            if job["input_identity"] in done: continue
            recovery["restoration_pending"] = True
            recovery["diagnostic_profile"] = "map-" + identity(dict(
                input=job["input_identity"], diagnostic_run=session.name + "/" + job["input_identity"]))
            _atomic_json(session / "recovery.json", recovery)
            run_dir = session / job["input_identity"]
            run_dir.mkdir(mode=0o700)
            started = time.monotonic()
            result = dict(schema=VERSION, input_identity=job["input_identity"], name=job["name"],
                          scenario=job["scenario"], binding=job["binding"], run_directory=str(run_dir),
                          started_at=time.time())
            fatal = None
            try:
                result.update(one_test(app, job, command, run_dir, timeout, memory, processes))
            except BaseException as exc:
                result.update(status="safety_stop", reason=str(exc)); fatal = exc
                if isinstance(exc, DriverCleanupError):
                    recovery["driver_group"] = exc.group
                    _atomic_json(session / "recovery.json", recovery)
            finally:
                if isinstance(fatal, DriverCleanupError):
                    result.update(restored=False, restoration_error=str(fatal))
                    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                    append_result(results, result)
                    raise fatal
                if processes.games():
                    result.update(restored=False, restoration_error="Game remains running; automatic restore refused")
                    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                    append_result(results, result)
                    raise SafetyError("Game remains running; see private recovery.json")
                # Steam may take a moment to clear its running flag. Profile manager
                # checks must succeed before it changes the live directory.
                until = time.monotonic() + 10
                while app.installation.game_running() is not False and time.monotonic() < until:
                    time.sleep(0.2)
                app._stopped()
                app.profiles.switch(previous)
                if (manifest(app.profiles.live) != original
                        or map_resources(app.profiles.live)["identity"] != original_resources):
                    result.update(restored=False, restoration_error="Original profile manifest mismatch; backup retained")
                    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                    append_result(results, result)
                    raise SafetyError("Original profile failed full manifest verification")
                result["restored"] = True
                recovery["restoration_pending"] = False
                _atomic_json(session / "recovery.json", recovery)
                if fatal is None:
                    try: cleanup_diagnostic(app, job, run_dir)
                    except Exception as exc:
                        result["cleanup_warning"] = str(exc)
                result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                append_result(results, result)
                print(json.dumps({k: result[k] for k in ("name", "scenario", "status", "restored", "elapsed_seconds")}), flush=True)
            if fatal is not None: raise fatal
        recovery["restoration_pending"] = False
        _atomic_json(session / "recovery.json", recovery)


def recover_session(app, session):
    """Explicit stopped-game recovery; never overwrite a changed original profile."""
    _assert_no_symlink_ancestor(session / "recovery.json")
    recovery = json.loads((session / "recovery.json").read_text())
    if (recovery.get("schema") != VERSION or recovery.get("library") != str(app.library)
            or recovery.get("game_sha256") != app.installation.executable_sha256):
        raise SafetyError("Recovery belongs to another library/game build")
    if recovery.get("restoration_pending") is False: return "already_restored"
    snapshot = session / "original-profile"
    if manifest(snapshot) != recovery["original_manifest"]:
        raise SafetyError("Recovery snapshot changed; inspection required")
    resource_identity = recovery.get("original_resources_identity")
    if resource_identity and map_resources(snapshot)["identity"] != resource_identity:
        raise SafetyError("Recovery resource metadata changed; inspection required")
    with app._locked():
        app._stopped()
        # Never signal this persisted group: its identity may have been reused.
        # Recovery remains conservative until the entire group is absent.
        require_driver_gone(recovery.get("driver_group"))
        active = app.profiles.active_profile()
        previous = recovery["previous_profile"]
        if active not in (previous, recovery.get("diagnostic_profile")):
            raise SafetyError("Another profile became active; recovery will not override it")
        original = app.profiles.live if active == previous else app.profiles._profile_path(previous)
        if manifest(original) != recovery["original_manifest"]:
            raise SafetyError("Original profile changed; independent backup retained for inspection")
        if resource_identity and map_resources(original)["identity"] != resource_identity:
            raise SafetyError("Original resource metadata changed; independent backup retained")
        app.profiles.switch(previous)
        if manifest(app.profiles.live) != recovery["original_manifest"]:
            raise SafetyError("Restoration manifest mismatch")
        if resource_identity and map_resources(app.profiles.live)["identity"] != resource_identity:
            raise SafetyError("Restoration resource metadata mismatch")
        recovery["restoration_pending"] = False
        _atomic_json(session / "recovery.json", recovery)
    return "restored"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Actually switch isolated profiles and launch games")
    parser.add_argument("--allow-ui-control", action="store_true", help="Acknowledge foreground game/mouse use by the driver")
    parser.add_argument("--batch", action="store_true", help="Run several scenarios after a recorded successful calibration")
    parser.add_argument("--recover-session", type=Path, help="Restore a stopped interrupted session; --run applies recovery")
    parser.add_argument("--driver", help="Quoted external GUI driver command; never run through a shell")
    parser.add_argument("--output", type=Path, help="Private output directory outside managed profiles")
    parser.add_argument("--variant", action="append", help="Exact imported variant ID; repeat to select maps")
    parser.add_argument("--scenario", action="append", help="Exact relative scenario path; repeat to select scenarios")
    parser.add_argument("--expected-title", help="Known displayed scenario title (single scenario selection only)")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--memory-gib", type=float, default=6)
    parser.add_argument("--retry", action="store_true", help="Retest completed exact-input jobs")
    args = parser.parse_args(argv)
    if args.recover_session:
        session = args.recover_session.expanduser().absolute()
        _assert_no_symlink_ancestor(session)
        session = session.resolve()
        if args.run: print(recover_session(LauncherApplication.discover(), session))
        else: print(json.dumps(dict(run=False, recovery_session=str(session))))
        return
    if not args.driver or not args.output: parser.error("--driver and --output are required for a test plan")
    if args.run and not args.allow_ui_control:
        parser.error("--run requires --allow-ui-control: the experimental GUI driver uses your desktop")
    if not 10 <= args.timeout <= 300 or not 1 <= args.memory_gib <= 12:
        parser.error("Timeout must be 10..300 seconds; memory ceiling 1..12 GiB")
    command, driver_key = driver_identity(shlex.split(args.driver))
    app = LauncherApplication.discover()
    records = app.catalogue()
    if args.variant:
        requested = set(args.variant)
        if requested - {r.variant_id for r in records}: parser.error("Unknown variant ID")
        records = tuple(r for r in records if r.variant_id in requested)
    memory = int(args.memory_gib * 1024**3)
    jobs = jobs_for(app, records, driver_key, args.timeout, memory, args.expected_title)
    if args.scenario:
        selected = set(args.scenario)
        if selected - {job["scenario"] for job in jobs}: parser.error("Unknown scenario path in selected variants")
        jobs = [job for job in jobs if job["scenario"] in selected]
    if args.expected_title and len(jobs) != 1: parser.error("--expected-title requires exactly one scenario")
    output = args.output.expanduser().absolute()
    _assert_no_symlink_ancestor(output)
    output = output.resolve()
    if args.run and len(jobs) > 1:
        if not args.batch: parser.error("Choose one scenario first; multiple tests require --batch after calibration")
        rows = output / "results.jsonl"
        _assert_no_symlink_ancestor(rows)
        calibrated = False
        if rows.exists():
            for row in (json.loads(line) for line in rows.read_text().splitlines() if line.strip()):
                binding = row.get("binding", {})
                if (row.get("schema") == VERSION and row.get("status") == "loaded"
                        and row.get("restored") is True and binding.get("driver") == driver_key
                        and binding.get("game_sha256") == app.installation.executable_sha256
                        and binding.get("resources", {}).get("implementation_sha256") == digest(Path(resource_identity.__file__))
                        and binding.get("runner_sha256") == digest(Path(__file__))):
                    calibrated = True
        if not calibrated: parser.error("No restored successful single-scenario calibration for this runner/driver/game")
    print(json.dumps(dict(schema=VERSION, run=args.run, jobs=[{k: j[k] for k in
        ("input_identity", "name", "scenario", "scenario_title")} for j in jobs]), indent=2), flush=True)
    if args.run: run_batch(app, jobs, command, output, args.timeout, memory, args.retry)


if __name__ == "__main__":
    main()
