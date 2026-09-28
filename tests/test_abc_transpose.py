import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from comfymax_audio_chunker.editor.abc_transpose import (ABCTransposeError,
    normalize_chord_symbols, restore_sheetsage_format, transpose_abc)
from comfymax_audio_chunker.editor.score_panel import ScorePanel
from comfymax_audio_chunker.editor.score_source import SheetSageABCSource


FIXTURE = (Path(__file__).parent / 'fixtures' / 'transpose.abc').read_text(encoding='utf-8')


def artifact(root, payload):
    path = root / 'music_analysis/sheetsage/test/native/score.abc'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return dict(raw_artifacts=[dict(path=path.relative_to(root).as_posix(),
                sha256=hashlib.sha256(payload).hexdigest())])


class TransposeEngineTests(unittest.TestCase):
    def test_key_aware_chord_normalization_only_touches_quoted_chords(self):
        source=('X:1\nK:Eb\n"D#"D#2 "G#"G/2 z/2|"A#7"A4 "Bb7"B2|\n'
                'K:D\n"C##"C2 "F##m"F2|"G##7"G4|\n')
        normalized=normalize_chord_symbols(source)
        self.assertIn('K:Eb\n"Eb"D#2 "Ab"G/2 z/2|"Bb7"A4',normalized)
        self.assertIn('"Bb7"B2',normalized)
        self.assertIn('K:D\n"D"C2 "Gm"F2|"A7"G4|',normalized)
        self.assertIn('D#2',normalized)  # Melody token is not normalized.
        self.assertIn('G/2 z/2|',normalized)

    def test_multiple_keys_and_voice_keys_keep_their_own_context(self):
        source=('X:1\nV:one name="Lead"\nV:two name="Harmony"\nK:Eb\n'
                'V:one\n"D#"C2|\nV:two\nK:E\n"Abm7"D2|\n'
                'V:one\n"G#sus4"E2|\n')
        normalized=normalize_chord_symbols(source)
        self.assertIn('V:one\n"Eb"C2|',normalized)
        self.assertIn('V:two\nK:E\n"G#m7"D2|',normalized)
        self.assertIn('V:one\n"Absus4"E2|',normalized)
        self.assertEqual(normalized.count('V:'),source.count('V:'))

    def test_real_sheetsage_spelling_regression_transpose_minus_one(self):
        source='X:1\nM:4/4\nL:1/4\nK:Eb\n"D#"E2 z2|"G#"A2 "A#7"B2|\n'
        output=transpose_abc(source,-1)
        self.assertIn('K:D',output)
        for chord in ('"D"','"G"','"A7"'):
            self.assertIn(chord,output)
        for exotic in ('"C##"','"F##"','"G##7"'):
            self.assertNotIn(exotic,output)
        self.assertIn('D2 z2|',output)

    def test_sheetsage_voice_key_and_line_format_is_restored_without_changing_music(self):
        source=('X:1\n\nV: Vocal clef=treble name="Vocal Melody" snm="Vocal"\n'
                'V: Ins clef=treble name="Instrumental Melody" snm="Ins"\n'
                'K:C\nV: Vocal\n"C"C2 z2|\n[K:Eb] "Eb"E2-F2|\nV: Ins\nG4|\n')
        raw=('X:1\r\n\r\nV:Vocal clef=treble name="Vocal Melody" snm="Vocal"\r\n'
             'V:Ins clef=treble name="Instrumental Melody" snm="Ins"\r\n'
             'K:Bbmaj\r\nV:Vocal\r\n"Bb"B,2 z2|\r\n[K:Dbmaj] "Db"D2-E2|\r\nV:Ins\r\nF4|\r\n')
        restored=restore_sheetsage_format(source,raw)
        self.assertIn('V: Vocal clef=treble',restored)
        self.assertIn('V: Ins clef=treble',restored)
        self.assertIn('\nV: Vocal\n',restored)
        self.assertIn('\nV: Ins\n',restored)
        self.assertIn('K:Bb\n',restored); self.assertNotIn('K:Bbmaj',restored)
        self.assertIn('[K:Db]',restored); self.assertNotIn('[K:Dbmaj]',restored)
        self.assertEqual(restored.count('\n'),source.count('\n'))
        self.assertTrue(restored.endswith('\n')); self.assertNotIn('\r',restored)
        raw_music=raw.replace('\r\n','\n').replace('V:Vocal','V: Vocal').replace('V:Ins','V: Ins').replace('K:Bbmaj','K:Bb').replace('[K:Dbmaj]','[K:Db]')
        self.assertEqual(restored,raw_music)

    def test_actual_transpose_restores_sheetsage_voice_and_major_key_format(self):
        source=('X:1\nV: Vocal name="Vocal Melody"\nV: Ins name="Instrumental Melody"\n'
                'K:C\nV: Vocal\n"C"C2 z2|\n[K:Eb] "Eb"E2 F2|\nV: Ins\nG4|\n')
        output=transpose_abc(source,-2)
        for token in ('V: Vocal','V: Ins','K:Bb','[K:Db]','"Bb"','"Db"'):
            self.assertIn(token,output)
        self.assertNotIn('V:Vocal',output); self.assertNotIn('V:Ins',output)
        self.assertNotIn('K:Bbmaj',output); self.assertNotIn('[K:Dbmaj]',output)

    def test_plus_and_minus_one_and_two_rewrite_key_notes_accidentals_chords_and_voices(self):
        outputs = {amount: transpose_abc(FIXTURE, amount) for amount in (-2, -1, 1, 2)}
        for amount, output in outputs.items():
            self.assertNotEqual(output, FIXTURE, amount)
            self.assertNotIn('%%transpose', output)
            self.assertIn('V:one', output)
            self.assertIn('V:two', output)
        expected = {
            -2: ('K:Bb', '"Bb"', '"Gm"', '"E7"'),
            -1: ('K:B', '"B"', '"G#m"', '"F7"'),
             1: ('K:Db', '"Db"', '"Bbm"', '"G7"'),
             2: ('K:D', '"D"', '"Bm"', '"G#7"'),
        }
        for amount, tokens in expected.items():
            for token in tokens:
                self.assertIn(token, outputs[amount], (amount, token))
        plus_two = outputs[2]
        self.assertRegex(plus_two, r'\bD\s+E\s+')
        self.assertIn('^G', plus_two)
        self.assertIn('=G', plus_two)

    def test_cumulative_transposition_matches_direct_result(self):
        cumulative = transpose_abc(transpose_abc(FIXTURE, 2), -1)
        direct = transpose_abc(FIXTURE, 1)
        # abc2abc adds provenance comments on each pass; compare musical lines.
        musical = lambda value: '\n'.join(line for line in value.splitlines() if not line.startswith('%'))
        self.assertEqual(musical(cumulative), musical(direct))

    def test_failure_is_clear(self):
        with self.assertRaisesRegex(ABCTransposeError, 'unavailable'):
            transpose_abc(FIXTURE, 1, Path('missing-abc2abc.exe'))


class WorkingABCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = ('\ufeff' + FIXTURE.replace('\n', '\r\n')).encode('utf-8')
        self.path = self.root / 'music_analysis/sheetsage/test/native/score.abc'
        self.panel = ScorePanel()
        self.panel.source = SheetSageABCSource(self.root, artifact(self.root, self.original))
        self.panel.load_source()

    def tearDown(self):
        self.panel.close()
        self.temp.cleanup()

    def test_original_is_untouched_and_reset_is_byte_identical(self):
        before = self.path.read_bytes()
        with patch.object(self.panel, 'preview_working'):
            self.panel.transpose(2)
            self.assertNotEqual(self.panel.document.payload, before)
            self.panel.reset_abc()
        self.assertEqual(self.panel.document.payload, before)
        self.assertEqual(self.panel.original_document.payload, before)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.panel.transpose_label.text(), 'Transpose: Original')

    def test_manual_edit_is_input_to_next_transpose_and_preview_gets_working_document(self):
        edited = self.panel.abc.toPlainText().replace('T:Transpose fixture', 'T:Manual edit')
        self.panel.abc.setPlainText(edited)
        captured = []
        with patch.object(self.panel, 'render_document', side_effect=captured.append):
            self.panel.transpose(1)
        self.assertIn('T:Manual edit', self.panel.abc.toPlainText())
        self.assertEqual(captured[-1].payload, self.panel.document.payload)
        self.assertEqual(self.panel.transpose_label.text(), 'Transpose: +1 semitone')

    def test_failed_transpose_preserves_previous_working_abc(self):
        self.panel.abc.setPlainText(self.panel.abc.toPlainText() + '% manual\n')
        before = self.panel.abc.toPlainText()
        with patch('comfymax_audio_chunker.editor.score_panel.transpose_abc', side_effect=ABCTransposeError('simulated')):
            self.panel.transpose(1)
        self.assertEqual(self.panel.abc.toPlainText(), before)
        self.assertIn('simulated', self.panel.status.text())
        self.assertEqual(self.panel.transpose_amount, 0)


if __name__ == '__main__':
    unittest.main()
