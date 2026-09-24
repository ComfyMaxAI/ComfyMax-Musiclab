"""UI-only evidence timing, isolation and rendering regression tests."""
import copy
import os
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from comfymax_audio_chunker.editor.marker_waveform import MarkerWaveform
from comfymax_audio_chunker.editor.music_overlay import chord_rows, downbeat_bars, measure_labels


def region(start, end, root=0, quality='maj', manual=None):
    return dict(start_frame=start, end_frame=end, root_pc=root, quality=quality,
                bass_pc=None, manual=manual or {})


def analysis():
    return dict(timeline=dict(sample_rate=1000000, frames=10000000),
                analysis=dict(rhythm_backend=dict(
                    detected_downbeats=[dict(frame=3599093, seconds=99), dict(frame=5599093)],
                    aligned_downbeats=[dict(frame=3600000)])),
                first_downbeat=dict(source='auto'), bars=[dict(start_frame=3600000)],
                regions=[region(0, 3645533), region(3645533, 10000000, 9, 'min')],
                template_regions=[region(0, 4000000, 5), region(4000000, 10000000, 7)],
                chord_detections=dict(raw_segments=[
                    dict(start_seconds=0., end_seconds=3.645533123, label='C:maj'),
                    dict(start_seconds=3.645533123, end_seconds=10., label='A:min')]))


class MusicOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.data = analysis()
        self.wave = MarkerWaveform()
        self.wave.resize(1016, 300)
        self.wave.doc = SimpleNamespace(duration=10., data=dict(
            timeline=self.data['timeline'], music_analysis=self.data))
        self.wave.peaks = (np.zeros(1000), np.ones(1000)*.3, .01)
        self.wave.markers = [1000000]
        self.wave.set_view(0, 10)

    def tearDown(self):
        self.wave.close()

    def render(self):
        return self.wave.grab().toImage()

    def test_downbeat_master_frames_and_numbering(self):
        self.data['analysis']['rhythm_backend']['detected_downbeats'] += [dict(frame=-1), dict(frame=3599093)]
        self.assertEqual(downbeat_bars(self.data), [(3.599093, 1), (5.599093, 2)])

    def test_final_canonical_regions(self):
        self.assertEqual(chord_rows(self.data, 'Final'), [(0, 3.645533, 'C'), (3.645533, 10, 'Am')])

    def test_raw_timing_and_label_are_unmodified(self):
        self.assertEqual(chord_rows(self.data, 'CNN raw')[1], (3.645533123, 10, 'A:min'))

    def test_template_regions(self):
        self.assertEqual(chord_rows(self.data, 'Template')[0], (0, 4, 'F'))

    def test_boundaries_independent_and_use_waveform_mapping(self):
        self.wave.set_view(3, 1)
        image = self.render()
        bar_x = self.wave.x(3.599093)
        chord_x = self.wave.x(3.645533)
        self.assertAlmostEqual(chord_x-bar_x, 46.44)
        self.assertAlmostEqual(self.wave.t(bar_x), 3.599093)
        # Actual chord boundary pixels occupy their own x, not the downbeat x.
        y = self.wave.height()-35-48+30
        self.assertNotEqual(image.pixelColor(round(chord_x), y), image.pixelColor(round(chord_x)+4, y))
        self.wave.resize(516, 300)
        self.render()
        self.assertAlmostEqual(self.wave.x(3.645533)-self.wave.x(3.599093), 23.22)
        self.wave.set_view(3.4, 2)
        self.render()
        self.assertAlmostEqual(self.wave.t(self.wave.x(3.645533)), 3.645533)

    def test_no_markers_created_or_signalled(self):
        before = copy.deepcopy(self.wave.markers)
        selected, moved = QSignalSpy(self.wave.cutSelected), QSignalSpy(self.wave.cutMoved)
        self.render()
        QTest.mouseClick(self.wave, Qt.LeftButton, pos=QPoint(round(self.wave.x(3.599093)), 90))
        self.assertEqual(self.wave.markers, before)
        self.assertEqual((selected.count(), moved.count()), (0, 0))

    def test_manual_corrections_and_analysis_are_read_only(self):
        self.data['regions'][0]['manual'] = dict(label='Dm', start_frame=100, end_frame=3200000)
        before = copy.deepcopy(self.data)
        for source in ('Final', 'CNN raw', 'Template'):
            self.wave.music_overlay.set_source(source)
            self.render()
        self.assertEqual(chord_rows(self.data, 'Final')[0], (.0001, 3.2, 'Dm'))
        self.assertEqual(self.data, before)

    def test_manual_saved_bars_and_pickup_numbering(self):
        self.data['first_downbeat']['source'] = 'manual'
        self.data['bars'] = [dict(start_frame=0, pickup=True), dict(start_frame=2000000), dict(start_frame=4000000)]
        self.assertEqual(downbeat_bars(self.data), [(2, 1), (4, 2)])

    def test_old_template_only_project(self):
        del self.data['chord_detections']; del self.data['template_regions']
        self.data['analysis'] = {}
        self.assertEqual(chord_rows(self.data, 'Template'), chord_rows(self.data, 'Final'))
        self.assertEqual(chord_rows(self.data, 'CNN raw'), [])
        self.assertEqual(downbeat_bars(self.data), [(3.6, 1)])
        self.render()
        self.wave.music_overlay.set_source('CNN raw'); self.render()

    def test_no_music_and_project_reload(self):
        self.render()
        del self.wave.doc.data['music_analysis']
        self.render()
        self.assertIsNone(self.wave.music_overlay.pixmap)
        self.assertEqual(chord_rows(None, 'Final'), [])
        self.assertEqual(downbeat_bars(None), [])
        newer = copy.deepcopy(self.data)
        newer['regions'][0]['root_pc'] = 2
        self.wave.doc.data['music_analysis'] = newer
        self.render()
        self.assertEqual(self.wave.music_overlay.rows[0][2], 'D')

    def test_N_and_unknown(self):
        self.data['regions'] = [region(0, 1000000, None, 'N'), region(1000000, 2000000, None, 'unknown')]
        self.assertEqual([r[2] for r in chord_rows(self.data, 'Final')], ['N', 'unknown'])
        self.render()

    def test_playhead_cache_and_invalidation(self):
        self.render(); cached = self.wave.music_overlay.pixmap
        for position in (1, 2, 3):
            self.wave.position = position; self.render()
            self.assertIs(self.wave.music_overlay.pixmap, cached)
        self.wave.set_view(2, 4); self.render()
        self.assertIsNot(self.wave.music_overlay.pixmap, cached)
        cached = self.wave.music_overlay.pixmap
        self.wave.music_overlay.set_source('CNN raw'); self.render()
        self.assertIsNot(self.wave.music_overlay.pixmap, cached)
        cached = self.wave.music_overlay.pixmap
        self.wave.music_overlay.invalidate(); self.render()
        self.assertIsNot(self.wave.music_overlay.pixmap, cached)

    def test_source_selector_is_display_only(self):
        from comfymax_audio_chunker.editor.marker_app import MarkerEditor
        window = MarkerEditor()
        try:
            self.assertEqual(window.chord_source.currentText(), 'Final')
            self.assertFalse(window.chord_source.isEnabled())
            before = copy.deepcopy(self.data)
            for wave in (window.overview, window.detail):
                wave.doc = self.wave.doc; wave.peaks = self.wave.peaks
            for source in ('CNN raw', 'Template', 'Final'):
                window.chord_source.setCurrentText(source)
                for wave in (window.overview, window.detail):
                    wave.grab()
                    self.assertEqual(wave.music_overlay.source, source)
            self.assertFalse(window.dirty)
            self.assertEqual(self.data, before)
        finally:
            window.close()

    def test_repeated_measure_labels_do_not_split_regions(self):
        rows = [(0, 7, 'C'), (7, 10, 'Am')]
        bars = [(0, 1), (2, 2), (4, 3), (6, 4), (8, 5)]
        before = copy.deepcopy(rows)
        self.assertEqual(list(measure_labels(rows, bars, 10, 0, 10)),
                         [(0, 2, 'C'), (2, 4, 'C'), (4, 6, 'C'), (6, 8, 'C'), (8, 10, 'Am')])
        self.assertEqual(rows, before)

    def test_measure_labels_use_selected_source_and_downbeat_not_midpoint(self):
        bars = [(3.599093, 1), (5.599093, 2)]
        before = copy.deepcopy(self.data)
        for source, expected in [('Final', ['C', 'Am']), ('CNN raw', ['C:maj', 'A:min']), ('Template', ['F', 'G'])]:
            rows = chord_rows(self.data, source)
            self.assertEqual([r[2] for r in measure_labels(rows, bars, 10, 3, 10)], expected)
        self.assertEqual(self.data, before)

    def test_partial_measures_exact_boundary_gaps_and_unknown(self):
        rows = [(0, 2, 'C'), (2, 4, 'N'), (4, 5, 'unknown'), (8, 10, 'G')]
        bars = [(0, 1), (2, 2), (4, 3), (6, 4), (8, 5)]
        self.assertEqual(list(measure_labels(rows, bars, 10, 1, 9)),
                         [(1, 2, 'C'), (2, 4, 'N'), (4, 6, 'unknown'), (8, 9, 'G')])
        self.assertEqual(list(measure_labels(rows, [], 10, 0, 10)), [])


if __name__ == '__main__':
    unittest.main()
