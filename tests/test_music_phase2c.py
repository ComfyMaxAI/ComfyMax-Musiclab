import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from comfymax_audio_chunker.music import sheetsage as sheet
from comfymax_audio_chunker.editor.project import Document, atomic_json
from comfymax_audio_chunker.editor.music_panel import MusicTask
from comfymax_audio_chunker.music.pipeline import Cancelled

ABC = b'X:1\r\nM:4/4\r\nQ:1/4=111\r\nV: Vocal name="Vocal Melody"\r\nV: Ins name="Ins Melody"\r\nK:C\r\n% verse\r\nV: Vocal\r\n"C"C4|[K:E][M:2/4]"B7"B2|\r\n'
EVENTS = dict(events=[dict(time=.25, global_subbeat=2, notes=[dict(pitch=60, track=0, end_time=.75)])])


class FakeProcess:
    mode = 'success'
    returncode = 0

    def __init__(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def poll(self): return self.returncode
    def communicate(self, payload=None, **kwargs):
        request = json.loads(payload)
        output = Path(request['output_dir']); native = output/'native'; native.mkdir()
        (native/'score.abc').write_bytes(ABC)
        (native/'score.mid').write_bytes(b'MThd\x00native-midi')
        raw = json.dumps(dict(payload_hex=json.dumps(EVENTS).encode().hex())).encode()
        (native/'events.json').write_bytes(raw)
        response = dict(protocol=sheet.PROTOCOL, success=True, provenance=dict(backend='sheetsage2'),
                        artifacts=['native/score.abc','native/score.mid','native/events.json'])
        if self.mode == 'failure': response.update(success=False, error='model failure')
        if self.mode == 'bad-path': response['artifacts'] = ['../../outside.abc']
        if self.mode == 'malformed': return 'not json', ''
        if self.mode == 'nonfinite': return '{"protocol":"comfymax.sheetsage.1","success":true,"provenance":{"time":NaN},"artifacts":[]}', ''
        if self.mode == 'crash': self.returncode = 5
        return json.dumps(response), ''


class SheetSageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.audio = self.root/'mix.wav'; self.audio.write_bytes(b'audio identity')
        self.config = dict(executable='external.exe', model='existing.gguf', timeout_seconds=30)
        FakeProcess.mode = 'success'; FakeProcess.returncode = 0

    def tearDown(self): self.temp.cleanup()

    def infer(self):
        with patch.object(sheet.subprocess, 'Popen', FakeProcess):
            return sheet.analyze(self.audio, self.root, config=self.config)

    def test_unavailable_is_evidence(self):
        with patch.object(sheet, 'configuration', side_effect=ValueError('not installed')):
            result = sheet.analyze(self.audio, self.root)
        self.assertEqual(result['status'], 'unavailable'); self.assertIn('not installed', result['warnings'][0])

    def test_worker_failure_crash_malformed_and_unsafe_path(self):
        for mode in ('failure', 'crash', 'malformed', 'bad-path', 'nonfinite'):
            with self.subTest(mode=mode):
                FakeProcess.mode = mode
                value = self.infer()
                self.assertEqual(value['status'], 'failed'); self.assertTrue(value['warnings'])

    def test_success_raw_abc_native_artifacts_and_parsing(self):
        value = self.infer(); self.assertEqual(value['status'], 'success')
        artifacts = {Path(a['path']).name:a for a in value['raw_artifacts']}
        self.assertEqual((self.root/artifacts['score.abc']['path']).read_bytes(), ABC)
        self.assertEqual((self.root/artifacts['score.mid']['path']).read_bytes(), b'MThd\x00native-midi')
        self.assertEqual(artifacts['score.mid']['origin'], 'native audio.cpp')
        self.assertEqual(json.loads((self.root/artifacts['events-decoded.json']['path']).read_bytes()), EVENTS)
        parsed = value['transcription']; self.assertEqual(parsed['notes'][0]['duration'], .5)
        self.assertEqual(parsed['notes'][0]['start'], .25)
        self.assertIsNone(parsed['notes'][0]['measure']); self.assertIsNone(parsed['notes'][0]['voice'])
        self.assertEqual([v['value'] for v in parsed['meters']], ['4/4','2/4'])
        self.assertEqual([v['value'] for v in parsed['keys']], ['C','E'])
        self.assertIsNone(parsed['keys'][0]['voice'])
        self.assertEqual(parsed['keys'][1]['voice'], 'Vocal')
        self.assertEqual([v['symbol'] for v in parsed['chords']], ['C','B7'])
        self.assertEqual(len(parsed['voices']), 2)
        self.assertFalse(value['warnings'])

    def test_schema_rejects_absolute_paths_and_nested_history(self):
        value = self.infer()
        for name in ('C:/temp/score.abc','../score.abc','music_analysis/sheetsage/../escape'):
            bad = copy.deepcopy(value); bad['raw_artifacts'][0]['path'] = name
            with self.assertRaises(ValueError): sheet.validate(bad)
        value['previous_results'] = [dict(previous_results=[])]
        with self.assertRaises(ValueError): sheet.validate(value)

    def test_cancellation_propagates(self):
        def cancel(): raise Cancelled('cancelled')
        with patch.object(sheet.subprocess, 'Popen', FakeProcess), self.assertRaises(Cancelled):
            sheet.analyze(self.audio, self.root, cancel, config=self.config)

    def test_task_additive_preserves_manual_rhythm_chords_and_history(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        previous = dict(beats=[1,2], regions=[dict(manual={'label':'Dm'})], template_regions=[3],
                        chord_detections={'raw_segments':[4]}, first_downbeat={'source':'manual'})
        before = copy.deepcopy(previous)
        task = MusicTask(None, 44100, 'hash', None); task.previous = previous
        task.sheet_root = self.root; task.sheet_audio = self.audio
        values = []; task.ready.connect(values.append)
        with patch('comfymax_audio_chunker.editor.music_panel.analyze', side_effect=AssertionError('Must not rerun Phase 2A/B')), patch.object(sheet, 'analyze', return_value=self.infer()):
            task.run()
            self.assertEqual({k:v for k,v in values[-1].items() if k != 'sheet_sage'}, before)
            task.previous = values[-1]; task.run()
        self.assertEqual(len(values[-1]['sheet_sage']['previous_results']), 1)
        self.assertEqual(previous, before)

    def test_sheet_failure_does_not_discard_new_main_analysis(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        task = MusicTask(None, 44100, 'hash', None)
        task.sheet_root = self.root; task.sheet_audio = self.audio
        values = []; task.ready.connect(values.append)
        FakeProcess.mode = 'failure'
        main = dict(beats=[1,2], regions=[3], template_regions=[4])
        with patch('comfymax_audio_chunker.editor.music_panel.analyze', return_value=copy.deepcopy(main)), patch.object(sheet, 'analyze', return_value=self.infer()):
            task.run()
        self.assertEqual(values[0]['sheet_sage']['status'], 'failed')
        self.assertEqual({k:v for k,v in values[0].items() if k != 'sheet_sage'}, main)


class SheetSagePersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_music
        cls.fixture = test_music.MusicPipelineTests
        cls.fixture.setUpClass()

    def test_save_saveas_reopen_recovery_and_old_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); fixture = self.fixture()
            doc = fixture.project(root)
            self.assertNotIn('music_analysis', doc.data)
            original = copy.deepcopy(self.fixture.result)
            original['analysis']['source_sha256'] = doc.data['assets']['mix']['sha256']
            original['regions'][0]['manual']['label'] = 'F'
            doc.data['music_analysis'] = original
            doc.save(); location = doc.root; doc.close(); doc = Document.open(location)
            self.assertNotIn('sheet_sage', doc.data['music_analysis'])
            FakeProcess.mode = 'success'; FakeProcess.returncode = 0
            with patch.object(sheet.subprocess, 'Popen', FakeProcess):
                evidence = sheet.analyze(doc.root/doc.data['assets']['mix']['path'], doc.root, config=dict(timeout_seconds=30))
            doc.data['music_analysis']['sheet_sage'] = evidence
            evidence['previous_results'] = [copy.deepcopy(evidence)]
            doc.save()
            clone = doc.save_as(root/'copy')
            for artifact in evidence['raw_artifacts']:
                self.assertEqual((doc.root/artifact['path']).read_bytes(), (clone.root/artifact['path']).read_bytes())
            self.assertEqual(clone.data['music_analysis']['regions'], original['regions']); clone.close()
            data = copy.deepcopy(doc.data); data['revision'] += 1
            atomic_json(doc.root/'recovery'/'pending.json', data)
            doc.close(); doc = Document.open(location)
            self.assertTrue(doc.recovered)
            self.assertEqual(doc.data['music_analysis']['sheet_sage'], evidence)
            self.assertEqual({k:v for k,v in doc.data['music_analysis'].items() if k != 'sheet_sage'}, original)
            doc.close()


if __name__ == '__main__': unittest.main()
