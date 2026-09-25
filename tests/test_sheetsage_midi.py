import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import mido
from comfymax_audio_chunker.music import sheetsage_music as music, sheetsage_midi as midi


def evidence(notes=None, bpm=120):
    return dict(schema_version='comfymax.sheetsage.1', status='success', provenance={}, warnings=[], raw_artifacts=[],
                transcription=dict(notes=notes if notes is not None else [dict(start=0.,end=.5,pitch=60,track=0)],
                                   tempo=[dict(value=f'1/4={bpm}',voice=None)],
                                   meters=[dict(value='4/4',voice=None)], keys=[dict(value='C',voice=None)]))


class MidiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def write(self,e=None,name='song.mid',**kwargs):
        return midi.export(e or evidence(),self.root,self.root/name,**kwargs)
    def native(self, events, known=True):
        e=evidence(); folder=self.root/'music_analysis/sheetsage/run/native'; folder.mkdir(parents=True)
        payload=json.dumps(dict(events=events)).encode()
        for p,b in [(folder/'events.json',json.dumps(dict(payload_hex=payload.hex())).encode()),
                    (folder.parent/'events-decoded.json',payload)]:
            p.write_bytes(b);e['raw_artifacts'].append(dict(path=p.relative_to(self.root).as_posix(),sha256=hashlib.sha256(b).hexdigest()))
        if known: e['provenance']=dict(executable_sha256=music.KNOWN_EXE,model_sha256=music.KNOWN_MODEL)
        return e

    def test_constant_120_90_beat_between_bar_crossing_roundtrip(self):
        for bpm in (120,90):
            beat=60/bpm
            notes=[dict(start=s*beat,end=t*beat,pitch=60+i,track=0) for i,(s,t) in enumerate([(0,1),(1,2),(2.123,2.789),(3.5,4.5),(8,9)])]
            result=self.write(evidence(notes,bpm),f'{bpm}.mid')
            self.assertEqual(result['note_count'],len(notes))
            for field in ('max_start_error_seconds','max_end_error_seconds'):
                self.assertLessEqual(result['timing'][field],result['timing']['half_tick_seconds']+1e-9)
            read=mido.MidiFile(self.root/f'{bpm}.mid')
            self.assertEqual((read.type,read.ticks_per_beat,len(read.tracks)),(1,960,2))
            self.assertEqual([m.note for m in read.tracks[1] if m.type=='note_on'],[60,61,62,63,64])
            self.assertEqual([m.velocity for m in read.tracks[1] if m.type=='note_on'],[80]*5)

    def test_simultaneous_overlapping_nested_same_pitch_and_order(self):
        notes=[dict(start=s,end=t,pitch=p,track=k) for s,t,p,k in [(0,2,60,0),(0,1,64,0),(.25,.75,60,0),(.25,3,60,0),(1,2,67,1),(2,3,60,0)]]
        result=self.write(evidence(notes))
        self.assertEqual(result['note_count'],6)
        self.assertEqual(len(result['tracks']),2)
        self.assertEqual(len(result['tracks'][0]['channels']),3)
        self.assertTrue(set(result['tracks'][0]['channels']).isdisjoint(result['tracks'][1]['channels']))

    def test_channels_skip_percussion_and_capacity_fails_without_file(self):
        notes=[dict(start=0,end=1,pitch=60,track=i) for i in range(15)]
        r=self.write(evidence(notes));self.assertEqual([t['channels'][0] for t in r['tracks']],list(midi.CHANNELS))
        notes.append(dict(start=0,end=1,pitch=60,track=15))
        with self.assertRaisesRegex(ValueError,'15'):self.write(evidence(notes),'overflow.mid')
        self.assertFalse((self.root/'overflow.mid').exists())
        notes=[dict(start=0,end=1,pitch=60,track=0) for i in range(16)]
        with self.assertRaisesRegex(ValueError,'15'):self.write(evidence(notes),'overlaps.mid')

    def test_malformed_notes_explicit_diagnostics(self):
        invalid=[None,{},dict(start=-1,end=2,pitch=60,track=0),dict(start=1,end=0,pitch=60,track=0),
                 dict(start=1,end=1,pitch=60,track=0),dict(start=0,end=1,pitch=128,track=0),
                 dict(start=0,end=1,pitch=60,track=None),dict(start=0,end=1,pitch=True,track=0)]
        e=self.native([None,dict(time=0,notes=['bad',dict(pitch=60,track=0,end_time=1)])])
        r=self.write(e);self.assertEqual(r['note_count'],1);self.assertEqual(len(r['diagnostics']),2)
        e=evidence(invalid+evidence()['transcription']['notes']);r=self.write(e,'normalized.mid')
        self.assertEqual(r['note_count'],1);self.assertEqual(len(r['diagnostics']),len(invalid)+1)

    def test_empty_notes_and_old_project_unavailable(self):
        for value in (None,{},evidence([])):
            self.assertFalse(midi.available(value))
        with self.assertRaisesRegex(ValueError,'No valid'):self.write(evidence([]))

    def test_empty_native_event_does_not_add_phantom_track(self):
        e=self.native([dict(time=0,notes=[]),dict(time=1,notes=[dict(end_time=2,pitch=60,track=4)])],False)
        r=self.write(e);self.assertEqual([t['name'] for t in r['tracks']],['SheetSage Track 4'])

    def test_nonfinite_notes_are_skipped_and_diagnostics_are_json_safe(self):
        notes=evidence()['transcription']['notes']+[dict(start=float('nan'),end=1,pitch=60,track=0),dict(start=0,end=float('inf'),pitch=60,track=0)]
        r=self.write(evidence(notes));self.assertEqual(r['note_count'],1)
        self.assertEqual(len(r['diagnostics']),3);json.dumps(r,allow_nan=False)

    def test_missing_ambiguous_or_invalid_tempo_never_defaults(self):
        for entries in ([],[dict(value='1/4=0')],[dict(value='1/4=120',voice='Vocal')],
                        [dict(value='1/4=120'),dict(value='1/4=90')],[dict(value='abc')]):
            e=evidence();e['transcription']['tempo']=entries
            self.assertFalse(midi.available(e))
            with self.assertRaises(ValueError):self.write(e)

    def test_initial_meter_key_and_untimed_changes_not_guessed(self):
        e=evidence();e['transcription']['meters'].append(dict(value='3/4',voice='Vocal',line=40))
        e['transcription']['keys'].append(dict(value='Eb',voice='Vocal',column=20))
        r=self.write(e);self.assertEqual(r['meters'],[dict(time=0,numerator=4,denominator=4)])
        self.assertEqual(r['keys'],[dict(time=0,key='C')])

    def test_native_timed_metadata_and_proven_track_names(self):
        e=self.native([dict(time=.02,tokens_by_field=dict(rhythm=[30537],key=[31002]),notes=[dict(end_time=1,pitch=60,track=0)]),
                       dict(time=2,tokens_by_field=dict(rhythm=[30531],key=[30991]),notes=[dict(end_time=3,pitch=63,track=1)])])
        r=self.write(e)
        self.assertEqual(r['meters'],[dict(time=.02,numerator=4,denominator=4),dict(time=2,numerator=3,denominator=4)])
        self.assertEqual(r['keys'],[dict(time=.02,key='Dm'),dict(time=2,key='Eb')])
        self.assertEqual([t['name'] for t in r['tracks']],['Vocal Melody','Instrumental Melody'])

    def test_unknown_runtime_cannot_decode_tokens_or_invent_names(self):
        e=self.native([dict(time=0,tokens_by_field=dict(rhythm=[30531],key=[30991]),notes=[dict(end_time=1,pitch=60,track=0)])],False)
        r=self.write(e);self.assertEqual(r['tracks'][0]['name'],'SheetSage Track 0')
        self.assertEqual(r['keys'][0]['key'],'C');self.assertEqual(r['meters'][0]['numerator'],4)

    def test_source_artifact_hashes_and_abc_unchanged_no_abc_read(self):
        e=self.native([dict(time=0,notes=[dict(end_time=1,pitch=60,track=0)])])
        abc=self.root/'music_analysis/sheetsage/run/native/score.abc';abc.write_bytes(b'NOT PARSEABLE ABC')
        before=copy.deepcopy(e);files={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('comfymax_audio_chunker.music.sheetsage.analyze',side_effect=AssertionError('rerun')):
            self.write(e)
        self.assertEqual(e,before)
        for p,payload in files.items():self.assertEqual(p.read_bytes(),payload)

    def test_corrupt_events_fail_no_output(self):
        e=self.native([dict(time=0,notes=[dict(end_time=1,pitch=60,track=0)])])
        (self.root/e['raw_artifacts'][0]['path']).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'checksum'):self.write(e)
        self.assertFalse((self.root/'song.mid').exists())

    def test_filename_suffix_and_source_protection(self):
        self.assertEqual(midi.filename('Cuando salí de España'),'Cuando salí de España.mid')
        self.assertEqual(midi.filename('CON'),'_CON.mid')
        self.assertNotIn('/',midi.filename('bad/name'))
        self.write(name='no-extension');self.assertTrue((self.root/'no-extension.mid').exists())
        for name in ('score.abc','music_analysis/x.mid','audio/x.mid','recovery/x.mid'):
            with self.assertRaises(ValueError):self.write(name=name)

    def test_existing_file_and_atomic_failure(self):
        p=self.root/'song.mid';p.write_bytes(b'original')
        with self.assertRaises(FileExistsError):self.write()
        with patch.object(midi,'verify',side_effect=ValueError('verification failure')):
            with self.assertRaises(ValueError):self.write(overwrite=True)
        self.assertEqual(p.read_bytes(),b'original')
        self.write(overwrite=True);self.assertEqual(p.read_bytes()[:4],b'MThd')

    def test_no_clobber_race(self):
        original=midi.os.link
        def racing_link(source,target):
            Path(target).write_bytes(b'racing writer');return original(source,target)
        with patch.object(midi.os,'link',side_effect=racing_link),self.assertRaises(FileExistsError):self.write()
        self.assertEqual((self.root/'song.mid').read_bytes(),b'racing writer')
        self.assertFalse(list(self.root.glob('.midi-export-*')))

    def test_subtick_positive_duration_is_explicit(self):
        r=self.write(evidence([dict(start=0,end=.000001,pitch=60,track=0)]))
        self.assertIn('sub-tick',r['diagnostics'][-1]['reason'])
        self.assertGreater(r['timing']['midi_note_duration_seconds'],0)

    def test_readback_detects_tampering(self):
        e=music.musical_events(evidence(),self.root);payload,notes,meta,tracks,adj,step=midi.build(e)
        f=mido.MidiFile(file=io.BytesIO(payload));next(m for m in f.tracks[1] if m.type=='note_on').note=61
        stream=io.BytesIO();f.save(file=stream)
        with self.assertRaisesRegex(ValueError,'note off'):midi.verify(stream.getvalue(),notes,meta,tracks,step)

    def test_optional_dependency_missing_disables_only_export(self):
        with patch.object(midi.importlib.util,'find_spec',return_value=None):self.assertFalse(midi.available(evidence()))


class MidiEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        import test_music
        cls.app=QApplication.instance() or QApplication([])
        cls.fixture=test_music.MusicPipelineTests;cls.fixture.setUpClass()

    def setUp(self):
        from comfymax_audio_chunker.editor.marker_app import MarkerEditor,open_audio
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        doc=self.fixture().project(self.root);self.project=doc.root;doc.close()
        self.window=MarkerEditor();self.window.loaded(open_audio(self.project))

    def tearDown(self):
        self.window.dirty=False;self.window.close();self.temp.cleanup()

    def attach(self):
        w=self.window
        w.doc.data['music_analysis']=copy.deepcopy(self.fixture.result)
        w.doc.data['music_analysis']['analysis']['source_sha256']=w.doc.data['assets']['mix']['sha256']
        w.doc.data['music_analysis']['sheet_sage']=evidence()
        w.refresh_music()

    def test_old_project_without_sheetsage_is_disabled(self):
        self.assertFalse(self.window.export_midi_button.isEnabled())
        self.window.doc.save()

    def test_click_no_rerun_save_reopen_export_and_provenance(self):
        from comfymax_audio_chunker.editor.project import Document
        self.attach();w=self.window;before=copy.deepcopy(w.doc.data['music_analysis'])
        self.assertTrue(w.export_midi_button.isEnabled())
        with patch('comfymax_audio_chunker.editor.music_panel.QFileDialog.getSaveFileName',return_value=(str(self.root/'ui.mid'),'')),\
             patch('comfymax_audio_chunker.editor.music_panel.QMessageBox.exec',return_value=0),\
             patch('comfymax_audio_chunker.editor.music_panel.sheetsage.analyze',side_effect=AssertionError('rerun')):
            w.export_midi_button.click()
        self.assertTrue((self.root/'ui.mid').exists());self.assertEqual(w.doc.data['music_analysis'],before)
        self.assertEqual(w.doc.data['midi_export']['filename'],'ui.mid');self.assertTrue(w.save(True))
        w.doc.close();w.doc=Document.open(self.project)
        result=midi.export(w.doc.data['music_analysis']['sheet_sage'],w.doc.root,self.root/'second.mid')
        self.assertEqual(result['note_count'],1);self.assertEqual(w.doc.data['music_analysis'],before)
        self.assertEqual(w.doc.data['midi_export']['filename'],'ui.mid')

    def test_cancel_save_and_overwrite_no_leave_file_untouched(self):
        from PySide6.QtWidgets import QMessageBox
        self.attach();w=self.window
        for choice in ('',str(self.root/'existing')):
            p=self.root/'existing.mid';p.write_bytes(b'original')
            with patch('comfymax_audio_chunker.editor.music_panel.QFileDialog.getSaveFileName',return_value=(choice,'')),\
                 patch('comfymax_audio_chunker.editor.music_panel.QMessageBox.question',return_value=QMessageBox.No):
                w.export_midi_button.click()
            self.assertEqual(p.read_bytes(),b'original');self.assertNotIn('midi_export',w.doc.data)

    def test_project_load_busy_transition_restores_export(self):
        self.attach();self.window.set_busy(True)
        self.window.refresh_music();self.assertFalse(self.window.export_midi_button.isEnabled())
        self.window.set_busy(False);self.assertTrue(self.window.export_midi_button.isEnabled())

    def test_busy_action_does_not_export(self):
        self.attach();self.window.busy=True
        with patch('comfymax_audio_chunker.editor.music_panel.QFileDialog.getSaveFileName',side_effect=AssertionError('busy')):
            self.window.export_sheet_midi()
        self.window.busy=False


if __name__=='__main__':unittest.main()
