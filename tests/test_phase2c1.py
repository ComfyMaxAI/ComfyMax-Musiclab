import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication,QMessageBox
from comfymax_audio_chunker.editor.follow import follow_start
from comfymax_audio_chunker.editor import transcript
from comfymax_audio_chunker.editor.marker_app import MarkerEditor,open_audio
from comfymax_audio_chunker.editor.project import Document,atomic_json
from comfymax_audio_chunker.editor.transcript_panel import TranscriptTask
from comfymax_audio_chunker.editor.waveform import Waveform
import test_editor as fixtures


def result(duration=1):
    return transcript.make_result([dict(start=.01,end=duration*.9,text='Cuando sali')],
        [dict(start=.01,end=duration*.9,text=' Cuando sali',tokens=[1,2])],
        dict(backend='faster-whisper'),dict(asset='vocals',sha256='hash'),duration)


class FollowTests(unittest.TestCase):
    def test_start_threshold_continuous_end_and_seek(self):
        self.assertEqual(follow_start(0,0,10,100),0)
        self.assertEqual(follow_start(4,0,10,100),0)
        self.assertAlmostEqual(follow_start(4.54,0,10,100),.04)
        self.assertAlmostEqual(follow_start(4.58,.04,10,100),.08)
        self.assertEqual(follow_start(100,80,10,100),90)
        self.assertEqual(follow_start(35,80,10,100),30.5)
        self.assertEqual(follow_start(10,0,100,100),0)

    def test_visible_peak_reduction_matches_original_pixels(self):
        app=QApplication.instance() or QApplication([])
        rng=np.random.default_rng(5); low=rng.uniform(-1,0,4000); high=rng.uniform(0,1,4000)
        wave=Waveform(); wave.resize(400,250); wave.peaks=(low,high,.01)
        image=QImage(400,250,QImage.Format_ARGB32); p=QPainter(image)
        for a,span in [(0,30),(.123,30),(2,1),(39.7,1),(0,.2)]:
            wave.start=a; wave.span=span; wave.draw_cached_samples(p,100,80)
            path=wave._sample_path; index=0; width=wave.width()-16
            for pixel in range(width):
                first=max(0,int((a+span*pixel/width)/.01))
                last=min(len(low),max(first+1,int((a+span*(pixel+1)/width)/.01)))
                if first<len(low):
                    self.assertAlmostEqual(path.elementAt(index).y,100-np.max(high[first:last])*80)
                    self.assertAlmostEqual(path.elementAt(index+1).y,100-np.min(low[first:last])*80)
                    index+=2
        p.end(); wave.close()


