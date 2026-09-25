"""Native audition, lyric correction and independent scene-cut editing."""
import argparse
from pathlib import Path
import sys
import traceback

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QEvent
from PySide6.QtGui import QAction, QPalette, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QDoubleSpinBox, QCheckBox, QComboBox, QSlider, QSplitter,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QTextEdit,
    QFileDialog, QMessageBox, QLineEdit, QAbstractSpinBox, QTabWidget, QScrollArea)

from .project import Document, playable
from .audio import Transport, load_audio
from .waveform import Waveform, timestamp
from .correction_panel import CorrectionPanel
from .corrections import effective_text
from .scene_panel import ScenePanel
from .phrase_panel import PhrasePanel

ROOT = Path(__file__).resolve().parents[3]


class Loader(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, path, destination=None, parent=None):
        super().__init__(parent)
        self.path, self.destination = path, destination

    def run(self):
        document = None
        try:
            document = Document.create(self.path,self.destination) if self.destination else Document.open(self.path)
            arrays, peaks = load_audio(document)
            self.ready.emit((document,arrays,peaks))
        except Exception as exc:
            if document:
                document.close()
            self.failed.emit(str(exc))


class SaveAsWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, document, destination, parent=None):
        super().__init__(parent)
        self.document, self.destination = document, destination

    def run(self):
        try: self.ready.emit(self.document.save_as(self.destination))
        except Exception as exc: self.failed.emit(str(exc))


