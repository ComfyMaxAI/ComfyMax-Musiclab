"""MusicLab page for deleting notes from the ABC Vocal voice."""
from fractions import Fraction
from PySide6.QtCore import Qt, QRectF, QElapsedTimer, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QPen, QKeySequence
from PySide6.QtWidgets import (QWidget, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)
from .vocal_note_parser import parse_vocal_notes
from .vocal_note_playback import PreviewSynth, playback_events, seconds_per_whole_note


class NoteItem(QGraphicsRectItem):
    def __init__(self, note, rect):
        super().__init__(rect)
        self.note = note
        self.setFlag(QGraphicsRectItem.ItemIsSelectable, True)
        self.setPen(QPen(QColor('#315f7d')))
        self.setBrush(QBrush(QColor('#62b5e5')))

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.ItemSelectedChange:
            self.setBrush(QBrush(QColor('#f2a93b') if value else QColor('#62b5e5')))
        return super().itemChange(change, value)


class VocalNoteEditor(QWidget):
    abcApplied = Signal(str)
    TIME_SCALE = 480.0
    PITCH_HEIGHT = 14.0

    def __init__(self, abc_text=None, parent=None):
        super().__init__(parent)
        self.original_abc = None
        self.part = None
        self.deleted_ranges = set()
        self.undo_stack = []
        self.result_abc = None
        self.audio = PreviewSynth(self)
        self.playback_timer = QTimer(self); self.playback_timer.setInterval(16)
        self.playback_timer.timeout.connect(self._playback_tick)
        self.playback_clock = QElapsedTimer()
        self.playback_start = Fraction(0)
        self.playhead = None
        self.playing = False

        layout = QVBoxLayout(self)
        self.instructions = QLabel('V: Vocal notes — click to select; Ctrl-click selects multiple notes.')
        layout.addWidget(self.instructions)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setDragMode(QGraphicsView.RubberBandDrag)
        layout.addWidget(self.view, 1)
        controls = QHBoxLayout()
        self.play_button = QPushButton('▶ Play')
        self.play_button.clicked.connect(self.play); controls.addWidget(self.play_button)
        self.stop_button = QPushButton('■ Stop'); self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop); controls.addWidget(self.stop_button)
        self.delete_button = QPushButton('Delete Note')
        self.delete_button.clicked.connect(self.delete_selected)
        controls.addWidget(self.delete_button)
        self.undo_button = QPushButton('Undo')
        self.undo_button.clicked.connect(self.undo)
        controls.addWidget(self.undo_button)
        controls.addStretch()
        self.revert_button = QPushButton('Revert Changes')
        self.revert_button.clicked.connect(self.revert_changes); controls.addWidget(self.revert_button)
        self.apply_button = QPushButton('Apply to ABC')
        self.apply_button.clicked.connect(self.apply); controls.addWidget(self.apply_button)
        layout.addLayout(controls)

        delete_action = QAction(self); delete_action.setShortcut(QKeySequence.Delete)
        delete_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        delete_action.triggered.connect(self.delete_selected); self.addAction(delete_action)
        if abc_text is not None:
            self.load_abc(abc_text)
        else:
            self._show_empty()

    @property
    def has_changes(self):
        return bool(self.deleted_ranges)

    def _show_empty(self):
        self.scene.clear(); self.scene.addText('Open Working ABC to edit V: Vocal notes.')
        for button in (self.play_button, self.stop_button, self.delete_button,
                       self.undo_button, self.revert_button, self.apply_button):
            button.setEnabled(False)

    def clear(self):
        self.stop()
        self.original_abc = None; self.part = None; self.result_abc = None
        self.deleted_ranges.clear(); self.undo_stack.clear()
        self._show_empty()

    def load_abc(self, abc_text):
        """Load a Working ABC snapshot; loading the same snapshot preserves edits."""
        if self.part is not None and abc_text == self.original_abc:
            return
        part = parse_vocal_notes(abc_text)
        self.stop()
        self.original_abc = abc_text; self.part = part; self.result_abc = None
        self.deleted_ranges.clear(); self.undo_stack.clear()
        self._populate()

    def visible_notes(self):
        if self.part is None:
            return []
        return [note for note in self.part.notes if note.abc_source_range not in self.deleted_ranges]

    def selected_notes(self):
        return [item.note for item in self.scene.selectedItems() if isinstance(item, NoteItem)]

    def scheduled_events(self, start=None):
        if self.part is None:
            return Fraction(0), []
        selected = self.selected_notes()
        position = min((note.start for note in selected), default=Fraction(0)) if start is None else start
        return position, playback_events(self.part, self.deleted_ranges, position)

    def play(self):
        if self.part is None:
            return
        self.stop()
        self.playback_start, events = self.scheduled_events()
        remaining = max(Fraction(0), self.part.total_duration - self.playback_start)
        if not remaining:
            return
        self.audio.play(events, remaining * seconds_per_whole_note(self.part.tempo_bpm))
        self.playing = True
        self.play_button.setEnabled(False); self.stop_button.setEnabled(True)
        self.playback_clock.start(); self.playback_timer.start()
        self._set_playhead(self.playback_start)

    def stop(self):
        self.playback_timer.stop()
        self.audio.stop()
        self.playing = False
        self.play_button.setEnabled(True); self.stop_button.setEnabled(False)
        if self.playhead is not None:
            self.scene.removeItem(self.playhead); self.playhead = None

    def _set_playhead(self, position):
        if self.playhead is not None and self.playhead.scene() is self.scene:
            self.scene.removeItem(self.playhead)
        x = float(position) * self.TIME_SCALE
        rect = self.scene.sceneRect()
        self.playhead = self.scene.addLine(x, rect.top(), x, rect.bottom(), QPen(QColor('#e53935'), 2))
        self.playhead.setZValue(10)
        self.view.centerOn(x, rect.center().y())

    def _playback_tick(self):
        elapsed = Fraction(self.playback_clock.elapsed(), 1000)
        position = self.playback_start + elapsed / seconds_per_whole_note(self.part.tempo_bpm)
        if position >= self.part.total_duration:
            self.stop(); return
        self._set_playhead(position)

    def _populate(self):
        was_playing = self.playing
        self.scene.clear()
        self.playhead = None
        if self.part is None:
            self._show_empty(); return
        self.play_button.setEnabled(True); self.stop_button.setEnabled(False)
        self.apply_button.setEnabled(True)
        self.revert_button.setEnabled(self.has_changes)
        if not self.part.notes:
            self.scene.addText('No notes found in V: Vocal.')
            self.delete_button.setEnabled(False)
            self.undo_button.setEnabled(False)
            return
        self.delete_button.setEnabled(True)
        high = max(note.pitch for note in self.part.notes)
        low = min(note.pitch for note in self.part.notes)
        for pitch in range(low, high + 1):
            y = (high - pitch) * self.PITCH_HEIGHT
            color = QColor('#eef1f3') if pitch % 12 in (1, 3, 6, 8, 10) else QColor('#fafafa')
            self.scene.addRect(0, y, float(self.part.total_duration) * self.TIME_SCALE,
                               self.PITCH_HEIGHT, QPen(Qt.NoPen), QBrush(color)).setZValue(-1)
        for note in self.part.notes:
            if note.abc_source_range in self.deleted_ranges:
                continue
            x = float(note.start) * self.TIME_SCALE
            y = (high - note.pitch) * self.PITCH_HEIGHT + 1
            width = max(3.0, float(note.duration) * self.TIME_SCALE - 1)
            self.scene.addItem(NoteItem(note, QRectF(x, y, width, self.PITCH_HEIGHT - 2)))
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-4, -4, 20, 20))
        self.undo_button.setEnabled(bool(self.undo_stack))
        self.revert_button.setEnabled(self.has_changes)
        if was_playing:
            self._playback_tick()

    def delete_selected(self):
        ranges = {item.note.abc_source_range for item in self.scene.selectedItems()
                  if isinstance(item, NoteItem)}
        if not ranges:
            return
        self.undo_stack.append(set(self.deleted_ranges))
        self.deleted_ranges.update(ranges)
        self._populate()

    def undo(self):
        if not self.undo_stack:
            return
        self.deleted_ranges = self.undo_stack.pop()
        self._populate()

    def apply(self):
        if self.part is None:
            return
        self.stop()
        selected = [note for note in self.part.notes if note.abc_source_range in self.deleted_ranges]
        text = self.part.delete_notes(selected)
        self.original_abc = text
        self.part = parse_vocal_notes(text)
        self.deleted_ranges.clear(); self.undo_stack.clear()
        self.result_abc = text
        self._populate()
        self.abcApplied.emit(text)

    def revert_changes(self):
        if self.original_abc is None:
            return
        self.stop()
        self.part = parse_vocal_notes(self.original_abc)
        self.deleted_ranges.clear(); self.undo_stack.clear(); self.result_abc = None
        self._populate()

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)


# Keep this import path stable for the rest of MusicLab while the professional
# piano-roll implementation lives in its own focused component.
from .vocal_note_piano_roll import NoteItem, VocalNoteEditor

