"""Phase 1 evaluation UI. No waveform edits or scene dependencies."""
import copy
import time
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog, QMessageBox, QSpinBox, QComboBox, QCheckBox, QDialog, QPlainTextEdit
from .project import atomic_json
from ..music.model import label, replacement, validate
from ..music.pipeline import analyze, Cancelled
from ..music.temporal import apply_structure
from ..music import sheetsage


class MusicTask(QThread):
    ready = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    progress = Signal(int, str)

    def __init__(self, wave, rate, checksum, parent):
        super().__init__(parent)
        # Playback arrays are immutable; avoid copying a whole song on the UI thread.
        self.wave, self.rate, self.checksum = wave, rate, checksum
        self.previous = None
        self.sheet_audio = None
        self.sheet_root = None

    def check_cancel(self):
        if self.isInterruptionRequested():
            raise Cancelled('Music analysis cancelled. Previous results retained.')

    def run(self):
        try:
            started = time.monotonic()
            if self.sheet_root is not None and self.previous:
                result = copy.deepcopy(self.previous)
            else:
                result = analyze(self.wave, self.rate, self.checksum,
                             progress=self.progress.emit, cancelled=self.isInterruptionRequested)
                if self.previous and 'sheet_sage' in self.previous:
                    result['sheet_sage'] = copy.deepcopy(self.previous['sheet_sage'])
            if self.sheet_root is not None:
                self.progress.emit(95, 'SheetSage2: independent symbolic transcription…')
                evidence = sheetsage.analyze(self.sheet_audio, self.sheet_root, self.check_cancel)
                if 'sheet_sage' in result:
                    previous = result['sheet_sage']
                    evidence['previous_results'] = previous.get('previous_results', []) + [
                        {k:v for k,v in previous.items() if k != 'previous_results'}]
                evidence['provenance']['total_analyze_music_seconds'] = time.monotonic()-started
                result['sheet_sage'] = evidence
            if self.isInterruptionRequested():
                raise Cancelled('Music analysis cancelled. Previous results retained.')
            self.ready.emit(result)
        except Cancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))


