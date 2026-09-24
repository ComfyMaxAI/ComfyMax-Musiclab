import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from comfymax_audio_chunker.music import chord_backend,neural_chords as neural
from comfymax_audio_chunker.music.model import validate,label,replacement
from comfymax_audio_chunker.music.pipeline import analyze,Cancelled
from comfymax_audio_chunker.music.rhythm import master_beats
from comfymax_audio_chunker.music.temporal import apply_structure


def response():
    return dict(protocol=chord_backend.PROTOCOL,success=True,backend='chord-cnn-lstm',
        metadata=dict(name='fixture',score_kind='unavailable'),timings={},segments=[
            dict(start_seconds=0.,end_seconds=.93,label='N',evidence=None),
            dict(start_seconds=.93,end_seconds=2.46,label='C:maj',evidence=None),
            dict(start_seconds=2.46,end_seconds=4.,label='D:min/5',evidence=None)])


def grid():
    return dict(timeline=dict(sample_rate=48000,frames=48000*6),beats=master_beats(np.arange(0,6,.5),48000,48000*6),
        tempo=dict(stability=1.,interval_count=11,fallback_reason=None),first_downbeat=dict(beat_id='b3',source='auto'),
        analysis=dict(rhythm_backend=dict(downbeats_usable=True,aligned_downbeats=[dict(beat_id='b3'),dict(beat_id='b11')])) )


