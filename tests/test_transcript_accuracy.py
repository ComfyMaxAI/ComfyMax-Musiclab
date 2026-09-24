"""Transcript accuracy: fixed language, native evidence and backwards-compatible UI."""
import copy
from dataclasses import dataclass
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
from comfymax_audio_chunker import cli
from comfymax_audio_chunker.editor import transcript,transcript_worker
from test_phase2c1 import EditorTests as BaseEditorTests,result

@dataclass
class Segment:
    id:int=1
    start:float=30.125
    end:float=33.375
    text:str=' texto'
    avg_logprob:float=-1.8
    no_speech_prob:float=.9
    compression_ratio:float=1.1
    temperature:float=1.0
    words:object=None

class AccuracyTests(unittest.TestCase):
    def test_language_task_and_all_chunks(self):
        for language in (None,'es','en'):
            calls=[]
            class Model:
                def __init__(self,*a,**kw): pass
                def transcribe(self,path,**kwargs):
                    calls.append(kwargs)
                    return iter([Segment(),Segment(id=2,start=60.25,end=65.5)]),SimpleNamespace(language=language or 'pl',language_probability=.2)
            native=[]
            with patch.dict(sys.modules,{'faster_whisper':SimpleNamespace(WhisperModel=Model)}):
                rows,_,_=cli.transcribe('audio.wav',SimpleNamespace(model='small',offline=True,language=language),100,[(0,100)],word_timestamps=False,raw_output=native)
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0]['language'],language)
            self.assertEqual(calls[0]['task'],'transcribe')
            self.assertFalse(calls[0]['multilingual'])
            self.assertIsNone(calls[0]['initial_prompt'])
            self.assertFalse(calls[0]['vad_filter'])
            self.assertEqual([(r['start'],r['end']) for r in rows],[(30.125,33.375),(60.25,65.5)])
            self.assertEqual(rows[0]['temperature'],1.0)
            self.assertEqual(native[0]['text'],' texto')

    def test_worker_language_provenance(self):
        for code in (None,'es','en'):
            def fake(path,args,duration,active,**kwargs):
                self.assertEqual(args.language,code)
                self.assertIsNone(args.initial_prompt)
                return [],[],{'language':code or 'pl','probability':.2}
            request=dict(protocol=transcript.SCHEMA,path='audio.wav',duration=100,active=[],source={'sha256':'hash'},language=code)
            with patch.object(transcript_worker,'sha256',return_value='hash'),patch.object(transcript_worker,'transcribe',side_effect=fake),patch.object(transcript_worker,'version',return_value='test'):
                value=transcript_worker.run(request)
            self.assertEqual(value['provenance']['requested_language'],code)
            self.assertEqual(value['provenance']['task'],'transcribe')
        with self.assertRaises(ValueError): transcript_worker.run(dict(protocol=transcript.SCHEMA,language='invalid'))

    def test_native_metadata_and_no_invented_scores(self):
        s=vars(Segment()).copy(); native=[copy.deepcopy(s)]
        value=transcript.make_result([s],native,{},None,100)
        original=copy.deepcopy(value['raw'])
        value['lyrics']['segments'][0]['text']='corrected'
        transcript.validate(value,100)
        self.assertEqual(value['raw'],original)
        self.assertEqual(value['raw']['whisper_segments'],native)
        for key in transcript.EVIDENCE_FIELDS: self.assertEqual(value['raw']['segments'][0][key],s[key])
        self.assertTrue(transcript.review_reasons(transcript.segment_evidence(value)[0]))
        empty=result()
        self.assertEqual(transcript.segment_evidence(empty),[{}])
        self.assertEqual(transcript.review_reasons({}),[])
        self.assertEqual(transcript.review_reasons(dict(avg_logprob=-.3,no_speech_prob=.2,compression_ratio=1.5)),[])
        self.assertEqual(transcript.review_reasons(dict(no_speech_prob=.95,avg_logprob=-.3)),[])
        self.assertEqual(transcript.review_reasons(dict(no_speech_prob=.95)),[])
        for evidence in [dict(avg_logprob=-1.1),dict(no_speech_prob=.81,avg_logprob=-.75),dict(compression_ratio=2.5)]:
            self.assertTrue(transcript.review_reasons(evidence))

    def test_legacy_evidence_is_read_only_and_not_misassigned(self):
        value=result(); native=value['raw']['whisper_segments'][0]
        native['avg_logprob']=-2; native['temperature']=1
        value['lyrics']['segments'][0]['text']='edited'
        before=copy.deepcopy(value)
        self.assertEqual(transcript.segment_evidence(value)[0]['avg_logprob'],-2)
        self.assertEqual(value,before)
        native['start']=.9
        self.assertEqual(transcript.segment_evidence(value),[{}])

class AccuracyEditorTests(BaseEditorTests):
    def test_selected_language_survives_discard_and_reaches_worker(self):
        w=self.window
        w.transcript_language.setCurrentIndex(w.transcript_language.findData('es'))
        def discard():
            w.refresh_transcript()  # discard resets the old transcript selection
            return True
        with patch.object(w,'resolve_lyrics_draft',side_effect=discard),patch.object(transcript,'availability',return_value=(True,'')),patch('comfymax_audio_chunker.editor.transcript_panel.QMessageBox.question',return_value=16384),patch('comfymax_audio_chunker.editor.transcript_panel.TranscriptTask') as factory:
            w.transcribe_lyrics()
            self.assertEqual(factory.call_args.args[0]['language'],'es')
            self.assertEqual(w.transcript_language.currentData(),'es')
            self.assertFalse(w.transcript_language.isEnabled())
            job=w.transcript_job; w.jobs.remove(job); w.transcript_job=None; w.busy=False

    def test_language_selector_warning_edit_save_reopen(self):
        w=self.window
        self.assertEqual([(w.transcript_language.itemText(i),w.transcript_language.itemData(i)) for i in range(w.transcript_language.count())],list(transcript.LANGUAGES))
        value=result(w.doc.duration); value['provenance']['requested_language']='es'
        value['raw']['whisper_segments'][0]['avg_logprob']=-2
        w.doc.data['transcript']=value; w.refresh_transcript()
        self.assertEqual(w.transcript_language.currentData(),'es')
        self.assertIn('Manual checking',w.transcript_table.item(0,2).toolTip())
        self.assertIn('1 amber',w.lyrics_status.text())
        raw=copy.deepcopy(value['raw'])
        w.transcript_table.item(0,2).setText('correction')
        self.assertTrue(w.save_lyrics())
        self.assertEqual(w.doc.data['transcript']['raw'],raw)
        self.assertEqual(w.doc.data['transcript']['provenance']['requested_language'],'es')
        w.refresh_transcript(); self.assertEqual(w.transcript_table.item(0,2).text(),'correction')
        del w.doc.data['transcript']['provenance']['requested_language']; w.refresh_transcript()
        self.assertIsNone(w.transcript_language.currentData())

if __name__=='__main__': unittest.main()
