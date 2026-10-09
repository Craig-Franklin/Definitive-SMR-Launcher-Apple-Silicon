"""Launch integration preserves existing guards and tolerates monitor failures."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from smr_launcher.gui import LauncherWindow


class LaunchControls(unittest.TestCase):
    def window(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.app = SimpleNamespace(installation=SimpleNamespace(executable=Path('/Applications/Game.app/Contents/MacOS/Game')))
        window.crash_monitor = Mock()
        return window

    @patch('smr_launcher.gui.subprocess.Popen')
    def test_arm_only_after_app_guards_then_launch(self, popen):
        window = self.window()
        calls = []
        window.crash_monitor.arm.side_effect = lambda profile: calls.append(('arm', profile))
        def play(profile, launch):
            calls.append(('guards', profile))
            return launch()
        window.app.play = play
        window._play_with_crash_monitor('profile')
        self.assertEqual(calls, [('guards', 'profile'), ('arm', 'profile')])
        popen.assert_called_once_with(['/usr/bin/open', '-a', '/Applications/Game.app'])

    @patch('smr_launcher.gui.subprocess.Popen')
    def test_rejected_launch_never_arms(self, popen):
        window = self.window()
        window.app.play = Mock(side_effect=ValueError('guard stopped'))
        with self.assertRaises(ValueError):
            window._play_with_crash_monitor('profile')
        window.crash_monitor.arm.assert_not_called()
        popen.assert_not_called()

    @patch('smr_launcher.gui.subprocess.Popen')
    def test_monitor_failure_does_not_block_game(self, popen):
        window = self.window()
        window.app.play = lambda profile, launch: launch()
        window.crash_monitor.arm.side_effect = OSError('SECRET_CANARY')
        window._play_with_crash_monitor('profile')
        popen.assert_called_once()
        self.assertNotIn('SECRET_CANARY', window.crash_monitor.status)

    @patch('smr_launcher.gui.CrashMonitor', side_effect=ValueError('SECRET_CANARY'))
    def test_monitor_start_failure_leaves_app_available(self, constructor):
        window = self.window()
        window.crash_status = Mock()
        original = window.app
        window._start_crash_monitor()
        self.assertIs(window.app, original)
        self.assertIsNone(window.crash_monitor)
        self.assertNotIn('SECRET_CANARY', window.crash_status.set.call_args.args[0])
