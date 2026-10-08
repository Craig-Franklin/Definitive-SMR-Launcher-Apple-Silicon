"""Synthetic smoke-test orchestration; never launch or touch the installed game."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts/research/batch_smoke.py"
spec = importlib.util.spec_from_file_location("batch_smoke", MODULE)
batch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = batch
BATCH_CODE = compile(MODULE.read_bytes(), str(MODULE), "exec")
exec(BATCH_CODE, batch.__dict__)


def fresh_batch():
    global batch
    batch = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = batch
    exec(BATCH_CODE, batch.__dict__)
    return batch


class FakeInstallation:
    """Explicit trusted fixture: product paths and stopped state are synthetic."""
    appid = "7600"
    steam_buildid = "synthetic-build"
    bundle_id = "com.feralinteractive.railroads"
    bundle_version = "fixture"
    executable_sha256 = "a" * 64
    beta_keys = ()
    def __init__(self, root):
        self.home = root / "synthetic-home"
        self.support_root = self.home / "Library/Application Support/Feral Interactive/Sid Meier's Railroads!"
        self.profile_root = self.support_root / "VFS/User/AppData/Roaming/My Games/WinDeveloper"
        self.steamapps_root = root / "steamapps"
        self.executable = root / "fixture-game"
        self.executable.write_bytes(b"synthetic game; never executed")
        for part in ("CustomAssets", "UserMaps", "Saves"):
            (self.profile_root / part).mkdir(parents=True, exist_ok=True)
        (self.profile_root / "Settings.ini").write_bytes(b"[User Settings]\nSkipOpeningMovies = 0\n")
        (self.profile_root / "Saves/stock.sav").write_bytes(b"original stock save")
        (self.support_root / "Preferences Data").write_bytes(b"original Feral preferences")
        preferences = self.home / "Library/Preferences"
        preferences.mkdir(parents=True)
        (preferences / (self.bundle_id + ".plist")).write_bytes(b"original global plist")
    def check_current(self): return None
    def game_running(self): return False


def trusted_single(app, jobs, command, output, timeout, memory):
    """Fresh module plus one installed root; no installed-owner reset or replacement."""
    candidate = importlib.util.module_from_spec(spec)
    exec(BATCH_CODE, candidate.__dict__)
    for name in ("MacProcesses", "SafetyError", "DriverCleanupError", "_QueueStopped",
                 "watch", "one_test", "stop_driver", "require_driver_gone",
                 "cleanup_diagnostic", "_atomic_json", "_fsync_dir", "_fsync_tree", "_copytree", "manifest"):
        candidate.__dict__[name] = batch.__dict__[name]
    stock = jobs[0]
    target = dict(stock, input_identity="9" * 64)
    owner = candidate._trusted_install(app, stock, [target], command, output, timeout, memory,
                                      execution_mode="synthetic")
    try:
        token = candidate.run_batch(app, [stock], command, output, timeout, memory, execution_mode="synthetic")
        if not owner.decide(token).next_job_allowed:
            raise batch.SafetyError("Harness failure stops queue; cause resolution required before next job")
        return token
    finally:
        owner._supervisor.close()



class Driver:
    returncode = 0
    def poll(self): return self.returncode


class Processes:
    def __init__(self, current): self.current = current
    def inspect(self, pid): return self.current
    def games(self): return [self.current] if self.current else []


def durable_fixture(root, key="a", status="loaded", restored=True, *, session_name="session-fixed",
                    protocol=None, started_at=100, observed_at=200):
    """Fixed synthetic receipts, never runtime calibration authority."""
    session = root / (session_name + "-" + key); session.mkdir(exist_ok=True)
    run = session / (key * 64); run.mkdir()
    journal = batch.RunJournal(run)
    binding = dict(protocol=protocol, driver={"files": {"synthetic-driver": "d" * 64}},
                   runner_sha256="e" * 64, game_sha256="f" * 64, timeout=90, memory_bytes=6 * 1024**3,
                   resources=dict(stock="1" * 64, implementation_sha256="2" * 64))
    if protocol is not None:
        binding["protocol"] = dict(protocol, driver_receipt=binding["driver"], runner_sha256=binding["runner_sha256"])
    journal.append("started", dict(run_id="synthetic-" + key, input_identity=key * 64,
        engine_input_identity="3" * 64, binding=binding, name="Synthetic", scenario="scenario.xml",
        run_directory=str(run), started_at_us=started_at, execution_mode="synthetic"))
    journal.append("terminal_pending", {"at_us": started_at})
    engine_inputs = {"synthetic_engine_input": True}
    journal.append("selected", dict(process=dict(pid=123, birth_us=456, executable="/synthetic/game"),
        selected_at_us=150, observed_inputs={"synthetic": True}, observed_engine_inputs=engine_inputs,
        observed_input_identity=batch.identity(engine_inputs)))
    journal.append("raw_outcome", dict(status=status, cause="fixture_" + status, reason="fixed fixture",
        process=dict(pid=123, birth_us=456, executable="/synthetic/game"),
        observed_inputs={"synthetic": True}, observed_engine_inputs=engine_inputs,
        observed_input_identity=batch.identity(engine_inputs), observed_at_us=observed_at))
    if restored:
        journal.append("restored", dict(restored=True, at_us=300, elapsed_seconds=.1,
            original_manifest={"synthetic": True}, original_resources_identity="5" * 64))
    recovery = dict(schema=batch.VERSION, session=str(session), current_run=run.name,
                    restoration_pending=True, terminal_state="pending", game_sha256=binding["game_sha256"],
                    original_manifest={"synthetic": True}, original_resources_identity="5" * 64)
    batch._atomic_json(session / "recovery.json", recovery)
    row = batch.journal_api.result_row(journal)
    batch.append_result(root / "results.jsonl", row)
    if restored: batch.finish_terminal(journal, recovery)
    return journal, row


class SmokeTests(unittest.TestCase):
    def setUp(self):
        fresh_batch()
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.process = batch.Process(123, 456, "/synthetic/game", 100)
        self.request = dict(schema=1, pid=123, birth_us=456, input_identity="a" * 64, scenario_name="map.xml")

    def evidence(self):
        shot = self.root / "loaded.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic image fixture")
        return dict(self.request, status="loaded", loaded_scene=True, assertion="Loaded HUD and expected scenario",
                    screenshot=str(shot), screenshot_sha256=batch.digest(shot))

    def test_product_file_roles_deny_directories_without_narrowing_profile_support(self):
        installation=FakeInstallation(self.root)
        app=SimpleNamespace(installation=installation)
        original=batch.capture_external_settings(app)
        self.assertEqual([p.kind for p in original],['file','file'])
        for path in batch.external_settings_locations(app):
            with self.subTest(path=path.name):
                content=path.read_bytes();path.unlink();path.mkdir()
                retained=path/'retained';retained.write_bytes(content)
                self.assertTrue(batch.live_authority.SettingsPreimage.capture(path).supported())
                with self.assertRaisesRegex(batch.SafetyError,'Settings link/type conflict'):
                    batch.capture_external_settings(app)
                self.assertEqual(retained.read_bytes(),content)
                retained.unlink();path.rmdir();path.write_bytes(content)

    def test_libproc_exit_race_requires_independent_pid_absence(self):
        for fail_at in ("path", "birth", "footprint"):
            with self.subTest(fail_at=fail_at):
                probe = object.__new__(batch.MacProcesses)
                def path(pid, buffer, size):
                    buffer.value = b"/synthetic/game"
                    return 0 if fail_at == "path" else len(buffer.value)
                probe.lib = SimpleNamespace(
                    proc_pidpath=path,
                    proc_pidinfo=lambda *args: 0 if fail_at == "birth" else batch.ctypes.sizeof(batch.BSDInfo),
                    proc_pid_rusage=lambda *args: -1)
                with patch.object(batch.os, "kill", side_effect=ProcessLookupError):
                    self.assertIsNone(probe.inspect(123))
                for result in (None, PermissionError):
                    with patch.object(batch.os, "kill", side_effect=result), self.assertRaises(batch.SafetyError):
                        probe.inspect(123)

    def test_termination_waits_for_absence_after_transient_identity_loss(self):
        probe = object.__new__(batch.MacProcesses)
        with patch.object(probe, "inspect", side_effect=[self.process,
                    batch.SafetyError("terminating"), None]), \
             patch.object(batch.os, "kill") as kill, patch.object(batch.time, "sleep"):
            probe.stop(self.process)
        kill.assert_called_once_with(123, signal.SIGTERM)

    def test_exit_between_identity_read_and_signal_is_already_stopped(self):
        probe = object.__new__(batch.MacProcesses)
        with patch.object(probe, "inspect", return_value=self.process), \
             patch.object(batch.os, "kill", side_effect=ProcessLookupError) as kill:
            probe.stop(self.process)
        kill.assert_called_once_with(123, signal.SIGTERM)

    def test_termination_never_escalates_signal_to_unidentified_or_reused_pid(self):
        probe = object.__new__(batch.MacProcesses)
        reused = batch.Process(123, 999, "/synthetic/game", 100)
        for observations in ([self.process, batch.SafetyError("unidentified")],
                             [self.process, reused]):
            with self.subTest(observations=observations), \
                 patch.object(probe, "inspect", side_effect=observations), \
                 patch.object(batch.os, "kill") as kill, \
                 patch.object(batch.time, "monotonic", side_effect=[0, 0, 3]), \
                 patch.object(batch.time, "sleep"), self.assertRaises(batch.SafetyError):
                probe.stop(self.process)
            kill.assert_called_once_with(123, signal.SIGTERM)

    def test_denied_group_probe_requires_independent_absence_evidence(self):
        with patch.object(batch.os, "killpg", side_effect=PermissionError), \
             patch.object(batch.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "111\n222\n", "")
            self.assertFalse(batch.group_exists(333))
            self.assertTrue(batch.group_exists(222))
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            with self.assertRaises(batch.SafetyError):
                batch.group_exists(333)

    def test_denied_cleanup_signal_requires_proven_empty_group(self):
        child = SimpleNamespace(pid=123, returncode=None, wait=lambda timeout: 0)
        with patch.object(batch.os, "killpg", side_effect=PermissionError), \
             patch.object(batch, "group_exists", return_value=False):
            batch.stop_driver(child)
        with patch.object(batch.os, "killpg", side_effect=PermissionError), \
             patch.object(batch, "group_exists", return_value=True):
            with self.assertRaises(PermissionError):
                batch.stop_driver(child)
        with patch.object(batch.os, "killpg", side_effect=PermissionError), \
             patch.object(batch, "group_exists", side_effect=batch.SafetyError("unknown")):
            with self.assertRaises(batch.SafetyError):
                batch.stop_driver(child)

    def test_plan_uses_scenario_title_instead_of_package_or_nested_name(self):
        stock = self.root / "steamapps/common/Sid Meier's Railroads/SMRailroadsData/assets"
        stock.mkdir(parents=True)
        for name in ("CustomAssets", "UserMaps"):
            (self.root / name).mkdir()
        (self.root / "scenario.xml").write_text(
            '<RRTScenario><szMapName>Scenario Display Title</szMapName>'
            '<Goal><szName>Unrelated Goal</szName></Goal></RRTScenario>')
        app = SimpleNamespace(_prepared_root=lambda record: self.root,
                              installation=SimpleNamespace(executable_sha256="a" * 64,
                                                           steamapps_root=self.root / "steamapps"))
        record = SimpleNamespace(variant_id="b" * 64, archive_sha256="c" * 64,
                                 scenarios=("scenario.xml",), name="Package_v9")
        jobs = batch.jobs_for(app, [record], {}, 90, 1024)
        self.assertEqual(jobs[0]["scenario_title"], "Scenario Display Title")

    def test_logging_provenance_is_separate_from_inputs_and_protocol_behavior(self):
        stock = self.root / "steamapps/common/Sid Meier's Railroads/SMRailroadsData/assets"
        stock.mkdir(parents=True)
        prepared = self.root / "prepared"; prepared.mkdir()
        for name in ("CustomAssets", "UserMaps"): (prepared / name).mkdir()
        (prepared / "scenario.xml").write_text('<RRTScenario><szMapName>Fixed Title</szMapName></RRTScenario>')
        app = SimpleNamespace(_prepared_root=lambda _: prepared,
            installation=SimpleNamespace(executable_sha256="a" * 64, steamapps_root=self.root / "steamapps"))
        record = SimpleNamespace(variant_id="b" * 64, archive_sha256="c" * 64,
            scenarios=("scenario.xml",), name="Synthetic")
        protocol = dict(protocol_id="d" * 64, driver_behavior_id="e" * 64, basis="synthetic fixed behavior")
        before = batch.jobs_for(app, [record], {"logging_receipt": "before"}, 90, 6 * 1024**3, protocol=protocol)[0]
        real_digest = batch.digest
        with patch.object(batch, "digest", side_effect=lambda p: "f" * 64 if Path(p) == MODULE else real_digest(p)):
            logged = batch.jobs_for(app, [record], {"logging_receipt": "after"}, 90, 6 * 1024**3,
                protocol=dict(protocol, basis="reviewed logging-only change"))[0]
            changed = batch.jobs_for(app, [record], {"logging_receipt": "after"}, 90, 6 * 1024**3,
                protocol=dict(protocol, driver_behavior_id="0" * 64))[0]
        self.assertEqual(before["engine_input_identity"], logged["engine_input_identity"])
        self.assertEqual(before["input_identity"], logged["input_identity"])
        self.assertNotEqual(before["binding"]["runner_sha256"], logged["binding"]["runner_sha256"])
        self.assertNotEqual(before["binding"]["driver"], logged["binding"]["driver"])
        self.assertEqual(before["engine_input_identity"], changed["engine_input_identity"])
        self.assertNotEqual(before["input_identity"], changed["input_identity"])
        (prepared / "scenario.xml").write_text('<RRTScenario><szMapName>Changed Input</szMapName></RRTScenario>')
        altered = batch.jobs_for(app, [record], {}, 90, 6 * 1024**3, protocol=protocol)[0]
        self.assertNotEqual(before["engine_input_identity"], altered["engine_input_identity"])

    def watch(self, processes=None, driver=None, deadline=10, memory=1000):
        return batch.watch(processes or Processes(self.process), self.process, driver or Driver(),
            self.request, self.root, lambda: None, memory, deadline, clock=lambda: 5,
            sleep=lambda _: self.fail("Fixture should return on its first poll"))

    def test_positive_scene_evidence_and_identity_are_required(self):
        evidence = self.evidence()
        self.assertTrue(batch.validate_evidence(evidence, self.request, self.root))
        for key, value in (("loaded_scene", False), ("assertion", ""), ("birth_us", 999),
                           ("screenshot_sha256", "b" * 64)):
            with self.subTest(key=key), self.assertRaises(batch.SafetyError):
                batch.validate_evidence(dict(evidence, **{key: value}), self.request, self.root)

    def test_external_or_linked_screenshot_rejected(self):
        evidence = self.evidence(); link = self.root / "linked.png"
        link.symlink_to(evidence["screenshot"])
        with self.assertRaises(RuntimeError):
            batch.validate_evidence(dict(evidence, screenshot=str(link)), self.request, self.root)

    def test_exit_success_without_evidence_is_automation_failure(self):
        self.assertEqual(self.watch()["status"], "automation_failed")

    def test_process_exit_limits_and_pid_reuse(self):
        self.assertEqual(self.watch(Processes(None))["status"], "crash_or_exit")
        self.assertEqual(self.watch(Processes(None))["cause"], "spontaneous_exit_unclassified")
        self.assertEqual(self.watch(memory=100)["status"], "memory_limit")
        self.assertEqual(self.watch(memory=100)["cause"], "watchdog_memory")
        self.assertEqual(self.watch(deadline=4)["status"], "timeout")
        self.assertEqual(self.watch(deadline=4)["cause"], "watchdog_timeout")
        with self.assertRaises(batch.SafetyError):
            self.watch(Processes(batch.Process(123, 789, "/synthetic/game", 100)))

    def test_loaded_result_is_accepted_only_while_process_is_live(self):
        (self.root / "driver-result.json").write_text(json.dumps(self.evidence()))
        self.assertEqual(self.watch()["status"], "loaded")
        self.assertEqual(self.watch(Processes(None))["status"], "crash_or_exit")

    def test_settings_only_change_diagnostic_copy_without_adding_bom(self):
        source = self.root / "original.ini"; target = self.root / "diagnostic.ini"
        original = b"SkipOpeningMovies = 0\nLastNumAIPlayers = 3\nUntouched = yes\n"
        source.write_bytes(original); target.write_bytes(original)
        batch.configure(target, "Scenario.XML")
        self.assertEqual(source.read_bytes(), original)
        self.assertIn("SkipOpeningMovies = 1", target.read_text())
        self.assertIn("LastNumAIPlayers = 0", target.read_text())
        self.assertIn("LastScenarioName = scenario.xml", target.read_text())
        self.assertIn("Untouched = yes", target.read_text())
        self.assertFalse(target.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_only_restored_completed_exact_ids_are_cached(self):
        path = self.root / "results.jsonl"
        for key, restored, status in (("a", True, "loaded"), ("b", False, "loaded"), ("c", True, "safety_stop")):
            durable_fixture(self.root, key, status, restored)
        durable_fixture(self.root, "d", "automation_failed", True)
        self.assertEqual(batch.completed(path), set())
        with path.open("a") as stream: stream.write('{"torn":')
        with self.assertRaises(batch.JournalError): batch.completed(path)

    def test_legacy_receipts_cannot_silently_satisfy_new_queue(self):
        path = self.root / "results.jsonl"
        batch.append_result(path, dict(schema=1, input_identity="a" * 64, restored=True, status="loaded"))
        before = path.read_bytes()
        with self.assertRaisesRegex(batch.JournalError, "Historical"): batch.completed(path)
        self.assertEqual(before, path.read_bytes())

    def test_resource_binding_changes_when_only_stock_or_map_mtime_changes(self):
        stock = self.root / "steamapps/common/Sid Meier's Railroads/SMRailroadsData/assets"
        stock.mkdir(parents=True)
        (stock / "RRT_Goods.xml").write_bytes(b"stock")
        prepared = self.root / "prepared"
        for name in ("CustomAssets", "UserMaps"):
            (prepared / name).mkdir(parents=True)
        resource = prepared / "UserMaps/RRT_Goods.xml"
        resource.write_bytes(b"custom")
        app = SimpleNamespace(installation=SimpleNamespace(steamapps_root=self.root / "steamapps"))
        initial = batch.resource_binding(batch.stock_resources(app), batch.map_resources(prepared))
        receipt = self.root / "resources.json"
        batch.check_resources(app, prepared, initial, receipt)
        self.assertEqual(json.loads(receipt.read_text())["custom"]["identity"], initial["custom"])
        for path in (resource, stock / "RRT_Goods.xml"):
            with self.subTest(path=path):
                before = path.stat()
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns+1_000_000))
                with self.assertRaisesRegex(batch.SafetyError, "timestamps changed"):
                    batch.check_resources(app, prepared, initial)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        batch.check_resources(app, prepared, initial)
        (prepared / "Saves").mkdir()
        (prepared / "Saves/new.sav").write_bytes(b"unrelated save")
        batch.check_resources(app, prepared, initial)

    def test_run_requires_foreground_ack_before_discovery(self):
        with patch.object(batch.LauncherApplication, "discover", side_effect=AssertionError("must not discover")):
            with self.assertRaises(SystemExit):
                batch.main(["--run", "--driver", sys.executable, "--output", str(self.root)])

    def test_plan_defaults_remain_six_gib_and_ninety_seconds_without_running(self):
        app = SimpleNamespace(catalogue=lambda: [])
        with patch.object(batch.LauncherApplication, "discover", return_value=app), \
                patch.object(batch, "driver_identity", return_value=(["synthetic-driver"], {})), \
                patch.object(batch, "jobs_for", return_value=[]) as jobs, \
                patch.object(batch, "run_batch", side_effect=AssertionError("plan must not run")):
            batch.main(["--driver", "synthetic-driver", "--output", str(self.root)])
        self.assertEqual(jobs.call_args.args[3:5], (90, 6 * 1024**3))

    @unittest.skipUnless(os.name == "posix", "Dedicated POSIX process groups")
    def test_driver_group_including_term_ignoring_child_is_gone(self):
        # No GUI or game. A child that ignores TERM reproduces the cleanup risk.
        code = """import subprocess, sys, signal, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c',
    'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)'])
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(120)
"""
        marker = self.root / "child.pid"
        driver = subprocess.Popen([sys.executable, "-c", code, str(marker)], start_new_session=True)
        try:
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline: time.sleep(.02)
            self.assertTrue(marker.exists())
            batch.stop_driver(driver)
            self.assertIsNotNone(driver.returncode)
            with self.assertRaises(ProcessLookupError): os.killpg(driver.pid, 0)
        finally:
            if driver.returncode is None:
                try: os.killpg(driver.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                driver.wait(timeout=3)

    @unittest.skipUnless(sys.platform == "darwin", "Read-only libproc ABI test on the test process")
    def test_libproc_reads_current_test_process(self):
        probe = batch.MacProcesses(sys.executable)
        observed = probe.inspect(os.getpid())
        self.assertEqual(observed.pid, os.getpid())
        self.assertGreater(observed.birth_us, 0)
        self.assertGreater(observed.footprint, 0)
        self.assertTrue(observed.same(probe.inspect(os.getpid())))


@unittest.skipUnless(sys.platform == "darwin", "Synthetic profile switching uses APFS exchange")
class RestoreTests(unittest.TestCase):
    setUp = SmokeTests.setUp
    def test_driver_cleanup_failure_stops_game_but_blocks_restore_until_group_gone(self):

        from smr_launcher.application import LauncherApplication
        from smr_launcher.activation import ORIGINAL
        import shutil
        installation = FakeInstallation(self.root)
        installation.executable = self.root / "fake.app/Contents/MacOS/game"
        (installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets").mkdir(parents=True)
        app = LauncherApplication(installation, self.root / "library")
        app.setup(); original = batch.manifest(app.profiles.live)
        prepared = self.root / "prepared"
        shutil.copytree(app.profiles.live, prepared)
        (prepared / "map.xml").write_text("<RRTScenario/>")
        job = dict(input_identity="a" * 64, name="Synthetic", scenario="map.xml",
                   scenario_name="map.xml", scenario_title="Synthetic", prepared_root=str(prepared),
                   binding=dict(game_sha256=installation.executable_sha256,
                       options={},
                       prepared=batch.manifest(prepared), resources=batch.resource_binding(
                       batch.stock_resources(app), batch.map_resources(prepared))))
        processes = Processes(None)
        stopped = []
        def stop(process):
            self.assertTrue(process.same(processes.current))
            stopped.append(process); processes.current = None
        processes.stop = stop
        def play(profile, launch, **kwargs):
            app.profiles.switch(profile)
            processes.current = batch.Process(123, time.time_ns() // 1000, "/synthetic/game", 10000)
        driver = Driver(); driver.pid = 456
        output = self.root / "private"
        with patch.object(batch, "MacProcesses", return_value=processes), \
                patch.object(app.profiles, "play", side_effect=play), \
                patch.object(batch.subprocess, "Popen", return_value=driver), \
                patch.object(batch, "watch", return_value=dict(status="memory_limit")), \
                patch.object(batch, "stop_driver", side_effect=batch.SafetyError("descendant remains")):
            with self.assertRaises(batch.DriverCleanupError):
                trusted_single(app, [job], [sys.executable], output, 90, 1000)
        self.assertEqual(len(stopped), 1)
        self.assertIsNone(processes.current)
        active = app.profiles.active_profile()
        self.assertNotEqual(active, ORIGINAL)
        self.assertEqual(batch.manifest(app.profiles._profile_path(ORIGINAL)), original)
        row = json.loads((output / "results.jsonl").read_text())
        self.assertFalse(row["restored"])
        session = next(output.glob("session-*"))
        recovery = json.loads((session / "recovery.json").read_text())
        self.assertTrue(recovery["restoration_pending"])
        self.assertEqual(recovery["driver_group"], 456)
        with patch.object(batch.os, "killpg", return_value=None):
            with self.assertRaises((batch.SafetyError, batch.JournalError)):
                batch.recover_session(app, session)
        self.assertEqual(app.profiles.active_profile(), active)
        with patch.object(batch.os, "killpg", side_effect=ProcessLookupError):
            with self.assertRaises((batch.SafetyError, batch.JournalError)):
                batch.recover_session(app, session)
        self.assertEqual(batch.manifest(app.profiles._profile_path(ORIGINAL)), original)

    def test_profile_and_saved_bytes_restore_after_failure(self):

        from smr_launcher.application import LauncherApplication
        from smr_launcher.activation import ORIGINAL
        installation = FakeInstallation(self.root)
        installation.executable = self.root / "fake.app/Contents/MacOS/game"
        app = LauncherApplication(installation, self.root / "library")
        app.setup(); original = batch.manifest(app.profiles.live)
        diagnostic = self.root / "prepared"
        import shutil
        shutil.copytree(app.profiles.live, diagnostic)
        (diagnostic / "Saves/stock.sav").unlink()
        variant = hashlib.sha256(b"diagnostic").hexdigest()
        profile = app.profiles.register_variant(variant, diagnostic)
        def fail_after_switch(*args):
            app.profiles.switch(profile)
            (app.profiles.live / "Saves/test-only.sav").write_text("test save")
            (app.profiles.live / "Settings.ini").write_text("test settings")
            raise RuntimeError("synthetic driver failure")
        job = dict(input_identity="a" * 64, name="Synthetic", scenario="map.xml", binding={})
        with patch.object(batch, "MacProcesses", return_value=Processes(None)), \
                patch.object(batch, "one_test", side_effect=fail_after_switch):
            with self.assertRaisesRegex(RuntimeError, "synthetic driver failure"):
                trusted_single(app, [job], [sys.executable], self.root / "private", 90, 1000)
        self.assertEqual(app.profiles.active_profile(), ORIGINAL)
        self.assertEqual(batch.manifest(app.profiles.live), original)
        row = json.loads((self.root / "private/results.jsonl").read_text())
        self.assertTrue(row["restored"])
        self.assertEqual(row["status"], "safety_stop")

        session = next((self.root / "private").glob("session-*"))
        recovery_file = session / "recovery.json"
        recovery = json.loads(recovery_file.read_text())
        recovery.update(restoration_pending=True, diagnostic_profile=profile)
        recovery_file.write_text(json.dumps(recovery))
        with self.assertRaises((batch.SafetyError, batch.JournalError)):
            batch.recover_session(app, session)
        # Reopening a restored transaction by editing its mutable checkpoint is
        # not new recovery authority. Preservation checks on genuinely pending
        # sessions are exercised below with independent synthetic failures.


@unittest.skipUnless(sys.platform == "darwin", "Synthetic profile switching uses APFS exchange")
class DurabilityIntegrationTests(unittest.TestCase):
    setUp = SmokeTests.setUp

    def setup_application(self):

        installation = FakeInstallation(self.root)
        installation.executable = self.root / "synthetic.app/Contents/MacOS/game"
        (installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets").mkdir(parents=True)
        self.app = batch.LauncherApplication(installation, self.root / "library")
        self.app.setup()
        self.original = batch.manifest(self.app.profiles.live)
        prepared = self.root / "prepared"
        import shutil
        shutil.copytree(self.app.profiles.live, prepared)
        (prepared / "UserMaps/scenario.xml").write_text('<RRTScenario><szMapName>Synthetic</szMapName></RRTScenario>')
        record = SimpleNamespace(variant_id="b" * 64, archive_sha256="c" * 64,
                                 scenarios=("UserMaps/scenario.xml",), name="Synthetic")
        with patch.object(self.app, "_prepared_root", return_value=prepared):
            self.job = batch.jobs_for(self.app, [record], {}, 90, 6 * 1024**3)[0]
        self.output = self.root / "evidence"
        self.processes = Processes(None)
        self.driver = SimpleNamespace(pid=456, returncode=0, poll=lambda: 0)
        self.raw_at_stop = []

    def fixtures(self, watch_result, *, stop_driver_error=None):
        stack = ExitStack()
        def play(profile, launch, **kwargs):
            self.app.profiles.switch(profile)
            self.processes.current = batch.Process(123, time.time_ns() // 1000,
                                                   str(self.app.installation.executable), 1000)
        def stop(process):
            self.assertTrue(process.same(self.processes.current))
            run = next(self.output.glob("session-*/*/raw_outcome.json"))
            self.raw_at_stop.append(run.read_bytes())
            self.processes.current = None
        def stop_driver(driver):
            self.assertTrue(next(self.output.glob("session-*/*/raw_outcome.json")).is_file())
            if stop_driver_error: raise stop_driver_error
        self.processes.stop = stop
        stack.enter_context(patch.object(batch, "MacProcesses", return_value=self.processes))
        stack.enter_context(patch.object(self.app.profiles, "play", side_effect=play))
        stack.enter_context(patch.object(batch.subprocess, "Popen", return_value=self.driver))
        stack.enter_context(patch.object(batch, "stop_driver", side_effect=stop_driver))
        stack.enter_context(patch.object(batch, "require_driver_gone"))
        stack.enter_context(patch.object(batch, "watch", side_effect=watch_result if isinstance(watch_result, BaseException) else None,
                                         return_value=watch_result))
        return stack

    def run_fixture(self, jobs=None):
        return trusted_single(self.app, jobs or [self.job], [sys.executable], self.output, 90, 6 * 1024**3)

    def test_raw_selected_inputs_and_watchdog_cause_are_durable_before_stop_and_restore(self):
        self.setup_application()
        with self.fixtures(dict(status="memory_limit", cause="watchdog_memory", peak_footprint=6 * 1024**3)):
            self.run_fixture()
        row = batch.result_rows(self.output / "results.jsonl")[0]
        journal = batch.RunJournal(Path(row["run_directory"]))
        selected = journal.read("selected")["payload"]
        self.assertEqual(selected["process"]["pid"], 123)
        self.assertGreater(selected["process"]["birth_us"], 0)
        self.assertEqual(selected["process"]["executable"], str(self.app.installation.executable))
        self.assertEqual(selected["observed_inputs"]["option_state"], "unattested")
        self.assertEqual(row["raw_outcome"]["cause"], "watchdog_memory")
        self.assertEqual(row["raw_outcome"]["process"], selected["process"])
        self.assertEqual(selected["observed_input_identity"], batch.identity(selected["observed_engine_inputs"]))
        self.assertEqual(row["raw_outcome"]["observed_input_identity"], selected["observed_input_identity"])
        self.assertNotIn("planned_input_identity", selected["observed_engine_inputs"])
        self.assertEqual(self.raw_at_stop, [journal.path("raw_outcome").read_bytes()])
        self.assertEqual(batch.manifest(self.app.profiles.live), self.original)
        self.assertTrue((Path(row["run_directory"]) / "diagnostic-saves").is_dir())

    def test_restored_automation_failure_stops_second_job_and_later_resume(self):
        self.setup_application()
        second = dict(self.job, input_identity="0" * 64)
        with self.fixtures(dict(status="automation_failed", reason="synthetic missing HUD")), \
                self.assertRaisesRegex(batch.SafetyError, "Harness failure stops queue"):
            self.run_fixture([self.job, second])
        rows = batch.result_rows(self.output / "results.jsonl")
        self.assertEqual(len(rows), 1); self.assertTrue(rows[0]["restored"])
        self.assertEqual(batch.manifest(self.app.profiles.live), self.original)
        self.assertEqual(batch.completed(self.output / "results.jsonl"), set())
        with patch.object(batch, "MacProcesses", side_effect=AssertionError("must not probe runtime")), \
                self.assertRaisesRegex(batch.SafetyError, "cause resolution"):
            self.run_fixture()

    def test_lost_permission_preserves_loaded_raw_and_requires_explicit_recovery(self):
        self.setup_application()
        with self.fixtures(dict(status="loaded", reason="synthetic positive fixture"), stop_driver_error=PermissionError("lost permission")), \
                self.assertRaises(batch.DriverCleanupError):
            self.run_fixture()
        row = batch.result_rows(self.output / "results.jsonl")[0]
        self.assertEqual(row["status"], "loaded"); self.assertFalse(row["restored"])
        self.assertIn("lost permission", row["harness_error"])
        self.assertEqual(batch.completed(self.output / "results.jsonl"), set())
        raw = batch.RunJournal(Path(row["run_directory"])).path("raw_outcome").read_bytes()
        session = Path(row["run_directory"]).parent
        with patch.object(batch, "require_driver_gone"):
            with self.assertRaises((batch.SafetyError, batch.JournalError)):
                batch.recover_session(self.app, session)
        restored = batch.result_rows(self.output / "results.jsonl")[0]
        self.assertFalse(restored["restored"]); self.assertEqual(restored["status"], "loaded")
        self.assertEqual(batch.RunJournal(Path(row["run_directory"])).path("raw_outcome").read_bytes(), raw)
        self.assertEqual(batch.completed(self.output / "results.jsonl"), set())

    def test_restoration_failure_keeps_raw_outcome_and_stops_queue(self):
        self.setup_application()
        real_switch = self.app.profiles.switch
        def switch(profile):
            if profile == "original-game": raise PermissionError("synthetic restoration permission")
            return real_switch(profile)
        with self.fixtures(dict(status="timeout", cause="watchdog_timeout")), \
                patch.object(self.app.profiles, "switch", side_effect=switch), self.assertRaises(PermissionError):
            self.run_fixture([self.job, dict(self.job, input_identity="0" * 64)])
        row = batch.result_rows(self.output / "results.jsonl")[0]
        self.assertEqual(row["status"], "timeout"); self.assertFalse(row["restored"])
        self.assertIn("restoration permission", row["restoration_error"])
        self.assertEqual(len(list(self.output.glob("session-*/*/started.json"))), 1)
        self.assertEqual(batch.manifest(self.app.profiles._profile_path("original-game")), self.original)
        session = Path(row["run_directory"]).parent
        inactive = self.app.profiles._profile_path("original-game")
        asset_root = inactive / "CustomAssets"; before = asset_root.stat()
        os.utime(asset_root, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000))
        with self.assertRaises((batch.SafetyError, batch.JournalError)):
            batch.recover_session(self.app, session)
        os.utime(asset_root, ns=(before.st_atime_ns, before.st_mtime_ns))
        save = inactive / "Saves/stock.sav"; saved = save.read_bytes()
        save.write_text("new personal progress")
        with self.assertRaises((batch.SafetyError, batch.JournalError)):
            batch.recover_session(self.app, session)
        self.assertEqual(save.read_text(), "new personal progress")
        save.write_bytes(saved)

    def test_raw_fsync_failure_does_not_allow_automatic_restore_or_cleanup(self):
        self.setup_application()
        real_write = batch.journal_api.write_once
        def fail_raw(path, value):
            real_write(path, value)
            if path.name == "raw_outcome.json": raise batch.JournalError("simulated post-write fsync failure")
        with self.fixtures(dict(status="loaded")), patch.object(batch.journal_api, "write_once", side_effect=fail_raw), \
                patch.object(batch, "cleanup_diagnostic", side_effect=AssertionError("must not cleanup")), \
                self.assertRaises(batch.JournalError): self.run_fixture()
        session = next(self.output.glob("session-*"))
        self.assertTrue(json.loads((session / "recovery.json").read_text())["restoration_pending"])
        self.assertFalse(list(session.glob("*/restored.json")))
        self.assertNotEqual(self.app.profiles.active_profile(), "original-game")
        self.assertEqual(batch.manifest(self.app.profiles._profile_path("original-game")), self.original)
        with patch.object(batch, "require_driver_gone"):
            with self.assertRaises((batch.SafetyError, batch.JournalError)):
                batch.recover_session(self.app, session)
        self.assertEqual(batch.completed(self.output / "results.jsonl"), set())
        run = next(session.glob("*/raw_outcome.json")).parent
        self.assertEqual(batch.RunJournal(run).read("raw_outcome", True)["payload"]["status"], "loaded")
        self.assertFalse((run / "harness_failure.json").exists())

    def test_interrupted_missing_outcome_recovery_records_unknown_and_retains_original(self):
        self.setup_application()
        with self.fixtures(dict(status="loaded")), \
                patch.object(batch.RunJournal, "outcome", side_effect=batch.JournalError("interrupted before write")), \
                self.assertRaises(batch.JournalError): self.run_fixture()
        session = next(self.output.glob("session-*"))
        with patch.object(batch, "require_driver_gone"):
            with self.assertRaises((batch.SafetyError, batch.JournalError)):
                batch.recover_session(self.app, session)
        self.assertFalse(list(session.glob("*/raw_outcome.json")))
        self.assertEqual(batch.manifest(self.app.profiles._profile_path("original-game")), self.original)
        self.assertEqual(batch.completed(self.output / "results.jsonl"), set())

    def test_pending_activation_transaction_blocks_without_implicit_recovery(self):
        self.setup_application()
        self.app.profiles.journal.write_bytes(b'{"pending":"synthetic"}')
        before = self.app.profiles.journal.read_bytes()
        with patch.object(self.app, "_locked", side_effect=AssertionError("must not implicitly recover")), \
                patch.object(batch, "MacProcesses", return_value=self.processes), \
                self.assertRaisesRegex(batch.SafetyError, "Pending activation transaction"):
            self.run_fixture()
        self.assertEqual(self.app.profiles.journal.read_bytes(), before)
        self.assertEqual(batch.manifest(self.app.profiles.live), self.original)

    def test_save_fsync_failure_retains_diagnostic_profile_and_blocks_baseline(self):
        self.setup_application()
        real_fsync = batch._fsync_tree
        def fsync(path):
            if Path(path).name == "diagnostic-saves": raise OSError("synthetic save fsync failure")
            return real_fsync(path)
        with self.fixtures(dict(status="memory_limit", cause="watchdog_memory")), \
                patch.object(batch, "_fsync_tree", side_effect=fsync), self.assertRaises(OSError):
            self.run_fixture()
        row = batch.result_rows(self.output / "results.jsonl")[0]
        recovery = json.loads((Path(row["run_directory"]).parent / "recovery.json").read_text())
        self.assertTrue(self.app.profiles._profile_path(recovery["diagnostic_profile"]).is_dir())
        self.assertIn("save fsync failure", row["cleanup_error"])
        self.assertEqual(row["status"], "memory_limit")
        self.assertTrue(row["restored"])
        self.assertEqual(batch.manifest(self.app.profiles.live), self.original)
        self.assertEqual(batch.completed(self.output / "results.jsonl"), set())


@unittest.skipUnless(sys.platform == "darwin", "Synthetic profile switching uses APFS exchange")
class TerminalBarrierTests(unittest.TestCase):
    setUp = SmokeTests.setUp
    setup_application = DurabilityIntegrationTests.setup_application
    fixtures = DurabilityIntegrationTests.fixtures
    run_fixture = DurabilityIntegrationTests.run_fixture

    def assert_blocked_before_process(self):
        for retry in (False, True):
            with patch.object(batch, "MacProcesses", side_effect=AssertionError("runtime gate opened")) as constructor:
                with self.assertRaises((batch.SafetyError, batch.JournalError)):
                    batch.run_batch(self.app, [self.job], ["synthetic"], self.output, 90, 6 * 1024**3,
                                    retry=retry, execution_mode="synthetic")
                self.assertEqual(constructor.call_count, 0)
        try:
            self.assertEqual(batch.completed(self.output / "results.jsonl"), set())
        except batch.JournalError:
            pass  # Unindexed/torn current evidence also refuses all credit.

    def terminal_fault(self, stack, mode):
        real_write = batch.journal_api.write_once
        real_journal_sync = batch.journal_api._fsync_dir
        real_activation_sync = sys.modules["smr_launcher.activation"]._fsync_dir
        real_sync = batch._fsync_dir
        real_atomic = batch._atomic_json
        real_fsync = os.fsync
        real_copy = batch._copytree
        real_tree = batch._fsync_tree
        real_manifest = batch.manifest
        real_remove = batch.shutil.rmtree
        def write(path, value):
            if mode == "cleanup_receipt_before" and path.name == "cleanup_failure.json":
                raise batch.JournalError("error receipt creation unavailable")
            for phase in ("terminal_pending", "restored", "terminal_finished"):
                if mode == phase + "_before" and path.name == phase + ".json":
                    raise KeyboardInterrupt("interrupted " + mode)
            real_write(path, value)
            for phase in ("terminal_pending", "restored", "terminal_finished"):
                if mode == phase + "_after" and path.name == phase + ".json":
                    raise KeyboardInterrupt("interrupted " + mode)
            if mode == "cleanup_receipt" and path.name == "cleanup_failure.json":
                raise batch.JournalError("error receipt unavailable after full-looking bytes")
        def sync(path):
            checkpoint = Path(path) / "recovery.json"
            if mode == "checkpoint_directory_fsync" and checkpoint.exists():
                if json.loads(checkpoint.read_bytes()).get("terminal_state") == "finished":
                    raise PermissionError("checkpoint directory fsync refused")
            if mode == "index_directory_fsync" and path == self.output and (path / "results.jsonl").exists():
                raise PermissionError("index directory fsync refused")
            if mode == "profile_parent_fsync" and Path(path) == self.app.profiles._profile_path("original-game").parent:
                if list(self.output.glob("session-*/*/diagnostic-saves")):
                    raise PermissionError("profile cleanup directory fsync refused")
            return real_sync(path)
        def fsync(fd):
            index = self.output / "results.jsonl"
            if mode == "index_file_fsync" and index.exists() and os.fstat(fd).st_ino == index.stat().st_ino:
                raise PermissionError("index file fsync refused")
            if mode == "terminal_finished_file_fsync":
                for path in self.output.glob("session-*/*/terminal_finished.json"):
                    if os.fstat(fd).st_ino == path.stat().st_ino:
                        raise PermissionError("finished receipt file fsync refused")
            return real_fsync(fd)
        def journal_sync(path):
            if mode == "terminal_finished_directory_fsync" and (Path(path) / "terminal_finished.json").exists():
                raise PermissionError("finished receipt directory fsync refused")
            return real_journal_sync(path)
        def activation_sync(path):
            checkpoint = Path(path) / "recovery.json"
            if mode == "checkpoint_directory_fsync" and checkpoint.exists():
                if json.loads(checkpoint.read_bytes()).get("terminal_state") == "finished":
                    raise PermissionError("checkpoint directory fsync refused")
            return real_activation_sync(path)
        def atomic(path, value):
            if mode == "rollback_preimage" and Path(path).name == "checkpoint-rollback.json":
                raise KeyboardInterrupt("rollback preimage interrupted")
            real_atomic(path, value)
            if mode == "final_checkpoint" and Path(path).name == "recovery.json" and value.get("terminal_state") == "finished":
                raise PermissionError("final checkpoint directory fsync refused with full bytes")
        def copy(src, dst, *args, **kwargs):
            if mode == "save_copy" and Path(dst).name == "diagnostic-saves":
                raise PermissionError("save copy interrupted")
            return real_copy(src, dst, *args, **kwargs)
        def tree(path):
            if mode == "save_fsync" and Path(path).name == "diagnostic-saves":
                raise PermissionError("save fsync refused")
            if mode == "before_cleanup_fsync" and (Path(path) / "restored.json").exists():
                raise KeyboardInterrupt("terminal cleanup fsync interrupted")
            result = real_tree(path)
            if mode == "save_rehash" and Path(path).name == "diagnostic-saves":
                (Path(path) / "future.sav").write_bytes(b"changed preserved copy")
            if mode == "after_copy_fsync" and (Path(path) / "diagnostic-saves").exists():
                raise KeyboardInterrupt("terminal save evidence fsync interrupted")
            return result
        def manifest(path):
            value = real_manifest(path)
            if mode == "initial_save_rehash" and Path(path).name == "diagnostic-saves":
                return dict(value, files={})
            return value
        def remove(path, **kwargs):
            if mode == "profile_cleanup" and Path(path).name.startswith("map-"):
                raise KeyboardInterrupt("profile unlink interrupted")
            if mode == "input_cleanup" and Path(path).name == "diagnostic-input":
                raise KeyboardInterrupt("input unlink interrupted")
            return real_remove(path, **kwargs)
        for obj, name, fn in ((batch.journal_api, "write_once", write), (batch, "_fsync_dir", sync),
                             (batch.journal_api, "_fsync_dir", journal_sync),
                             (sys.modules["smr_launcher.activation"], "_fsync_dir", activation_sync),
                             (batch.os, "fsync", fsync), (batch, "_atomic_json", atomic),
                             (batch, "_copytree", copy), (batch, "_fsync_tree", tree),
                             (batch, "manifest", manifest), (batch.shutil, "rmtree", remove)):
            stack.enter_context(patch.object(obj, name, side_effect=fn))
        if mode in ("cleanup_receipt", "cleanup_receipt_before"):
            stack.enter_context(patch.object(batch, "cleanup_diagnostic", side_effect=PermissionError("save copy denied")))

    def test_interruptions_at_terminal_steps_never_credit_or_reach_process_constructor(self):
        modes = ("terminal_pending_before", "terminal_pending_after", "restored_before", "restored_after",
                 "index_file_fsync", "index_directory_fsync", "before_cleanup_fsync", "save_copy",
                 "initial_save_rehash", "save_fsync", "save_rehash", "after_copy_fsync",  "cleanup_receipt", "cleanup_receipt_before", "rollback_preimage",
                 "terminal_finished_before", "terminal_finished_after", "terminal_finished_file_fsync",
                 "terminal_finished_directory_fsync", "final_checkpoint", "checkpoint_directory_fsync")
        base = self.root
        for mode in modes:
            with self.subTest(mode=mode):
                self.root = base / mode; self.root.mkdir()
                self.setup_application()
                def watch(*args, **kwargs):
                    (self.app.profiles.live / "Saves/future.sav").write_bytes(b"future diagnostic progress")
                    return dict(status="loaded", cause="synthetic_load")
                with self.fixtures(dict(status="loaded")) as stack:
                    stack.enter_context(patch.object(batch, "watch", side_effect=watch))
                    self.terminal_fault(stack, mode)
                    with self.assertRaises((KeyboardInterrupt, OSError, batch.JournalError, batch.SafetyError)):
                        self.run_fixture([self.job, dict(self.job, input_identity="0" * 64)])
                session = next(self.output.glob("session-*"))
                checkpoint = batch.journal_api.read_json(session / "recovery.json")
                self.assertTrue(checkpoint["restoration_pending"])
                self.assertEqual(checkpoint["terminal_state"], "pending")
                self.assertEqual(len(list(session.glob("*/started.json"))), 1)
                self.assert_blocked_before_process()
                run = next(session.glob("*/started.json")).parent
                raw = run / "raw_outcome.json"
                if raw.exists():
                    before = raw.read_bytes()
                    try: rows = batch.result_rows(self.output / "results.jsonl")
                    except batch.JournalError: rows = []
                    if rows:
                        self.assertFalse(batch.calibration_matches(rows[-1], self.job, execution_mode="synthetic"))
                    self.assertEqual(raw.read_bytes(), before)
                    profile = self.app.profiles._profile_path(checkpoint["diagnostic_profile"])
                    sources = [profile / "Saves/future.sav", run / "diagnostic-saves/future.sav"]
                    self.assertTrue(any(p.exists() and p.read_bytes() == b"future diagnostic progress" for p in sources))
                self.assertEqual(batch.manifest(self.app.profiles.live), self.original)
                if mode in ("index_file_fsync", "index_directory_fsync"):
                    self.assertTrue((self.output / "results.jsonl").read_bytes().endswith(b"\n"))

    def test_runner_generated_stale_clean_calibration_and_fresh_finished_positive(self):
        self.setup_application()
        self.job["binding"]["protocol"] = dict(protocol_id="6" * 64, driver_behavior_id="7" * 64,
            basis="Synthetic gate fixture only", driver_receipt=self.job["binding"]["driver"],
            runner_sha256=self.job["binding"]["runner_sha256"])
        with self.fixtures(dict(status="loaded")), \
                patch.object(batch, "cleanup_diagnostic", side_effect=PermissionError("genuine cleanup fault")), \
                self.assertRaises(PermissionError): self.run_fixture()
        lines = [json.loads(line) for line in (self.output / "results.jsonl").read_bytes().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertIsNone(lines[0]["cleanup_error"])
        self.assertEqual(lines[1]["cleanup_error"], "genuine cleanup fault")
        batch.journal_api.validate_row(lines[0])
        self.assertFalse(batch.calibration_matches(lines[0], self.job, execution_mode="synthetic"))
        self.output = self.root / "failed-attempt"
        with self.fixtures(dict(status="automation_failed")), self.assertRaises(batch.SafetyError):
            self.run_fixture()
        failed = batch.result_rows(self.output / "results.jsonl")[0]
        resolved_at = time.time_ns() // 1000
        authority = {failed["raw_outcome_id"]: dict(schema="smoke-retry-authority-v1",
            failed_raw_outcome_id=failed["raw_outcome_id"], target_input_identity=self.job["input_identity"],
            cause_resolution="Exact synthetic cause resolved", resolved_at_us=resolved_at,
            execution_mode="synthetic", calibration_row=lines[0])}
        self.assertFalse(batch.retry_authorized(failed, self.job, authority, execution_mode="synthetic"))
        self.output = self.root / "fresh-finished"
        with self.fixtures(dict(status="loaded")): self.run_fixture()
        fresh = batch.result_rows(self.output / "results.jsonl")[0]
        authority[failed["raw_outcome_id"]]["calibration_row"] = fresh
        self.assertFalse(batch.calibration_matches(fresh, self.job, execution_mode="synthetic"))
        self.assertFalse(batch.retry_authorized(failed, self.job, authority, execution_mode="synthetic"))
        self.assertFalse(batch.retry_authorized(failed, self.job, authority))
        self.assertEqual(batch.completed(self.output / "results.jsonl"), set())

    def test_explicit_restored_pending_reconciliation_retains_sources_and_failed_disposition(self):
        self.setup_application()
        with self.fixtures(dict(status="loaded")) as stack:
            self.terminal_fault(stack, "cleanup_receipt_before")
            with self.assertRaises(batch.JournalError): self.run_fixture()
        session = next(self.output.glob("session-*"))
        run = next(session.glob("*/raw_outcome.json")).parent
        raw = (run / "raw_outcome.json").read_bytes()
        restored = (run / "restored.json").read_bytes()
        checkpoint = batch.journal_api.read_json(session / "recovery.json")
        profile = self.app.profiles._profile_path(checkpoint["diagnostic_profile"])
        before = batch.manifest(profile)
        with patch.object(batch, "require_driver_gone"), \
                patch.object(batch, "cleanup_diagnostic", side_effect=AssertionError("retain save sources during reconciliation")):
            with self.assertRaises((batch.SafetyError, batch.JournalError)):
                batch.recover_session(self.app, session)
        self.assertEqual((run / "raw_outcome.json").read_bytes(), raw)
        self.assertEqual((run / "restored.json").read_bytes(), restored)
        self.assertEqual(batch.manifest(profile), before)
        current = batch.result_rows(self.output / "results.jsonl")[0]
        self.assertIsNone(current["harness_error"])
        self.assertFalse(batch.terminal_authoritative(current))
        self.assertEqual(batch.completed(self.output / "results.jsonl"), set())
        with self.assertRaisesRegex(batch.SafetyError, "Unfinished session"):
            batch.queue_state(self.output, [self.job], execution_mode="synthetic")

    def test_full_looking_finished_receipt_with_pending_checkpoint_is_inspection_only(self):
        self.setup_application()
        with self.fixtures(dict(status="loaded")) as stack:
            self.terminal_fault(stack, "terminal_finished_after")
            with self.assertRaises(KeyboardInterrupt): self.run_fixture()
        session = next(self.output.glob("session-*"))
        run = next(session.glob("*/raw_outcome.json")).parent
        before = {p.name: p.read_bytes() for p in run.glob("*.json")}
        with self.assertRaises((batch.SafetyError, batch.JournalError)):
            batch.recover_session(self.app, session)
        self.assertEqual(before, {p.name: p.read_bytes() for p in run.glob("*.json")})
        self.assert_blocked_before_process()


if __name__ == "__main__": unittest.main()
