from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from smr_launcher.editions import enable_editor
from smr_launcher.variants import _prepare_variant, _tree_manifest

class EditionTests(unittest.TestCase):
    def test_editor_preserves_settings_and_rejects_ambiguous_sections(self):
        source = b'[User Settings]\r\nPlayerName = Test\r\n[Other]\r\nEditorEnabled = 0\r\n'
        result = enable_editor(source)
        self.assertIn(b'PlayerName = Test\r\n', result)
        self.assertIn(b'[User Settings]\r\nEditorEnabled = 1\r\n', result)
        self.assertTrue(result.endswith(b'[Other]\r\nEditorEnabled = 0\r\n'))
        self.assertEqual(enable_editor(result), result)
        with self.assertRaises(ValueError): enable_editor(b'[UserSettings]\nEditorEnabled=0')
        with self.assertRaises(ValueError): enable_editor(b'[User Settings]\nEditorEnabled=0\nEditorEnabled=1')

    def test_editions_preserve_sources_empty_saves_and_have_separate_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source=root/'source'; source.mkdir()
            for name in ('CustomAssets','UserMaps','Saves'): (source/name).mkdir()
            (source/'Settings.ini').write_text('[User Settings]\nPlayerName=Test\n')
            (source/'Saves/keep.sav').write_text('precious')
            before=_tree_manifest(source)
            base=_prepare_variant(source,source,root/'base','a'*64,'b'*64,())
            editor=_prepare_variant(source,source,root/'editor','a'*64,'b'*64,(),dict(editor=True,difficulty=False,parent=base.variant_id))
            with patch('smr_launcher.editions.difficulty_template',return_value=b'<RRTDifficultyLevels/>'):
                difficulty=_prepare_variant(source,source,root/'difficulty','a'*64,'b'*64,(),dict(editor=False,difficulty=True,parent=base.variant_id))
            self.assertEqual(len({base.variant_id,editor.variant_id,difficulty.variant_id}),3)
            self.assertEqual(base.assets_sha256,editor.assets_sha256)
            self.assertNotEqual(base.assets_sha256,difficulty.assets_sha256)
            self.assertEqual(list((root/'editor/Saves').iterdir()),[])
            self.assertEqual(_tree_manifest(source),before)
            self.assertNotEqual((source/'Settings.ini').stat().st_ino,(root/'editor/Settings.ini').stat().st_ino)
            (source/'UserMaps/RRT_Difficulty.xml').write_text('<Custom/>')
            with self.assertRaises(ValueError):
                _prepare_variant(source,source,root/'conflict','a'*64,'b'*64,(),dict(editor=False,difficulty=True,parent=base.variant_id))
            self.assertFalse((root/'conflict').exists())
            for p in root.rglob('*'):
                if p.is_dir(): p.chmod(0o700)

if __name__ == '__main__': unittest.main()
