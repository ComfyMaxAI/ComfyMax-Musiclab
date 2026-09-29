import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QMessageBox,QPlainTextEdit

from comfymax_audio_chunker.editor.generation_panel import GenerationPanel
from comfymax_audio_chunker.editor.style_presets import (DEFAULT_STYLE_PRESETS,StylePresetStore,
                                                         StylePresetsPanel)


class Score:
    def working_document(self): return None


class Host(GenerationPanel,StylePresetsPanel):
    def __init__(self,root):
        self.generation_settings_path=Path(root)/'settings.json'
        self.style_presets_path=Path(root)/'style-presets.json'
        self.model_settings={'yue2_main_path':''}
        self.lyrics_editor=QPlainTextEdit(); self.score_panel=Score(); self.audiocpp_runtime=Mock()
        self.build_generation(); self.build_style_presets()


class StylePresetStoreTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'style-presets.json'
    def tearDown(self): self.temp.cleanup()

    def test_defaults_load_with_exact_existing_content(self):
        store=StylePresetStore(self.path)
        self.assertEqual(store.presets,DEFAULT_STYLE_PRESETS)
        self.assertEqual(store.prompt('Reggae'),DEFAULT_STYLE_PRESETS['Reggae'])

    def test_create_update_delete_duplicates_and_persistence(self):
        store=StylePresetStore(self.path)
        store.save('New Preset','first')
        store.save('Renamed','changed','New Preset')
        self.assertNotIn('New Preset',store.names()); self.assertEqual(store.prompt('Renamed'),'changed')
        with self.assertRaisesRegex(ValueError,'already exists'): store.save('Rock','duplicate')
        with self.assertRaisesRegex(ValueError,'already exists'): store.save('rock','case duplicate')
        with self.assertRaisesRegex(ValueError,'empty'): store.save('   ','empty')
        reopened=StylePresetStore(self.path)
        self.assertEqual(reopened.prompt('Renamed'),'changed')
        reopened.delete('Renamed'); self.assertNotIn('Renamed',StylePresetStore(self.path).names())
        payload=json.loads(self.path.read_text(encoding='utf-8'))
        self.assertIsInstance(payload['presets'],list)


class StylePresetPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.host=Host(self.temp.name)
    def tearDown(self):
        self.host.generation_pane.deleteLater(); self.host.style_presets_pane.deleteLater()
        self.host.lyrics_editor.deleteLater(); self.app.processEvents(); self.temp.cleanup()

    def test_new_edit_save_as_delete_and_generation_sync(self):
        h=self.host; h.new_style_preset(); h.style_preset_name.setText('Dream Pop')
        h.style_preset_prompt.setPlainText('dreamy prompt'); h.save_style_preset()
        self.assertGreaterEqual(h.generation_style_preset.findText('Dream Pop'),0)
        h.generation_style_preset.setCurrentText('Dream Pop')
        self.assertEqual(h.generation_style.text(),'dreamy prompt')
        h.style_preset_prompt.setPlainText('updated prompt'); h.save_style_preset()
        self.assertEqual(h.generation_style.text(),'updated prompt')
        h.style_preset_name.setText('Dream Pop Copy'); h.save_style_preset_as_new()
        self.assertEqual(h.style_preset_store.prompt('Dream Pop'),'updated prompt')
        self.assertEqual(h.style_preset_store.prompt('Dream Pop Copy'),'updated prompt')
        h.generation_style_preset.setCurrentText('Dream Pop Copy')
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes): h.delete_style_preset()
        self.assertNotIn('Dream Pop Copy',h.style_preset_store.names())
        self.assertEqual(h.generation_style_preset.currentText(),'Custom')

    def test_duplicate_name_shows_clear_warning(self):
        h=self.host; h.new_style_preset(); h.style_preset_name.setText('Rock')
        with patch.object(QMessageBox,'warning') as warning: h.save_style_preset()
        warning.assert_called_once(); self.assertIn('already exists',warning.call_args.args[2])


if __name__=='__main__': unittest.main()
