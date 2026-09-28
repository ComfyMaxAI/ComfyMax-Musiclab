"""Editable lyrics text, imported one-way from the current transcript."""
import re
from pathlib import Path

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QFileDialog,QHBoxLayout,QMessageBox,QPlainTextEdit,
                               QVBoxLayout,QWidget)


def next_section_label(editor,name):
    text=editor.toPlainText()
    if name=='Verse':
        used={int(value) for value in re.findall(r'^\[Verse (\d+)\]\s*$',text,re.MULTILINE)}
        number=1
        while number in used: number+=1
        return f'[Verse {number}]'
    if name=='Chorus':
        if not re.search(r'^\[Chorus(?: \d+)?\]\s*$',text,re.MULTILINE): return '[Chorus]'
        used={int(value) for value in re.findall(r'^\[Chorus (\d+)\]\s*$',text,re.MULTILINE)}
        number=2
        while number in used: number+=1
        return f'[Chorus {number}]'
    return f'[{name}]'


def insert_section(editor,name):
    cursor=editor.textCursor(); position=cursor.position(); text=editor.toPlainText()
    if position>0 and text[position-1]!='\n': cursor.insertText('\n')
    cursor.insertText(next_section_label(editor,name))
    current=editor.toPlainText()
    if cursor.position()<len(current) and current[cursor.position()]=='\n': cursor.movePosition(QTextCursor.NextCharacter)
    else: cursor.insertText('\n')
    editor.setTextCursor(cursor); editor.setFocus()


class LyricsPanel:
    def build_lyrics_editor(self):
        self.lyrics_file_path=None
        self.lyrics_editor_dirty=False
        self.lyrics_pane=QWidget()
        layout=QVBoxLayout(self.lyrics_pane)
        actions=QHBoxLayout()
        self.button(actions,'Import Transcript',self.import_transcript_to_lyrics)
        self.button(actions,'Load Lyrics',self.load_lyrics)
        self.button(actions,'Save Lyrics',self.save_lyrics_file)
        actions.addStretch(); layout.addLayout(actions)
        sections=QHBoxLayout()
        for name in ('Intro','Verse','Chorus','Pre-Chorus','Bridge','Instrumental','Solo','Outro'):
            self.button(sections,name,lambda checked=False,n=name:self.insert_lyrics_section(n))
        sections.addStretch(); layout.addLayout(sections)
        self.lyrics_editor=QPlainTextEdit()
        self.lyrics_editor.setAccessibleName('Lyrics editor')
        self.lyrics_editor.setPlaceholderText('Import the current transcript or type lyrics here…')
        self.lyrics_editor.textChanged.connect(self._lyrics_changed)
        layout.addWidget(self.lyrics_editor,1)

    def _lyrics_changed(self):
        self.lyrics_editor_dirty=True

    def _set_lyrics_text(self,text,dirty):
        self.lyrics_editor.blockSignals(True); self.lyrics_editor.setPlainText(text); self.lyrics_editor.blockSignals(False)
        self.lyrics_editor_dirty=dirty

    def current_transcript_text(self):
        draft=getattr(self,'transcript_draft',None)
        if not draft: return ''
        segments=draft.get('lyrics',{}).get('segments',[])
        return '\n'.join(str(segment.get('text','')).strip() for segment in segments
                         if str(segment.get('text','')).strip())

    def import_transcript_to_lyrics(self):
        text=self.current_transcript_text()
        if not text:
            QMessageBox.information(self,'Import Transcript','No transcript text is available.'); return False
        if self.lyrics_editor.toPlainText() and not self._confirm_replace_lyrics(): return False
        self._set_lyrics_text(text,True); return True

    def _confirm_replace_lyrics(self):
        dialog=QMessageBox(QMessageBox.Question,'Import Transcript',
            'Replace existing lyrics with current transcript?',parent=self)
        replace=dialog.addButton('Replace',QMessageBox.AcceptRole)
        dialog.addButton('Cancel',QMessageBox.RejectRole)
        dialog.exec()
        return dialog.clickedButton() is replace

    def _next_section_label(self,name):
        return next_section_label(self.lyrics_editor,name)

    def insert_lyrics_section(self,name):
        insert_section(self.lyrics_editor,name)

    def save_lyrics_file(self):
        path=self.lyrics_file_path
        if path is None:
            chosen,_=QFileDialog.getSaveFileName(self,'Save Lyrics','','Text files (*.txt)')
            if not chosen: return False
            path=Path(chosen)
        try: path.write_text(self.lyrics_editor.toPlainText(),encoding='utf-8')
        except OSError as exc:
            QMessageBox.warning(self,'Save Lyrics',f'Could not save lyrics: {exc}'); return False
        self.lyrics_file_path=path; self.lyrics_editor_dirty=False; return True

    def load_lyrics(self):
        if not self.resolve_lyrics_editor_changes(): return False
        chosen,_=QFileDialog.getOpenFileName(self,'Load Lyrics','','Text files (*.txt);;All files (*)')
        if not chosen: return False
        path=Path(chosen)
        try: text=path.read_text(encoding='utf-8')
        except (OSError,UnicodeError) as exc:
            QMessageBox.warning(self,'Load Lyrics',f'Could not load UTF-8 lyrics: {exc}'); return False
        self._set_lyrics_text(text,False); self.lyrics_file_path=path; return True

    def resolve_lyrics_editor_changes(self):
        if not self.lyrics_editor_dirty: return True
        answer=QMessageBox.question(self,'Unsaved Lyrics','Save lyrics before continuing?',
            QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Cancel)
        if answer==QMessageBox.Cancel: return False
        if answer==QMessageBox.Save: return self.save_lyrics_file()
        return True
