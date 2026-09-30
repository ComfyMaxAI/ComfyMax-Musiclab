"""Editable, source-preserving piano roll for the ABC Vocal voice."""
from fractions import Fraction
from PySide6.QtCore import Qt, QRectF, QElapsedTimer, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen, QKeySequence
from PySide6.QtWidgets import (QWidget,QGraphicsItem,QGraphicsRectItem,QGraphicsScene,QGraphicsView,QHBoxLayout,
    QLabel,QPushButton,QVBoxLayout,QComboBox,QCheckBox,QSpinBox,QLineEdit,QFormLayout,QGroupBox,QSplitter)
from .vocal_note_parser import abc_length_text,abc_pitch_text,parse_vocal_notes,rest_token,VocalABCError
from .vocal_note_playback import PreviewSynth,playback_events,seconds_per_whole_note

BLACK={1,3,6,8,10}

class PianoKeyboard(QWidget):
    def __init__(self,editor):super().__init__(editor);self.editor=editor;self.setFixedWidth(58)
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#f5f6f8'))
        if not self.editor.part:return
        scroll=self.editor.view.verticalScrollBar().value();h=self.editor.pitch_height
        for pitch in range(self.editor.high_pitch,self.editor.low_pitch-1,-1):
            y=(self.editor.high_pitch-pitch)*h-scroll;black=pitch%12 in BLACK
            p.fillRect(QRectF(0,y,36 if black else self.width(),h),QColor('#252a32') if black else QColor('#fafafa'))
            p.setPen(QColor('#9aa2ad'));p.drawRect(QRectF(0,y,self.width()-1,h))
            if pitch%12==0:p.setPen(QColor('#222831'));p.drawText(QRectF(37,y,20,h),Qt.AlignCenter,f'C{pitch//12-1}')

class NoteItem(QGraphicsRectItem):
    HANDLE=7.0
    def __init__(self,editor,note,rect):
        super().__init__(rect);self.editor=editor;self.note=note;self._resizing=False
        flags=QGraphicsItem.GraphicsItemFlag
        self.setFlags(flags.ItemIsSelectable|flags.ItemIsMovable|flags.ItemSendsGeometryChanges);self.setCursor(Qt.SizeAllCursor);self.setZValue(3)
    def paint(self,painter,option,widget=None):
        painter.setRenderHint(QPainter.Antialiasing);selected=self.isSelected()
        painter.setPen(QPen(QColor('#ffd180') if selected else QColor('#1f78c1'),1.5));painter.setBrush(QColor('#ff9f43') if selected else QColor('#42a5f5'));painter.drawRoundedRect(self.rect(),3,3)
        if selected:painter.fillRect(QRectF(self.rect().right()-self.HANDLE,self.rect().top()+2,self.HANDLE-2,self.rect().height()-4),QColor('#fff3d6'))
    def mousePressEvent(self,event):
        self._press_pos=self.pos();self._resizing=event.pos().x()>=self.rect().right()-self.HANDLE
        if self._resizing:self.setCursor(Qt.SizeHorCursor)
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self._resizing:
            self.setRect(QRectF(self.rect().left(),self.rect().top(),max(self.editor.grid_pixels(),event.pos().x()-self.rect().left()),self.rect().height()));event.accept();return
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        action=None
        if self._resizing:
            duration=self.editor.snap_fraction(Fraction.from_float(self.rect().width()/self.editor.time_scale));action=('duration',duration)
        elif self.pos()!=self._press_pos:
            d=self.pos()-self._press_pos;delta=self.editor.snap_fraction(Fraction.from_float(d.x()/self.editor.time_scale));pitch=-round(d.y()/self.editor.pitch_height)
            action=('move',self.editor.selected_notes() or [self.note],delta,pitch)
        self.setCursor(Qt.SizeAllCursor);super().mouseReleaseEvent(event)
        # The edit rebuilds the scene and may delete this graphics item, so it
        # must happen only after Qt has finished dispatching the release.
        if action and action[0]=='duration':self.editor.edit_duration(self.note,action[1])
        elif action:self.editor.move_notes(action[1],action[2],action[3])