class NeuralBoundaryTests(unittest.TestCase):
    def test_full_vocabulary_and_inversions(self):
        for quality in neural.QUALITIES:
            value,status=neural.map_label('C:'+quality)
            self.assertEqual(status,'mapped'); self.assertEqual(value['quality'],quality)
        for name,pc in [('C',0),('Db',1),('D',2),('Eb',3),('E',4),('F',5),('F#',6),('G',7),('Ab',8),('A',9),('Bb',10),('B',11)]:
            self.assertEqual(neural.map_label(name+':min')[0]['root_pc'],pc)
        self.assertEqual(label(neural.map_label('D:min/5')[0]),'Dm/A')
        self.assertEqual(neural.map_label('C:maj/b7')[0]['bass_pc'],10)
        self.assertEqual(neural.map_label('C:maj/E')[0]['bass_pc'],4)

    def test_n_and_unsupported_not_coerced(self):
        self.assertEqual(neural.map_label('N')[0]['quality'],'N')
        for value in ('X','C:unsupported','C:maj/99','nonsense'):
            self.assertEqual(neural.map_label(value)[0]['quality'],'unknown')

    def test_master_clock_and_original_seconds(self):
        data=response(); data['segments'][0]['end_seconds']=.930001
        data['segments'][1]['start_seconds']=.930001
        result=neural.normalize(data,48000,192000)
        self.assertEqual(result[0]['end_frame'],round(.930001*48000))
        self.assertEqual(result[0]['original_end_seconds'],.930001)
        self.assertEqual(result[0]['end_seconds'],result[0]['end_frame']/48000)

    def test_near_grid_shared_boundary_and_evidence(self):
        raw=neural.normalize(response(),48000,192000)
        before=copy.deepcopy(raw)
        result=neural.align(raw,grid(),neural.DEFAULT_ALIGNMENT)
        self.assertEqual(result[0]['end_frame'],48000)
        self.assertEqual(result[1]['start_frame'],48000)
        self.assertEqual(result[1]['alignment']['start']['grid_kind'],'downbeat')
        self.assertEqual(raw,before)

    def test_maximum_tolerance_midbar_and_n_prefix(self):
        data=response(); data['segments'][1]['end_seconds']=2.25; data['segments'][2]['start_seconds']=2.25
        result=neural.align(neural.normalize(data,48000,192000),grid(),neural.DEFAULT_ALIGNMENT)
        self.assertEqual(result[1]['end_frame'],108000)
        self.assertEqual(result[0]['quality'],'N'); self.assertEqual(result[0]['start_frame'],0)
        self.assertEqual(result[1]['alignment']['end']['reason'],'outside_tolerance')
        for before,after in zip(neural.normalize(data,48000,192000),result):
            for edge in ('start','end'): self.assertLessEqual(abs(before[edge+'_frame']-after[edge+'_frame'])/48000,.08)

    def test_downbeat_preference_is_bounded(self):
        data=grid(); data['beats'][1]['frame']=round(.98*48000); data['beats'][2]['frame']=round(1.02*48000)
        raw=response(); raw['segments'][0]['end_seconds']=.99; raw['segments'][1]['start_seconds']=.99
        settings=dict(neural.DEFAULT_ALIGNMENT,downbeat_preference_seconds=.025)
        result=neural.align(neural.normalize(raw,48000,192000),data,settings)
        self.assertEqual(result[0]['end_frame'],round(1.02*48000))
        settings['downbeat_preference_seconds']=0
        self.assertEqual(neural.align(neural.normalize(raw,48000,192000),data,settings)[0]['end_frame'],round(.98*48000))

    def test_short_change_cannot_collapse(self):
        value=response(); value['segments']=[dict(start_seconds=a,end_seconds=b,label=l) for a,b,l in [(0,.98,'N'),(.98,1.02,'C:maj'),(1.02,4,'G:maj')]]
        result=neural.align(neural.normalize(value,48000,192000),grid(),neural.DEFAULT_ALIGNMENT)
        self.assertEqual([r['original_label'] for r in result],['N','C:maj','G:maj'])
        self.assertTrue(all(r['end_frame']>r['start_frame'] for r in result))
        self.assertEqual(result[1]['alignment']['start']['reason'],'would_collapse_or_reorder_interval')

    def test_unreliable_grid_does_not_snap(self):
        data=grid(); data['tempo']['stability']=.2
        raw=neural.normalize(response(),48000,192000)
        result=neural.align(raw,data,neural.DEFAULT_ALIGNMENT)
        self.assertEqual([r['start_frame'] for r in result],[r['start_frame'] for r in raw])

    def test_malformed_segments_rejected(self):
        for update in [dict(start_seconds=-1),dict(end_seconds=float('nan')),dict(end_seconds=0),dict(label=''),dict(end_seconds=30)]:
            value=response(); value['segments'][0].update(update)
            with self.assertRaises(chord_backend.BackendUnavailable): neural.normalize(value,48000,192000)
        value=response(); value['segments'][1]['start_seconds']=.5
        with self.assertRaises(chord_backend.BackendUnavailable): neural.normalize(value,48000,192000)

    def test_config_and_actual_process_failures(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(chord_backend,'CONFIG',Path(directory)/'config.json'):
            with patch.dict(os.environ,COMFYMAX_CHORD_CONFIG=str(Path(directory)/'missing.json')):
                with self.assertRaises(chord_backend.BackendUnavailable): chord_backend.configuration()
            for command in [[str(Path(directory)/'missing.exe')],[sys.executable,'-c','raise RuntimeError("fixture")'],[sys.executable,'-c','print("not json")'],[sys.executable,'-c','print("{}")']]:
                with self.assertRaises(chord_backend.BackendUnavailable): chord_backend.infer(np.zeros(1000),22050,dict(command=command))
            code='import json,sys; json.load(sys.stdin); print('+repr(json.dumps(response()))+')'
            self.assertEqual(chord_backend.infer(np.zeros(1000),22050,dict(command=[sys.executable,'-c',code]))['segments'],response()['segments'])
            def cancel(): raise Cancelled('fixture')
            with self.assertRaises(Cancelled): chord_backend.infer(np.zeros(1000),22050,dict(command=[sys.executable,'-c',code]),cancel)


class NeuralPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t=np.arange(44100*4)/44100
        tone=.1*np.sin(t*2*np.pi*261.626); tone[:44100]=0
        cls.wave=np.column_stack([tone,tone]).astype('float32')
        cls.template=analyze(cls.wave,44100,rhythm_config=dict(backend='librosa'),chord_config=dict(backend='template'))
        with patch.object(chord_backend,'infer',return_value=response()):
            cls.result=neural.apply_backend(cls.template,cls.wave[:,0],44100,dict(backend='chord-cnn-lstm'))

    def test_backend_selection_preserves_template_evidence(self):
        validate(self.result,self.result['timeline'])
        self.assertEqual(self.result['raw_predictions'],self.template['raw_predictions'])
        self.assertEqual(self.result['template_regions'],self.template['regions'])
        self.assertEqual([label(r) for r in self.result['regions']],['N','C','Dm/A'])
        self.assertTrue(all(r['score'] is None for r in self.result['regions']))

    def test_template_selection_never_starts_worker(self):
        with patch.object(chord_backend,'infer',side_effect=AssertionError('must not launch')):
            data=neural.apply_backend(self.template,self.wave[:,0],44100,dict(backend='template'))
        self.assertEqual(data['regions'],self.template['regions'])

    def test_malformed_response_falls_back_and_gap_stays_unknown(self):
        value=response(); value['segments'][1]['start_seconds']=.5
        with patch.object(chord_backend,'infer',return_value=value):
            data=neural.apply_backend(self.template,self.wave[:,0],44100,dict(backend='chord-cnn-lstm'))
        self.assertEqual(data['analysis']['chord_backend']['selected_backend'],'template')
        self.assertEqual(data['regions'],self.template['regions'])
        value=response(); value['segments']=value['segments'][1:]
        with patch.object(chord_backend,'infer',return_value=value):
            data=neural.apply_backend(self.template,self.wave[:,0],44100,dict(backend='chord-cnn-lstm'))
        self.assertEqual(data['regions'][0]['quality'],'unknown')
        self.assertEqual(data['regions'][1]['start_frame'],round(.93*44100))
        validate(data,data['timeline'])

    def test_full_pipeline_selected_backend(self):
        with patch.object(chord_backend,'infer',return_value=response()):
            data=analyze(self.wave,44100,rhythm_config=dict(backend='librosa'),chord_config=dict(backend='chord-cnn-lstm'))
        self.assertEqual(data['raw_predictions'],self.template['raw_predictions'])
        self.assertEqual(data['beats'],self.template['beats'])
        self.assertEqual(data['first_downbeat'],self.template['first_downbeat'])
        self.assertEqual(data['analysis']['algorithm_version'],'2B')
        self.assertIn('total_analysis_seconds',data['analysis']['chord_backend']['timings'])
        validate(data,data['timeline'])

    def test_fallback_and_programming_error(self):
        for problem in [chord_backend.BackendUnavailable('model missing'),chord_backend.BackendUnavailable('bad json')]:
            with patch.object(chord_backend,'infer',side_effect=problem):
                data=neural.apply_backend(self.template,self.wave[:,0],44100,dict(backend='chord-cnn-lstm'))
            self.assertEqual(data['regions'],self.template['regions']); self.assertTrue(data['analysis']['chord_backend']['fallback_reason'])
        with patch.object(chord_backend,'infer',side_effect=TypeError('internal')):
            with self.assertRaises(TypeError): neural.apply_backend(self.template,self.wave[:,0],44100,dict(backend='chord-cnn-lstm'))

    def test_unknown_template_does_not_veto_neural(self):
        value=copy.deepcopy(self.template)
        for row in value['regions']: row.update(root_pc=None,quality='unknown',bass_pc=None)
        with patch.object(chord_backend,'infer',return_value=response()):
            data=neural.apply_backend(value,self.wave[:,0],44100,dict(backend='chord-cnn-lstm'))
        self.assertEqual(label(data['regions'][1]),'C')

    def test_manual_structure_keeps_neural_identity_and_raw(self):
        data=apply_structure(self.result,meter=dict(numerator=3,denominator=4,source='manual'))
        self.assertEqual(data['chord_detections']['raw_segments'],self.result['chord_detections']['raw_segments'])
        self.assertEqual([label(r) for r in data['regions']],[label(r) for r in self.result['regions']])
        validate(data,data['timeline'])

    def test_manual_corrections_protected(self):
        for field in ('meter','first_downbeat'):
            data=copy.deepcopy(self.result); data[field]['source']='manual'
            with self.assertRaises(ValueError): replacement(data,self.result)
        data=copy.deepcopy(self.result); data['regions'][1]['manual']['label']='G'
        validate(data,data['timeline'])
        with self.assertRaises(ValueError): replacement(data,self.result)
        with self.assertRaises(ValueError): apply_structure(data)

    def test_phase1_phase12_phase2a_compatibility(self):
        for version in ('1.0','1.2','2A'):
            data=copy.deepcopy(self.template); data['analysis'].pop('chord_backend'); data['analysis']['algorithm_version']=version
            validate(data,data['timeline'])
        path=Path(__file__).resolve().parents[1]/'.cache/nonexistent'
        self.assertFalse(path.exists())

    def test_evidence_corruption_rejected(self):
        for part in ('raw_segments','normalized_segments','aligned_segments'):
            data=copy.deepcopy(self.result)
            key='start_seconds' if part=='raw_segments' else 'start_frame'
            data['chord_detections'][part][1][key]+=1
            with self.assertRaises(ValueError): validate(data,data['timeline'])
        data=copy.deepcopy(self.result); data['regions'][1]['root_pc']=7
        with self.assertRaises(ValueError): validate(data,data['timeline'])

    def test_save_save_as_reopen_recovery(self):
        import soundfile as sf
        from comfymax_audio_chunker.editor.project import Document
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); run=root/'run'; run.mkdir()
            for name in ('analysis_mix','vocals'): sf.write(run/(name+'.wav'),self.wave,44100,subtype='FLOAT')
            source=dict(schema_version='1.0',stage=1,source=dict(path='fixture.wav'),timeline=dict(analysis_frames=len(self.wave),analysis_sample_rate=44100),artifacts=dict(analysis_mix='analysis_mix.wav',vocals='vocals.wav'),segments=[],words=[],regions=[])
            path=run/'analysis.json'; path.write_text(json.dumps(source))
            doc=Document.create(path,root/'project'); boundaries=copy.deepcopy(doc.data['chunk_boundaries'])
            value=copy.deepcopy(self.result); value['analysis']['source_sha256']=doc.data['assets']['mix']['sha256']
            doc.data['music_analysis']=value; doc.save()
            self.assertEqual(boundaries,doc.data['chunk_boundaries'])
            clone=doc.save_as(root/'copy'); self.assertEqual(clone.data['music_analysis'],value); clone.close()
            path=doc.root; doc.close(); doc=Document.open(path)
            self.assertEqual(doc.data['music_analysis'],value)
            doc.data['music_analysis']['regions'][1]['start_frame']=-1
            with self.assertRaises(ValueError): doc.save()
            doc.close(); doc=Document.open(path); self.assertEqual(doc.data['music_analysis'],value); doc.close()


if __name__=='__main__': unittest.main()
