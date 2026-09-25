"""The main application: load/separate, edit markers, explicitly create scenes."""
import argparse
import copy
from datetime import datetime
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import Qt,QThread,Signal,QTimer
from PySide6.QtGui import QAction,QUndoStack,QUndoCommand,QColor,QFont,QPalette
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QComboBox,QDoubleSpinBox,QSlider,QCheckBox,QSplitter,QTabWidget,QTableWidget,QTableWidgetItem,
    QAbstractItemView,QHeaderView,QFileDialog,QMessageBox,QProgressBar,QLineEdit,QTextEdit,QAbstractSpinBox)
from .project import Document,playable
from .audio import Transport,Cursor,load_audio
from .marker_waveform import MarkerWaveform
from .markers import (initial_state,boundaries,intervals,created_scenes,scenes_current,create_scenes,
                      add_marker,move_marker,delete_marker,validate_marker_state,set_interval_type,add_automatic_markers)
from .interval_types import VocalActivity,TYPES
from .theme import apply_theme
from .music_panel import MusicPanel
from .follow import follow_start
from .transcript_panel import TranscriptPanel
from .exporter import export_project,validate_scenes,ExportValidationError

ROOT=Path(__file__).resolve().parents[3]


def exact_time(seconds):
    minutes=int(seconds//60)
    return f'{minutes:02d}:{seconds-minutes*60:09.6f}'


class Task(QThread):
    ready=Signal(object); failed=Signal(str)
    def __init__(self,operation,parent):
        super().__init__(parent); self.operation=operation
    def run(self):
        try: self.ready.emit(self.operation())
        except Exception as exc: self.failed.emit(str(exc))


class ExportTask(QThread):
    ready=Signal(object); failed=Signal(str); progress=Signal(str)
    def __init__(self,root,data,destination,parent):
        super().__init__(parent); self.root=root; self.data=copy.deepcopy(data); self.destination=destination
    def run(self):
        try: self.ready.emit(export_project(self.root,self.data,self.destination,self.progress.emit))
        except Exception as exc: self.failed.emit(str(exc))


class StateCommand(QUndoCommand):
    def __init__(self,window,before,after,label):
        super().__init__(label); self.window=window; self.before=copy.deepcopy(before); self.after=copy.deepcopy(after)
    def redo(self): self.window.apply_state(self.after)
    def undo(self): self.window.apply_state(self.before)


def open_audio(path,destination=None):
    doc=Document.create(path,destination) if destination else Document.open(path)
    try:
        arrays,peaks=load_audio(doc)
        return doc,arrays,peaks,VocalActivity(arrays,doc.data['timeline']['sample_rate'])
    except BaseException: doc.close(); raise


def separate_song(song,destination):
    """Run only the established Demucs pipeline in its dedicated interpreter."""
    if Path(destination).exists(): raise ValueError('Choose a new project folder; existing projects are never overwritten.')
    run=ROOT/'runs'/('markers-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    interpreter=Path(sys.executable).with_name('python.exe')
    result=subprocess.run([str(interpreter),'-m','comfymax_audio_chunker',str(song),'--output',str(run),'--separate-only'],
                          capture_output=True,text=True,encoding='utf-8',errors='replace',
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode: raise RuntimeError(f'Demucs preparation failed. Run folder: {run}\n'+result.stderr[-2500:])
    return open_audio(run/'analysis.json',destination)


class MarkerEditor(QMainWindow, MusicPanel, TranscriptPanel):
    def __init__(self):
        super().__init__(); apply_theme(self); self.doc=None; self.transport=None; self.peaks={}; self.selected_marker=None
        self.dirty=False; self.busy=False; self.jobs=[]; self.history=QUndoStack(self)
        self.setWindowTitle('ComfyMax MusicLab — Markers'); self.resize(1220,880); self.setMinimumSize(900,700)
        shell=QWidget(); self.setCentralWidget(shell); layout=QVBoxLayout(shell)
        header=QHBoxLayout(); self.title=QLabel('ComfyMax MusicLab'); self.title.setObjectName('projectTitle'); header.addWidget(self.title,1)
        self.load_button=self.button(header,'Load Song',self.choose_song)
        self.open_button=self.button(header,'Open Project',self.choose_open)
        self.import_button=self.button(header,'Import Stage 1',self.choose_import)
        self.save_button=self.button(header,'Save',lambda:self.save())
        self.save_as_button=self.button(header,'Save As',self.choose_save_as); layout.addLayout(header)
        self.content=QWidget(); body=QVBoxLayout(self.content); body.setContentsMargins(0,0,0,0); layout.addWidget(self.content,1)
        controls=QHBoxLayout(); self.play_button=self.button(controls,'Play',self.toggle)
        self.source=QComboBox(); self.source.addItem('Full Mix','mix'); self.source.currentIndexChanged.connect(self.switch_source); controls.addWidget(self.source)
        self.loop=QCheckBox('Loop audition'); self.loop.toggled.connect(self.loop_changed); controls.addWidget(self.loop)
        self.follow_playhead=QCheckBox('Follow Playhead'); self.follow_playhead.setChecked(True)
        self.follow_playhead.setToolTip('Follow the audio clock at 45% of the window. Manual zoom, pan or Fit song switches Follow off; enable it again to resume.')
        controls.addWidget(self.follow_playhead)
        controls.addStretch(); self.clock=QLabel('00:00.000000'); controls.addWidget(self.clock)
        controls.addWidget(QLabel('Volume')); self.volume=QSlider(Qt.Horizontal); self.volume.setRange(0,100); self.volume.setValue(70); self.volume.setMaximumWidth(110)
        self.volume.valueChanged.connect(self.volume_changed); controls.addWidget(self.volume); body.addLayout(controls)
        self.overview=MarkerWaveform(True); self.overview.setMaximumHeight(90); body.addWidget(self.overview)
        splitter=QSplitter(Qt.Vertical); waves=QWidget(); wl=QVBoxLayout(waves); wl.setContentsMargins(0,0,0,0)
        zoom=QHBoxLayout(); zoom.addWidget(QLabel('Click to seek • drag an amber marker to move it')); zoom.addStretch()
        zoom.addWidget(QLabel('Chords:'))
        self.chord_source=QComboBox(); self.chord_source.addItems(['Final','CNN raw','Template'])
        self.chord_source.setEnabled(False)
        self.chord_source.setAccessibleName('Displayed chord source')
        self.chord_source.setToolTip('Display saved evidence only. Purple dashed lines are downbeats, not markers.\nCNN raw preserves original neural timing; no analysis or snapping is performed.')
        self.chord_source.currentTextChanged.connect(self.set_chord_source)
        zoom.addWidget(self.chord_source)
        self.button(zoom,'Zoom −',lambda:self.detail.zoom(1.5)); self.button(zoom,'Zoom +',lambda:self.detail.zoom(2/3))
        self.button(zoom,'Fit song',lambda:self.set_view(0,self.doc.duration)); wl.addLayout(zoom)
        self.detail=MarkerWaveform(); wl.addWidget(self.detail,1); splitter.addWidget(waves)
        self.navigation=QSlider(Qt.Horizontal); self.navigation.setObjectName('waveNavigation')
        self.navigation.setAccessibleName('Scroll waveform window'); self.navigation.setMinimumHeight(44)
        self.navigation.setRange(0,0); self.navigation.setTracking(True)
        self.navigation.setToolTip('Move the visible waveform window. Does not seek audio or move markers.')
        self.navigation.valueChanged.connect(self.scroll_waveform); wl.addWidget(self.navigation)
        for wave in (self.overview,self.detail):
            wave.seek.connect(self.seek); wave.viewChanged.connect(self.set_view)
            wave.cutSelected.connect(self.select_marker); wave.cutMoved.connect(self.drag_marker)
        self.tabs=QTabWidget(); splitter.addWidget(self.tabs); splitter.setSizes([300,320])
        from .score_panel import ScorePanel
        self.score_panel = ScorePanel(self)
        self.views = QTabWidget()
        self.views.addTab(splitter, 'Timeline')
        self.views.addTab(self.score_panel, 'Score')
        self.views.addTab(self.score_panel.abc, 'ABC')
        self.views.currentChanged.connect(self.change_notation_view)
        body.addWidget(self.views, 1)
        markers=QWidget(); ml=QVBoxLayout(markers); ml.setContentsMargins(4,4,4,4)
        edit=QHBoxLayout(); self.add_button=self.button(edit,'Place Marker at Playhead',self.add_at_playhead)
        edit.addWidget(QLabel('Selected marker (seconds):')); self.marker_time=QDoubleSpinBox(); self.marker_time.setDecimals(6); self.marker_time.setRange(0,999999); self.marker_time.setSingleStep(.1); edit.addWidget(self.marker_time)
        self.move_button=self.button(edit,'Move Marker',self.move_selected); self.delete_button=self.button(edit,'Delete Marker',self.delete_selected)
        self.undo_button=self.button(edit,'Undo',self.history.undo); self.redo_button=self.button(edit,'Redo',self.history.redo)
        self.history.canUndoChanged.connect(self.undo_button.setEnabled); self.history.canRedoChanged.connect(self.redo_button.setEnabled)
        self.undo_button.setEnabled(False); self.redo_button.setEnabled(False); ml.addLayout(edit)
        automatic=QHBoxLayout(); automatic.addWidget(QLabel('Automatic marker interval:'))
        self.automatic_interval=QDoubleSpinBox(); self.automatic_interval.setDecimals(3)
        self.automatic_interval.setRange(.001,999999); self.automatic_interval.setValue(15.0)
        self.automatic_interval.setSuffix(' seconds'); self.automatic_interval.setAccessibleName('Automatic marker interval')
        self.automatic_interval.setToolTip('Absolute master-song intervals. Existing export validation requires scenes of at most 15 seconds.')
        automatic.addWidget(self.automatic_interval)
        self.clear_existing_markers=QCheckBox('Clear existing markers first'); self.clear_existing_markers.setChecked(True)
        automatic.addWidget(self.clear_existing_markers)
        self.automatic_button=self.button(automatic,'Add automatic markers',self.add_automatic)
        automatic.addStretch(); ml.addLayout(automatic)
        listen=QHBoxLayout(); self.audition_button=self.button(listen,'Audition Around Marker',self.audition_marker)
        listen.addWidget(QLabel('Before / after:')); self.before=QDoubleSpinBox(); self.after=QDoubleSpinBox()
        for spin in (self.before,self.after):
            spin.setRange(0,5); spin.setDecimals(2); spin.setValue(1.5); spin.setSuffix(' s'); spin.valueChanged.connect(self.changed); listen.addWidget(spin)
        listen.addStretch(); self.create_button=self.button(listen,'Create Scenes',self.create_scene_snapshot); ml.addLayout(listen)
        self.marker_table=self.table(['Marker','Exact timestamp','Interval to next marker','Type','Warning']); ml.addWidget(self.marker_table,1)
        self.marker_table.itemSelectionChanged.connect(self.marker_selection_changed)
        self.marker_table.cellDoubleClicked.connect(lambda row,col:self.audition_marker() if col!=3 else None)
        self.marker_summary=QLabel(); self.marker_summary.setWordWrap(True); ml.addWidget(self.marker_summary); self.tabs.addTab(markers,'Markers')
        scenes=QWidget(); sl=QVBoxLayout(scenes); self.scene_status=QLabel('No scenes created. Place markers, then choose Create Scenes.'); self.scene_status.setWordWrap(True); sl.addWidget(self.scene_status)
        bar=QHBoxLayout(); self.button(bar,'Back to Markers',lambda:self.tabs.setCurrentIndex(0)); self.button(bar,'Create Scenes Again',self.create_scene_snapshot)
        self.scene_play=self.button(bar,'Play Selected Scene',self.play_scene); bar.addStretch()
        self.export_button=self.button(bar,'Export Scenes',self.choose_export); self.export_button.setEnabled(False)
        self.export_button.setObjectName('primary'); self.create_button.setObjectName('primary'); self.load_button.setObjectName('primary')
        self.export_button.setToolTip('All scenes use Demucs vocals. Type is metadata only. Exact boundaries, no padding.'); sl.addLayout(bar)
        self.scene_table=self.table(['Scene','Start','End','Duration','Type','Warning']); sl.addWidget(self.scene_table,1)
        self.scene_table.cellDoubleClicked.connect(lambda *_:self.play_scene()); self.tabs.addTab(scenes,'Scenes')
        optional=QHBoxLayout(); self.transcript_toggle=QCheckBox('Show transcript (optional listening aid)'); self.transcript_toggle.toggled.connect(self.show_transcript); optional.addWidget(self.transcript_toggle); optional.addStretch(); body.addLayout(optional)
        self.build_transcript()
        self.build_music(header)
        self.progress=QProgressBar(); self.progress.setRange(0,0); self.progress.hide(); layout.addWidget(self.progress)
        self.status=QLabel('Load a song for local Demucs separation, or open an existing project.'); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.content.setEnabled(False); self.save_button.setEnabled(False); self.save_as_button.setEnabled(False)
        self.autosave=QTimer(self); self.autosave.setSingleShot(True); self.autosave.setInterval(1200); self.autosave.timeout.connect(lambda:self.save(True))
        self.timer=QTimer(self); self.timer.setInterval(40); self.timer.timeout.connect(self.tick); self.timer.start()
        for key,slot in [('Ctrl+S',lambda:self.save()),('Ctrl+Z',self.history.undo),('Ctrl+Y',self.history.redo),('Space',self.toggle),('M',self.add_at_playhead),('Escape',self.stop)]:
            action=QAction(self); action.setShortcut(key); action.triggered.connect(lambda checked=False,s=slot,k=key:self.shortcut(k,s)); self.addAction(action)

    @staticmethod
    def button(layout,text,slot):
        b=QPushButton(text); b.clicked.connect(slot); layout.addWidget(b); return b

    def change_notation_view(self, index):
        if index == 1:
            self.score_panel.show_score()
        elif index == 2:
            self.score_panel.load_source()

    @staticmethod
    def table(labels):
        table=QTableWidget(0,len(labels)); table.setHorizontalHeaderLabels(labels); table.verticalHeader().hide(); table.verticalHeader().setDefaultSectionSize(38)
        table.setSelectionBehavior(QAbstractItemView.SelectRows); table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers); table.setAlternatingRowColors(True); table.setWordWrap(False)
        for col in range(len(labels)-1): table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(len(labels)-1,QHeaderView.Stretch); return table

    def shortcut(self,key,slot):
        if self.busy or not self.doc: return
        if key in ('M','Space','Ctrl+Z','Ctrl+Y') and isinstance(QApplication.focusWidget(),(QLineEdit,QAbstractSpinBox,QTextEdit)): return
        slot()

    def set_busy(self,value,message=''):
        self.busy=value; self.progress.setVisible(value); self.content.setEnabled(not value and self.doc is not None)
        for b in (self.load_button,self.open_button,self.import_button): b.setEnabled(not value)
        for b in (self.save_button,self.save_as_button): b.setEnabled(not value and self.doc is not None)
        if hasattr(self, 'export_midi_button'):
            from ..music.sheetsage_midi import available
            self.export_midi_button.setEnabled(not value and self.doc is not None and available(self.doc.data.get('music_analysis', {}).get('sheet_sage')))
        if message: self.status.setText(message)

    def run_task(self,operation,ready,message):
        self.set_busy(True,message); job=Task(operation,self); self.jobs.append(job)
        job.ready.connect(ready); job.failed.connect(self.task_failed)
        job.finished.connect(lambda:self.finish_task(job)); job.start()

    def finish_task(self,job): self.jobs.remove(job); job.deleteLater()

    def task_failed(self,message):
        self.set_busy(False,'Operation failed: '+message); QMessageBox.warning(self,'Cannot complete operation',message)

    def choose_song(self):
        song,_=QFileDialog.getOpenFileName(self,'Load song','','Audio (*.mp3 *.wav *.flac *.m4a);;All files (*)')
        if not song: return
        dest,_=QFileDialog.getSaveFileName(self,'New project folder',str(ROOT/'projects'/(Path(song).stem+'.comfymax')),'Project folder (*.comfymax)')
        if not dest or not self.prepare_leave(): return
        self.stop(); self.run_task(lambda:separate_song(song,dest),self.loaded,'Separating vocals with Demucs locally. This can take a few minutes. No transcription is required.')

    def choose_open(self):
        path,_=QFileDialog.getOpenFileName(self,'Open project',str(ROOT/'projects'),'Project (project.json *.json)')
        if path: self.load(path)

    def choose_import(self):
        path,_=QFileDialog.getOpenFileName(self,'Import existing Stage 1 analysis',str(ROOT/'runs'),'Analysis (*.json)')
        if not path: return
        dest,_=QFileDialog.getSaveFileName(self,'New project folder',str(ROOT/'projects'/'Song.comfymax'),'Project folder (*.comfymax)')
        if dest: self.load(path,dest)

    def load(self,path,destination=None):
        if self.busy or not self.prepare_leave(): return
        self.stop(); self.run_task(lambda:open_audio(path,destination),self.loaded,'Opening audio and waveform…')

    def loaded(self,result):
        if self.transport: self.transport.close()
        if self.doc: self.doc.close()
        self.doc,arrays,self.peaks=result[:3]
        self.classifier=result[3] if len(result)>3 else VocalActivity(arrays,self.doc.data['timeline']['sample_rate'])
        self.doc.data['marker_editor']=initial_state(self.doc.data,self.classifier)
        self.transport=Transport(arrays,self.doc.data['timeline']['sample_rate']); settings=self.doc.data['settings']
        self.follow_playhead.setChecked(settings.get('follow_playhead',True))
        self.history.clear(); self.selected_marker=0
        self.source.blockSignals(True); self.source.clear(); self.source.addItem('Full Mix','mix')
        if 'vocals' in arrays: self.source.addItem('Vocals','vocals')
        self.source.setCurrentIndex(max(0,self.source.findData(settings.get('source','mix')))); self.source.blockSignals(False)
        self.transport.source=self.source.currentData(); self.transport.parked=max(0,min(self.transport.total,round(self.doc.data['view'].get('position',0)*self.transport.rate)))
        for control,value in ((self.volume,round(settings.get('volume',.7)*100)),(self.before,settings.get('marker_before',1.5)),(self.after,settings.get('marker_after',1.5)),(self.loop,settings.get('loop',False))):
            control.blockSignals(True)
            if control is self.loop: control.setChecked(value)
            else: control.setValue(value)
            control.blockSignals(False)
        self.transport.volume=self.volume.value()/100; self.transport.cursor.loop=self.loop.isChecked()
        for wave in (self.detail,self.overview):
            wave.doc=self.doc; wave.peaks=self.peaks[self.transport.source]; wave.context=None; wave.position=self.transport.parked/self.transport.rate
            wave.set_view(self.doc.data['view'].get('start',0),self.doc.data['view'].get('span',30))
        self.title.setText(self.doc.data['title']); self.setWindowTitle('ComfyMax MusicLab — '+self.doc.data['title'])
        self.refresh_transcript()
        self.sync_navigation(); self.tabs.setCurrentIndex(0); self.refresh(); self.refresh_music(); self.set_busy(False)
        self.autosave.stop(); self.dirty=False
        self.status.setText('Recovered saved project. Save to retain it.' if self.doc.recovered else 'Ready. Markers alone define scene boundaries; press Create Scenes when ready.')

    @property
    def state(self): return self.doc.data['marker_editor']

    def refresh(self):
        bounds=boundaries(self.state,self.transport.total); rows=intervals(bounds,self.transport.rate)
        self.marker_table.blockSignals(True); self.marker_table.setRowCount(len(bounds))
        for i,frame in enumerate(bounds):
            label='Start (fixed)' if i==0 else 'End (fixed)' if i==len(bounds)-1 else f'Marker {i}'
            row=rows[i] if i<len(rows) else None
            record=self.state['interval_types'][i] if row else None
            values=[label,exact_time(frame/self.transport.rate),f"{row['duration']:.6f} s" if row else '—',record['type'] if record else '—','OVER 15 SECONDS' if row and row['over_limit'] else '']
            for col,value in enumerate(values):
                item=QTableWidgetItem(value); item.setToolTip(f'Sample {frame} • {frame/self.transport.rate:.9f} seconds' if col==1 else value)
                if row and row['over_limit']: item.setBackground(QColor('#54282d'))
                self.marker_table.setItem(i,col,item)
            if record:
                combo=QComboBox(); combo.addItems(TYPES); combo.setCurrentText(record['type'])
                combo.setAccessibleName(f'Type of interval {i+1} after {label}')
                combo.setToolTip('Type belongs to the following interval. '+('Your manual choice.' if record['source']=='manual' else 'Suggested from vocal-stem activity; change freely.' if record['source']=='suggested' else 'Default Vocal: no vocal stem was available. Change freely.'))
                combo.textActivated.connect(lambda value,index=i:self.change_interval_type(index,value))
                self.marker_table.setCellWidget(i,3,combo)
            else: self.marker_table.removeCellWidget(i,3)
        if self.selected_marker in bounds: self.marker_table.selectRow(bounds.index(self.selected_marker))
        self.marker_table.blockSignals(False)
        movable=self.selected_marker in self.state['markers']
        self.move_button.setEnabled(movable); self.delete_button.setEnabled(movable); self.audition_button.setEnabled(self.selected_marker is not None)
        for wave in (self.detail,self.overview): wave.markers=self.state['markers']; wave.selected_marker=self.selected_marker; wave.update()
        self.marker_summary.setText(f'{len(self.state["markers"])} editable markers + fixed start/end • {sum(r["over_limit"] for r in rows)} intervals OVER 15 SECONDS. Type applies to the following interval; suggestions can be overridden. Press Create Scenes to copy current intervals and types.')
        scenes=created_scenes(self.state,self.transport.rate); selected=self.scene_table.currentRow()
        self.scene_table.setRowCount(len(scenes))
        for i,r in enumerate(scenes):
            for col,value in enumerate((str(r['scene']),exact_time(r['start']),exact_time(r['end']),f"{r['duration']:.6f} s",r['type'],'OVER 15 SECONDS' if r['over_limit'] else '')):
                item=QTableWidgetItem(value)
                if r['over_limit']: item.setBackground(QColor('#54282d'))
                self.scene_table.setItem(i,col,item)
        if scenes: self.scene_table.selectRow(max(0,min(selected,len(scenes)-1)))
        self.scene_play.setEnabled(bool(scenes) and scenes_current(self.state,self.transport.total))
        self.export_button.setEnabled(bool(scenes))
        self.scene_status.setText('No scenes created. Place markers, then press Create Scenes.' if not scenes else
                                 f'{len(scenes)} scenes created from current markers. Export: Demucs vocals for every scene.' if scenes_current(self.state,self.transport.total) else
                                 'Markers have changed or interval types have changed. These are the previous scenes; press Create Scenes again to replace them. Playback is disabled until then.')

    def choose_export(self):
        if not self.doc or self.busy: return
        try: validate_scenes(self.doc.data)
        except ExportValidationError as exc:
            scene=next((number for number,message in exc.issues if number),None)
            if scene and scene<=self.scene_table.rowCount():
                self.tabs.setCurrentIndex(1); self.scene_table.selectRow(scene-1)
                self.scene_table.scrollToItem(self.scene_table.item(scene-1,0))
            box=QMessageBox(self); box.setWindowTitle('Scenes need attention before export')
            box.setIcon(QMessageBox.Warning); box.setText(str(exc) if len(str(exc))<1500 else str(exc)[:1500]+'\nSee details for all issues.')
            box.setDetailedText(str(exc)); box.exec(); return
        parent=QFileDialog.getExistingDirectory(self,'Choose output folder — a new project export folder will be created',str(self.doc.root.parent))
        if not parent: return
        if not self.save(): return
        self.stop(); self.set_busy(True,'Validating scenes and aligned audio before export…')
        job=ExportTask(self.doc.root,self.doc.data,parent,self); self.jobs.append(job)
        job.progress.connect(self.status.setText); job.ready.connect(self.export_finished); job.failed.connect(self.export_failed)
        job.finished.connect(lambda:self.finish_task(job)); job.start()

    def export_finished(self,result):
        self.last_export=result
        message=f"Exported and verified {result['scene_count']} scenes.\n{result['folder']}"
        self.set_busy(False,message)
        QMessageBox.information(self,'Export complete',message+'\n\nscenes.json and all WAV files are ready. All scenes use Demucs vocals; types remain metadata.')

    def export_failed(self,message):
        self.set_busy(False,'Export failed: '+message)
        QMessageBox.warning(self,'Export failed — no completed export created',message)

    def change_interval_type(self,index,value):
        self.edit(lambda:set_interval_type(self.state,index,value,self.transport.total),'change interval type')

    def apply_state(self,state):
        validate_marker_state(state,self.transport.total)
        self.doc.data['marker_editor']=copy.deepcopy(state)
        if self.selected_marker not in boundaries(state,self.transport.total): self.selected_marker=None
        self.refresh(); self.changed()

    def commit(self,state,label):
        if state!=self.state: self.history.push(StateCommand(self,self.state,state,label))

    def edit(self,operation,label):
        try: self.commit(operation(),label)
        except ValueError as exc: QMessageBox.warning(self,'Marker',str(exc))

    def add_at_playhead(self):
        if not self.doc: return
        frame=self.transport.position(); self.edit(lambda:add_marker(self.state,frame,self.transport.total,self.classifier),'place marker')
        if frame in self.state['markers']: self.select_marker(frame)

    def add_automatic(self):
        if not self.doc or self.busy: return
        timeline=self.doc.data['timeline']
        self.edit(lambda:add_automatic_markers(self.state,timeline['frames'],timeline['sample_rate'],
                  self.automatic_interval.value(),self.clear_existing_markers.isChecked()),'add automatic markers')

    def select_marker(self,frame):
        self.selected_marker=frame; self.marker_time.setValue(frame/self.transport.rate); self.refresh()

    def marker_selection_changed(self):
        row=self.marker_table.currentRow()
        if self.doc and row>=0: self.select_marker(boundaries(self.state,self.transport.total)[row])

    def drag_marker(self,old,new):
        if self.busy: return
        self.edit(lambda:move_marker(self.state,old,new,self.transport.total,self.classifier),'move marker')
        if new in self.state['markers']: self.select_marker(new)

    def move_selected(self):
        if self.selected_marker is not None: self.drag_marker(self.selected_marker,round(self.marker_time.value()*self.transport.rate))

    def delete_selected(self):
        self.edit(lambda:delete_marker(self.state,self.selected_marker,self.transport.total,self.classifier),'delete marker')

    def create_scene_snapshot(self):
        if not self.doc: return
        self.commit(create_scenes(self.state,self.transport.total),'create scenes'); self.tabs.setCurrentIndex(1)

    def audition_marker(self):
        if self.selected_marker is None: return
        a=max(0,self.selected_marker-round(self.before.value()*self.transport.rate)); b=min(self.transport.total,self.selected_marker+round(self.after.value()*self.transport.rate))
        self.play_range(a,b)

    def play_range(self,a,b):
        if b<=a: self.status.setText('Choose a nonzero audition range.'); return
        for wave in (self.detail,self.overview): wave.context=(a/self.transport.rate,b/self.transport.rate)
        self.audio_action(lambda:self.transport.play(a,b,loop=self.loop.isChecked()))

    def play_scene(self):
        if not scenes_current(self.state,self.transport.total): return
        rows=created_scenes(self.state,self.transport.rate); i=self.scene_table.currentRow()
        if 0<=i<len(rows): self.play_range(rows[i]['start_frame'],rows[i]['end_frame'])

    def show_transcript(self,visible):
        index=self.tabs.indexOf(self.transcript_pane)
        if visible and index<0: self.tabs.addTab(self.transcript_pane,'Transcript aid')
        elif not visible and index>=0: self.tabs.removeTab(index)

    def play_transcript(self,row,column=0):
        if column==2 or not self.transcript_draft: return
        p=self.transcript_draft['lyrics']['segments'][row]
        if playable(p,self.doc.duration): self.play_range(round(p['start']*self.transport.rate),round(p['end']*self.transport.rate))

    def audio_action(self,operation):
        try: operation()
        except Exception as exc: self.status.setText('Audio error: '+str(exc)); QMessageBox.warning(self,'Audio playback',str(exc))

    def toggle(self):
        if self.transport: self.audio_action(self.transport.pause if self.transport.active else self.transport.resume)
    def stop(self):
        if self.transport: self.transport.halt()
    def seek(self,seconds):
        if self.transport: self.audio_action(lambda:self.transport.seek(round(seconds*self.transport.rate)))
    def set_view(self,start,span,automatic=False):
        if self.doc:
            if not automatic: self.follow_playhead.setChecked(False)
            for wave in (self.detail,self.overview): wave.set_view(start,span)
            self.sync_navigation()
    def sync_navigation(self):
        # Millisecond navigation resolution is independent of exact sample boundaries.
        extent=max(0,self.doc.duration-self.detail.span)
        self.navigation.blockSignals(True)
        self.navigation.setRange(0,round(extent*1000))
        self.navigation.setPageStep(max(1,round(self.detail.span*1000)))
        self.navigation.setSingleStep(max(1,round(self.detail.span*10)))
        self.navigation.setValue(round(self.detail.start*1000))
        self.navigation.setEnabled(self.navigation.maximum()>0)
        self.navigation.blockSignals(False)
    def scroll_waveform(self,value):
        if self.doc:
            extent=max(0,self.doc.duration-self.detail.span)
            start=extent if value==self.navigation.maximum() else value/1000
            self.set_view(start,self.detail.span)
    def switch_source(self):
        if self.transport:
            self.audio_action(lambda:self.transport.switch(self.source.currentData()))
            for wave in (self.detail,self.overview): wave.peaks=self.peaks[self.transport.source]; wave.update()
            self.changed()
    def loop_changed(self,value):
        if self.transport: self.audio_action(lambda:self.transport.set_loop(value)); self.changed()
    def volume_changed(self,value):
        if self.transport: self.transport.volume=value/100; self.changed()
    def tick(self):
        if not self.transport or (self.busy and not self.music_job and not self.transcript_job): return
        try: frame=self.transport.poll()
        except Exception as exc: self.status.setText('Audio device unavailable: '+str(exc)); return
        self.clock.setText(f'{exact_time(frame/self.transport.rate)} / {exact_time(self.doc.duration)}')
        self.play_button.setText('Pause' if self.transport.active else 'Play')
        if self.transport.active and self.follow_playhead.isChecked() and self.detail.marker_drag is None:
            start=follow_start(frame/self.transport.rate,self.detail.start,self.detail.span,self.doc.duration)
            if start!=self.detail.start: self.set_view(start,self.detail.span,automatic=True)
        for wave in (self.detail,self.overview): wave.position=frame/self.transport.rate; wave.update()
        if self.transport.warning: self.status.setText('Audio device: '+self.transport.warning); self.transport.warning=''

    def changed(self,*_):
        if self.doc and not self.busy: self.dirty=True; self.status.setText('Unsaved changes'); self.autosave.start()
    def collect(self):
        self.doc.data['settings'].update(source=self.transport.source,loop=self.loop.isChecked(),volume=self.volume.value()/100,
                                         marker_before=self.before.value(),marker_after=self.after.value(),follow_playhead=self.follow_playhead.isChecked())
        self.doc.data['view'].update(position=self.transport.position()/self.transport.rate,start=self.detail.start,span=self.detail.span)
    def save(self,silent=False):
        if not self.doc or self.busy: return False
        self.autosave.stop(); self.collect()
        try:
            self.doc.save(); self.dirty=False; self.history.setClean(); self.status.setText('Saved • '+str(self.doc.root)); return True
        except Exception as exc:
            self.dirty=True; self.status.setText('Save failed. Edits remain in memory; retry Save or use Save As. '+str(exc))
            if not silent: QMessageBox.warning(self,'Save failed',self.status.text())
            return False
    def choose_save_as(self):
        if not self.doc: return
        if not self.resolve_lyrics_draft(): return
        dest,_=QFileDialog.getSaveFileName(self,'Save as new project folder',str(self.doc.root.parent/'Song-copy.comfymax'),'Project folder (*.comfymax)')
        if not dest: return
        self.autosave.stop(); self.collect(); self.stop(); self.run_task(lambda:self.doc.save_as(dest),self.saved_as,'Saving project copy…')
    def saved_as(self,doc):
        self.doc.close(); self.doc=doc
        for wave in (self.detail,self.overview): wave.doc=doc
        self.score_panel.set_project(doc)
        self.change_notation_view(self.views.currentIndex())
        self.dirty=False; self.history.setClean(); self.set_busy(False,'Saved • '+str(doc.root))
    def prepare_leave(self):
        if not self.doc: return True
        if not self.resolve_lyrics_draft(): return False
        self.autosave.stop()
        if self.dirty or self.doc.recovered:
            answer=QMessageBox.question(self,'Unsaved changes','Save this project before leaving?',QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Save)
            if answer==QMessageBox.Cancel:
                if self.dirty: self.autosave.start()
                return False
            if answer==QMessageBox.Discard:
                try: self.doc.discard_pending(); return True
                except OSError as exc: QMessageBox.warning(self,'Cannot discard recovery',str(exc)); return False
        return self.save(True)
    def closeEvent(self,event):
        if self.busy: QMessageBox.information(self,'Working','Please wait for audio preparation or saving to finish.'); event.ignore(); return
        if not self.prepare_leave(): event.ignore(); return
        self.score_panel.close_renderer()
        if self.transport: self.transport.close()
        if self.doc: self.doc.close()
        event.accept()


def main():
    from .audio import configure_playback_logging
    configure_playback_logging(ROOT / '.cache' / 'playback.log')
    parser=argparse.ArgumentParser(description='ComfyMax — manual waveform markers')
    parser.add_argument('project',nargs='?',type=Path); parser.add_argument('--import-analysis',type=Path); parser.add_argument('--destination',type=Path)
    args=parser.parse_args()
    if bool(args.import_analysis)!=bool(args.destination): parser.error('--import-analysis requires --destination')
    app=QApplication(sys.argv[:1]); app.setStyle('Fusion'); app.setFont(QFont('Segoe UI',10))
    window=MarkerEditor(); window.show()
    if args.import_analysis: QTimer.singleShot(0,lambda:window.load(args.import_analysis,args.destination))
    elif args.project: QTimer.singleShot(0,lambda:window.load(args.project))
    return app.exec()


if __name__=='__main__':
    raise SystemExit(main())
