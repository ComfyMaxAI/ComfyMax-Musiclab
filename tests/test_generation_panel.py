import os
import tempfile
import unittest
import wave
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtGui import QTextCursor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication,QFileDialog,QGroupBox,QLabel,QMessageBox,QPlainTextEdit,
                               QPushButton,QSlider,QSpinBox,QTabBar)

from comfymax_audio_chunker.editor.generation_panel import (GenerationPanel,GpuQueryTask,STYLE_PRESETS,
                                                            parse_nvidia_smi)
from comfymax_audio_chunker.editor.score_panel import ScorePanel
from comfymax_audio_chunker.editor.score_source import SheetSageABCSource

SEMANTIC_DEFAULTS={'semantic_temperature':1.0,'semantic_top_p':.95,'semantic_top_k':100,
    'semantic_repetition_penalty':1.2,'semantic_penalty_window':50,
    'semantic_min_tokens':200,'semantic_max_tokens':9000}
PRIMARY_DEFAULTS={'guidance_scale':1.5,'num_inference_steps':16}
ABC_DEFAULTS={'abc_temperature':.7,'abc_top_p':.9,'abc_top_k':30,
    'abc_repetition_penalty':1.005,'abc_penalty_window':100,'abc_min_tokens':32,'abc_max_tokens':4096}


class Source:
    def __init__(self,text=''): self.text=text
    def read(self): return SimpleNamespace(payload=self.text.encode(),text=self.text)


class ScoreSource:
    def __init__(self,text=''): self.source=Source(text)
    def working_document(self): return self.source.read()


class Host(GenerationPanel):
    def __init__(self,settings_path=None,model_settings=None):
        self.generation_settings_path=settings_path
        self.model_settings={'yue2_main_path':'models/YuE2',**(model_settings or {})}
        self.lyrics_editor=QPlainTextEdit('original lyrics')
        self.score_panel=ScoreSource('X:1\nK:C\nCDEF|')
        self.audiocpp_runtime=Mock()
        self.build_generation()


class GenerationPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.config=Path(self.temp.name)/'settings.json'
        self.host=Host(self.config)
    def tearDown(self):
        self.host.generation_pane.deleteLater(); self.host.lyrics_editor.deleteLater(); self.app.processEvents(); self.temp.cleanup()

    def test_layout_model_routes_and_output_placeholder(self):
        h=self.host; h.refresh_generation_inputs()
        self.assertEqual(h.generation_model.text(),'Model: models/YuE2')
        self.assertEqual([h.generation_route.itemText(i) for i in range(h.generation_route.count())],['Off','Melody','Full'])
        self.assertEqual(h.generation_output_message.text(),'Generated audio will appear here.')
        self.assertFalse(h.generation_play.isEnabled()); self.assertFalse(h.generation_save.isEnabled())
        output=next(group for group in h.generation_pane.findChildren(QGroupBox) if group.title()=='Output')
        self.assertLess(output.sizeHint().height(),300)
        self.assertEqual(h.generation_time.text(),'0:00 / 0:00'); self.assertFalse(h.generation_seek.isEnabled())

    def test_primary_controls_share_one_row_and_have_single_instances(self):
        h=self.host
        columns=[h.generation_primary_row.itemAt(index).layout() for index in range(h.generation_primary_row.count())]
        self.assertEqual(len(columns),3); self.assertGreaterEqual(columns[0].indexOf(h.generation_route),0)
        self.assertGreaterEqual(columns[2].indexOf(h.generation_nar_steps),0)
        self.assertIs(h.generation_route.parentWidget(),h.generation_guidance.parentWidget())
        self.assertIs(h.generation_route.parentWidget(),h.generation_nar_steps.parentWidget())
        self.assertEqual(len(h.generation_pane.findChildren(QSlider,'semanticGuidance')),1)
        self.assertEqual(len(h.generation_pane.findChildren(QSpinBox,'narSteps')),1)
        labels=[label.text() for label in h.generation_route.parentWidget().findChildren(QLabel)]
        self.assertIn('Planning route',labels); self.assertIn('Semantic Guidance',labels); self.assertIn('NAR Steps',labels)

    def test_primary_defaults_and_persisted_values(self):
        self.assertEqual(self.host.generation_guidance.value(),150)
        self.assertEqual(self.host.generation_guidance_value.text(),'1.50')
        self.assertEqual(self.host.generation_nar_steps.value(),16)
        self.assertEqual((self.host.generation_guidance.minimum(),self.host.generation_guidance.maximum()),(0,500))
        self.assertEqual((self.host.generation_nar_steps.minimum(),self.host.generation_nar_steps.maximum()),(1,64))
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'settings.json'
            config.write_text('{"yue2_semantic_guidance": 2.25, "yue2_nar_steps": 24}',encoding='utf-8')
            host=Host(config); self.addCleanup(host.generation_pane.deleteLater); self.addCleanup(host.lyrics_editor.deleteLater)
            self.assertEqual(host.generation_guidance.value(),225); self.assertEqual(host.generation_guidance_value.text(),'2.25')
            self.assertEqual(host.generation_nar_steps.value(),24)
            host.generation_guidance.setValue(175); host.generation_nar_steps.setValue(20)
            stored=__import__('json').loads(config.read_text(encoding='utf-8'))
            self.assertEqual(stored['yue2_semantic_guidance'],1.75); self.assertEqual(stored['yue2_nar_steps'],20)

    def test_lyrics_and_abc_copy_one_way_then_preserve_generation_edits(self):
        h=self.host; h.refresh_generation_inputs()
        self.assertEqual(h.generation_lyrics.toPlainText(),'original lyrics')
        self.assertEqual(h.generation_abc.toPlainText(),'X:1\nK:C\nCDEF|'); self.assertTrue(h.generation_use_abc.isChecked())
        h.generation_use_abc.setChecked(False); h.refresh_generation_inputs(); self.assertFalse(h.generation_use_abc.isChecked())
        h.generation_lyrics.setPlainText('generation lyrics'); h.generation_abc.setPlainText('edited ABC')
        h.lyrics_editor.setPlainText('changed original'); h.score_panel.source=Source('new original ABC'); h.refresh_generation_inputs()
        self.assertEqual(h.generation_lyrics.toPlainText(),'generation lyrics')
        self.assertEqual(h.generation_abc.toPlainText(),'edited ABC')

    def test_project_lyrics_save_and_direct_load_utf8(self):
        h=self.host; root=Path(self.temp.name)/'Cuando Sali De Cuba.comfymax'; root.mkdir()
        h.doc=SimpleNamespace(root=root,data={'title':'Cuando Sali De Cuba'})
        text='[intro]\nMañana\n[verse1]\n[chorus]\n[instrumental]\n[outro]\n'
        h.generation_lyrics.setPlainText(text); h.save_generation_lyrics()
        target=root/'Cuando Sali De Cuba_lyrics.txt'
        self.assertEqual(target.read_text(encoding='utf-8'),text)
        h.generation_lyrics.clear()
        with patch.object(QFileDialog,'getOpenFileName',side_effect=AssertionError('project file must load directly')):
            h.load_generation_lyrics()
        self.assertEqual(h.generation_lyrics.toPlainText(),text)

    def test_project_lyrics_overwrite_confirmation_and_import_fallback(self):
        h=self.host; root=Path(self.temp.name)/'Project.comfymax'; root.mkdir()
        h.doc=SimpleNamespace(root=root,data={'title':'Project'})
        target=root/'Project_lyrics.txt'; target.write_text('old',encoding='utf-8')
        h.generation_lyrics.setPlainText('new')
        with patch.object(QMessageBox,'question',return_value=QMessageBox.No): h.save_generation_lyrics()
        self.assertEqual(target.read_text(encoding='utf-8'),'old')
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes): h.save_generation_lyrics()
        self.assertEqual(target.read_text(encoding='utf-8'),'new')
        target.unlink(); imported=root.parent/'external.txt'; imported.write_text('[verse1]\nExtern',encoding='utf-8')
        with patch.object(QFileDialog,'getOpenFileName',return_value=(str(imported),'Text files (*.txt)')):
            h.load_generation_lyrics()
        self.assertEqual(h.generation_lyrics.toPlainText(),'[verse1]\nExtern')
        self.assertFalse(target.exists())

    def test_standalone_lyrics_and_abc_files_load_without_project(self):
        h=self.host; h.doc=None
        lyrics=Path(self.temp.name)/'lyrics.txt'; lyrics.write_text('[verse1]\nStandalone',encoding='utf-8')
        abc=Path(self.temp.name)/'score.abc'; abc.write_text('X:1\nK:C\nCDEF|',encoding='utf-8')
        with patch.object(QFileDialog,'getOpenFileName',return_value=(str(lyrics),'Text files (*.txt)')):
            h.load_generation_lyrics()
        self.assertEqual(h.generation_lyrics.toPlainText(),'[verse1]\nStandalone')
        with patch.object(QFileDialog,'getOpenFileName',return_value=(str(abc),'ABC notation (*.abc)')):
            h.load_generation_abc()
        self.assertEqual(h.generation_abc.toPlainText(),'X:1\nK:C\nCDEF|')
        self.assertTrue(h.generation_use_abc.isChecked())

    def test_abc_unavailable_defaults_checkbox_off(self):
        self.host.score_panel.source=Source(''); self.host.refresh_generation_inputs()
        self.assertFalse(self.host.generation_use_abc.isChecked()); self.assertEqual(self.host.generation_abc.toPlainText(),'')

    def test_working_abc_transpose_manual_edit_reset_and_generation_copy(self):
        fixture=(Path(__file__).parent/'fixtures'/'transpose.abc').read_bytes()
        project=Path(self.temp.name)/'project'
        source=project/'music_analysis/sheetsage/test/native/score.abc'
        source.parent.mkdir(parents=True); source.write_bytes(fixture)
        evidence=dict(raw_artifacts=[dict(path=source.relative_to(project).as_posix(),
                      sha256=hashlib.sha256(fixture).hexdigest())])
        score=ScorePanel(); self.addCleanup(score.close)
        score.source=SheetSageABCSource(project,evidence); score.load_source()
        self.host.score_panel=score

        self.host.refresh_generation_inputs()
        self.assertEqual(self.host.generation_abc.toPlainText(),score.abc.toPlainText())

        for amount,key,chord,note in ((2,'K:D','"D"','D E'),(-2,'K:Bb','"Bb"','B, C')):
            with patch.object(score,'preview_working'):
                score.reset_abc()
                score.transpose(amount)
            self.host.refresh_generation_inputs()
            copied=self.host.generation_abc.toPlainText()
            self.assertIn(key,copied); self.assertIn(chord,copied); self.assertIn(note,copied)
            self.assertEqual(copied,score.abc.toPlainText())

        score.abc.setPlainText(score.abc.toPlainText().replace('T:Transpose fixture','T:Manual Working edit'))
        self.host.refresh_generation_inputs()
        self.assertIn('T:Manual Working edit',self.host.generation_abc.toPlainText())

        working_before=score.abc.toPlainText()
        self.host.generation_abc.setPlainText('generation-only edit')
        self.assertEqual(score.abc.toPlainText(),working_before)
        self.assertEqual(self.host.generation_request()['abc'],'generation-only edit')

        self.host.reset_generation_inputs()
        with patch.object(score,'preview_working'):
            score.reset_abc()
        self.host.reset_generation_inputs()
        self.assertEqual(self.host.generation_abc.toPlainText(),score.original_document.text)
        self.assertEqual(source.read_bytes(),fixture)

    def test_sampling_sections_collapsed_and_request_values(self):
        h=self.host; h.refresh_generation_inputs()
        self.assertFalse(h.generation_abc_toggle.isChecked())
        buttons=[button for button in h.generation_pane.findChildren(type(h.generation_abc_toggle)) if button.text() in ('Semantic sampling','ABC sampler sampling')]
        self.assertEqual(len(buttons),2); self.assertTrue(all(not button.isChecked() for button in buttons))
        h.generation_style.setText('warm acoustic'); h.generation_seed.setValue(42); h.generation_route.setCurrentText('Full')
        request=h.generation_request()
        self.assertEqual((request['style'],request['seed'],request['planning_route']),('warm acoustic',42,'Full'))
        self.assertEqual(set(request['semantic_sampling']),{'temperature','top_p','top_k','repetition_penalty','penalty_window','min_tokens','max_tokens'})

    def test_style_presets_exist_and_fill_editable_style(self):
        h=self.host; expected=['Custom',*STYLE_PRESETS]
        self.assertEqual([h.generation_style_preset.itemText(i) for i in range(h.generation_style_preset.count())],expected)
        required={'Pop','Rock','Soft Rock','Indie Pop','Indie Rock','Acoustic','Folk','Country','Blues','Soul','R&B','Funk','Disco','Jazz','Swing','Reggae','Ska','Latin Pop','Salsa','Bachata','Spanish Rumba','Flamenco','Bossa Nova','EDM','House','Dance Pop','Synthwave','Hip-Hop','Trap','Ballad','Cinematic','Orchestral'}
        self.assertEqual(set(STYLE_PRESETS),required)
        h.generation_style.setText('replace me'); h.generation_style_preset.setCurrentText('Spanish Rumba')
        expected_rumba='Spanish rumba, lively rhythmic groove, Spanish guitar, hand percussion, lush strings, warm male vocals'
        self.assertEqual(h.generation_style.text(),expected_rumba)
        self.assertEqual(h.generation_style.cursorPosition(),len(expected_rumba)); self.assertFalse(h.generation_style.isReadOnly())
        h.generation_style_preset.setCurrentText('Jazz'); self.assertEqual(h.generation_style.text(),STYLE_PRESETS['Jazz'])

    def test_manual_style_edit_returns_to_custom_without_changing_text(self):
        h=self.host; h.generation_style_preset.setCurrentText('Rock'); h.generation_style.setFocus()
        QTest.keyClicks(h.generation_style,' extra')
        self.assertEqual(h.generation_style_preset.currentText(),'Custom')
        self.assertEqual(h.generation_style.text(),STYLE_PRESETS['Rock']+' extra')
        h.style_manually_edited(h.generation_style.text())
        self.assertEqual(h.generation_style_preset.currentText(),'Custom')

    def test_preset_style_remains_existing_request_style(self):
        h=self.host; h.generation_style_preset.setCurrentText('Funk')
        self.assertEqual(h.generation_request()['style'],STYLE_PRESETS['Funk'])

    def test_random_seed(self):
        with patch('comfymax_audio_chunker.editor.generation_panel.secrets.randbelow',return_value=123): self.host.randomize_generation_seed()
        self.assertEqual(self.host.generation_seed.value(),123)

    def test_off_with_abc_is_rejected_before_runtime(self):
        h=self.host; h.refresh_generation_inputs(); h.generate_music()
        self.assertEqual(h.generation_output_status.text(),'MusicLab ABC requires Planning route Melody or Full.')
        h.audiocpp_runtime.generate_yue2.assert_not_called()

    def test_off_without_abc_starts_with_no_external_abc(self):
        h=self.host; h.generation_use_abc.setChecked(False)
        path=Path(h.generation_preview_dir.path())/'result.wav'; path.write_bytes(b'wav')
        h.audiocpp_runtime.generate_yue2.return_value={'path':path,'response':{},'request':{}}
        h.generate_music(); self.assertTrue(h.generation_job.wait(3000)); self.app.processEvents()
        h.audiocpp_runtime.generate_yue2.assert_called_once_with(
            '','', '',0,'off',h.generation_preview_dir.path(),{**PRIMARY_DEFAULTS,**SEMANTIC_DEFAULTS})

    def test_generate_is_async_passes_inputs_and_restores_button(self):
        h=self.host; h.refresh_generation_inputs(); h.generation_style.setText('warm acoustic'); h.generation_seed.setValue(42)
        h.generation_route.setCurrentText('Melody')
        path=Path(h.generation_preview_dir.path())/'result.wav'
        with wave.open(str(path),'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b'\0\0'*800)
        h.audiocpp_runtime.generate_yue2.return_value={'path':path,'response':{},'request':{}}
        h.generate_music()
        self.assertFalse(h.generation_generate.isEnabled()); self.assertEqual(h.generation_output_status.text(),'Generating...')
        active_style=h.generation_output_status.styleSheet()
        self.assertIn('background-color:#dc2626',active_style); self.assertIn('color:white',active_style)
        self.assertIn('padding:',active_style); self.assertIn('border-radius:',active_style)
        self.assertTrue(h.generation_job is not None)
        self.assertTrue(h.generation_job.wait(3000)); self.app.processEvents()
        h.audiocpp_runtime.generate_yue2.assert_called_once_with(
            'original lyrics','warm acoustic','X:1\nK:C\nCDEF|',42,'melody',h.generation_preview_dir.path(),
            {**PRIMARY_DEFAULTS,**SEMANTIC_DEFAULTS,**ABC_DEFAULTS})
        self.assertTrue(h.generation_generate.isEnabled()); self.assertTrue(h.generation_play.isEnabled())
        self.assertEqual(h.generation_output_status.styleSheet(),'')
        self.assertEqual(h.generation_output_path,path)
        self.assertEqual(Path(h.generation_player.source().toLocalFile()),path)
        self.assertNotIn(str(Path('D:/ComfyMax-Musiclab/outputs/music_generation')),str(path))

    def test_full_with_abc_uses_full_and_all_sampling_options(self):
        h=self.host; h.refresh_generation_inputs(); h.generation_route.setCurrentText('Full')
        path=Path(h.generation_preview_dir.path())/'full.wav'; path.write_bytes(b'wav')
        h.audiocpp_runtime.generate_yue2.return_value={'path':path,'response':{},'request':{}}
        h.generate_music(); self.assertTrue(h.generation_job.wait(3000)); self.app.processEvents()
        call=h.audiocpp_runtime.generate_yue2.call_args.args
        self.assertEqual(call[2],'X:1\nK:C\nCDEF|'); self.assertEqual(call[4],'full')
        self.assertEqual(call[6],{**PRIMARY_DEFAULTS,**SEMANTIC_DEFAULTS,**ABC_DEFAULTS})
        for key,value in call[6].items():
            self.assertIsInstance(value,float if key in ('guidance_scale','semantic_temperature','semantic_top_p','semantic_repetition_penalty','abc_temperature','abc_top_p','abc_repetition_penalty') else int)

    def test_abc_sampling_omitted_without_external_abc(self):
        h=self.host; h.generation_use_abc.setChecked(False)
        path=Path(h.generation_preview_dir.path())/'off.wav'; path.write_bytes(b'wav')
        h.audiocpp_runtime.generate_yue2.return_value={'path':path,'response':{},'request':{}}
        h.generate_music(); self.assertTrue(h.generation_job.wait(3000)); self.app.processEvents()
        options=h.audiocpp_runtime.generate_yue2.call_args.args[6]
        self.assertEqual(options,{**PRIMARY_DEFAULTS,**SEMANTIC_DEFAULTS}); self.assertFalse(any(key.startswith('abc_') for key in options))

    def test_primary_options_are_sent_for_all_routes_with_correct_types(self):
        for route in ('Off','Melody','Full'):
            h=Host(self.config); self.addCleanup(h.generation_pane.deleteLater); self.addCleanup(h.lyrics_editor.deleteLater)
            h.generation_route.setCurrentText(route); h.generation_use_abc.setChecked(False)
            h.generation_guidance.setValue(223); h.generation_nar_steps.setValue(31)
            path=Path(h.generation_preview_dir.path())/(route+'.wav'); path.write_bytes(b'wav')
            h.audiocpp_runtime.generate_yue2.return_value={'path':path,'response':{},'request':{}}
            h.generate_music(); self.assertTrue(h.generation_job.wait(3000)); self.app.processEvents()
            options=h.audiocpp_runtime.generate_yue2.call_args.args[6]
            self.assertEqual(options['guidance_scale'],2.23); self.assertIsInstance(options['guidance_scale'],float)
            self.assertEqual(options['num_inference_steps'],31); self.assertIsInstance(options['num_inference_steps'],int)

    def test_generation_section_buttons_match_lyrics_logic_and_are_local(self):
        h=self.host; original=h.lyrics_editor.toPlainText(); h.generation_lyrics.setPlainText('tail')
        cursor=h.generation_lyrics.textCursor(); cursor.setPosition(0); h.generation_lyrics.setTextCursor(cursor)
        buttons={button.text():button for button in h.generation_pane.findChildren(type(h.generation_generate))}
        buttons['Intro'].click(); self.assertTrue(h.generation_lyrics.toPlainText().startswith('[Intro]\n'))
        h.generation_lyrics.setPlainText('[Verse 1]\n[Verse 3]\n[Chorus]\n[Chorus 2]\n')
        cursor=h.generation_lyrics.textCursor(); cursor.movePosition(QTextCursor.End); h.generation_lyrics.setTextCursor(cursor)
        buttons['Verse'].click(); buttons['Chorus'].click()
        self.assertIn('[Verse 2]',h.generation_lyrics.toPlainText()); self.assertIn('[Chorus 3]',h.generation_lyrics.toPlainText())
        for name in ('Pre-Chorus','Bridge','Instrumental','Solo','Outro'):
            buttons[name].click(); self.assertIn(f'[{name}]',h.generation_lyrics.toPlainText())
        self.assertEqual(h.lyrics_editor.toPlainText(),original)

    def test_generation_failure_is_nonfatal_and_restores_button(self):
        h=self.host; previous=Path(h.generation_preview_dir.path())/'previous.wav'; previous.write_bytes(b'previous')
        h.generation_finished({'path':previous}); h.generation_use_abc.setChecked(False)
        h.audiocpp_runtime.generate_yue2.side_effect=RuntimeError('local failure')
        h.generate_music(); self.assertTrue(h.generation_job.wait(3000)); self.app.processEvents()
        self.assertTrue(h.generation_generate.isEnabled()); self.assertIn('local failure',h.generation_output_status.text())
        self.assertEqual(h.generation_output_status.styleSheet(),'')
        self.assertEqual(h.generation_output_path,previous); self.assertTrue(previous.exists())

    def test_new_preview_replaces_and_removes_previous(self):
        h=self.host; first=Path(h.generation_preview_dir.path())/'first.wav'; second=Path(h.generation_preview_dir.path())/'second.wav'
        first.write_bytes(b'first'); second.write_bytes(b'second')
        h.generation_finished({'path':first}); h.generation_finished({'path':second})
        self.assertFalse(first.exists()); self.assertTrue(second.exists())
        self.assertEqual(h.generation_output_path,second)
        self.assertEqual(Path(h.generation_player.source().toLocalFile()),second)

    def test_save_audio_copies_current_preview_to_chosen_location(self):
        h=self.host; preview=Path(h.generation_preview_dir.path())/'preview.wav'; preview.write_bytes(b'preview-audio')
        h.generation_finished({'path':preview})
        with tempfile.TemporaryDirectory() as folder:
            saved=Path(folder)/'chosen.wav'
            with patch('comfymax_audio_chunker.editor.generation_panel.QFileDialog.getSaveFileName',return_value=(str(saved),'Wave audio (*.wav)')):
                h.save_generation_audio()
            self.assertEqual(saved.read_bytes(),b'preview-audio'); self.assertTrue(preview.exists())

    def test_player_time_seek_and_play(self):
        h=self.host; h._generation_duration_changed(184000); h._generation_position_changed(32000)
        self.assertEqual(h.generation_time.text(),'0:32 / 3:04'); self.assertEqual(h.generation_seek.maximum(),184000)
        with patch.object(h.generation_player,'setPosition') as seek:
            h.seek_generation_audio(45000); seek.assert_called_once_with(45000)
        with patch.object(h.generation_player,'play') as play:
            h.toggle_generation_audio(); play.assert_called_once()

    def test_gpu_parse_failure_and_no_overlap(self):
        value=parse_nvidia_smi('NVIDIA GeForce RTX 5060 Ti, 7987, 16303, 96\n')
        self.assertEqual(value,dict(name='NVIDIA GeForce RTX 5060 Ti',used_mib=7987.0,total_mib=16303.0,utilization=96))
        self.host._gpu_ready(value); self.assertIn('VRAM 7.8 / 15.9 GB',self.host.generation_gpu.text())
        self.host._gpu_unavailable(); self.assertEqual(self.host.generation_gpu.text(),'GPU information unavailable')
        sentinel=object(); self.host.generation_gpu_job=sentinel
        with patch('comfymax_audio_chunker.editor.generation_panel.GpuQueryTask') as task:
            self.host.update_gpu_info(); task.assert_not_called()
        self.host.generation_gpu_job=None

    def test_gpu_query_failure_emits_unavailable_without_crash(self):
        task=GpuQueryTask()
        with patch('comfymax_audio_chunker.editor.generation_panel.subprocess.run',side_effect=FileNotFoundError):
            failed=Mock(); task.failed.connect(failed); task.run()
        failed.assert_called_once_with()

    def test_cleanup_removes_temporary_preview(self):
        h=self.host; directory=Path(h.generation_preview_dir.path()); preview=directory/'preview.wav'; preview.write_bytes(b'audio')
        h.generation_finished({'path':preview}); h._cleanup_generation_preview()
        self.assertFalse(preview.exists()); self.assertFalse(directory.exists()); self.assertIsNone(h.generation_output_path)

    def test_main_window_starts_with_music_generation_tab(self):
        from comfymax_audio_chunker.editor.marker_app import MarkerEditor
        values=dict(sheet_sage_path='',yue2_main_path='selected-yue2',yue2_vae_path='',whisper_model='medium')
        with patch('comfymax_audio_chunker.editor.settings_panel.load_settings',return_value=values), \
             patch('comfymax_audio_chunker.editor.audiocpp_runtime.AudioCppRuntime.ensure_started'):
            window=MarkerEditor()
        self.addCleanup(window.close); self.addCleanup(window.deleteLater)
        labels=[window.views.tabText(i) for i in range(window.views.count())]
        self.assertIn('Music Generation',labels)
        generation_index=window.views.indexOf(window.generation_pane)
        self.assertTrue(window.views.isTabEnabled(generation_index))
        self.assertIs(window.views.currentWidget(),window.generation_pane)
        self.assertTrue(window.generation_lyrics.isEnabled())
        self.assertTrue(window.generation_abc.isEnabled())
        self.assertIsNone(window.doc)
        self.assertFalse(window.views.isTabEnabled(window.views.indexOf(window.views.widget(0))))
        self.assertFalse(window.play_button.isEnabled())
        self.assertFalse(window.analyze_music_button.isEnabled())
        self.assertFalse(hasattr(window,'transcript_toggle')); self.assertGreaterEqual(window.tabs.indexOf(window.transcript_pane),0)
        self.assertFalse(hasattr(window,'include_sheetsage'))
        with patch.object(window.audiocpp_runtime,'ensure_started'):
            window.views.setCurrentWidget(window.generation_pane)
        self.assertEqual(window.generation_model.text(),'Model: selected-yue2')
        directory=Path(window.generation_preview_dir.path()); preview=directory/'closing.wav'; preview.write_bytes(b'audio')
        window.generation_finished({'path':preview}); window.close(); self.app.processEvents()
        self.assertFalse(preview.exists()); self.assertFalse(directory.exists())

    def test_project_enables_existing_pages_and_keeps_generation_available(self):
        from comfymax_audio_chunker.editor.marker_app import MarkerEditor
        values=dict(sheet_sage_path='',yue2_main_path='selected-yue2',yue2_vae_path='',whisper_model='medium')
        with patch('comfymax_audio_chunker.editor.settings_panel.load_settings',return_value=values), \
             patch('comfymax_audio_chunker.editor.audiocpp_runtime.AudioCppRuntime.ensure_started'):
            window=MarkerEditor()
        self.addCleanup(window.close); self.addCleanup(window.deleteLater)
        self.addCleanup(setattr,window,'doc',None)
        window.doc=SimpleNamespace(data={'music_analysis':{}},root=Path(self.temp.name),duration=10)
        window._update_workspace_access()
        self.assertTrue(window.views.isTabEnabled(window.views.indexOf(window.views.widget(0))))
        self.assertTrue(window.views.isTabEnabled(window.views.indexOf(window.generation_pane)))
        self.assertTrue(window.play_button.isEnabled())

    def test_single_analyze_music_button_between_timeline_and_score_keeps_callback(self):
        from comfymax_audio_chunker.editor.marker_app import MarkerEditor
        values=dict(sheet_sage_path='',yue2_main_path='',yue2_vae_path='',whisper_model='medium')
        with patch('comfymax_audio_chunker.editor.settings_panel.load_settings',return_value=values), \
             patch.object(MarkerEditor,'analyze_music',autospec=True) as analyze:
            window=MarkerEditor()
            self.addCleanup(window.close); self.addCleanup(window.deleteLater)
            buttons=[button for button in window.findChildren(QPushButton) if button.text()=='Analyze Music']
            self.assertEqual(buttons,[window.analyze_music_button])
            tabs=window.views.tabBar()
            self.assertEqual((tabs.tabText(0),tabs.tabText(1)),('Timeline','Score'))
            self.assertIs(tabs.tabButton(1,QTabBar.ButtonPosition.LeftSide),window.analyze_music_button)
            window.content.setEnabled(True); window.analyze_music_button.setEnabled(True)
            window.analyze_music_button.click()
            analyze.assert_called_once_with(window)


if __name__=='__main__': unittest.main()
