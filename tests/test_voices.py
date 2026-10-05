"""Speech boundaries: real installed identifiers, stdin transport, no audio in tests."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher import voices


class VoiceTests(unittest.TestCase):
    def test_full_voice_identifiers_and_languages_are_preserved(self):
        result = voices.parse_voices('''Albert              en_US    # Hello! My name is Albert.
Bad News            en_US    # Hello!
Eddy (German (Germany)) de_DE    # Hallo! Ich heiße Eddy.
Eddy (Chinese (China mainland)) zh_CN    # 你好！我叫Eddy。
not a voice line
''')
        self.assertEqual(len(result), 4)
        self.assertEqual(voices.voices_for_language(result, "de")[0].identifier, "Eddy (German (Germany))")
        self.assertEqual(voices.voices_for_language(result, "zh-Hans")[0].language, "zh-CN")
        self.assertEqual(len(voices.voices_for_language(result, "en")), 2)

    def test_speech_is_async_stdin_and_stoppable(self):
        process = Mock()
        process.communicate.return_value = (None, "")
        process.returncode = 0
        process.poll.return_value = None
        voice = voices.Voice("Bad News", "en-US")
        with patch.object(voices, "list_voices", return_value=(voice,)), patch.object(voices.subprocess, "Popen", return_value=process) as start:
            briefing = "--output-file malicious; $(echo value)"
            handle = voices.speak(briefing, "Bad News", available_voices=(voice,))
            handle._thread.join(timeout=2)
            self.assertEqual(start.call_args.args[0], ["/usr/bin/say", "-v", "Bad News"])
            process.communicate.assert_called_once_with(briefing)
            voices.list_voices.assert_not_called()
            handle.stop()
            process.terminate.assert_called_once()

    def test_native_voice_failure_is_captured_without_enumeration(self):
        process = Mock()
        process.communicate.return_value = (None, "Voice not found")
        process.returncode = 1
        process.poll.return_value = 1
        with patch.object(voices, "list_voices") as listing, patch.object(voices.subprocess, "Popen", return_value=process):
            handle = voices.speak("Hello", "Removed voice")
            handle._thread.join(timeout=2)
            self.assertTrue(handle.done)
            self.assertEqual(handle.error, "Voice not found")
            listing.assert_not_called()

    def test_removed_voice_never_launches_process(self):
        with patch.object(voices, "list_voices", return_value=()), patch.object(voices.subprocess, "Popen") as start:
            with self.assertRaisesRegex(voices.VoiceError, "not installed"):
                voices.speak("Hello", "Unavailable", available_voices=())
            start.assert_not_called()

    def test_voice_settings_has_system_fallback(self):
        with patch.object(voices.subprocess, "run") as run:
            run.side_effect = [subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]
            voices.open_voice_settings()
            self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/open", "-a", "System Settings"])


if __name__ == "__main__":
    unittest.main()