class MusicPanel:
    def build_music(self, header):
        self.music_job = None
        self.music_pane = QWidget(self); self.music_pane.hide(); layout = QVBoxLayout(self.music_pane)
        buttons = QHBoxLayout()
        self.analyze_music_button = QPushButton('Analyze Music'); header.addWidget(self.analyze_music_button)
        self.include_sheetsage = QCheckBox('SheetSage2'); header.addWidget(self.include_sheetsage)
        available, message = sheetsage.availability()
        self.include_sheetsage.setToolTip(message+'\nOptional independent evidence. With an existing analysis, only SheetSage runs; rhythm/chords/manual corrections are retained.')
        self.sheet_status = QLabel('SheetSage2: '+message); self.sheet_status.setWordWrap(True)
        layout.addWidget(self.sheet_status)
        self.view_sheet_abc = QPushButton('View SheetSage ABC'); self.view_sheet_abc.setEnabled(False)
        self.view_sheet_abc.clicked.connect(self.show_sheet_abc); buttons.addWidget(self.view_sheet_abc)
        self.cancel_music_button = QPushButton('Cancel analysis'); self.cancel_music_button.setEnabled(False)
        buttons.addWidget(self.cancel_music_button)
        self.export_music_button = QPushButton('Export music_analysis.json'); buttons.addWidget(self.export_music_button)
        buttons.addStretch(); layout.addLayout(buttons)
        self.music_summary = QLabel('No music analysis. Full MIX will be analyzed.'); self.music_summary.setWordWrap(True)
        layout.addWidget(self.music_summary)
        controls=QHBoxLayout()
        self.music_downbeat=QSpinBox(); self.music_downbeat.setRange(0,0)
        self.music_downbeat.setSpecialValueText('Unknown')
        self.music_meter_numerator=QSpinBox(); self.music_meter_numerator.setRange(1,32); self.music_meter_numerator.setValue(4)
        self.music_meter_denominator=QComboBox(); self.music_meter_denominator.addItems(['1','2','4','8','16','32']); self.music_meter_denominator.setCurrentText('4')
        self.music_structure_button=QPushButton('Apply meter / first downbeat')
        self.music_selected_downbeat_button=QPushButton('Set selected beat as first downbeat')
        for widget in (QLabel('First downbeat index:'),self.music_downbeat,QLabel('Meter:'),
                       self.music_meter_numerator,self.music_meter_denominator,
                       self.music_structure_button,self.music_selected_downbeat_button): controls.addWidget(widget)
        layout.addLayout(controls)
        self.music_structure_button.clicked.connect(self.apply_music_structure)
        self.music_selected_downbeat_button.clicked.connect(self.set_selected_downbeat)
        self.music_structure_button.setEnabled(False); self.music_selected_downbeat_button.setEnabled(False)
        self.music_table = self.table(['Beat', 'Time interval', 'Raw', 'Score', 'Initial smoothed', 'Final stabilized',
                                      'Extension support', 'Unknown recovery', 'Top candidates'])
        self.music_table.setToolTip('Scores are uncalibrated similarities. Double-click a row to listen.')
        layout.addWidget(self.music_table)
        self.music_note = QLabel('4/4 is a default; first downbeat is unknown. No automatic measure detection.'); self.music_note.setWordWrap(True)
        layout.addWidget(self.music_note)
        self.analyze_music_button.clicked.connect(self.analyze_music)
        self.cancel_music_button.clicked.connect(self.cancel_music)
        self.export_music_button.clicked.connect(self.export_music)
        self.music_table.cellDoubleClicked.connect(self.listen_music)
        self.export_music_button.setEnabled(False)

    def analyze_music(self):
        if not self.doc or self.busy or self.music_job:
            return
        previous = self.doc.data.get('music_analysis')
        try:
            if not (self.include_sheetsage.isChecked() and previous):
                replacement(previous, {})  # Reject destructive replacement of future overrides up front.
        except ValueError as exc:
            QMessageBox.warning(self, 'Music analysis', str(exc)); return
        self.autosave.stop()
        job = MusicTask(self.transport.arrays['mix'], self.transport.rate,
                        self.doc.data['assets']['mix'].get('sha256'), self)
        job.previous = copy.deepcopy(previous)
        if self.include_sheetsage.isChecked():
            job.sheet_root = self.doc.root
            job.sheet_audio = self.doc.root / self.doc.data['assets']['mix']['path']
        self.include_sheetsage.setEnabled(False)
        self.music_job = job; self.jobs.append(job)
        self.music_project_id = self.doc.data['project_id']
        # Keep playback and scrolling responsive; block project mutation/switching only.
        self.busy = True
        self.music_structure_button.setEnabled(False); self.music_selected_downbeat_button.setEnabled(False)
        if self.tabs.indexOf(self.music_pane) < 0: self.tabs.addTab(self.music_pane,'Music analysis')
        for button in (self.load_button,self.open_button,self.import_button,self.save_button,self.save_as_button):
            button.setEnabled(False)
        for i in range(self.tabs.count()):
            self.tabs.setTabEnabled(i, self.tabs.widget(i) is self.music_pane)
        self.tabs.setCurrentWidget(self.music_pane)
        self.analyze_music_button.setEnabled(False); self.export_music_button.setEnabled(False)
        self.cancel_music_button.setEnabled(True); self.progress.setRange(0,100); self.progress.setValue(0); self.progress.show()
        job.progress.connect(self.music_progress)
        job.ready.connect(self.music_ready)
        job.failed.connect(self.music_failed)
        job.cancelled.connect(self.music_cancelled)
        job.finished.connect(lambda:self.music_finished(job))
        job.start()

    def music_progress(self, value, message):
        self.progress.setValue(value); self.status.setText(message)

    def cancel_music(self):
        if self.music_job:
            self.music_job.requestInterruption(); self.cancel_music_button.setEnabled(False)
            self.status.setText('Cancelling after the current analysis stage…')

    def music_ready(self, result):
        try:
            if self.music_job.isInterruptionRequested():
                self.music_cancelled('Music analysis cancelled. Previous results retained.'); return
            if self.doc.data['project_id'] != self.music_project_id:
                raise ValueError('Project changed during analysis; result was not applied.')
            validate(result,self.doc.data['timeline'])
            if self.music_job.sheet_root is not None and self.music_job.previous:
                value = copy.deepcopy(self.doc.data['music_analysis'])
                value['sheet_sage'] = result['sheet_sage']
            else:
                value = replacement(self.doc.data.get('music_analysis'), result)
            if 'sheet_sage' in value:
                sheetsage.validate(value['sheet_sage'])
            self.doc.data['music_analysis'] = value
            self.dirty = True
            self.refresh_music()
            self.status.setText('Analysis complete. Saving results…')
        except Exception as exc:
            self.music_failed(str(exc))

    def music_failed(self, message):
        self.status.setText('Music analysis failed; previous results retained: '+message)
        QMessageBox.warning(self,'Music analysis failed',message)

    def music_cancelled(self, message):
        self.status.setText(message)

    def music_finished(self, job):
        self.music_job = None; self.busy = False
        self.include_sheetsage.setEnabled(True)
        for button in (self.load_button,self.open_button,self.import_button,self.save_button,self.save_as_button):
            button.setEnabled(True)
        for i in range(self.tabs.count()): self.tabs.setTabEnabled(i,True)
        self.analyze_music_button.setEnabled(True); self.cancel_music_button.setEnabled(False)
        self.export_music_button.setEnabled('music_analysis' in self.doc.data)
        self.music_structure_button.setEnabled('music_analysis' in self.doc.data)
        self.music_selected_downbeat_button.setEnabled('music_analysis' in self.doc.data)
        self.progress.hide(); self.progress.setRange(0,0)
        self.finish_task(job)
        # Also restore a pre-existing pending autosave on cancellation/failure.
        if self.dirty: self.autosave.start()

    def show_sheet_abc(self):
        from .project import inside
        evidence = self.doc.data.get('music_analysis', {}).get('sheet_sage', {})
        artifact = next((a for a in evidence.get('raw_artifacts', []) if a['path'].endswith('/score.abc')), None)
        if not artifact:
            return
        try:
            path = inside(self.doc.root, artifact['path'])
            text = path.read_text(encoding='utf-8-sig')
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'SheetSage ABC', str(exc)); return
        dialog = QDialog(self); dialog.setWindowTitle('Original SheetSage ABC — read only'); dialog.resize(900,650)
        layout = QVBoxLayout(dialog); location = QLabel(str(path)); location.setWordWrap(True); layout.addWidget(location)
        editor = QPlainTextEdit(); editor.setReadOnly(True); editor.setPlainText(text); layout.addWidget(editor)
        dialog.exec()

    def set_chord_source(self, source):
        for wave in (self.overview, self.detail):
            wave.music_overlay.set_source(source)
            wave.update()

    def refresh_music(self):
        from PySide6.QtWidgets import QTableWidgetItem
        data = self.doc.data.get('music_analysis') if self.doc else None
        available, message = sheetsage.availability()
        evidence = (data or {}).get('sheet_sage')
        status = evidence['status'] if evidence else 'not analyzed'
        warnings = '; '.join(evidence.get('warnings', [])) if evidence else ''
        self.sheet_status.setText(f'SheetSage2: {message} | Evidence: {status}' + (' | '+warnings if warnings else ''))
        self.view_sheet_abc.setEnabled(bool(evidence and any(a['path'].endswith('/score.abc') for a in evidence['raw_artifacts'])))
        self.chord_source.setEnabled(bool(data))
        for wave in (self.overview, self.detail):
            wave.music_overlay.invalidate()
            wave.update()
        self.music_table.setRowCount(0); self.export_music_button.setEnabled(bool(data))
        self.music_structure_button.setEnabled(bool(data) and not self.busy)
        self.music_selected_downbeat_button.setEnabled(bool(data) and not self.busy)
        if not data:
            index=self.tabs.indexOf(self.music_pane)
            if index>=0: self.tabs.removeTab(index)
            self.music_summary.setText('No music analysis. Full MIX will be analyzed.'); return
        if self.tabs.indexOf(self.music_pane)<0: self.tabs.addTab(self.music_pane,'Music analysis')
        self.music_downbeat.setMaximum(len(data['beats']))
        down=next((b['index'] for b in data['beats'] if b['id']==data['first_downbeat']['beat_id']),0)
        self.music_downbeat.setValue(down)
        self.music_meter_numerator.setValue(data['meter']['numerator'])
        self.music_meter_denominator.setCurrentText(str(data['meter']['denominator']))
        tempo = data['tempo']
        def bpm_text(field):
            value = tempo.get(field, tempo.get('bpm') if field not in ('grid_bpm','span_bpm') else None)
            return f'{value:.2f}' if value is not None else 'unknown'
        self.music_summary.setText(f"Librosa BPM: {bpm_text('librosa_bpm')} | Grid BPM: {bpm_text('grid_bpm')} | "
                                   f"Span BPM: {bpm_text('span_bpm')} | Effective BPM: {bpm_text('effective_bpm')} | Key: {data['key']['estimated']} (score {data['key']['score']:.3f}) | "
                                   f"Beats: {len(data['beats'])} | Chord regions: {len(data['regions'])} | "
                                   f"Meter: {data['meter']['numerator']}/{data['meter']['denominator']} ({data['meter']['source']}) | "
                                   f"First downbeat: {down or 'unknown'} ({data['first_downbeat']['source']}) | Bars: {len(data.get('bars',[]))}")
        notes=[w for w in data['warnings'] if not w.startswith('Meter 4/4 is a default.')]
        bar_status=data.get('bar_context_disabled_reason') or ('available; context does not force one chord per bar.' if data.get('bars') else 'not computed; apply structural settings when a downbeat is known.')
        notes.append('Bar context: '+bar_status)
        self.music_note.setText('\n'.join(notes))
        # A neural boundary may split one template beat: show every final label.
        by_raw = {p['id']: ' → '.join(dict.fromkeys(label(r) for r in data['regions']
                  if p['id'] in r['raw_ids'])) for p in data['raw_predictions']}
        self.music_table.setRowCount(len(data['raw_predictions']))
        for i,row in enumerate(data['raw_predictions']):
            candidates = ' | '.join(f"{c['label']} {c['score']:.3f}" for c in row['candidates'])
            evidence=row.get('temporal_extension_evidence',[])
            support=' | '.join(f"{e['seventh_label']}: {e['supporting_beats']}/{e['total_beats']} ({e['supporting_fraction']:.0%}), {'keep' if e['accepted'] else 'reject'}" for e in evidence)
            recovery=row.get('unknown_resolution')
            values = [str(row['beat_index']) if row['beat_index'] is not None else row['interval_kind'],
                      f"{row['start_seconds']:.3f} – {row['end_seconds']:.3f}",row['best_label'],f"{row['score']:.3f}",label(row['initial_smoothed']) if 'initial_smoothed' in row else by_raw[row['id']],
                      by_raw[row['id']],support,recovery['resolved_to'] if recovery else '',candidates]
            for col,value in enumerate(values):
                item=QTableWidgetItem(value); item.setToolTip(value); self.music_table.setItem(i,col,item)

    def set_selected_downbeat(self):
        if self.busy or not self.doc: return
        data=self.doc.data.get('music_analysis'); row=self.music_table.currentRow()
        if data and 0<=row<len(data['raw_predictions']):
            beat=data['raw_predictions'][row]['beat_index']
            if beat is not None:
                self.music_downbeat.setValue(beat); self.apply_music_structure()

    def apply_music_structure(self):
        if self.busy or not self.doc or 'music_analysis' not in self.doc.data: return
        data=self.doc.data['music_analysis']; index=self.music_downbeat.value()
        try:
            meter=dict(numerator=self.music_meter_numerator.value(),
                       denominator=int(self.music_meter_denominator.currentText()),source='manual')
            down=dict(beat_id=data['beats'][index-1]['id'] if index else None,source='manual')
            value=apply_structure(data,meter,down)
            self.doc.data['music_analysis']=value
            self.refresh_music(); self.changed()
            self.status.setText('Meter/downbeat and structural smoothing updated; audio features reused.')
        except Exception as exc:
            QMessageBox.warning(self,'Music structure',str(exc))

    def listen_music(self, row, column=0):
        if self.busy: return
        data = self.doc.data.get('music_analysis')
        if data and 0 <= row < len(data['raw_predictions']):
            item=data['raw_predictions'][row]; self.play_range(item['start_frame'],item['end_frame'])

    def export_music(self):
        if self.busy or not self.doc or 'music_analysis' not in self.doc.data: return
        path,_=QFileDialog.getSaveFileName(self,'Export music analysis',str(self.doc.root/'music_analysis.json'),'JSON (*.json)')
        if not path: return
        try:
            from pathlib import Path
            target=Path(path).resolve()
            protected=[self.doc.root/'project.json',self.doc.root/'source'/'analysis.json']
            protected += [self.doc.root/a['path'] for a in self.doc.data['assets'].values()]
            if self.doc.data.get('source',{}).get('path'): protected.append(Path(self.doc.data['source']['path']))
            if target in [p.resolve() for p in protected] or target.is_relative_to((self.doc.root/'recovery').resolve()):
                raise ValueError('Choose a separate debug JSON file; project/audio/recovery files are protected.')
            value=copy.deepcopy(self.doc.data['music_analysis']); validate(value,self.doc.data['timeline'])
            atomic_json(target,value); self.status.setText('Exported '+str(target))
        except Exception as exc:
            QMessageBox.warning(self,'Music export failed',str(exc))
