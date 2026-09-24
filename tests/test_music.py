import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from comfymax_audio_chunker.music.model import (Settings, chord, label, effective_chord,
    frame_to_seconds, seconds_to_frame, validate, replacement)
from comfymax_audio_chunker.music.chords import templates, recognize
from comfymax_audio_chunker.music.rhythm import master_beats
from comfymax_audio_chunker.music.smoothing import smooth, regions
from comfymax_audio_chunker.music.tonal import select_channel, estimate_key
from comfymax_audio_chunker.music.pipeline import analyze, Cancelled
from comfymax_audio_chunker.editor.project import Document


class MusicTests(unittest.TestCase):
    def test_master_clock_roundtrip(self):
        for rate in (44100,48000):
            for frame in (0,1,22049,123456789):
                self.assertEqual(seconds_to_frame(frame_to_seconds(frame,rate),rate),frame)
        with self.assertRaises(ValueError): seconds_to_frame(float('nan'),44100)

    def test_beat_order_deduplication_and_bounds(self):
        beats=master_beats([1,.5,.500000001,0,2,float('nan')],44100,88200)
        self.assertEqual([b['frame'] for b in beats],[0,22050,44100])
        self.assertEqual([b['index'] for b in beats],[1,2,3])

    def test_all_major_minor_templates(self):
        values,bank=templates(); self.assertEqual(bank.shape,(60,12))
        for expected,vector in zip(values[:24],bank[:24]):
            actual,score,margin,candidates,_=recognize(vector,.1,.001,Settings())
            self.assertEqual(actual,expected); self.assertAlmostEqual(score,1)
            self.assertGreater(margin,.1); self.assertEqual(len(candidates),5)

    def test_silence_and_ambiguity(self):
        self.assertEqual(recognize(np.zeros(12),0,.001,Settings())[0]['quality'],'N')
        self.assertEqual(recognize(np.ones(12),.1,.001,Settings())[0]['quality'],'unknown')

    def test_antiphase_preserves_signal(self):
        x=np.sin(np.arange(1000)*.1).astype('float32')
        y,index=select_channel(np.column_stack([x,-x]))
        np.testing.assert_array_equal(y,x); self.assertEqual(index,0)

    def test_key_profiles_all_transpositions(self):
        for mode,profile in [('major',[6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88]),
                             ('minor',[6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])]:
            for root in range(12):
                result=estimate_key(np.roll(profile,root)[:,None],np.array([.1]),.001)
                self.assertEqual((result['root_pc'],result['mode']),(root,mode))

    def sequence(self,strong):
        values,_=templates(); c=0; other=7 if strong else 21
        scores=np.zeros((5,len(values))); scores[:,c]=.9
        scores[2,c]=.15 if strong else .30; scores[2,other]=.94 if strong else .31
        raw=[dict(id=f'p{i}',start_frame=i*100,end_frame=(i+1)*100,
                  raw=values[other if i==2 else c]) for i in range(5)]
        return raw,scores

    def test_weak_isolated_chord_and_raw_preservation(self):
        raw,scores=self.sequence(False); before=copy.deepcopy(raw)
        sequence,selected=smooth(raw,scores,.12)
        self.assertEqual([label(v) for v in sequence],['C']*5); self.assertEqual(raw,before)
        merged=regions(raw,sequence,selected,100)
        self.assertEqual(len(merged),1); self.assertEqual(merged[0]['raw_ids'],[r['id'] for r in raw])
        self.assertEqual(merged[0]['end_frame'],500)

    def test_strong_single_beat_preserved(self):
        raw,scores=self.sequence(True); sequence,_=smooth(raw,scores,.12)
        self.assertEqual([label(v) for v in sequence],['C','C','G','C','C'])

    def test_manual_precedence(self):
        r=dict(**chord(0,'maj'),start_frame=0,end_frame=100,manual=dict(label='Am',start_frame=5,end_frame=None))
        self.assertEqual(effective_chord(r),dict(label='Am',start_frame=5,end_frame=100))

    def test_cancel_before_work(self):
        with self.assertRaises(Cancelled): analyze(np.zeros((10,2)),44100,cancelled=lambda:True)


class MusicPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Real librosa path on an antiphase C-major chord plus rhythmic clicks.
        rate=44100; t=np.arange(rate*4)/rate
        tone=sum(np.sin(2*np.pi*f*t) for f in (261.6256,329.6276,391.9954))*.08
        for frame in range(0,len(t),rate//2): tone[frame:frame+80] += np.hanning(80)*.3
        cls.wave=np.column_stack([tone,-tone]).astype('float32')
        cls.result=analyze(cls.wave,rate,'fixture')

    def test_actual_pipeline_json_and_clock(self):
        data=self.result; validate(data,data['timeline']); json.dumps(data,allow_nan=False)
        self.assertEqual(data['timeline']['frames'],len(self.wave))
        self.assertEqual(data['first_downbeat'],dict(beat_id=None,source='default'))
        self.assertEqual(data['raw_predictions'][-1]['end_frame'],len(self.wave))
        self.assertTrue(any(label(r)=='C' for r in data['regions']))
        self.assertTrue(all(len(r['chroma'])==12 for r in data['raw_predictions']))

    def test_silence_actual_pipeline(self):
        data=analyze(np.zeros((4410,2),dtype='float32'),44100)
        self.assertEqual(data['key']['mode'],'unknown')
        self.assertTrue(all(r['quality']=='N' for r in data['regions']))
        self.assertIsNone(data['tempo']['bpm'])

    def test_structural_rerun_protects_raw_and_manual(self):
        from comfymax_audio_chunker.music.temporal import apply_structure
        original=copy.deepcopy(self.result)
        beat=original['beats'][0]['id']
        changed=apply_structure(original,dict(numerator=3,denominator=4,source='manual'),dict(beat_id=beat,source='manual'))
        self.assertTrue(changed['bars'])
        self.assertEqual(original,self.result)
        derived=('initial_smoothed','temporal_extension_evidence','unknown_resolution','stabilization_reason')
        for old,new in zip(original['raw_predictions'],changed['raw_predictions']):
            self.assertEqual({k:v for k,v in old.items() if k not in derived},{k:v for k,v in new.items() if k not in derived})
        validate(changed,changed['timeline']); json.dumps(changed,allow_nan=False)
        changed['regions'][0]['manual']['label']='Manual C'
        saved=copy.deepcopy(changed)
        with self.assertRaisesRegex(ValueError,'Manual chord'):
            apply_structure(changed)
        self.assertEqual(changed,saved)

    def test_phase11_data_still_loads_and_restructures(self):
        from comfymax_audio_chunker.music.temporal import apply_structure
        value=copy.deepcopy(self.result)
        value['analysis']['algorithm_version']='1.1'
        value['analysis'].pop('structural_algorithm_version',None)
        value['analysis']['settings']={k:v for k,v in value['analysis']['settings'].items() if k in (
            'analysis_rate','hop_length','silence_db','relative_silence_db','min_similarity','min_margin',
            'transition_penalty','top_candidates','harmonic_margin','extension_min_relative_energy',
            'extension_min_score_gain','tempo_min_intervals','tempo_max_relative_mad','tempo_inlier_tolerance',
            'tempo_min_inlier_fraction','tempo_min_bpm','tempo_max_bpm')}
        value['tempo'].pop('span_bpm',None); value['tempo'].pop('span_intervals',None)
        value['tempo']['source']='grid'; value['tempo']['bpm']=value['tempo']['effective_bpm']=value['tempo']['grid_bpm']
        for name in ('bars','bar_context_disabled_reason','initial_regions'): value.pop(name,None)
        for row in value['raw_predictions']:
            for name in ('initial_smoothed','temporal_extension_evidence','unknown_resolution','stabilization_reason'): row.pop(name,None)
        for region in value['regions']:
            for name in ('triad_family','temporal_extension_evidence','unknown_resolution','stabilization_reason'): region.pop(name,None)
        validate(value,value['timeline'])
        with tempfile.TemporaryDirectory() as temp:
            doc=self.project(Path(temp)); root=doc.root
            value['analysis']['source_sha256']=doc.data['assets']['mix']['sha256']
            doc.data['music_analysis']=value; doc.save(); doc.close()
            doc=Document.open(root); self.assertEqual(doc.data['music_analysis'],value); doc.close()
        result=apply_structure(value,first_downbeat=dict(beat_id=value['beats'][0]['id'],source='manual'))
        self.assertTrue(result['bars'])

    def test_invalid_structural_metadata_rejected(self):
        from comfymax_audio_chunker.music.temporal import apply_structure
        result=apply_structure(self.result,first_downbeat=dict(beat_id=self.result['beats'][0]['id'],source='manual'))
        result['bars'][0]['beat_ids']=['invalid']
        with self.assertRaises(ValueError): validate(result,result['timeline'])

    def test_gui_manual_downbeat_uses_stored_features(self):
        from PySide6.QtWidgets import QApplication
        from comfymax_audio_chunker.editor.marker_app import MarkerEditor,open_audio
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            doc=self.project(Path(temp)); root=doc.root; doc.close()
            window=MarkerEditor(); window.loaded(open_audio(root)); window.show()
            value=copy.deepcopy(self.result); value['analysis']['source_sha256']=window.doc.data['assets']['mix']['sha256']
            window.doc.data['music_analysis']=value; window.refresh_music()
            markers=copy.deepcopy(window.state)
            with patch('comfymax_audio_chunker.editor.music_panel.analyze',side_effect=AssertionError('Audio analysis must not run')):
                window.music_downbeat.setValue(1); window.music_meter_numerator.setValue(3)
                window.music_structure_button.click()
            actual=window.doc.data['music_analysis']
            self.assertEqual(actual['first_downbeat']['source'],'manual'); self.assertTrue(actual['bars'])
            self.assertEqual(actual['meter']['numerator'],3)
            self.assertEqual(window.state,markers)
            self.assertIn('Span BPM:',window.music_summary.text()); self.assertIn('Meter: 3/4',window.music_summary.text())
            self.assertTrue(window.save()); window.close(); app.processEvents()
            doc=Document.open(root); self.assertEqual(doc.data['music_analysis'],actual); doc.close()

    def test_bad_recovery_data_rejected(self):
        data=copy.deepcopy(self.result); data['regions'][0]['raw_ids']=['missing']
        with self.assertRaises(ValueError): validate(data,data['timeline'])

    def test_phase1_tempo_and_settings_roundtrip(self):
        value=copy.deepcopy(self.result)
        value['analysis']['algorithm_version']='1.0'
        value['tempo']=dict(bpm=117.45,beat_unit='1/4')
        old_fields=('analysis_rate','hop_length','silence_db','relative_silence_db',
                    'min_similarity','min_margin','transition_penalty','top_candidates','harmonic_margin')
        value['analysis']['settings']={k:v for k,v in value['analysis']['settings'].items() if k in old_fields}
        for row in value['raw_predictions']:
            for candidate in row['candidates']: candidate.pop('extension_evidence',None)
        validate(value,value['timeline'])
        with tempfile.TemporaryDirectory() as temp:
            doc=self.project(Path(temp)); root=doc.root
            value['analysis']['source_sha256']=doc.data['assets']['mix']['sha256']
            doc.data['music_analysis']=value; doc.save(); doc.close()
            doc=Document.open(root)
            self.assertEqual(doc.data['music_analysis'],value); doc.close()

    def test_invalid_phase11_metadata_rejected(self):
        for field,value in [('grid_bpm',float('nan')),('stability',2),('effective_bpm',-1)]:
            data=copy.deepcopy(self.result); data['tempo'][field]=value
            with self.assertRaises(ValueError): validate(data,data['timeline'])

    def test_cancellation_during_stages(self):
        stopped=[False]
        def progress(n,message):
            if n>=10: stopped[0]=True
        with self.assertRaises(Cancelled):
            analyze(self.wave,44100,progress=progress,cancelled=lambda:stopped[0])

    def test_reanalysis_protects_overrides(self):
        data=copy.deepcopy(self.result); data['regions'][0]['manual']['label']='F'
        with self.assertRaisesRegex(ValueError,'manual corrections'): replacement(data,self.result)
        self.assertEqual(data['regions'][0]['manual']['label'],'F')

    def project(self,root):
        run=root/'run'; run.mkdir()
        for name in ('analysis_mix','vocals'): sf.write(run/(name+'.wav'),self.wave,44100,subtype='FLOAT')
        source=dict(schema_version='1.0',stage=1,source=dict(path='fixture.wav'),
                    timeline=dict(analysis_frames=len(self.wave),analysis_sample_rate=44100),
                    artifacts=dict(analysis_mix='analysis_mix.wav',vocals='vocals.wav'),segments=[],words=[],regions=[])
        path=run/'analysis.json'; path.write_text(json.dumps(source),encoding='utf-8')
        return Document.create(path,root/'project')

    def test_old_project_persistence_save_as_and_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); doc=self.project(root)
            self.assertNotIn('music_analysis',doc.data)
            before=copy.deepcopy(doc.data)
            value=copy.deepcopy(self.result); value['analysis']['source_sha256']=doc.data['assets']['mix']['sha256']
            doc.data['music_analysis']=value; doc.save()
            self.assertEqual(doc.data['chunk_boundaries'],before['chunk_boundaries'])
            clone=doc.save_as(root/'copy'); self.assertEqual(clone.data['music_analysis'],value); clone.close()
            path=doc.root; doc.close(); doc=Document.open(path)
            self.assertEqual(doc.data['music_analysis'],value)
            good=copy.deepcopy(doc.data)
            doc.data['music_analysis']['regions'][0]['end_frame']=-1
            with self.assertRaises(ValueError): doc.save()
            doc.close(); doc=Document.open(path)
            self.assertEqual(doc.data['music_analysis'],good['music_analysis']); doc.close()

    def test_gui_background_results_and_markers_unchanged(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest
        from comfymax_audio_chunker.editor.marker_app import MarkerEditor,open_audio
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            doc=self.project(Path(temp)); path=doc.root; doc.close()
            window=MarkerEditor(); window.loaded(open_audio(path)); window.show()
            value=copy.deepcopy(self.result); value['analysis']['source_sha256']=window.doc.data['assets']['mix']['sha256']
            state=copy.deepcopy(window.state)
            with patch('comfymax_audio_chunker.editor.music_panel.analyze',return_value=value):
                window.analyze_music()
                for _ in range(500):
                    QTest.qWait(10)
                    if window.music_job is None: break
            self.assertIsNone(window.music_job); self.assertEqual(window.state,state)
            self.assertIn('Librosa BPM:',window.music_summary.text())
            self.assertIn('Grid BPM:',window.music_summary.text())
            self.assertIn('Effective BPM:',window.music_summary.text())
            self.assertEqual(window.music_table.rowCount(),len(value['raw_predictions']))
            self.assertTrue(window.save()); self.assertIn('music_analysis',window.doc.data)
            # A second successful analysis must also preserve existing markers.
            with patch('comfymax_audio_chunker.editor.music_panel.analyze',return_value=value):
                window.analyze_music()
                for _ in range(500):
                    QTest.qWait(10)
                    if window.music_job is None: break
            self.assertIsNone(window.music_job); self.assertEqual(window.state,state)
            with patch('comfymax_audio_chunker.editor.music_panel.analyze',side_effect=ValueError('test failure')), patch('comfymax_audio_chunker.editor.music_panel.QMessageBox.warning'):
                window.analyze_music()
                for _ in range(500):
                    QTest.qWait(10)
                    if window.music_job is None: break
            self.assertEqual(window.doc.data['music_analysis'],value); self.assertEqual(window.state,state)
            self.assertTrue(window.save())
            window.close(); app.processEvents()

