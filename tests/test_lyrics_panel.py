import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication,QMessageBox,QPushButton,QVBoxLayout,QWidget

from comfymax_audio_chunker.editor.lyrics_panel import LyricsPanel


class Host(QWidget,LyricsPanel):
    def __init__(self):
        super().__init__(); self.layout=QVBoxLayout(self); self.build_lyrics_editor(); self.layout.addWidget(self.lyrics_pane)

    @staticmethod
    def button(layout,text,slot):
        button=QPushButton(text); button.clicked.connect(slot); layout.addWidget(button); return button


class LyricsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.host=Host()

    def tearDown(self):
        self.host.deleteLater(); self.app.processEvents()

    def test_import_uses_current_segments_without_timestamps_or_mutation(self):
        segments=[{'start':1.25,'end':2.5,'text':'  Hé hallo  '},{'start':3,'end':4,'text':'wereld'}]
        self.host.transcript_draft={'lyrics':{'segments':segments}}
        self.assertTrue(self.host.import_transcript_to_lyrics())
        self.assertEqual(self.host.lyrics_editor.toPlainText(),'Hé hallo\nwereld')
        self.assertEqual(segments[0]['text'],'  Hé hallo  ')
        self.assertNotIn('1.25',self.host.lyrics_editor.toPlainText())
        self.assertTrue(self.host.lyrics_editor_dirty)

    def test_import_protects_existing_text(self):
        self.host.transcript_draft={'lyrics':{'segments':[{'text':'new'}]}}
        self.host.lyrics_editor.setPlainText('keep')
        with patch.object(self.host,'_confirm_replace_lyrics',return_value=False):
            self.assertFalse(self.host.import_transcript_to_lyrics())
        self.assertEqual(self.host.lyrics_editor.toPlainText(),'keep')
        with patch.object(self.host,'_confirm_replace_lyrics',return_value=True):
            self.assertTrue(self.host.import_transcript_to_lyrics())
        self.assertEqual(self.host.lyrics_editor.toPlainText(),'new')

    def test_section_insertion_and_numbering(self):
        self.host.lyrics_editor.setPlainText('first line\nsecond line')
        cursor=self.host.lyrics_editor.textCursor(); cursor.setPosition(len('first'))
        self.host.lyrics_editor.setTextCursor(cursor); self.host.insert_lyrics_section('Verse')
        self.assertEqual(self.host.lyrics_editor.toPlainText(),'first\n[Verse 1]\n line\nsecond line')
        self.host.insert_lyrics_section('Verse'); self.host.insert_lyrics_section('Chorus'); self.host.insert_lyrics_section('Chorus')
        text=self.host.lyrics_editor.toPlainText()
        for label in ('[Verse 1]','[Verse 2]','[Chorus]','[Chorus 2]'): self.assertIn(label,text)
        self.assertEqual(self.host.lyrics_editor.textCursor().position(),text.index('\n',text.index('[Chorus 2]'))+1)

    def test_all_fixed_section_labels(self):
        for name in ('Intro','Pre-Chorus','Bridge','Instrumental','Solo','Outro'):
            self.host.insert_lyrics_section(name)
        text=self.host.lyrics_editor.toPlainText()
        for name in ('Intro','Pre-Chorus','Bridge','Instrumental','Solo','Outro'):
            self.assertIn(f'[{name}]',text)

    def test_save_reuses_path_and_load_preserves_utf8_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            first=Path(folder)/'lyrics.txt'; second=Path(folder)/'other.txt'
            value='[Verse 1]\nCafé déjà vu\n\n終わり'
            self.host.lyrics_editor.setPlainText(value)
            with patch('comfymax_audio_chunker.editor.lyrics_panel.QFileDialog.getSaveFileName',return_value=(str(first),'')) as dialog:
                self.assertTrue(self.host.save_lyrics_file()); self.assertTrue(self.host.save_lyrics_file())
            dialog.assert_called_once(); self.assertEqual(first.read_text(encoding='utf-8'),value)
            second.write_text('nieuw\n\naccentué',encoding='utf-8')
            self.host.lyrics_editor.setPlainText('discard me')
            with patch('comfymax_audio_chunker.editor.lyrics_panel.QMessageBox.question',return_value=QMessageBox.Discard), \
                 patch('comfymax_audio_chunker.editor.lyrics_panel.QFileDialog.getOpenFileName',return_value=(str(second),'')):
                self.assertTrue(self.host.load_lyrics())
            self.assertEqual(self.host.lyrics_editor.toPlainText(),'nieuw\n\naccentué')
            self.assertEqual(self.host.lyrics_file_path,second); self.assertFalse(self.host.lyrics_editor_dirty)

    def test_dirty_resolution_save_discard_cancel(self):
        self.host.lyrics_editor.setPlainText('changed')
        with patch('comfymax_audio_chunker.editor.lyrics_panel.QMessageBox.question',return_value=QMessageBox.Cancel):
            self.assertFalse(self.host.resolve_lyrics_editor_changes())
        with patch('comfymax_audio_chunker.editor.lyrics_panel.QMessageBox.question',return_value=QMessageBox.Discard):
            self.assertTrue(self.host.resolve_lyrics_editor_changes())
        with patch('comfymax_audio_chunker.editor.lyrics_panel.QMessageBox.question',return_value=QMessageBox.Save), \
             patch.object(self.host,'save_lyrics_file',return_value=True) as save:
            self.assertTrue(self.host.resolve_lyrics_editor_changes()); save.assert_called_once()


if __name__=='__main__': unittest.main()