class Editor(QMainWindow, CorrectionPanel, ScenePanel, PhrasePanel):
    def __init__(self):
        super().__init__()
        self.doc = None
        self.transport = None
        self.peaks = {}
        self.selected = -1
        self.audition_range = None
        self.dirty = False
        self.loading = False
        self.jobs = []
        self.setWindowTitle('ComfyMax MusicLab — Lyrics + Audio Alignment')
        self.resize(1180,850)
        self.setMinimumSize(880,660)
        shell=QWidget(); self.setCentralWidget(shell)
        layout=QVBoxLayout(shell)
        header=QHBoxLayout()
        self.title=QLabel('Lyrics + Audio Alignment')
        self.title.setStyleSheet('font-size: 18px; font-weight: 600;')
        header.addWidget(self.title,1)
        self.import_button=QPushButton('Import Stage 1…')
        self.open_button=QPushButton('Open project…')
        self.save_button=QPushButton('Save')
        self.save_as_button=QPushButton('Save As…')
        for w in (self.import_button,self.open_button,self.save_button,self.save_as_button): header.addWidget(w)
        layout.addLayout(header)
        self.import_button.clicked.connect(self.choose_import)
        self.open_button.clicked.connect(self.choose_open)
        self.save_button.clicked.connect(lambda:self.save())
        self.save_as_button.clicked.connect(self.choose_save_as)
        self.content=QWidget(); body=QVBoxLayout(self.content); body.setContentsMargins(0,0,0,0)
        layout.addWidget(self.content,1)
        controls=QHBoxLayout()
        self.play_button=QPushButton('Play')
        self.replay_button=QPushButton('Replay phrase')
        self.prev_button=QPushButton('Previous')
        self.next_button=QPushButton('Next')
        self.loop=QCheckBox('Loop')
        self.source=QComboBox(); self.source.addItem('Full mix','mix')
        self.source.setAccessibleName('Playback source')
        for w in (self.play_button,self.replay_button,self.prev_button,self.next_button,self.loop,self.source): controls.addWidget(w)
        controls.addStretch()
        self.clock=QLabel('00:00.000 / 00:00.000'); self.clock.setMinimumWidth(185)
        controls.addWidget(self.clock)
        body.addLayout(controls)
        self.play_button.clicked.connect(self.toggle)
        self.replay_button.clicked.connect(self.replay)
        self.prev_button.clicked.connect(lambda:self.adjacent(-1))
        self.next_button.clicked.connect(lambda:self.adjacent(1))
        self.loop.toggled.connect(self.loop_changed)
        self.source.currentIndexChanged.connect(self.source_changed)
        options=QHBoxLayout()
        options.addWidget(QLabel('Phrase context: before'))
        self.before=QDoubleSpinBox(); self.after=QDoubleSpinBox()
        self.before.setAccessibleName('Context before'); self.after.setAccessibleName('Context after')
        for spin in (self.before,self.after):
            spin.setRange(0,3); spin.setSingleStep(.1); spin.setDecimals(2); spin.setSuffix(' s'); spin.setValue(.5)
            spin.valueChanged.connect(self.context_changed)
        options.addWidget(self.before); options.addWidget(QLabel('after')); options.addWidget(self.after)
        options.addStretch(); options.addWidget(QLabel('Volume'))
        self.volume=QSlider(Qt.Horizontal); self.volume.setRange(0,100); self.volume.setValue(70); self.volume.setMaximumWidth(110)
        self.volume.setAccessibleName('Playback volume'); self.volume.valueChanged.connect(self.volume_changed)
        options.addWidget(self.volume)
        body.addLayout(options)
        splitter=QSplitter(Qt.Vertical)
        waves=QWidget(); wlayout=QVBoxLayout(waves); wlayout.setContentsMargins(0,0,0,0)
        wlayout.addWidget(QLabel('Whole song'))
        self.overview=Waveform(True); self.overview.setMaximumHeight(100); wlayout.addWidget(self.overview)
        tools=QHBoxLayout(); tools.addWidget(QLabel('Detail • regions / phrases / scene cuts'))
        tools.addStretch()
        for label, action in [('Zoom −',lambda:self.detail.zoom(1.5)),('Zoom +',lambda:self.detail.zoom(2/3)),('Fit song',lambda:self.set_view(0,self.doc.duration if self.doc else 1))]:
            button=QPushButton(label); button.clicked.connect(action); tools.addWidget(button)
        wlayout.addLayout(tools)
        self.detail=Waveform(); wlayout.addWidget(self.detail,1)
        for wave in (self.overview,self.detail):
            wave.seek.connect(self.seek); wave.viewChanged.connect(self.set_view)
            wave.audition.connect(self.audition); wave.rangeSelected.connect(self.select_range)
        splitter.addWidget(waves)
        transcript=QWidget(); tl=QVBoxLayout(transcript); tl.setContentsMargins(0,0,0,0)
        self.count_label=QLabel('All transcript segments • click a row to audition')
        tl.addWidget(self.count_label)
        self.table=QTableWidget(0,6)
        self.table.setHorizontalHeaderLabels(['Play','Start','End','Duration','Lyrics (correction or original)','Status'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setAccessibleName('All Whisper transcript phrases')
        for col in (0,1,2,3): self.table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5,QHeaderView.Interactive); self.table.setColumnWidth(5,175)
        self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.cellClicked.connect(lambda row,col:self.audition(row))
        tl.addWidget(self.table,1)
        self.build_corrections(tl)
        self.build_phrase_workflow(tl)
        self.lyrics_scroll=QScrollArea(); self.lyrics_scroll.setWidgetResizable(True)
        self.lyrics_scroll.setFrameShape(QScrollArea.NoFrame); self.lyrics_scroll.setWidget(transcript)
        lower=QSplitter(Qt.Horizontal); lower.addWidget(self.lyrics_scroll); lower.addWidget(self.reference_panel)
        lower.setSizes([760,340])
        self.tabs=QTabWidget(); self.tabs.addTab(lower,'Lyrics'); self.tabs.addTab(self.build_scenes(),'Scenes')
        splitter.addWidget(self.tabs); splitter.setSizes([320,410])
        body.addWidget(splitter,1)
        self.hint=QLabel('Click a phrase to play • drag waveform for a range • edit independent scene cuts in Scenes')
        self.hint.setWordWrap(True); body.addWidget(self.hint)
        self.status=QLabel('Import the analysis.json from a Stage 1 run to begin.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.content.setEnabled(False); self.save_button.setEnabled(False); self.save_as_button.setEnabled(False)
        action=QAction('Save project',self); action.setShortcut('Ctrl+S'); action.triggered.connect(lambda:self.save()); self.addAction(action)
        self.timer=QTimer(self); self.timer.setInterval(40); self.timer.timeout.connect(self.tick); self.timer.start()
        self.autosave=QTimer(self); self.autosave.setSingleShot(True); self.autosave.setInterval(1200); self.autosave.timeout.connect(lambda:self.save(silent=True))
        QApplication.instance().installEventFilter(self)

    def eventFilter(self,obj,event):
        if event.type()==QEvent.KeyPress and self.doc and not self.loading and self.isActiveWindow():
            focus=QApplication.focusWidget()
            if not isinstance(focus,(QLineEdit,QAbstractSpinBox)):
                if event.matches(QKeySequence.Undo): self.undo_edit(); return True
                if event.matches(QKeySequence.Redo): self.redo_edit(); return True
            if event.key()==Qt.Key_Escape:
                self.safe(self.transport.halt); return True
            if event.key() in (Qt.Key_Return,Qt.Key_Enter) and (focus is self.scene_table or
                    (self.tabs.currentIndex()==1 and event.modifiers() & Qt.ControlModifier)):
                self.play_scene(); return True
            if event.key() in (Qt.Key_Return,Qt.Key_Enter) and (event.modifiers() & Qt.ControlModifier or focus is self.table):
                self.replay(); return True
            if event.key()==Qt.Key_Space and not isinstance(focus,(QLineEdit,QAbstractSpinBox,QTextEdit)):
                self.toggle(); return True
        return super().eventFilter(obj,event)

    def choose_import(self):
        analysis,_=QFileDialog.getOpenFileName(self,'Choose Stage 1 analysis.json',str(ROOT/'runs'),'Analysis (*.json)')
        if not analysis: return
        destination,_=QFileDialog.getSaveFileName(self,'Create a new project folder',str(ROOT/'projects'/'Song.comfymax'),'ComfyMax project folder (*.comfymax)')
        if destination: self.load(analysis,destination)

    def choose_open(self):
        path,_=QFileDialog.getOpenFileName(self,'Open project.json',str(ROOT/'projects'),'ComfyMax project (project.json *.json)')
        if path: self.load(path)

    def load(self,path,destination=None):
        if self.loading: return
        if self.doc and not self.prepare_leave(): return
        if self.transport: self.safe(self.transport.halt)
        self.autosave.stop()
        self.loading=True
        self.content.setEnabled(False); self.save_button.setEnabled(False); self.save_as_button.setEnabled(False)
        self.import_button.setEnabled(False); self.open_button.setEnabled(False)
        self.status.setText('Loading and checking audio…')
        worker=Loader(path,destination,self); self.jobs.append(worker)
        worker.ready.connect(self.loaded); worker.failed.connect(self.load_failed)
        worker.finished.connect(lambda:self.release_job(worker))
        worker.start()

    def release_job(self,worker):
        self.jobs.remove(worker); worker.deleteLater()

    def load_failed(self,message):
        self.loading=False
        self.import_button.setEnabled(True); self.open_button.setEnabled(True)
        self.content.setEnabled(self.doc is not None); self.save_button.setEnabled(self.doc is not None)
        self.save_as_button.setEnabled(self.doc is not None)
        self.status.setText('Open/import failed. '+message)
        QMessageBox.warning(self,'Cannot open project',message)

    def loaded(self,result):
        if self.transport: self.transport.close()
        if self.doc: self.doc.close()
        self.doc,arrays,self.peaks=result
        self.cancel_split()
        self.reset_corrections()
        self.transport=Transport(arrays,self.doc.data['timeline']['sample_rate'])
        settings=self.doc.data['settings']; view=self.doc.data['view']
        self.transport.parked=max(0,min(self.transport.total,round(view['position']*self.transport.rate)))
        self.transport.cursor.loop=settings['loop']
        self.source.blockSignals(True); self.source.clear()
        self.source.addItem('Full mix','mix')
        if 'vocals' in arrays: self.source.addItem('Vocals','vocals')
        self.source.setCurrentIndex(max(0,self.source.findData(settings['source'])))
        self.transport.source=self.source.currentData(); self.source.blockSignals(False)
        for control,value in [(self.before,settings['before']),(self.after,settings['after'])]:
            control.blockSignals(True); control.setValue(value); control.blockSignals(False)
        self.loop.blockSignals(True); self.loop.setChecked(settings['loop']); self.loop.blockSignals(False)
        self.volume.blockSignals(True); self.volume.setValue(round(settings.get('volume',.7)*100)); self.volume.blockSignals(False)
        self.transport.volume=self.volume.value()/100
        self.selected=-1; self.audition_range=None
        self.selected_scene=view.get('selected_scene',-1); self.selected_cut=None
        self.tabs.setCurrentIndex(1 if view.get('active_tab')=='scenes' else 0)
        self.refresh_phrase_table()
        for wave in (self.detail,self.overview):
            wave.doc=self.doc; wave.peaks=self.peaks[self.transport.source]; wave.selected=-1
            wave.context=None; wave.position=self.transport.parked/self.transport.rate
            wave.set_view(view.get('start',0),view.get('span',30))
        self.title.setText(self.doc.data['title'])
        self.setWindowTitle(f"ComfyMax MusicLab — {self.doc.data['title']}")
        self.original.clear()
        self.loading=False; self.content.setEnabled(True); self.save_button.setEnabled(True)
        self.save_as_button.setEnabled(True)
        self.import_button.setEnabled(True); self.open_button.setEnabled(True)
        self.dirty=False
        self.status.setText('Recovered last good save — click Save to keep it.' if self.doc.recovered else f'Saved • {self.doc.root}')
        selected=next((i for i,p in enumerate(self.doc.phrases) if p['id']==view.get('selected_id')),-1)
        if selected>=0:
            self.table.selectRow(selected)
            self.detail.set_view(view.get('start',0),view.get('span',30)); self.overview.set_view(self.detail.start,self.detail.span)
        self.autosave.stop(); self.dirty=False
        self.history.setClean()
        self.refresh_scenes()
        self.refresh_review()
        self.status.setText('Recovered last good save — click Save to keep it.' if self.doc.recovered else f'Saved • {self.doc.root}')

    def changed(self):
        if self.doc and not self.loading:
            self.dirty=True; self.status.setText('Unsaved changes'); self.autosave.start()

    def collect(self):
        self.doc.data['settings'].update(before=self.before.value(),after=self.after.value(),loop=self.loop.isChecked(),
                                        source=self.source.currentData(),volume=self.volume.value()/100)
        self.doc.data['view'].update(position=self.transport.position()/self.transport.rate,start=self.detail.start,
                                    span=self.detail.span,selected_id=self.doc.phrases[self.selected]['id'] if self.selected>=0 else None,
                                    selected_scene=self.selected_scene,active_tab='scenes' if self.tabs.currentIndex()==1 else 'lyrics')

    def save(self,silent=False):
        if not self.doc or self.loading: return False
        self.autosave.stop(); self.collect()
        try:
            self.status.setText('Saving…'); self.status.repaint()
            self.doc.save(); self.dirty=False
            self.history.setClean(); self.break_edit_group()
            self.status.setText(f'Saved • {self.doc.root}')
            return True
        except Exception as exc:
            self.dirty=True; self.status.setText(f'Save failed — edits remain in memory. Use Save to retry or Save As. {exc}')
            if not silent:
                message=QMessageBox(self); message.setWindowTitle('Save failed')
                message.setText('Your edits remain in memory. Retry saving or save a new project copy.')
                message.setDetailedText(str(exc))
                retry=message.addButton('Retry',QMessageBox.AcceptRole)
                save_as=message.addButton('Save As…',QMessageBox.ActionRole)
                message.addButton(QMessageBox.Cancel); message.exec()
                if message.clickedButton() is retry: return self.save()
                if message.clickedButton() is save_as: self.choose_save_as()
            return False

    def choose_save_as(self):
        if not self.doc or self.loading: return
        destination,_=QFileDialog.getSaveFileName(self,'Save as a new project folder',str(self.doc.root.parent/'Song-copy.comfymax'),'ComfyMax project folder (*.comfymax)')
        if not destination: return
        self.autosave.stop(); self.collect(); self.transport.halt(); self.loading=True
        self.content.setEnabled(False)
        for b in (self.import_button,self.open_button,self.save_button,self.save_as_button): b.setEnabled(False)
        self.status.setText('Saving project copy…')
        worker=SaveAsWorker(self.doc,destination,self); self.jobs.append(worker)
        worker.ready.connect(self.saved_as); worker.failed.connect(self.save_as_failed)
        worker.finished.connect(lambda:self.release_job(worker)); worker.start()

    def saved_as(self,document):
        self.doc.close(); self.doc=document
        for wave in (self.detail,self.overview): wave.doc=document
        self.loading=False; self.dirty=False; self.history.setClean()
        self.content.setEnabled(True)
        for b in (self.import_button,self.open_button,self.save_button,self.save_as_button): b.setEnabled(True)
        self.status.setText(f'Saved • {document.root}')

    def save_as_failed(self,message):
        self.loading=False; self.dirty=True; self.content.setEnabled(True)
        for b in (self.import_button,self.open_button,self.save_button,self.save_as_button): b.setEnabled(True)
        self.status.setText('Save As failed — original project and edits retained. '+message)
        QMessageBox.warning(self,'Save As failed',message)

    def prepare_leave(self):
        self.autosave.stop()
        if self.dirty or self.doc.recovered:
            choice=QMessageBox.question(self,'Unsaved changes','Save changes before leaving this project?',
                QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Save)
            if choice==QMessageBox.Cancel:
                if self.dirty: self.autosave.start()
                return False
            if choice==QMessageBox.Save: return self.save()
            try: self.doc.discard_pending()
            except OSError as exc:
                QMessageBox.warning(self,'Cannot discard recovery',str(exc)); return False
            return True
        return self.save(silent=True)

    def safe(self,operation):
        try:
            operation()
        except Exception as exc:
            self.status.setText(f'Audio error: {exc}. Check the default Windows output device and try Play again.')
            QMessageBox.warning(self,'Audio playback',str(exc))

    def selection_changed(self):
        row=self.table.currentRow()
        if not self.doc or row<0: return
        if self.split_phrase_id and self.doc.phrases[row]['id']!=self.split_phrase_id: self.cancel_split()
        self.break_edit_group(); self.selected=row
        phrase=self.doc.phrases[row]; self.refresh_correction()
        for wave in (self.overview,self.detail): wave.selected=row
        if playable(phrase,self.doc.duration):
            self.audition_range=None
            self.update_context()
            span=min(self.doc.duration,max(12.,phrase['end']-phrase['start']+4+self.before.value()+self.after.value()))
            self.set_view(max(0.,phrase['start']-2-self.before.value()),span)
        else:
            self.hint.setText('This original segment has invalid timing. It is preserved, but cannot be played in milestone 1.')
        self.changed()

    def update_context(self):
        if not self.doc: return
        if self.audition_range:
            bounds=self.audition_range
        elif self.selected>=0 and playable(self.doc.phrases[self.selected],self.doc.duration):
            p=self.doc.phrases[self.selected]
            bounds=(max(0,p['start']-self.before.value()),min(self.doc.duration,p['end']+self.after.value()))
        else: bounds=None
        for wave in (self.overview,self.detail): wave.context=bounds; wave.update()
        return bounds

    def audition(self,row):
        if not self.doc or not 0<=row<len(self.doc.phrases): return
        self.table.selectRow(row)
        self.selected=row; self.audition_range=None
        for wave in (self.detail,self.overview): wave.selected=row
        if not playable(self.doc.phrases[row],self.doc.duration):
            self.safe(self.transport.halt); self.hint.setText('Cannot audition this segment: its original timing is invalid.'); return
        bounds=self.update_context()
        self.safe(lambda:self.transport.play(round(bounds[0]*self.transport.rate),round(bounds[1]*self.transport.rate),loop=self.loop.isChecked()))
        self.hint.setText(f'Phrase {row+1} • audition {timestamp(bounds[0])} – {timestamp(bounds[1])} • timing locked')

    def replay(self):
        if self.doc and self.selected>=0: self.audition(self.selected)

    def adjacent(self,direction):
        if self.doc and self.doc.phrases:
            row=max(0,min(len(self.doc.phrases)-1,(self.selected if self.selected>=0 else -1)+direction))
            self.audition(row); self.table.scrollToItem(self.table.item(row,0))

    def toggle(self):
        if not self.transport: return
        if self.transport.active: self.safe(self.transport.pause)
        else: self.safe(self.transport.resume)

    def seek(self,seconds):
        if self.transport:
            self.safe(lambda:self.transport.seek(round(seconds*self.transport.rate)))
            self.changed()

    def select_range(self,start,end):
        if not self.doc or end-start<1/self.transport.rate: return
        self.audition_range=(start,end); self.update_context()
        self.safe(lambda:self.transport.play(round(start*self.transport.rate),round(end*self.transport.rate),loop=self.loop.isChecked()))
        self.hint.setText(f'Range audition {timestamp(start)} – {timestamp(end)} • phrase timing unchanged')

    def set_view(self,start,span):
        if not self.doc: return
        for wave in (self.detail,self.overview): wave.set_view(start,span)
        self.changed()

    def context_changed(self):
        if not self.transport: return
        self.update_context(); self.changed()
        if self.transport.active and self.selected>=0 and not self.audition_range:
            self.audition(self.selected)

    def loop_changed(self,value):
        if self.transport:
            self.safe(lambda:self.transport.set_loop(value)); self.changed()

    def source_changed(self):
        if self.transport:
            self.safe(lambda:self.transport.switch(self.source.currentData()))
            for wave in (self.detail,self.overview): wave.peaks=self.peaks[self.transport.source]; wave.update()
            self.changed()

    def volume_changed(self,value):
        if self.transport: self.transport.volume=value/100; self.changed()

    def tick(self):
        if not self.transport or self.loading: return
        try:
            position=self.transport.poll()/self.transport.rate
        except Exception as exc:
            self.status.setText(f'Audio device unavailable: {exc}'); self.transport.active=False; return
        self.clock.setText(f'{timestamp(position)} / {timestamp(self.doc.duration)}')
        self.play_button.setText('Pause' if self.transport.active else 'Play')
        for wave in (self.overview,self.detail): wave.position=position; wave.update()
        for row,p in enumerate(self.doc.phrases):
            now=playable(p,self.doc.duration) and p['start']<=position<p['end'] and self.transport.active
            text='Playing' if now else '▶'
            if self.table.item(row,0).text()!=text: self.table.item(row,0).setText(text)
        if self.transport.warning:
            self.status.setText('Audio device reported: '+self.transport.warning)
            self.transport.warning=''

    def closeEvent(self,event):
        if self.loading:
            QMessageBox.information(self,'Loading','Please wait for the current import/open to finish.'); event.ignore(); return
        if self.doc and not self.prepare_leave(): event.ignore(); return
        if self.transport: self.transport.close()
        if self.doc: self.doc.close()
        QApplication.instance().removeEventFilter(self)
        event.accept()


def legacy_main():
    parser=argparse.ArgumentParser(description='ComfyMax Lyrics + Audio Alignment — comfortable correction')
    parser.add_argument('project',nargs='?',type=Path)
    parser.add_argument('--import-analysis',type=Path)
    parser.add_argument('--destination',type=Path)
    args=parser.parse_args()
    if bool(args.import_analysis)!=bool(args.destination): parser.error('--import-analysis requires --destination')
    app=QApplication(sys.argv[:1]); app.setApplicationName('ComfyMax MusicLab'); app.setStyle('Fusion')
    palette=QPalette()
    for role,color in [(QPalette.Window,'#f3f5f7'),(QPalette.WindowText,'#263342'),
                       (QPalette.Base,'#ffffff'),(QPalette.AlternateBase,'#f0f3f7'),
                       (QPalette.Text,'#182532'),(QPalette.Button,'#edf0f4'),
                       (QPalette.ButtonText,'#182532'),(QPalette.Highlight,'#315f92'),
                       (QPalette.HighlightedText,'#ffffff'),(QPalette.ToolTipBase,'#ffffed'),
                       (QPalette.ToolTipText,'#182532')]:
        palette.setColor(role,QColor(color))
    app.setPalette(palette); app.setFont(QFont('Segoe UI',10))
    app.setStyleSheet('QPushButton { padding: 5px 9px; } QTableWidget { font-size: 13px; } QLabel { color: #263342; }')
    window=Editor(); window.show()
    if args.import_analysis: QTimer.singleShot(0,lambda:window.load(args.import_analysis,args.destination))
    elif args.project: QTimer.singleShot(0,lambda:window.load(args.project))
    return app.exec()


def main():
    from .audio import configure_playback_logging
    configure_playback_logging(ROOT / '.cache' / 'playback.log')
    # The former lyrics editor remains preserved for project compatibility;
    # the public application now opens the independent manual marker editor.
    from .marker_app import main as marker_main
    return marker_main()


if __name__=='__main__':
    raise SystemExit(main())
