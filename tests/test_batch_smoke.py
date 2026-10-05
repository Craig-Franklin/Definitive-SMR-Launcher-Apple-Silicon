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
from types import SimpleNamespace
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts/research/batch_smoke.py"
spec = importlib.util.spec_from_file_location("batch_smoke", MODULE)
batch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = batch
spec.loader.exec_module(batch)


class Driver:
    returncode = 0
    def poll(self): return self.returncode


class Processes:
    def __init__(self, current): self.current = current
    def inspect(self, pid): return self.current
    def games(self): return [self.current] if self.current else []


class SmokeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.process = batch.Process(123, 456, "/synthetic/game", 100)
        self.request = dict(schema=1, pid=123, birth_us=456, input_identity="a" * 64, scenario_name="map.xml")

    def evidence(self):
        shot = self.root / "loaded.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic image fixture")
        return dict(self.request, status="loaded", loaded_scene=True, assertion="Loaded HUD and expected scenario",
                    screenshot=str(shot), screenshot_sha256=batch.digest(shot))

    def test_plan_uses_scenario_title_instead_of_package_or_nested_name(self):
        (self.root / "scenario.xml").write_text(
            '<RRTScenario><szMapName>Scenario Display Title</szMapName>'
            '<Goal><szName>Unrelated Goal</szName></Goal></RRTScenario>')
        app = SimpleNamespace(_prepared_root=lambda record: self.root,
                              installation=SimpleNamespace(executable_sha256="a" * 64))
        record = SimpleNamespace(variant_id="b" * 64, archive_sha256="c" * 64,
                                 scenarios=("scenario.xml",), name="Package_v9")
        jobs = batch.jobs_for(app, [record], {}, 90, 1024)
        self.assertEqual(jobs[0]["scenario_title"], "Scenario Display Title")

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
        self.assertEqual(self.watch(memory=100)["status"], "memory_limit")
        self.assertEqual(self.watch(deadline=4)["status"], "timeout")
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
            batch.append_result(path, dict(schema=1, input_identity=key, restored=restored, status=status))
        self.assertEqual(batch.completed(path), {"a"})
        with path.open("a") as stream: stream.write('{"torn":')
        with self.assertRaises(ValueError): batch.completed(path)

    def test_run_requires_foreground_ack_before_discovery(self):
        with patch.object(batch.LauncherApplication, "discover", side_effect=AssertionError("must not discover")):
            with self.assertRaises(SystemExit):
                batch.main(["--run", "--driver", sys.executable, "--output", str(self.root)])

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
        from test_application import FakeInstallation
        from smr_launcher.application import LauncherApplication
        from smr_launcher.activation import ORIGINAL
        import shutil
        installation = FakeInstallation(self.root)
        installation.executable = self.root / "fake.app/Contents/MacOS/game"
        app = LauncherApplication(installation, self.root / "library")
        app.setup(); original = batch.manifest(app.profiles.live)
        prepared = self.root / "prepared"
        shutil.copytree(app.profiles.live, prepared)
        job = dict(input_identity="a" * 64, name="Synthetic", scenario="map.xml",
                   scenario_name="map.xml", scenario_title="Synthetic", prepared_root=str(prepared),
                   binding=dict(prepared=batch.manifest(prepared)))
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
                batch.run_batch(app, [job], [sys.executable], output, 90, 1000)
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
            with self.assertRaisesRegex(batch.SafetyError, "Driver group still exists"):
                batch.recover_session(app, session)
        self.assertEqual(app.profiles.active_profile(), active)
        with patch.object(batch.os, "killpg", side_effect=ProcessLookupError):
            self.assertEqual(batch.recover_session(app, session), "restored")
        self.assertEqual(batch.manifest(app.profiles.live), original)

    def test_profile_and_saved_bytes_restore_after_failure(self):
        from test_application import FakeInstallation
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
                batch.run_batch(app, [job], [sys.executable], self.root / "private", 90, 1000)
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
        app.profiles.switch(profile)
        self.assertEqual(batch.recover_session(app, session), "restored")
        self.assertEqual(batch.manifest(app.profiles.live), original)

        recovery["restoration_pending"] = True
        recovery_file.write_text(json.dumps(recovery))
        app.profiles.switch(profile)
        personal = app.profiles._profile_path(ORIGINAL) / "Saves/stock.sav"
        personal.write_text("new personal progress")
        with self.assertRaisesRegex(batch.SafetyError, "Original profile changed"):
            batch.recover_session(app, session)
        self.assertEqual(personal.read_text(), "new personal progress")
        self.assertEqual(app.profiles.active_profile(), profile)


if __name__ == "__main__": unittest.main()
