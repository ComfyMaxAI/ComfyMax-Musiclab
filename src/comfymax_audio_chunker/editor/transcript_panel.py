"""Explicitly saved lyrics drafts, independent of scene phrases and music data."""
import copy
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import time

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,
    QTableWidgetItem,QAbstractItemView,QMessageBox,QDialog,QPlainTextEdit,QComboBox)
from . import transcript


class TranscriptTask(QThread):
    ready=Signal(object); failed=Signal(str)

    def __init__(self,request,parent):
        super().__init__(parent); self.request=request

    def run(self):
        try:
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0)
            started=time.monotonic()
            with subprocess.Popen([sys.executable,'-m','comfymax_audio_chunker.editor.transcript_worker'],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    text=True,encoding='utf-8',errors='replace',creationflags=flags) as process:
                try:
                    payload=json.dumps(self.request)
                    while True:
                        if self.isInterruptionRequested(): raise RuntimeError('Transcription cancelled; previous lyrics retained.')
                        if time.monotonic()-started>3600: raise RuntimeError('Whisper timed out after one hour.')
                        try:
                            stdout,stderr=process.communicate(payload,timeout=.2); break
                        except subprocess.TimeoutExpired: payload=None
                    if process.returncode: raise RuntimeError('Whisper worker failed: '+stderr[-2000:])
                finally:
                    if process.poll() is None: process.kill(); process.communicate()
            response=json.loads(stdout)
            if not isinstance(response,dict) or response.get('protocol')!=transcript.SCHEMA or response.get('success') is not True:
                raise ValueError(str(response.get('error','Invalid Whisper response')) if isinstance(response,dict) else 'Invalid Whisper response')
            value=response['transcript']; transcript.validate(value,self.request['duration'])
            if value['source_audio']!=self.request['source']: raise ValueError('Whisper source mismatch')
            self.ready.emit(value)
        except Exception as exc:
            self.failed.emit(str(exc))


