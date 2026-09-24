import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from comfymax_audio_chunker.music import rhythm, beat_backend
from comfymax_audio_chunker.music.model import Settings, validate, replacement
from comfymax_audio_chunker.music.pipeline import analyze, Cancelled


def response():
    beats = [.952 + i * 2/3 for i in range(16)]
    return dict(protocol=beat_backend.PROTOCOL, success=True, beats=beats,
                downbeats=beats[::4], bpm=90., beat_unit='1/4',
                meter=dict(numerator=4, denominator=4), meter_reliable=True,
                downbeats_reliable=True, metadata=dict(name='model-fixture'))


class RhythmBackendTests(unittest.TestCase):
    def setUp(self):
        self.rate = 48000
        self.audio = np.ones(12 * self.rate, dtype='float32') * .1
        self.audio[:self.rate] = 0
        self.config = dict(backend='beat-transformer')

    def run_fixture(self, value=None):
        with patch.object(beat_backend, 'infer', return_value=value or response()):
            return rhythm.analyze_rhythm(self.audio[::2], Settings(), self.rate, len(self.audio),
                source_audio=(self.audio, self.rate), config=self.config)

    def test_configured_backend_and_master_clock(self):
        original = rhythm.master_beats
        with patch.object(rhythm, 'master_beats', wraps=original) as convert:
            data = self.run_fixture()
        self.assertEqual(convert.call_count, 2)
        self.assertEqual(data['beats'][0]['frame'], round(.952*self.rate))
        self.assertEqual(data['first_downbeat'], dict(beat_id='b1', source='auto'))
        self.assertEqual(data['meter'], dict(numerator=4, denominator=4, source='auto'))
        self.assertIsNone(data['tempo']['librosa_bpm'])
        self.assertEqual(data['tempo']['detector_bpm'], 90.)
        self.assertEqual(data['provenance']['selected_backend'], 'beat-transformer')

    def test_prefix_tail_and_no_invented_zero_beat(self):
        value = response(); value['beats'].insert(0, .1)
        data = self.run_fixture(value)
        self.assertEqual(data['provenance']['removed_prefix_beats'], 1)
        rows = list(rhythm.intervals(data['beats'],len(self.audio)))
        self.assertEqual(rows[0], (0,round(.952*self.rate),None,'prefix'))
        self.assertEqual(rows[-1][3], 'tail')
        self.assertEqual(list(rhythm.intervals([],100)), [(0,100,None,'unmetered')])

    def test_missing_or_unreliable_downbeats_stay_unknown(self):
        for downbeats, flag in [([],True),([.952],True),(response()['downbeats'],False),([.2,3.,6.],True)]:
            value=response(); value.update(downbeats=downbeats,downbeats_reliable=flag)
            data=self.run_fixture(value)
            self.assertEqual(data['first_downbeat'],dict(beat_id=None,source='default'))
            self.assertEqual(data['meter']['source'],'default')

    def test_meter_absent_invalid_or_inconsistent_stays_default(self):
        for meter in (None,{},dict(numerator=3,denominator=4),dict(numerator=6,denominator=8),dict(numerator=True,denominator=4)):
            value=response(); value['meter']=meter
            self.assertEqual(self.run_fixture(value)['meter'],dict(numerator=4,denominator=4,source='default'))
        value=response(); value['meter_reliable']=False
        self.assertEqual(self.run_fixture(value)['meter']['source'],'default')

    def test_three_four_meter(self):
        value=response(); value['downbeats']=value['beats'][::3]; value['meter']['numerator']=3
        self.assertEqual(self.run_fixture(value)['meter'],dict(numerator=3,denominator=4,source='auto'))

    def test_unavailable_and_malformed_fall_back_to_existing_librosa(self):
        for field, bad in [('beats',[1,float('nan')]),('beats',[2,1]),('beats',[]),('bpm',-1),('beat_unit','1/8'),('downbeats',[100])]:
            value=response(); value[field]=bad
            with patch.object(beat_backend,'infer',return_value=value), patch.object(rhythm,'analyze',return_value=(120.,rhythm.master_beats([1,1.5],self.rate,len(self.audio)))) as old:
                data=rhythm.analyze_rhythm(self.audio,Settings(),self.rate,len(self.audio),config=self.config)
            old.assert_called_once()
            self.assertEqual(data['provenance']['selected_backend'],'librosa')
            self.assertTrue(data['provenance']['fallback_reason'])
        with patch.object(beat_backend,'infer',side_effect=beat_backend.BackendUnavailable('missing model')), patch.object(rhythm,'analyze',return_value=(None,[])):
            data=rhythm.analyze_rhythm(self.audio,Settings(),self.rate,len(self.audio),config=self.config)
        self.assertIn('missing model',data['warnings'][0])

    def test_internal_programming_errors_not_swallowed(self):
        with patch.object(beat_backend,'infer',side_effect=TypeError('internal bug')):
            with self.assertRaisesRegex(TypeError,'internal bug'):
                rhythm.analyze_rhythm(self.audio,Settings(),self.rate,len(self.audio),config=self.config)

    def test_explicit_librosa_does_not_launch_worker(self):
        with patch.object(beat_backend,'infer') as infer, patch.object(rhythm,'analyze',return_value=(None,[])):
            data=rhythm.analyze_rhythm(self.audio,Settings(),self.rate,len(self.audio),config=dict(backend='librosa'))
        infer.assert_not_called(); self.assertIsNone(data['provenance']['fallback_reason'])

    def test_configuration_missing_and_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'config.json'
            with patch.dict(os.environ,COMFYMAX_RHYTHM_CONFIG=str(p)):
                with self.assertRaises(beat_backend.BackendUnavailable): beat_backend.configuration()
                p.write_text('[]')
                with self.assertRaises(beat_backend.BackendUnavailable): beat_backend.configuration()
                p.write_text('{"backend":"beat-transformer"}')
                self.assertEqual(beat_backend.configuration()['backend'],'beat-transformer')

    def test_silent_input_does_not_acquire_neural_grid(self):
        with patch.object(beat_backend,'infer',return_value=response()), patch.object(rhythm,'analyze',return_value=(None,[])):
            data=rhythm.analyze_rhythm(np.zeros(12*22050),Settings(),22050,12*22050,config=self.config)
        self.assertEqual(data['beats'],[])
        self.assertEqual(data['first_downbeat']['source'],'default')

    def test_worker_watchdog_without_model_dependencies(self):
        import subprocess
        from comfymax_audio_chunker.music import beat_worker
        with tempfile.TemporaryDirectory() as directory:
            cancel=Path(directory)/'cancel'; cancel.touch()
            command='import runpy,time; m=runpy.run_path('+repr(str(Path(beat_worker.__file__)))+'); m["watch_request"]('+repr(dict(cancel_path=str(cancel),timeout_seconds=5))+'); time.sleep(10)'
            result=subprocess.run([sys.executable,'-c',command],timeout=5,capture_output=True)
            self.assertEqual(result.returncode,130)

    def test_worker_paths_with_spaces(self):
        self.assertEqual(beat_backend.worker_path('D:\\My Audio\\song.wav','wsl'),'/mnt/d/My Audio/song.wav')
        with self.assertRaises(beat_backend.BackendUnavailable): beat_backend.worker_path('x','bad')

    def test_real_process_protocol_failure_timeout_and_cancel(self):
        audio=np.zeros(1000,dtype='float32')
        with tempfile.TemporaryDirectory() as directory, patch.object(beat_backend,'CONFIG',Path(directory)/'config.json'):
            valid='import json,sys; r=json.load(sys.stdin); print(json.dumps('+repr(response())+'))'
            config=dict(command=[sys.executable,'-c',valid])
            self.assertEqual(beat_backend.infer(audio,44100,config)['beats'],response()['beats'])
            with self.assertRaises(beat_backend.BackendUnavailable):
                beat_backend.infer(audio,44100,dict(command=[str(Path(directory)/'missing-python.exe')]))
            for code in ('print("bad")','raise RuntimeError("fixture failure")','print("{}")'):
                with self.assertRaises(beat_backend.BackendUnavailable):
                    beat_backend.infer(audio,44100,dict(command=[sys.executable,'-c',code]))
            with self.assertRaisesRegex(beat_backend.BackendUnavailable,'timed out'):
                beat_backend.infer(audio,44100,dict(command=[sys.executable,'-c','import time; time.sleep(10)'],timeout_seconds=1))
            def cancel(): raise Cancelled('fixture')
            with self.assertRaises(Cancelled): beat_backend.infer(audio,44100,config,cancel)
            self.assertEqual(list((Path(directory)/'rhythm-worker').iterdir()),[])


class RhythmPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rate=22050; t=np.arange(rate*12)/rate
        wave=(.1*np.sin(2*np.pi*261.626*t)).astype('float32'); wave[:rate]=0
        cls.wave=wave[:,None]
        with patch.object(beat_backend,'infer',return_value=response()):
            cls.result=analyze(wave[:,None],rate,rhythm_config=dict(backend='beat-transformer'))

    def test_pipeline_auto_structure_and_json(self):
        data=self.result
        validate(data,data['timeline']); json.dumps(data,allow_nan=False)
        self.assertEqual(data['first_downbeat']['source'],'auto')
        self.assertEqual(data['meter']['source'],'auto')
        self.assertTrue(data['bars']); self.assertTrue(data['bars'][0]['pickup'])
        self.assertEqual(data['raw_predictions'][0]['interval_kind'],'prefix')
        self.assertEqual(data['raw_predictions'][-1]['end_frame'],data['timeline']['frames'])

    def test_legacy_documents_without_new_metadata_still_validate(self):
        from comfymax_audio_chunker.music.temporal import apply_structure
        data=copy.deepcopy(self.result)
        data['analysis'].pop('rhythm_backend'); data['analysis']['algorithm_version']='1.2'
        data['tempo']=dict(bpm=90.,beat_unit='1/4')
        data=apply_structure(data,meter=dict(numerator=4,denominator=4,source='default'),first_downbeat=dict(beat_id=None,source='default'))
        validate(data,data['timeline'])
        data['analysis']['algorithm_version']='1.0'
        validate(data,data['timeline'])

    def test_auto_evidence_persistence_save_as_and_recovery(self):
        import soundfile as sf
        from comfymax_audio_chunker.editor.project import Document
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); run=root/'run'; run.mkdir()
            for name in ('analysis_mix','vocals'):
                sf.write(run/(name+'.wav'),np.repeat(self.wave,2,axis=1),22050,subtype='FLOAT')
            source=dict(schema_version='1.0',stage=1,source=dict(path='fixture.wav'),
                timeline=dict(analysis_frames=len(self.wave),analysis_sample_rate=22050),
                artifacts=dict(analysis_mix='analysis_mix.wav',vocals='vocals.wav'),segments=[],words=[],regions=[])
            path=run/'analysis.json'; path.write_text(json.dumps(source),encoding='utf-8')
            doc=Document.create(path,root/'project')
            boundaries=copy.deepcopy(doc.data['chunk_boundaries'])
            data=copy.deepcopy(self.result); data['analysis']['source_sha256']=doc.data['assets']['mix']['sha256']
            doc.data['music_analysis']=data; doc.save()
            self.assertEqual(boundaries,doc.data['chunk_boundaries'])
            clone=doc.save_as(root/'copy'); self.assertEqual(clone.data['music_analysis'],data); clone.close()
            path=doc.root; doc.close(); doc=Document.open(path)
            self.assertEqual(doc.data['music_analysis'],data)
            doc.data['music_analysis']['analysis']['rhythm_backend']['detected_downbeats'][0]['frame']=-1
            with self.assertRaises(ValueError): doc.save()
            doc.close(); doc=Document.open(path)
            self.assertEqual(doc.data['music_analysis'],data); doc.close()

    def test_corrupt_master_clock_provenance_rejected(self):
        for key in ('detected_downbeats','aligned_downbeats'):
            data=copy.deepcopy(self.result)
            data['analysis']['rhythm_backend'][key][0]['seconds']+=.001
            with self.assertRaises(ValueError): validate(data,data['timeline'])

    def test_manual_protection_unchanged(self):
        data=copy.deepcopy(self.result); data['meter']['source']='manual'
        with self.assertRaises(ValueError): replacement(data,self.result)
        data=copy.deepcopy(self.result); data['first_downbeat']['source']='manual'
        with self.assertRaises(ValueError): replacement(data,self.result)


if __name__=='__main__': unittest.main()

