import os
import unittest
from fractions import Fraction
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QDialog

from comfymax_audio_chunker.editor.score_panel import ScorePanel
from comfymax_audio_chunker.editor.score_source import ScoreDocument
from comfymax_audio_chunker.editor.vocal_note_editor import NoteItem, VocalNoteEditor
from comfymax_audio_chunker.editor.vocal_note_parser import VocalABCError, parse_vocal_notes
from comfymax_audio_chunker.editor.vocal_note_playback import playback_events
from comfymax_audio_chunker.editor.vocal_note_playback import seconds_per_whole_note


ABC = '''X:1\nT:Regression\nM:4/4\nL:1/8\nQ:1/4=120\nV: Vocal name="Vocal"\nV: Ins name="Instrumental"\nK:C\nV: Vocal\n"C"C2 ^D/2 E3/2-F | z2 c,2 |\nV: Ins\nC,2 D,2 E,2 F,2 |\nV: Vocal\n=B,,4 A4 |\n'''


class ParserTests(unittest.TestCase):
    def test_only_vocal_and_accidentals_lengths(self):
        part = parse_vocal_notes(ABC)
        self.assertEqual([n.abc_token for n in part.notes], ['C2', '^D/2', 'E3/2', 'F', 'c,2', '=B,,4', 'A4'])
        self.assertEqual([n.pitch for n in part.notes], [60, 63, 64, 65, 60, 47, 69])
        self.assertEqual([n.duration for n in part.notes[:4]], [1/4, 1/16, 3/16, 1/8])
        self.assertEqual(part.tempo_bpm, 120)

    def test_delete_is_source_preserving_and_timing_stable(self):
        part = parse_vocal_notes(ABC)
        target = part.notes[1]
        edited = part.delete_notes([target])
        self.assertIn('C2 z/2 E3/2-F', edited)
        self.assertEqual(edited[edited.index('V: Ins'):edited.index('V: Vocal', edited.index('V: Ins'))],
                         ABC[ABC.index('V: Ins'):ABC.index('V: Vocal', ABC.index('V: Ins'))])
        after = parse_vocal_notes(edited)
        self.assertEqual(part.total_duration, after.total_duration)
        self.assertEqual([(n.abc_token, n.start) for n in part.notes[2:]],
                         [(n.abc_token, n.start) for n in after.notes[1:]])

    def test_tie_is_recognised(self):
        notes = parse_vocal_notes(ABC).notes
        self.assertTrue(notes[2].tied_to_next)
        self.assertTrue(notes[3].tied_from_previous)
        edited = parse_vocal_notes(ABC).delete_notes([notes[3]])
        self.assertIn('E3/2 z |', edited)

    def test_key_signature_and_measure_accidentals_affect_pitch(self):
        abc = 'X:1\nL:1/8\nV: Vocal\nV: Ins\nK:G\nV: Vocal\nF ^F F | F =F F |\nV: Ins\nF F F|\n'
        self.assertEqual([n.pitch for n in parse_vocal_notes(abc).notes],
                         [66, 66, 66, 66, 65, 65])

    def test_unsupported_polyphony_fails_clearly(self):
        with self.assertRaisesRegex(VocalABCError, 'Chord note groups'):
            parse_vocal_notes('X:1\nV: Vocal\nK:C\nV: Vocal\n[CEG]2|\n')

    def test_tempo_is_exact_quarter_note_timing(self):
        part = parse_vocal_notes('X:1\nL:1/4\nQ:1/4=111\nV: Vocal\nK:C\nV: Vocal\nC D|\n')
        events = playback_events(part)
        self.assertEqual(part.tempo_bpm, 111)
        self.assertEqual(seconds_per_whole_note(part.tempo_bpm), Fraction(80, 37))
        self.assertEqual(events[1].start_seconds, Fraction(20, 37))
        alternate = parse_vocal_notes('X:1\nL:1/4\nQ:1/8=120\nV: Vocal\nK:C\nV: Vocal\nC|\n')
        self.assertEqual(alternate.tempo_bpm, 60)


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    class FakeAudio:
        def __init__(self, parent=None):
            self.calls = []; self.stops = 0
        def play(self, events, duration):
            self.calls.append((list(events), duration))
        def stop(self):
            self.stops += 1

    def editor(self):
        with patch('comfymax_audio_chunker.editor.vocal_note_editor.PreviewSynth', self.FakeAudio):
            return VocalNoteEditor(ABC)

    @staticmethod
    def select(editor, *indices):
        wanted = {editor.part.notes[index].abc_source_range for index in indices}
        for item in editor.scene.items():
            if isinstance(item, NoteItem) and item.note.abc_source_range in wanted:
                item.setSelected(True)

    def test_cancel_does_not_produce_changed_abc(self):
        editor = self.editor()
        editor.reject()
        self.assertIsNone(editor.result_abc)
        self.assertEqual(editor.original_abc, ABC)

    def test_events_are_vocal_only_and_start_at_begin(self):
        editor = self.editor()
        start, events = editor.scheduled_events()
        self.assertEqual(start, 0)
        self.assertEqual([event.pitch for event in events], [60, 63, 64, 65, 60, 47, 69])

    def test_selected_and_multiselected_start_position(self):
        editor = self.editor(); self.select(editor, 3)
        self.assertEqual(editor.scheduled_events()[0], editor.part.notes[3].start)
        editor.scene.clearSelection(); self.select(editor, 4, 1)
        self.assertEqual(editor.scheduled_events()[0], editor.part.notes[1].start)

    def test_deleted_notes_are_silent_without_shifting_following_events(self):
        editor = self.editor(); deleted = editor.part.notes[1]
        before = playback_events(editor.part)
        editor.deleted_ranges.add(deleted.abc_source_range)
        after = editor.scheduled_events(start=Fraction(0))[1]
        self.assertNotIn(deleted.pitch, [event.pitch for event in after[:1]])
        expected = [(event.pitch, event.musical_start) for event in before
                    if event.musical_start != deleted.start]
        self.assertEqual([(event.pitch, event.musical_start) for event in after], expected)

    def test_stop_and_close_end_all_playback(self):
        editor = self.editor(); editor.play()
        self.assertTrue(editor.playing); self.assertTrue(editor.playback_timer.isActive())
        editor.stop()
        self.assertFalse(editor.playing); self.assertFalse(editor.playback_timer.isActive())
        stops = editor.audio.stops
        editor.play(); editor.close()
        self.assertFalse(editor.playing); self.assertFalse(editor.playback_timer.isActive())
        self.assertGreater(editor.audio.stops, stops)

    def test_playback_does_not_change_abc(self):
        editor = self.editor(); before = editor.original_abc
        editor.play(); editor.stop()
        self.assertEqual(editor.original_abc, before)
        self.assertIsNone(editor.result_abc)

    def test_apply_updates_score_panel_and_preview(self):
        panel = ScorePanel()
        panel.original_document = ScoreDocument(ABC.encode(), 'fixture')
        panel.document = panel.original_document
        panel.abc.setPlainText(ABC)

        class AppliedEditor:
            Accepted = QDialog.Accepted
            result_abc = ABC.replace('^D/2', 'z/2')
            def __init__(self, *args): pass
            def exec(self): return self.Accepted

        with patch('comfymax_audio_chunker.editor.vocal_note_editor.VocalNoteEditor', AppliedEditor), \
             patch.object(panel, 'preview_working') as preview:
            panel.edit_vocal_notes()
        self.assertIn('C2 z/2 E3/2', panel.abc.toPlainText())
        preview.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
