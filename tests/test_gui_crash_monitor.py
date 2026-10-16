"""Launch integration preserves existing guards and tolerates monitor failures."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import fcntl
import os
import sys
import time
from threading import Thread

from smr_launcher.gui import LauncherWindow


class LaunchControls(unittest.TestCase):
    def window(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.app = SimpleNamespace(
            installation=SimpleNamespace(
                executable=Path("/Applications/Game.app/Contents/MacOS/Game")
            )
        )
        window.crash_monitor = Mock()
        window.closed = window.close_when_idle = False
        return window

    @patch("smr_launcher.gui.subprocess.Popen")
    def test_arm_only_after_app_guards_then_launch(self, popen):
        window = self.window()
        calls = []
        window.crash_monitor.arm.side_effect = lambda profile: calls.append(("arm", profile))

        def play(profile, launch):
            calls.append(("guards", profile))
            return launch()

        window.app.play = play
        window._play_with_crash_monitor("profile")
        self.assertEqual(calls, [("guards", "profile"), ("arm", "profile")])
        popen.assert_called_once_with(["/usr/bin/open", "-a", "/Applications/Game.app"])

    @patch("smr_launcher.gui.subprocess.Popen")
    def test_rejected_launch_never_arms(self, popen):
        window = self.window()
        window.app.play = Mock(side_effect=ValueError("guard stopped"))
        with self.assertRaises(ValueError):
            window._play_with_crash_monitor("profile")
        window.crash_monitor.arm.assert_not_called()
        popen.assert_not_called()

    @patch("smr_launcher.gui.subprocess.Popen")
    def test_monitor_failure_does_not_block_game(self, popen):
        window = self.window()
        window.app.play = lambda profile, launch: launch()
        window.crash_monitor.arm.side_effect = OSError("SECRET_CANARY")
        window._play_with_crash_monitor("profile")
        popen.assert_called_once()
        self.assertNotIn("SECRET_CANARY", window.crash_monitor.status)

    @patch("smr_launcher.gui.CrashMonitor", side_effect=ValueError("SECRET_CANARY"))
    def test_monitor_start_failure_leaves_app_available(self, constructor):
        window = self.window()
        window.crash_status = Mock()
        original = window.app
        window._start_crash_monitor()
        self.assertIs(window.app, original)
        self.assertIsNone(window.crash_monitor)
        self.assertNotIn("SECRET_CANARY", window.crash_status.set.call_args.args[0])


class NativeLaunchLifetime(unittest.TestCase):
    def setUp(self):
        from test_application import ApplicationTests
        from test_native_backend import window

        self.fixture = ApplicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        install = self.fixture.installation
        install.executable = self.fixture.root / "Game.app/Contents/MacOS/Game"
        install.executable.parent.mkdir(parents=True)
        install.executable.write_bytes(b"test game")
        self.app = self.fixture.application
        self.app.setup()
        self.owner = window()
        self.owner.close_when_idle = False
        self.owner.app = self.app
        self.owner.crash_monitor = None

    def held_transaction(self, change_owner):
        """Hold the real flock/transaction; intercept only the final subprocess."""

        class BoundaryReached(Exception):
            pass

        errors = []

        def run():
            try:
                self.owner._native_launch(self.app, "original-game")
            except BaseException as exc:
                errors.append(exc)

        fd = os.open(self.app.library / ".application.lock", os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        thread = Thread(target=run)
        try:
            with patch("smr_launcher.gui.subprocess.Popen", side_effect=BoundaryReached) as popen:
                thread.start()
                until = time.monotonic() + 2
                blocked = False
                while time.monotonic() < until:
                    frame, names = sys._current_frames().get(thread.ident), []
                    while frame:
                        names.append(frame.f_code.co_name)
                        frame = frame.f_back
                    if "_locked" in names and "_native_launch" in names:
                        blocked = True
                        break
                    time.sleep(0.005)
                self.assertTrue(blocked, "The real application transaction must be held")
                change_owner()
                fcntl.flock(fd, fcntl.LOCK_UN)
                thread.join(2)
                self.assertFalse(thread.is_alive())
                return popen.call_count, errors
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            if thread.is_alive():
                thread.join(2)

    def test_real_transaction_closing_blocks_dispatch_and_live_control_reaches_boundary(self):
        calls, errors = self.held_transaction(lambda: None)
        self.assertEqual(calls, 1, "Positive control must detect the actual Popen boundary")
        self.assertEqual(type(errors[0]).__name__, "BoundaryReached")
        calls, errors = self.held_transaction(lambda: setattr(self.owner, "close_when_idle", True))
        self.assertEqual(calls, 0)
        self.assertIn("owner changed or is closing", str(errors[0]))

    def test_real_input_pipe_eof_while_transaction_held_blocks_late_dispatch(self):
        read_fd, write_fd = os.pipe()
        with os.fdopen(read_fd, "rb") as stream:
            reader = Thread(target=self.owner._read_native_requests, args=(stream,))
            reader.start()

            def parent_lost():
                os.close(write_fd)
                self.assertTrue(self.owner._native_input_ended.wait(1))

            calls, errors = self.held_transaction(parent_lost)
            reader.join(1)
        self.assertFalse(reader.is_alive())
        self.assertEqual(calls, 0)
        self.assertIn("owner changed or is closing", str(errors[0]))

    def test_finite_guard_refuses_busy_transaction_without_wait_or_dispatch(self):
        with self.app._locked(), patch("smr_launcher.gui.subprocess.Popen") as popen:
            with self.assertRaisesRegex(Exception, "application is busy"):
                self.owner._native_launch(
                    self.app, "original-game", launch_deadline=time.monotonic() + 10
                )
        popen.assert_not_called()

    def test_finite_deadline_rechecked_after_monitor_arm_before_real_dispatch(self):
        deadline = time.monotonic() + 0.2

        def arm(_):
            time.sleep(max(0, deadline - time.monotonic()) + 0.01)

        self.owner.crash_monitor = SimpleNamespace(arm=arm)
        with patch("smr_launcher.gui.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "deadline expired"):
                self.owner._native_launch(self.app, "original-game", launch_deadline=deadline)
        popen.assert_not_called()