class TranscriptPanel:
    def build_transcript(self):
        self.transcript_job=None; self.transcript_draft=None; self.lyrics_dirty=False
        self.transcript_pane=QWidget(); layout=QVBoxLayout(self.transcript_pane)
        buttons=QHBoxLayout()
        self.transcribe_button=self.button(buttons,'Transcribe Lyrics',self.transcribe_lyrics)
        self.cancel_transcript_button=self.button(buttons,'Cancel',self.cancel_transcript)
        self.cancel_transcript_button.setEnabled(False)
        self.save_lyrics_button=self.button(buttons,'Save Lyrics',self.save_lyrics)
        self.revert_lyrics_button=self.button(buttons,'Revert to Whisper',self.revert_lyrics)
        self.button(buttons,'View raw transcript',self.view_raw_transcript)
        self.button(buttons,'Play Segment',lambda:self.play_transcript(self.transcript_table.currentRow()) if self.transcript_table.currentRow()>=0 else None)
        layout.addLayout(buttons)
        options=QHBoxLayout(); options.addWidget(QLabel('Language'))
        self.transcript_language=QComboBox()
        for label,code in transcript.LANGUAGES: self.transcript_language.addItem(label,code)
        self.transcript_language.setToolTip('Language for the next complete transcription. Auto detects once. Existing lyrics are unchanged.')
        options.addWidget(self.transcript_language); options.addStretch(); layout.addLayout(options)
        self.lyrics_status=QLabel('No transcript. Transcribe Lyrics uses existing VOCALS, otherwise MIX.'); self.lyrics_status.setWordWrap(True)
        layout.addWidget(self.lyrics_status)
        self.transcript_table=self.table(['Start','End','Lyrics — double-click text to edit'])
        self.transcript_table.setEditTriggers(QAbstractItemView.DoubleClicked|QAbstractItemView.EditKeyPressed)
        self.transcript_table.itemChanged.connect(self.lyric_edited)
        self.transcript_table.cellDoubleClicked.connect(self.play_transcript)
        layout.addWidget(self.transcript_table)

    def refresh_transcript(self):
        self.transcript_draft=transcript.from_existing(self.doc.data) if self.doc else None
        selected=(self.transcript_draft or {}).get('provenance',{}).get('requested_language')
        self.transcript_language.setCurrentIndex(max(0,self.transcript_language.findData(selected)))
        self.lyrics_dirty=False; self.render_lyrics()

    def render_lyrics(self):
        rows=self.transcript_draft['lyrics']['segments'] if self.transcript_draft else []
        evidence=transcript.segment_evidence(self.transcript_draft) if self.transcript_draft else []
        self.lyrics_review_count=sum(bool(transcript.review_reasons(e)) for e in evidence)
        self.transcript_table.blockSignals(True); self.transcript_table.setRowCount(len(rows))
        for row,segment in enumerate(rows):
            for col,key in enumerate(('start','end','text')):
                value=f'{segment[key]:.6f}' if col<2 else segment[key]
                item=QTableWidgetItem(value)
                reasons=transcript.review_reasons(evidence[row])
                details='\n'.join(f'{k}: {v}' for k,v in evidence[row].items())
                item.setToolTip(('Manual checking recommended: '+ '; '.join(reasons)+'\n' if reasons else '')+details)
                if reasons:
                    item.setBackground(QColor('#604515')); item.setForeground(QColor('#fff0c2'))
                if col<2: item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.transcript_table.setItem(row,col,item)
        self.transcript_table.blockSignals(False); self.update_lyrics_status()

    def update_lyrics_status(self):
        draft=self.transcript_draft
        source=(draft or {}).get('source_audio') or {}
        count=len(draft['lyrics']['segments']) if draft else 0
        state='Unsaved lyrics — use Save Lyrics' if self.lyrics_dirty else 'Saved lyrics / imported reference'
        language=(draft or {}).get('provenance',{}).get('requested_language')
        selected=dict((code,label) for label,code in transcript.LANGUAGES).get(language,'Auto')
        self.lyrics_status.setText(f'{state} · {count} segments · source: {source.get("asset","imported/none")} · requested language: {selected} · {getattr(self,"lyrics_review_count",0)} amber segments: manual checking recommended (not a confidence probability). Text edits never change timing.')
        self.save_lyrics_button.setEnabled(self.lyrics_dirty and not self.transcript_job)
        self.revert_lyrics_button.setEnabled(bool(draft) and not self.transcript_job)

    def lyric_edited(self,item):
        if item.column()!=2 or not self.transcript_draft: return
        self.transcript_draft['lyrics']['segments'][item.row()]['text']=item.text()
        self.lyrics_dirty=True; self.update_lyrics_status()

    def resolve_lyrics_draft(self):
        if not self.lyrics_dirty: return True
        answer=QMessageBox.question(self,'Unsaved lyrics','Save Lyrics before continuing?',
            QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Cancel)
        if answer==QMessageBox.Cancel: return False
        if answer==QMessageBox.Save: return self.save_lyrics()
        self.refresh_transcript(); return True

    def save_lyrics(self):
        if not self.doc or self.busy or not self.transcript_draft: return False
        self.transcript_table.clearFocus()
        value=copy.deepcopy(self.transcript_draft)
        value['lyrics']['edited']=value['lyrics']['segments']!=value['raw']['segments']
        value['lyrics']['saved_at']=datetime.now(timezone.utc).isoformat()
        transcript.validate(value,self.doc.duration)
        previous=self.doc.data.get('transcript')
        self.doc.data['transcript']=value
        if not self.save():
            if previous is None: self.doc.data.pop('transcript',None)
            else: self.doc.data['transcript']=previous
            return False
        self.transcript_draft=copy.deepcopy(value); self.lyrics_dirty=False; self.update_lyrics_status(); return True

    def revert_lyrics(self):
        if not self.transcript_draft or self.busy: return
        draft=self.transcript_draft
        if draft['lyrics']['segments']!=draft['raw']['segments']:
            answer=QMessageBox.question(self,'Revert to Whisper','Replace all current lyric corrections with the preserved raw text? Use Save Lyrics to commit the revert.',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
            if answer!=QMessageBox.Yes: return
        draft['lyrics']['segments']=copy.deepcopy(draft['raw']['segments'])
        self.lyrics_dirty=True; self.render_lyrics()

    def view_raw_transcript(self):
        if not self.transcript_draft: return
        dialog=QDialog(self); dialog.setWindowTitle('Raw transcript — read only'); dialog.resize(850,600)
        layout=QVBoxLayout(dialog); text=QPlainTextEdit(); text.setReadOnly(True)
        text.setPlainText(json.dumps(self.transcript_draft['raw'],ensure_ascii=False,indent=2)); layout.addWidget(text)
        dialog.exec()

    def transcribe_lyrics(self):
        selected_language=self.transcript_language.currentData()
        if not self.doc or self.busy or self.transcript_job or not self.resolve_lyrics_draft(): return
        available,message=transcript.availability()
        if not available: self.lyrics_status.setText(message); return
        if self.transcript_draft and self.transcript_draft['raw']['segments']:
            if QMessageBox.question(self,'Transcribe Lyrics','Generate a new transcript? Existing saved raw/edited lyrics will be retained in history when you Save Lyrics.',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        key,asset=transcript.source_audio(self.doc.data)
        active=[(a/self.transport.rate,b/self.transport.rate) for a,b in self.classifier.spans] if self.classifier.available else [(0,self.doc.duration)]
        request=dict(protocol=transcript.SCHEMA,path=str(self.doc.root/asset['path']),duration=self.doc.duration,
                     active=active,source=dict(asset=key,sha256=asset['sha256']),language=selected_language)
        self.transcript_language.setCurrentIndex(max(0,self.transcript_language.findData(selected_language)))
        job=TranscriptTask(request,self); self.transcript_job=job; self.jobs.append(job)
        self.busy=True; self.autosave.stop()
        for button in (self.load_button,self.open_button,self.import_button,self.save_button,self.save_as_button,self.analyze_music_button,self.transcribe_button,self.save_lyrics_button,self.revert_lyrics_button): button.setEnabled(False)
        self.transcript_language.setEnabled(False)
        self.transcript_table.setEnabled(False); self.cancel_transcript_button.setEnabled(True)
        self.lyrics_status.setText(f'Transcribing {key.upper()} with cached Whisper {transcript.MODEL} (CPU/int8)…')
        job.ready.connect(self.transcript_ready); job.failed.connect(self.lyrics_status.setText)
        job.finished.connect(lambda:self.transcript_finished(job)); job.start()

    def cancel_transcript(self):
        if self.transcript_job: self.transcript_job.requestInterruption()

    def transcript_ready(self,value):
        if self.transcript_job.isInterruptionRequested(): return
        previous=self.doc.data.get('transcript')
        if previous:
            value['history']=copy.deepcopy(previous.get('history',[]))+[{k:copy.deepcopy(v) for k,v in previous.items() if k!='history'}]
        self.transcript_draft=value; self.lyrics_dirty=True; self.render_lyrics()

    def transcript_finished(self,job):
        self.transcript_job=None; self.busy=False
        self.transcript_language.setEnabled(True)
        for button in (self.load_button,self.open_button,self.import_button,self.save_button,self.save_as_button,self.analyze_music_button,self.transcribe_button): button.setEnabled(True)
        self.cancel_transcript_button.setEnabled(False); self.transcript_table.setEnabled(True)
        self.save_lyrics_button.setEnabled(self.lyrics_dirty); self.revert_lyrics_button.setEnabled(bool(self.transcript_draft))
        self.finish_task(job)
        if self.dirty: self.autosave.start()
