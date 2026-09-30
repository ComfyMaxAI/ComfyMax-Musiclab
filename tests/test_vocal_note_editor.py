import os
import unittest
from fractions import Fraction
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QDialog, QWidget

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

    def editor(self, text=ABC):
        with patch('comfymax_audio_chunker.editor.vocal_note_piano_roll.PreviewSynth', self.FakeAudio):
            return VocalNoteEditor(text)

    @staticmethod
    def select(editor, *indices):
        wanted = {editor.part.notes[index].abc_source_range for index in indices}
        for item in editor.scene.items():
            if isinstance(item, NoteItem) and item.note.abc_source_range in wanted:
                item.setSelected(True)

    def test_revert_does_not_produce_changed_abc(self):
        editor = self.editor(); self.select(editor, 1); editor.delete_selected()
        self.assertTrue(editor.has_changes)
        editor.revert_changes()
        self.assertIsNone(editor.result_abc)
        self.assertEqual(editor.original_abc, ABC)
        self.assertFalse(editor.has_changes)

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

    def test_delete_undo_and_rubber_band_remain_available(self):
        from PySide6.QtWidgets import QGraphicsView
        editor=self.editor(); self.select(editor,1,3); editor.delete_selected()
        self.assertEqual(len(editor.visible_notes()),5)
        self.assertEqual(editor.view.dragMode(),QGraphicsView.RubberBandDrag)
        editor.undo()
        self.assertEqual(len(editor.visible_notes()),len(editor.part.notes))

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

        with patch.object(panel, 'preview_working') as preview:
            panel.apply_vocal_notes(ABC.replace('^D/2', 'z/2'))
        self.assertIn('C2 z/2 E3/2', panel.abc.toPlainText())
        preview.assert_called_once_with()

    def test_editor_is_a_page_widget_not_a_dialog(self):
        editor = self.editor()
        self.assertIsInstance(editor, QWidget)
        self.assertNotIsInstance(editor, QDialog)

    def test_pitch_edit_is_source_local_and_undo_redo(self):
        editor=self.editor(); before=editor.current_abc; target=editor.part.notes[0]
        self.assertTrue(editor.edit_pitch(target,61))
        self.assertIn('"C"^C2 ^D/2',editor.current_abc)
        self.assertEqual(editor.current_abc.replace('^C2','C2'),before)
        editor.undo(); self.assertEqual(editor.current_abc,before)
        editor.redo(); self.assertIn('"C"^C2',editor.current_abc)

    def test_duration_edit_changes_only_selected_token(self):
        editor=self.editor(); before=editor.current_abc; target=editor.part.notes[0]
        self.assertTrue(editor.edit_duration(target,Fraction(3,8)))
        self.assertIn('"C"C3 ^D/2',editor.current_abc)
        self.assertEqual(editor.current_abc.replace('C3','C2',1),before)

    def test_start_move_and_multi_move_consume_only_adjacent_rest(self):
        text='X:1\nT:Move\nM:4/4\nL:1/8\nV: Vocal\nV: Ins\nK:C\nV: Vocal\nz2 C D z2 |\nV: Ins\nC, D,|\n'
        editor=self.editor(text); notes=list(editor.part.notes)
        self.assertTrue(editor.move_notes([notes[1]],Fraction(1,8),0))
        self.assertEqual(editor.part.notes[1].start,Fraction(1,2))
        editor.undo(); notes=list(editor.part.notes)
        self.assertTrue(editor.move_notes(notes,Fraction(1,8),1))
        self.assertEqual([n.start for n in editor.part.notes],[Fraction(3,8),Fraction(1,2)])
        self.assertEqual([n.pitch for n in editor.part.notes],[61,63])
        self.assertIn('V: Ins\nC, D,|',editor.current_abc)

    def test_all_snap_resolutions_are_exact_fractions(self):
        editor=self.editor()
        for value in ('1/4','1/8','1/16','1/32'):
            editor.grid_combo.setCurrentText(value);grid=Fraction(value)
            self.assertEqual(editor.snap_fraction(grid+grid/3),grid)

    def test_complex_constructs_block_edits_without_source_change(self):
        examples=[
            'X:1\nL:1/8\nV: Vocal\nK:C\nV: Vocal\nC-D|\n',
            'X:1\nL:1/8\nV: Vocal\nK:C\nV: Vocal\n(3CDE|\n',
            'X:1\nL:1/8\nV: Vocal\nK:C\nV: Vocal\nC>D|\n']
        for text in examples:
            editor=self.editor(text);before=editor.current_abc
            self.assertFalse(editor.edit_pitch(editor.part.notes[0],62))
            self.assertEqual(editor.current_abc,before)

    def test_round_trip_preserves_ins_lyrics_chords_comments_and_headers(self):
        text='X:9\nT:Keep me\nM:4/4\nL:1/8\nV: Vocal\nV: Ins\nK:G\n% header comment\nV: Vocal\n"G"F2 A2 | % vocal comment\nw: keep these lyrics\nV: Ins\n"D"D,2 E,2 | % exact instrumental\n'
        editor=self.editor(text);before=text;self.assertTrue(editor.edit_pitch(editor.part.notes[0],67))
        changed=editor.current_abc
        start=before.index('V: Ins\n"D"');self.assertEqual(changed[changed.index('V: Ins\n"D"'):],before[start:])
        for preserved in ('X:9\nT:Keep me','"G"','% header comment','% vocal comment','w: keep these lyrics'):
            self.assertIn(preserved,changed)
        editor.apply();self.assertEqual(editor.result_abc,changed)

    def test_revert_restores_exact_opening_snapshot_after_multiple_edits(self):
        editor=self.editor();self.assertTrue(editor.edit_pitch(editor.part.notes[0],61));editor.edit_duration(editor.part.notes[0],Fraction(3,8))
        editor.revert_changes();self.assertEqual(editor.current_abc,ABC);self.assertFalse(editor.has_changes)


class PageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    select = staticmethod(EditorTests.select)

    def setUp(self):
        from comfymax_audio_chunker.editor.marker_app import MarkerEditor
        values=dict(sheet_sage_path='',yue2_main_path='',yue2_vae_path='',whisper_model='medium')
        settings=patch('comfymax_audio_chunker.editor.settings_panel.load_settings',return_value=values)
        settings.start(); self.addCleanup(settings.stop)
        self.window=MarkerEditor(); self.addCleanup(self.window.close); self.addCleanup(self.window.deleteLater)
        self.window.content.setEnabled(True)
        self.window.views.setTabEnabled(self.window.views.indexOf(self.window.score_panel.abc_view),True)
        self.window.views.setTabEnabled(self.window.views.indexOf(self.window.vocal_notes_page),True)
        panel=self.window.score_panel
        panel.original_document=ScoreDocument(ABC.encode(),'fixture')
        panel.document=panel.original_document; panel.abc.setPlainText(ABC)

    def test_vocal_notes_is_normal_page_directly_after_abc(self):
        labels=[self.window.views.tabText(i) for i in range(self.window.views.count())]
        self.assertEqual(labels[labels.index('ABC')+1], 'Vocal Notes')
        self.assertIs(self.window.views.widget(labels.index('Vocal Notes')), self.window.vocal_notes_page)

    def test_abc_button_navigates_and_loads_current_working_abc(self):
        current=ABC.replace('T:Regression','T:Current Working ABC')
        self.window.score_panel.abc.setPlainText(current)
        self.window.score_panel.vocal_editor_button.click()
        self.assertIs(self.window.views.currentWidget(),self.window.vocal_notes_page)
        self.assertEqual(self.window.vocal_notes_page.original_abc,current)
        self.assertEqual([note.abc_token for note in self.window.vocal_notes_page.part.notes],
                         ['C2','^D/2','E3/2','F','c,2','=B,,4','A4'])

    def test_apply_revert_and_navigation_do_not_double_apply_or_lose_edits(self):
        page=self.window.vocal_notes_page; panel=self.window.score_panel
        panel.vocal_editor_button.click(); self.select(page,1); page.delete_selected()
        self.assertTrue(page.has_changes); before=panel.abc.toPlainText()
        self.window.views.setCurrentWidget(panel.abc_view)
        self.window.views.setCurrentWidget(page)
        self.assertTrue(page.has_changes)
        page.revert_changes()
        self.assertEqual(panel.abc.toPlainText(),before)
        self.select(page,1); page.delete_selected()
        with patch.object(panel,'preview_working') as preview:
            page.apply()
            self.window.views.setCurrentWidget(panel.abc_view)
            self.window.views.setCurrentWidget(page)
        self.assertIn('C2 z/2 E3/2',panel.abc.toPlainText())
        preview.assert_called_once_with()
        self.assertFalse(page.has_changes)

    def test_main_ctrl_z_routes_to_vocal_page_undo(self):
        page=self.window.vocal_notes_page
        self.window.score_panel.vocal_editor_button.click(); self.select(page,1); page.delete_selected()
        self.window.undo_current()
        self.assertFalse(page.has_changes)

    def test_leaving_page_stops_playback_without_losing_edits(self):
        page=self.window.vocal_notes_page
        with patch('comfymax_audio_chunker.editor.vocal_note_piano_roll.PreviewSynth',EditorTests.FakeAudio):
            # The page already owns its audio object; replace only the output adapter.
            page.audio=EditorTests.FakeAudio(page)
        self.window.score_panel.vocal_editor_button.click(); self.select(page,1); page.delete_selected()
        page.play(); self.assertTrue(page.playing)
        self.window.views.setCurrentWidget(self.window.score_panel.abc_view)
        self.assertFalse(page.playing); self.assertTrue(page.has_changes)


if __name__ == '__main__':
    unittest.main()
