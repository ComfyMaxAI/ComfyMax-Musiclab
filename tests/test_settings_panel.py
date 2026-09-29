import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication

from comfymax_audio_chunker.editor import settings_panel
from comfymax_audio_chunker.editor.settings_panel import SettingsPanel


class Host(SettingsPanel):
    pass


class SettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.config=self.root/'settings.json'
        self.patch=patch.object(settings_panel,'settings_file',return_value=self.config); self.patch.start()

    def tearDown(self): self.patch.stop(); self.temp.cleanup()

    def panel(self):
        host=Host(); host.build_settings(); self.addCleanup(host.settings_pane.deleteLater); return host

    def test_sections_keys_and_current_whisper_model(self):
        with patch.object(settings_panel,'_detected_sheet_sage_path',return_value=''):
            host=self.panel()
        self.assertEqual(set(host.settings_fields),set(settings_panel.SETTINGS_KEYS))
        self.assertEqual(host.settings_fields['whisper_model'].text(),settings_panel.WHISPER_MODEL)
        labels=[group.title() for group in host.settings_pane.findChildren(settings_panel.QGroupBox)]
        self.assertEqual(labels,['Music Analysis','Music Generation','YuE2 model downloads','Transcription'])
        text=' '.join(label.text() for label in host.settings_pane.findChildren(settings_panel.QLabel))
        for engine in ('SheetSage','YuE2','Whisper'): self.assertIn(engine,text)

    def test_existing_and_missing_paths_have_expected_status(self):
        model=self.root/'model.gguf'; model.write_bytes(b'model')
        host=self.panel(); host.set_model_path('yue2_main_path',str(model))
        host.set_model_path('yue2_vae_path',str(self.root/'missing'))
        self.assertEqual(host.settings_status['yue2_main_path'].text(),'Available')
        self.assertEqual(host.settings_status['yue2_vae_path'].text(),'Not configured')

    def test_file_and_folder_browse_select_existing_paths(self):
        model=self.root/'model.bin'; model.write_bytes(b'model'); folder=self.root/'vae'; folder.mkdir()
        host=self.panel()
        with patch.object(settings_panel.QFileDialog,'getOpenFileName',return_value=(str(model),'')):
            host._choose_file('yue2_main_path')
        with patch.object(settings_panel.QFileDialog,'getExistingDirectory',return_value=str(folder)):
            host._choose_folder('yue2_vae_path')
        self.assertEqual(host.settings_fields['yue2_main_path'].text(),str(model))
        self.assertEqual(host.settings_fields['yue2_vae_path'].text(),str(folder))

    def test_settings_persist_and_reload(self):
        model=self.root/'sheet.gguf'; model.write_bytes(b'model')
        host=self.panel(); host.set_model_path('sheet_sage_path',str(model)); host.set_model_path('yue2_main_path','main')
        saved=json.loads(self.config.read_text(encoding='utf-8'))
        self.assertEqual(set(saved),set(settings_panel.CONFIG_KEYS))
        with patch.object(settings_panel,'_detected_sheet_sage_path',side_effect=AssertionError('saved value must win')):
            reopened=self.panel()
        self.assertEqual(reopened.settings_fields['sheet_sage_path'].text(),str(model))
        self.assertEqual(reopened.settings_fields['yue2_main_path'].text(),'main')

    def test_existing_sheetsage_model_is_initial_value(self):
        model=self.root/'sheetsage.gguf'; model.write_bytes(b'model')
        with patch.object(settings_panel,'_detected_sheet_sage_path',return_value=str(model)):
            host=self.panel()
        self.assertEqual(host.settings_fields['sheet_sage_path'].text(),str(model))
        self.assertEqual(host.settings_status['sheet_sage_path'].text(),'Available')

    def test_yue2_download_choices_and_existing_warning(self):
        host=self.panel()
        self.assertEqual(host.yue2_package_choices['main'].count(),3); self.assertEqual(host.yue2_package_choices['vae'].count(),2)
        package_id,filename=host.yue2_package_choices['main'].currentData()
        with patch.object(settings_panel,'YUE2_MODEL_ROOT',self.root),patch.object(settings_panel.QMessageBox,'information') as warning:
            (self.root/filename).write_bytes(b'model'); host.download_yue2('main')
        warning.assert_called_once(); self.assertIn('already installed',host.yue2_download_status.text())

    def test_download_starts_worker_and_ready_selects_installed_path(self):
        host=self.panel(); host.audiocpp_runtime=Mock()
        task=Mock(); task.progress=Mock(); task.ready=Mock(); task.failed=Mock(); task.finished=Mock(); task.start=Mock()
        with patch.object(settings_panel,'YUE2_MODEL_ROOT',self.root),patch.object(settings_panel,'ModelDownloadTask',return_value=task):
            host.download_yue2('main')
            task.start.assert_called_once(); self.assertTrue(all(not button.isEnabled() for button in host.yue2_download_buttons.values()))
            package_id,filename=host.yue2_package_choices['main'].currentData()
            host._download_ready('main',package_id,str(self.root/filename))
        self.assertEqual(host.settings_fields['yue2_main_path'].text(),str(self.root/filename))

    def test_download_task_stages_then_places_model_under_yue2_root(self):
        runtime=Mock(); runtime.models_root.return_value={'models_root':'original'}
        runtime.model_install_status.return_value=[{'id':'yue2_main_q4_0','state':'complete','message':'done','progress_percent':100}]
        def install(_package):
            package=self.root/'.downloads'/settings_panel.YUE2_PACKAGE_DIRECTORY
            package.mkdir(parents=True); (package/'yue2-3b-q4_0.gguf').write_bytes(b'model')
        runtime.install_model_package.side_effect=install
        with patch.object(settings_panel,'YUE2_MODEL_ROOT',self.root):
            task=settings_panel.ModelDownloadTask(runtime,'yue2_main_q4_0','yue2-3b-q4_0.gguf')
            ready=Mock(); task.ready.connect(ready); task.run()
        self.assertEqual((self.root/'yue2-3b-q4_0.gguf').read_bytes(),b'model')
        ready.assert_called_once_with('yue2_main_q4_0',str(self.root/'yue2-3b-q4_0.gguf'))
        runtime.set_models_root.assert_any_call(self.root/'.downloads'); runtime.set_models_root.assert_called_with('original')

    def test_main_window_starts_with_settings_tab(self):
        from comfymax_audio_chunker.editor.marker_app import MarkerEditor
        with patch.object(settings_panel,'_detected_sheet_sage_path',return_value=''):
            window=MarkerEditor()
        self.addCleanup(window.deleteLater)
        self.assertIn('Settings',[window.views.tabText(i) for i in range(window.views.count())])
        self.assertIs(window.views.widget(window.views.indexOf(window.settings_pane)),window.settings_pane)
        tabs=[window.views.tabText(i) for i in range(window.views.count())]
        self.assertEqual(tabs.index('Style Presets'),tabs.index('Settings')+1)


if __name__=='__main__': unittest.main()