class VocalNoteEditor(QWidget):
    abcApplied=Signal(str);TIME_SCALE=640.;PITCH_HEIGHT=18.
    def __init__(self,abc_text=None,parent=None):
        super().__init__(parent);self.original_abc=None;self.current_abc=None;self.part=None;self.result_abc=None;self.undo_stack=[];self.redo_stack=[];self._compat_deleted_ranges=set()
        self.time_scale=self.TIME_SCALE;self.pitch_height=self.PITCH_HEIGHT;self.low_pitch=48;self.high_pitch=72
        self.audio=PreviewSynth(self);self.playing=False;self.playhead=None;self.playback_start=Fraction(0);self._building=False
        self.playback_timer=QTimer(self);self.playback_timer.setInterval(16);self.playback_timer.timeout.connect(self._playback_tick);self.playback_clock=QElapsedTimer();self._build_ui()
        self.load_abc(abc_text) if abc_text is not None else self._show_empty()
    def _build_ui(self):
        layout=QVBoxLayout(self);bar=QHBoxLayout();self.instructions=QLabel('V: Vocal piano-roll — drag notes to move; drag the right edge to resize.');bar.addWidget(self.instructions);bar.addStretch();bar.addWidget(QLabel('Grid'))
        self.grid_combo=QComboBox();self.grid_combo.addItems(['1/4','1/8','1/16','1/32']);self.grid_combo.setCurrentText('1/16');bar.addWidget(self.grid_combo)
        self.snap_box=QCheckBox('Snap to grid');self.snap_box.setChecked(True);bar.addWidget(self.snap_box)
        for label,factor in [('H−',.8),('H+',1.25)]:b=QPushButton(label);b.clicked.connect(lambda checked=False,f=factor:self.zoom_horizontal(f));bar.addWidget(b)
        for label,delta in [('V−',-2),('V+',2)]:b=QPushButton(label);b.clicked.connect(lambda checked=False,d=delta:self.zoom_vertical(d));bar.addWidget(b)
        layout.addLayout(bar);self.scene=QGraphicsScene(self);self.view=QGraphicsView(self.scene);self.view.setDragMode(QGraphicsView.RubberBandDrag);self.view.setRenderHint(QPainter.Antialiasing)
        self.keyboard=PianoKeyboard(self);self.view.verticalScrollBar().valueChanged.connect(self.keyboard.update);roll=QWidget();row=QHBoxLayout(roll);row.setContentsMargins(0,0,0,0);row.setSpacing(0);row.addWidget(self.keyboard);row.addWidget(self.view,1)
        self.properties=QGroupBox('Note Properties');form=QFormLayout(self.properties);self.pitch_input=QSpinBox();self.pitch_input.setRange(0,127);form.addRow('Pitch (MIDI)',self.pitch_input);self.start_input=QLineEdit();form.addRow('Start',self.start_input);self.duration_input=QLineEdit();form.addRow('Duration',self.duration_input);self.properties_apply=QPushButton('Apply Properties');self.properties_apply.clicked.connect(self.apply_properties);form.addRow(self.properties_apply);self.properties.setEnabled(False);self.properties.setMaximumWidth(240)
        split=QSplitter();split.addWidget(roll);split.addWidget(self.properties);split.setStretchFactor(0,1);layout.addWidget(split,1);controls=QHBoxLayout()
        self.play_button=self._button(controls,'▶ Play',self.play);self.stop_button=self._button(controls,'■ Stop',self.stop);self.delete_button=self._button(controls,'Delete Note',self.delete_selected);self.undo_button=self._button(controls,'Undo',self.undo);self.redo_button=self._button(controls,'Redo',self.redo);controls.addStretch();self.revert_button=self._button(controls,'Revert Changes',self.revert_changes);self.apply_button=self._button(controls,'Apply to ABC',self.apply);layout.addLayout(controls)
        self.scene.selectionChanged.connect(self._selection_changed)
        for key,callback in [(QKeySequence.Delete,self.delete_selected),(QKeySequence.Undo,self.undo),(QKeySequence.Redo,self.redo)]:a=QAction(self);a.setShortcut(key);a.setShortcutContext(Qt.WidgetWithChildrenShortcut);a.triggered.connect(callback);self.addAction(a)
    @staticmethod
    def _button(layout,text,callback):b=QPushButton(text);b.clicked.connect(callback);layout.addWidget(b);return b
    @property
    def has_changes(self):return self.current_abc is not None and self.current_abc!=self.original_abc
    @property
    def deleted_ranges(self):return self._compat_deleted_ranges
    def grid_fraction(self):return Fraction(self.grid_combo.currentText())
    def grid_pixels(self):return max(3.,float(self.grid_fraction())*self.time_scale)
    def snap_fraction(self,value):
        value=Fraction(value)
        return round(value/self.grid_fraction())*self.grid_fraction() if self.snap_box.isChecked() else value.limit_denominator(4096)
    def _show_error(self,message):self.instructions.setText(message)
    def _show_empty(self):
        self.scene.clear();self.scene.addText('Open Working ABC to edit V: Vocal notes.')
        for b in (self.play_button,self.stop_button,self.delete_button,self.undo_button,self.redo_button,self.revert_button,self.apply_button):b.setEnabled(False)
    def clear(self):self.stop();self.original_abc=None;self.current_abc=None;self.part=None;self.result_abc=None;self.undo_stack.clear();self.redo_stack.clear();self._compat_deleted_ranges.clear();self._show_empty()
    def load_abc(self,text):
        if self.part is not None and text==self.original_abc:return
        self.stop();self.original_abc=text;self.current_abc=text;self.part=parse_vocal_notes(text);self.result_abc=None;self.undo_stack.clear();self.redo_stack.clear();self._compat_deleted_ranges.clear();self._populate()
    def visible_notes(self):return list(self.part.notes) if self.part else []
    def selected_notes(self):return [i.note for i in self.scene.selectedItems() if isinstance(i,NoteItem)]
    def scheduled_events(self,start=None):
        if self.part is None:return Fraction(0),[]
        selected=self.selected_notes();position=min((n.start for n in selected),default=Fraction(0)) if start is None else start
        return position,playback_events(self.part,self._compat_deleted_ranges,position)
    def _replace(self,replacements,selection=()):
        try:after=self.part.source_edit(replacements);parsed=parse_vocal_notes(after)
        except (ValueError,VocalABCError) as exc:self._show_error('Edit blocked: '+str(exc));return False
        self.undo_stack.append(self.current_abc);self.redo_stack.clear();self.current_abc=after;self.part=parsed;self.result_abc=None;self._populate(set(selection));return True
    @staticmethod
    def _safe(note):return not(note.complex_timing or note.tied_from_previous or note.tied_to_next)
    def edit_pitch(self,note,pitch):
        if not self._safe(note):self._show_error('Edit blocked: complex/tied note.');return False
        return self._replace({note.abc_source_range:abc_pitch_text(pitch)+note.duration_text},[note.start])
    def edit_duration(self,note,duration):
        duration=Fraction(duration)
        if duration<=0 or not self._safe(note):self._show_error('Edit blocked: invalid or complex/tied duration.');self._populate({note.start});return False
        pitch=note.abc_token[:-len(note.duration_text)] if note.duration_text else note.abc_token
        return self._replace({note.abc_source_range:pitch+abc_length_text(duration,self.part.unit_length)},[note.start])
    def move_notes(self,notes,time_delta=Fraction(0),pitch_delta=0):
        notes=sorted({n.abc_source_range:n for n in notes}.values(),key=lambda n:n.start)
        if not notes:return False
        if any(not self._safe(n) for n in notes):self._show_error('Move blocked: complex/tied note.');self._populate({n.start for n in notes});return False
        delta=self.snap_fraction(time_delta);replacements={}
        for note in notes:
            if pitch_delta:replacements[note.abc_source_range]=abc_pitch_text(note.pitch+pitch_delta)+note.duration_text
        first,last=notes[0],notes[-1]
        if delta>0:
            rest=next((r for r in self.part.rests if r.start==last.start+last.duration and r.duration>=delta and not r.complex_timing),None)
            if not rest:self._show_error('Move blocked: no sufficient following Vocal rest.');self._populate({n.start for n in notes});return False
            replacements[first.abc_source_range]='z'+abc_length_text(delta,self.part.unit_length)+' '+replacements.get(first.abc_source_range,first.abc_token)
            replacements[rest.abc_source_range]=rest_token(rest,rest.duration-delta,self.part.unit_length) if rest.duration>delta else ''
        elif delta<0:
            amount=-delta;rest=next((r for r in self.part.rests if r.start+r.duration==first.start and r.duration>=amount and not r.complex_timing),None)
            if not rest:self._show_error('Move blocked: no sufficient preceding Vocal rest.');self._populate({n.start for n in notes});return False
            replacements[rest.abc_source_range]=rest_token(rest,rest.duration-amount,self.part.unit_length) if rest.duration>amount else ''
            replacements[last.abc_source_range]=replacements.get(last.abc_source_range,last.abc_token)+' z'+abc_length_text(amount,self.part.unit_length)
        return self._replace(replacements,[n.start+delta for n in notes])
    def delete_selected(self):
        notes=self.selected_notes()
        if not notes:return
        after=self.part.delete_notes(notes);self.undo_stack.append(self.current_abc);self.redo_stack.clear();self.current_abc=after;self.part=parse_vocal_notes(after);self._populate()
    def undo(self):
        if self.undo_stack:self.redo_stack.append(self.current_abc);self.current_abc=self.undo_stack.pop();self.part=parse_vocal_notes(self.current_abc);self._populate()
    def redo(self):
        if self.redo_stack:self.undo_stack.append(self.current_abc);self.current_abc=self.redo_stack.pop();self.part=parse_vocal_notes(self.current_abc);self._populate()
    def apply_properties(self):
        notes=self.selected_notes()
        if len(notes)!=1:return
        note=notes[0]
        try:start=Fraction(self.start_input.text());duration=Fraction(self.duration_input.text())
        except (ValueError,ZeroDivisionError):self._show_error('Properties blocked: use fractions such as 3/8.');return
        pitch=self.pitch_input.value()
        if pitch!=note.pitch:
            if not self.edit_pitch(note,pitch):return
            note=next(n for n in self.part.notes if n.start==note.start)
        if duration!=note.duration:
            if not self.edit_duration(note,duration):return
            note=next(n for n in self.part.notes if n.start==note.start)
        delta=self.snap_fraction(start-note.start)
        if delta:self.move_notes([note],delta,0)
    def _selection_changed(self):
        if self._building:return
        notes=self.selected_notes();self.properties.setEnabled(len(notes)==1)
        if len(notes)==1:n=notes[0];self.pitch_input.setValue(n.pitch);self.start_input.setText(str(n.start));self.duration_input.setText(str(n.duration))
    def zoom_horizontal(self,factor):self.time_scale=max(160,min(2560,self.time_scale*factor));self._populate({n.start for n in self.selected_notes()})
    def zoom_vertical(self,delta):self.pitch_height=max(10,min(40,self.pitch_height+delta));self._populate({n.start for n in self.selected_notes()})
    def _populate(self,selected=None):
        playing=self.playing;self._building=True;self.scene.clear();self.playhead=None
        if self.part is None:self._building=False;self._show_empty();return
        notes=self.part.notes;self.low_pitch=min([n.pitch for n in notes]+[48])-2;self.high_pitch=max([n.pitch for n in notes]+[72])+2;width=max(float(self.part.total_duration)*self.time_scale,self.view.viewport().width());top=24.;height=(self.high_pitch-self.low_pitch+1)*self.pitch_height
        for pitch in range(self.high_pitch,self.low_pitch-1,-1):y=top+(self.high_pitch-pitch)*self.pitch_height;self.scene.addRect(0,y,width,self.pitch_height,QPen(QColor('#394452'),.5),QColor('#202733') if pitch%12 in BLACK else QColor('#29323f')).setZValue(-4)
        meter=self.part.meter if self.part.meter>0 else Fraction(1);position=Fraction(0);sub=self.grid_fraction()
        while position<=self.part.total_duration:
            x=float(position)*self.time_scale;measure=(position/meter).denominator==1;self.scene.addLine(x,top,x,top+height,QPen(QColor('#91a4ba') if measure else QColor('#465567'),2 if measure else .7)).setZValue(-2)
            if measure:self.scene.addText(str(int(position/meter)+1)).setPos(x+3,0)
            position+=sub
        for note in notes:
            item=NoteItem(self,note,QRectF(float(note.start)*self.time_scale,top+(self.high_pitch-note.pitch)*self.pitch_height+2,max(5,float(note.duration)*self.time_scale-2),self.pitch_height-4));self.scene.addItem(item)
            if selected and note.start in selected:item.setSelected(True)
        self.scene.setSceneRect(QRectF(-2,0,width+25,top+height+8));self.keyboard.update();self.play_button.setEnabled(bool(notes));self.stop_button.setEnabled(playing);self.delete_button.setEnabled(bool(notes));self.undo_button.setEnabled(bool(self.undo_stack));self.redo_button.setEnabled(bool(self.redo_stack));self.revert_button.setEnabled(self.has_changes);self.apply_button.setEnabled(True);self._building=False;self._selection_changed()
        if playing:self._playback_tick()
    def play(self):
        if self.part is None:return
        self.stop();self.playback_start,events=self.scheduled_events();remaining=max(Fraction(0),self.part.total_duration-self.playback_start)
        if not remaining:return
        self.audio.play(events,remaining*seconds_per_whole_note(self.part.tempo_bpm));self.playing=True;self.play_button.setEnabled(False);self.stop_button.setEnabled(True);self.playback_clock.start();self.playback_timer.start();self._set_playhead(self.playback_start)
    def stop(self):
        self.playback_timer.stop();self.audio.stop();self.playing=False
        if hasattr(self,'play_button'):self.play_button.setEnabled(self.part is not None);self.stop_button.setEnabled(False)
        if self.playhead is not None and self.playhead.scene() is self.scene:self.scene.removeItem(self.playhead)
        self.playhead=None
    def _set_playhead(self,position):
        if self.playhead is not None and self.playhead.scene() is self.scene:self.scene.removeItem(self.playhead)
        x=float(position)*self.time_scale;r=self.scene.sceneRect();self.playhead=self.scene.addLine(x,r.top(),x,r.bottom(),QPen(QColor('#ff5252'),2));self.playhead.setZValue(20);self.view.ensureVisible(QRectF(x,r.top(),2,r.height()),80,0)
    def _playback_tick(self):
        position=self.playback_start+Fraction(self.playback_clock.elapsed(),1000)/seconds_per_whole_note(self.part.tempo_bpm)
        if position>=self.part.total_duration:self.stop()
        else:self._set_playhead(position)
    def apply(self):
        if self.part is None:return
        self.stop()
        try:self.part=parse_vocal_notes(self.current_abc)
        except VocalABCError as exc:self._show_error('Apply blocked: '+str(exc));return
        text=self.current_abc;self.original_abc=text;self.undo_stack.clear();self.redo_stack.clear();self.result_abc=text;self._populate();self.abcApplied.emit(text)
    def revert_changes(self):
        if self.original_abc is not None:self.stop();self.current_abc=self.original_abc;self.part=parse_vocal_notes(self.original_abc);self.undo_stack.clear();self.redo_stack.clear();self.result_abc=None;self._populate()
    def closeEvent(self,event):self.stop();super().closeEvent(event)
