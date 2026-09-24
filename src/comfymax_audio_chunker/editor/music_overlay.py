"""Read-only music evidence overlay, using the waveform's master-clock mapping."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

from ..music.model import effective_chord


SOURCES = ('Final', 'CNN raw', 'Template')
LANE_HEIGHT = 48


def chord_rows(data, source):
    """Return paint coordinates only; never normalize or align saved evidence."""
    if not data:
        return []
    if source == 'CNN raw':
        return [(r['start_seconds'], r['end_seconds'], r['label'])
                for r in data.get('chord_detections', {}).get('raw_segments', [])]
    if source == 'Template':
        # Before Phase 2B, regions themselves were the template result.
        regions = data.get('template_regions', [] if data.get('chord_detections') else data.get('regions', []))
    else:
        regions = data.get('regions', [])
    rate = data['timeline']['sample_rate']
    return [(r['start_frame']/rate, r['end_frame']/rate, r['label'])
            for r in map(effective_chord, regions)]


def downbeat_bars(data):
    """Original detected master frames, or explicitly saved manual/legacy bars."""
    if not data:
        return []
    timeline = data['timeline']
    rhythm = data.get('analysis', {}).get('rhythm_backend', {})
    if data.get('first_downbeat', {}).get('source') != 'manual' and 'detected_downbeats' in rhythm:
        frames = [b['frame'] for b in rhythm['detected_downbeats']]
    else:
        frames = [b['start_frame'] for b in data.get('bars', []) if not b.get('pickup')]
    frames = sorted(set(f for f in frames if isinstance(f, (int, float))
                        and math.isfinite(f) and 0 <= f < timeline['frames']))
    return [(f/timeline['sample_rate'], number) for number, f in enumerate(frames, 1)]


def measure_labels(rows, bars, duration, start, end):
    """Transient text spans only; chord geometry always comes from rows."""
    for index, (downbeat, _) in enumerate(bars):
        next_downbeat = bars[index+1][0] if index+1 < len(bars) else duration
        if next_downbeat <= start or downbeat >= end:
            continue
        label = next((label for left, right, label in rows if left <= downbeat < right), None)
        if label is not None:
            yield max(start, downbeat), min(end, next_downbeat), label


class MusicOverlay:
    """One cached transparent layer; playhead ticks only blit this pixmap."""
    def __init__(self):
        self.source = 'Final'
        self._data = None
        self._key = None
        self.pixmap = None
        self.rows = []
        self.bars = []

    def invalidate(self):
        self._key = None

    def set_source(self, source):
        if source not in SOURCES:
            raise ValueError(source)
        self.source = source

    def draw(self, painter, wave, top, bottom):
        data = wave.doc.data.get('music_analysis') if wave.doc else None
        if not data:
            self._data = None
            self._key = None
            self.rows = []; self.bars = []; self.pixmap = None
            return
        key = (self.source, wave.visible_window(), wave.width(), wave.height(),
               wave.devicePixelRatioF(), wave.font().toString(), wave.overview, top, bottom)
        if self._data is not data or self._key != key:
            self._data = data
            self._key = key
            self.rows = chord_rows(data, self.source)
            self.bars = downbeat_bars(data)
            dpr = wave.devicePixelRatioF()
            self.pixmap = QPixmap(math.ceil(wave.width()*dpr), math.ceil(wave.height()*dpr))
            self.pixmap.setDevicePixelRatio(dpr)
            self.pixmap.fill(Qt.transparent)
            p = QPainter(self.pixmap)
            p.setFont(wave.font())
            p.setRenderHint(QPainter.Antialiasing)
            p.setClipRect(QRectF(8, top, max(1, wave.width()-16), wave.height()-top))
            a, span = wave.visible_window()
            if not wave.overview:
                p.fillRect(QRectF(8, bottom, wave.width()-16, LANE_HEIGHT), QColor('#1e2634'))
                p.setPen(QColor('#bac7da'))
                caption = f'Chords · {self.source}' + (' · labels at downbeats' if self.bars else '') + (' · no saved data' if not self.rows else '')
                p.drawText(QRectF(12, bottom, wave.width()-24, 18), Qt.AlignVCenter, caption)
                for start, end, label in self.rows:
                    if end <= a or start >= a+span:
                        continue
                    left, right = wave.x(max(a, start)), wave.x(min(a+span, end))
                    rect = QRectF(left, bottom+19, right-left, 27)
                    color = '#344657' if label not in ('N', 'unknown') else '#30333b'
                    p.fillRect(rect, QColor(color))
                    # Without a stored downbeat grid retain the original region labels.
                    if not self.bars and right-left > p.fontMetrics().horizontalAdvance(label)+8:
                        p.setPen(QColor('#edf5fa'))
                        p.drawText(rect, Qt.AlignCenter, label)
                for start, end, label in measure_labels(self.rows, self.bars, wave.duration, a, a+span):
                    left, right = wave.x(start), wave.x(end)
                    if right-left > p.fontMetrics().horizontalAdvance(label)+8:
                        p.setPen(QColor('#edf5fa'))
                        p.drawText(QRectF(left, bottom+19, right-left, 27), Qt.AlignCenter, label)
                # Keep actual changes visible even when they cross a measure label.
                p.setPen(QPen(QColor('#83becb'), 1))
                for start, end, _ in self.rows:
                    for seconds in (start, end):
                        if a <= seconds <= a+span:
                            x = wave.x(seconds)
                            p.drawLine(QPointF(x, bottom+19), QPointF(x, bottom+46))
            last_label_right = -100
            for seconds, number in self.bars:
                if not a <= seconds <= a+span:
                    continue
                x = wave.x(seconds)
                p.setPen(QPen(QColor('#ba9cf5'), 1, Qt.DashLine))
                end_y = bottom if wave.overview else bottom+LANE_HEIGHT
                p.drawLine(QPointF(x, top), QPointF(x, end_y))
                text = str(number)
                label_width = p.fontMetrics().horizontalAdvance(text)+8
                if not wave.overview and x >= last_label_right:
                    p.fillRect(QRectF(x+2, top, label_width, 18), QColor('#272135'))
                    p.drawText(QRectF(x+6, top, label_width, 18), Qt.AlignVCenter, text)
                    last_label_right = x+label_width+6
            p.end()
        painter.drawPixmap(0, 0, self.pixmap)

    def tooltip(self, seconds):
        for start, end, label in self.rows:
            if start <= seconds < end:
                return f'{self.source}: {label}\n{start:.6f} – {end:.6f} s (stored timing)'
        return f'{seconds:.6f} seconds'
