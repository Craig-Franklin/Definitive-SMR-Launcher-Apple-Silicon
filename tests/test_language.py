"""Language settings and the native provider contract use isolated fixtures."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher import language
from smr_launcher.activation import ActivationError


class LanguageTests(unittest.TestCase):
    def tearDown(self):
        language.set_language("en")

    def test_all_languages_have_same_source_keys_and_english_fallback(self):
        keys = set(language.TRANSLATIONS["en"])
        self.assertGreater(len(keys), 100)
        for code in language.LANGUAGES:
            self.assertEqual(set(language.TRANSLATIONS[code]), keys)
            language.set_language(code)
            self.assertTrue(language.tr("Map Library"))
            self.assertEqual(language.tr("Unknown map {name}", name="Private"), "Unknown map Private")
        language.set_language("fr")
        self.assertEqual(language.tr("Close"), "Fermer")
        with self.assertRaises(ValueError):
            language.set_language("bogus")

    def test_preferences_persist_only_in_library_and_reject_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            sentinel = root / "save.smr"
            sentinel.write_bytes(b"private save")
            preferences = language.LanguagePreferences(root / "library")
            preferences.save("de", "Eddy (German (Germany))")
            loaded = language.LanguagePreferences(preferences.library).load()
            self.assertEqual((loaded.language, loaded.voice), ("de", "Eddy (German (Germany))"))
            self.assertEqual(loaded.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(sentinel.read_bytes(), b"private save")
            loaded.path.unlink()
            loaded.path.symlink_to(sentinel)
            with self.assertRaises(ActivationError):
                loaded.save("fr")
            with self.assertRaises(ActivationError):
                loaded.load()
            self.assertEqual(sentinel.read_bytes(), b"private save")

    def test_invalid_preferences_not_silently_rewritten(self):
        with tempfile.TemporaryDirectory() as folder:
            prefs = language.LanguagePreferences(Path(folder).resolve())
            prefs.path.write_text('{"schema":99}')
            original = prefs.path.read_bytes()
            with self.assertRaises(ValueError):
                prefs.load()
            self.assertEqual(prefs.path.read_bytes(), original)

    def test_translation_uses_stdin_and_preserves_source(self):
        with patch.object(language, "translation_helper", return_value=Path(__file__)), patch.object(language.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps({"text": "Bonjour"}), "")
            source = "Hello; $(touch forbidden)"
            self.assertEqual(language.translate_briefing(source, "fr"), "Bonjour")
            arguments, kwargs = run.call_args
            self.assertEqual(arguments[0], [__file__])
            self.assertEqual(json.loads(kwargs["input"])["text"], source)
            self.assertNotIn("shell", kwargs)

    def test_missing_models_are_actionable_and_invalid_response_fails(self):
        with patch.object(language, "translation_helper", return_value=Path(__file__)), patch.object(language.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps({"error": "Download Translation Languages"}), "")
            with self.assertRaisesRegex(language.TranslationError, "Download Translation Languages"):
                language.translate_briefing("Hello", "fr")
            for value in ("[]", "null", '{"text": 12}', 'garbage'):
                run.return_value.stdout = value
                with self.assertRaises(language.TranslationError):
                    language.translate_briefing("Hello", "fr")

    def test_same_language_needs_no_helper_and_missing_helper_is_explicit(self):
        with patch.object(language, "translation_helper", return_value=Path("/nonexistent/launcher-helper")):
            self.assertEqual(language.translate_briefing("Hello", "en"), "Hello")
            with self.assertRaisesRegex(language.TranslationError, "unavailable in this build"):
                language.translate_briefing("Hello", "fr")


if __name__ == "__main__":
    unittest.main()
