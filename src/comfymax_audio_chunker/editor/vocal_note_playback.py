"""Deterministic scheduling and lightweight local audio for Vocal preview."""
from array import array
from dataclasses import dataclass
from fractions import Fraction
import math


@dataclass(frozen=True)
class PlaybackEvent:
    pitch: int
    musical_start: Fraction
    musical_duration: Fraction
    start_seconds: Fraction
    duration_seconds: Fraction


def seconds_per_whole_note(tempo_bpm):
    """ABC positions use whole-note units; one whole note is four beats."""
    return Fraction(240, 1) / tempo_bpm


def playback_events(part, deleted_ranges=frozenset(), start=Fraction(0)):
    scale = seconds_per_whole_note(part.tempo_bpm)
    return [PlaybackEvent(note.pitch, note.start, note.duration,
                          (note.start - start) * scale, note.duration * scale)
            for note in part.notes
            if note.abc_source_range not in deleted_ranges and note.start >= start]


class PreviewSynth:
    """Render the small monophonic preview to a Qt audio buffer."""
    RATE = 22050

    def __init__(self, parent=None):
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink
        self._QBuffer, self._QByteArray, self._QIODevice = QBuffer, QByteArray, QIODevice
        fmt = QAudioFormat(); fmt.setSampleRate(self.RATE); fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.Int16)
        self.sink = QAudioSink(fmt, parent)
        self.buffer = None

    def play(self, events, duration_seconds):
        self.stop()
        count = max(1, int(float(duration_seconds) * self.RATE) + 1)
        samples = array('h', [0]) * count
        for event in events:
            first = max(0, int(float(event.start_seconds) * self.RATE))
            length = max(1, int(float(event.duration_seconds) * self.RATE))
            frequency = 440.0 * (2.0 ** ((event.pitch - 69) / 12.0))
            attack = max(1, min(int(.008 * self.RATE), length // 3))
            release = max(1, min(int(.025 * self.RATE), length // 3))
            for index in range(min(length, count - first)):
                envelope = min(1.0, index / attack, (length - index) / release)
                value = int(7000 * envelope * math.sin(2 * math.pi * frequency * index / self.RATE))
                position = first + index
                samples[position] = max(-32768, min(32767, samples[position] + value))
        self.buffer = self._QBuffer()
        self.buffer.setData(self._QByteArray(samples.tobytes()))
        self.buffer.open(self._QIODevice.ReadOnly)
        self.sink.start(self.buffer)

    def stop(self):
        self.sink.stop()
        if self.buffer is not None:
            self.buffer.close(); self.buffer = None