class TranscriptModelTests(unittest.TestCase):
    def test_vocals_mix_and_read_without_whisper(self):
        data=dict(assets=dict(vocals={'path':'v.wav'},mix={'path':'m.wav'}),transcript=result())
        self.assertEqual(transcript.source_audio(data)[0],'vocals')
        del data['assets']['vocals']; self.assertEqual(transcript.source_audio(data)[0],'mix')
        with patch.object(transcript.importlib.util,'find_spec',return_value=None):
            self.assertFalse(transcript.availability()[0])
            self.assertEqual(transcript.from_existing(data),data['transcript'])

    def test_empty_malformed_and_immutable_timing(self):
        value=result(); transcript.validate(value,1)
        for field,value2 in [('start',.03),('end',.8),('id','changed')]:
            bad=copy.deepcopy(value); bad['lyrics']['segments'][0][field]=value2
            with self.assertRaises(ValueError): transcript.validate(bad,1)
        with self.assertRaises(ValueError): transcript.validate({},1)
        value=transcript.make_result([],[],{},None,1); transcript.validate(value,1)
        self.assertEqual(value['raw']['segments'],[])

    def test_worker_responses_failure_empty_malformed(self):
        app=QApplication.instance() or QApplication([])
        class Process:
            returncode=0
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def poll(self): return 0
            def communicate(self,*args,**kwargs): return payload,''
        request=dict(duration=1,source=dict(asset='vocals',sha256='hash'))
        for payload,success in [('{',False),(json.dumps(dict(protocol=transcript.SCHEMA,success=False,error='failure')),False),
              (json.dumps(dict(protocol=transcript.SCHEMA,success=True,transcript=result())),True),
              (json.dumps(dict(protocol=transcript.SCHEMA,success=True,transcript=transcript.make_result([],[],{},request['source'],1))),True)]:
            task=TranscriptTask(request,None); ready=[]; failed=[]; task.ready.connect(ready.append); task.failed.connect(failed.append)
            with patch('comfymax_audio_chunker.editor.transcript_panel.subprocess.Popen',return_value=Process()): task.run()
            self.assertEqual(bool(ready),success); self.assertEqual(bool(failed),not success)


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        source=fixtures.ProjectTests().source(self.root)
        doc=Document.create(source,self.root/'project'); path=doc.root; doc.close()
        self.window=MarkerEditor(); self.window.loaded(open_audio(path))

    def tearDown(self):
        if hasattr(self,'original_transport'):
            self.window.transport=self.original_transport; self.window.doc=self.original_doc
            for wave in (self.window.detail,self.window.overview): wave.doc=self.original_doc
        self.window.lyrics_dirty=False; self.window.dirty=False; self.window.close(); self.temp.cleanup()

    def test_edit_save_revert_and_project_persistence(self):
        w=self.window; before=copy.deepcopy(w.doc.data)
        value=result(w.doc.duration); w.transcript_draft=value; w.lyrics_dirty=True; w.render_lyrics()
        raw=copy.deepcopy(value['raw']); timing=copy.deepcopy(value['lyrics']['segments'][0])
        w.transcript_table.item(0,2).setText('Cuando salí de Cuba')
        self.assertTrue(w.lyrics_dirty); self.assertIn('Unsaved',w.lyrics_status.text())
        self.assertNotIn('transcript',w.doc.data)
        w.save(True); self.assertNotIn('transcript',w.doc.data)  # autosave excludes drafts
        self.assertTrue(w.save_lyrics()); self.assertFalse(w.lyrics_dirty)
        self.assertEqual(w.doc.data['transcript']['raw'],raw)
        self.assertEqual(w.doc.data['transcript']['lyrics']['segments'][0]['start'],timing['start'])
        self.assertEqual(w.doc.data['phrases'],before['phrases'])
        self.assertEqual(w.state,before['marker_editor'])
        clone=w.doc.save_as(self.root/'copy'); saved=copy.deepcopy(clone.data['transcript']); clone.close()
        clone=Document.open(self.root/'copy'); self.assertEqual(clone.data['transcript'],saved)
        checkpoint=copy.deepcopy(clone.data); checkpoint['revision']+=1
        atomic_json(clone.root/'recovery'/'pending.json',checkpoint); clone.close()
        clone=Document.open(self.root/'copy'); self.assertTrue(clone.recovered); self.assertEqual(clone.data['transcript'],saved); clone.close()
        with patch('comfymax_audio_chunker.editor.transcript_panel.QMessageBox.question',return_value=QMessageBox.No): w.revert_lyrics()
        self.assertFalse(w.lyrics_dirty)
        with patch('comfymax_audio_chunker.editor.transcript_panel.QMessageBox.question',return_value=QMessageBox.Yes): w.revert_lyrics()
        self.assertTrue(w.lyrics_dirty); self.assertEqual(w.transcript_draft['lyrics']['segments'],raw['segments'])
        self.assertEqual(w.doc.data['transcript'],saved)
        self.assertTrue(w.save_lyrics()); self.assertEqual(w.doc.data['transcript']['raw'],raw)

    def test_follow_controls_clock_only_pause_zoom_resize_fit(self):
        w=self.window; original=w.transport
        self.original_transport=original; self.original_doc=w.doc
        w.doc=SimpleNamespace(duration=100,data=w.doc.data)
        for wave in (w.detail,w.overview): wave.doc=w.doc
        clock=SimpleNamespace(rate=1000,active=True,poll=lambda:60000,warning='')
        w.transport=clock; w.detail.set_view(0,50); w.overview.set_view(0,50)
        w.follow_playhead.setChecked(True); state=copy.deepcopy(w.state); w.tick()
        self.assertAlmostEqual(w.detail.start,37.5)
        self.assertEqual(w.detail.position,60)
        clock.active=False; clock.poll=lambda:80000; w.tick(); self.assertAlmostEqual(w.detail.start,37.5)
        clock.active=True; w.follow_playhead.setChecked(False); w.tick(); self.assertAlmostEqual(w.detail.start,37.5)
        w.set_view(0,50); self.assertFalse(w.follow_playhead.isChecked())
        w.follow_playhead.setChecked(True); w.detail.resize(600,250); w.tick()
        self.assertAlmostEqual(w.detail.start,50)
        self.assertAlmostEqual(w.detail.t(w.detail.x(80)),80)
        w.set_view(0,w.doc.duration); w.tick(); self.assertEqual(w.detail.start,0)
        self.assertEqual(w.state,state); w.transport=original

    def test_pending_draft_close_cancel_and_unavailable(self):
        w=self.window; self.assertNotIn('transcript',w.doc.data)
        w.lyrics_dirty=True
        with patch('comfymax_audio_chunker.editor.transcript_panel.QMessageBox.question',return_value=QMessageBox.Cancel):
            self.assertFalse(w.prepare_leave())
        w.lyrics_dirty=False
        with patch.object(transcript,'availability',return_value=(False,'Whisper unavailable')):
            w.transcribe_lyrics()
        self.assertIsNone(w.transcript_job); self.assertIn('unavailable',w.lyrics_status.text())


if __name__=='__main__': unittest.main()
