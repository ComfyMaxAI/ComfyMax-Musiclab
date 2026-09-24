import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from comfymax_audio_chunker.editor.audio import Transport

class FakeStream:
    def __init__(self,**kwargs):
        self.settings=kwargs; self.time=10.; self.latency=.2; self.device=4
        self.starts=self.aborts=self.closes=self.stops=0
    def start(self): self.starts+=1
    def abort(self): self.aborts+=1
    def stop(self): self.stops+=1
    def close(self): self.closes+=1

class PlaybackTests(unittest.TestCase):
    def make(self,channels=2):
        x=np.arange(44100*channels,dtype=np.float32).reshape(-1,channels)/100000
        return Transport(dict(mix=x,vocals=-x),44100)
    def render(self,t,n=512,when=10.,underflow=False):
        out=np.empty((n,t.channels),np.float32)
        class Status:
            output_underflow=underflow
            def __bool__(self): return self.output_underflow
            def __str__(self): return 'Output underflow' if self.output_underflow else ''
        t._callback(out,n,SimpleNamespace(outputBufferDacTime=when),Status())
        return out
    def test_pause_seek_resume_reuse_device(self):
        t=self.make()
        with patch('comfymax_audio_chunker.editor.audio.sd.OutputStream',side_effect=FakeStream) as factory:
            t.play(0,t.total); stream=t.stream
            self.render(t,2048); stream.time=10.01
            heard=t.position(); queued=t.cursor.frame; t.pause(); self.assertEqual(t.parked,queued)
            self.assertGreater(queued,heard); self.assertEqual(stream.stops,1)
            t.resume(); self.assertIs(t.stream,stream); self.assertEqual(t.cursor.frame,queued)
            t.seek(22050); self.assertEqual(t.cursor.frame,22050); self.assertEqual(t.position(),22050)
            self.assertEqual(stream.closes,0); self.assertEqual(factory.call_count,1)
            self.assertEqual(stream.settings['blocksize'],2048); self.assertEqual(stream.settings['latency'],.2)
            t.close(); self.assertEqual(stream.closes,1); self.assertIsNone(t.stream)
    def test_pcm_sequence_no_repeat_or_drop(self):
        t=self.make(); t.volume=1; t._fade_in=False
        first=self.render(t,512); second=self.render(t,512,when=10.02)
        np.testing.assert_array_equal(np.vstack([first,second]),t.arrays['mix'][:1024])
    def test_source_switch_crossfade_aligned_samples(self):
        t=self.make(); t.volume=1; t._fade_in=False
        self.render(t,512); t.switch('vocals'); out=self.render(t,512)
        n=t._fade_frames
        np.testing.assert_allclose(out[:n],t.arrays['vocals'][512:512+n]*t._fade+t.arrays['mix'][512:512+n]*t._fade_out,atol=1e-8)
        np.testing.assert_array_equal(out[n:],t.arrays['vocals'][512+n:1024])
        self.assertEqual(t.cursor.frame,1024)
    def test_mono_and_underrun_diagnostics(self):
        t=self.make(1); self.render(t,512,underflow=True)
        self.assertEqual(t.diagnostics()['output_underflows'],1)
        self.assertEqual(t.diagnostics()['channels'],1)
        with self.assertLogs('comfymax_audio_chunker.editor.audio',level='WARNING'): t.poll()
        self.assertEqual(t.output_underflows,1)
    def test_loop_toggle_and_seek_outside_audition(self):
        t=self.make()
        with patch('comfymax_audio_chunker.editor.audio.sd.OutputStream',side_effect=FakeStream):
            t.play(1000,2000,loop=True); stream=t.stream
            t.set_loop(False); self.assertIs(t.stream,stream); self.assertFalse(t.cursor.loop)
            t.seek(4000); self.assertEqual((t.cursor.start,t.cursor.end,t.cursor.frame),(0,t.total,4000))
            t.seek(t.total); self.assertFalse(t.active); self.assertEqual(t.position(),t.total); t.close()
    def test_reject_misaligned_sources(self):
        with self.assertRaises(ValueError): Transport(dict(mix=np.zeros((100,2),np.float32),vocals=np.zeros((99,2),np.float32)),44100)

class WaveformCacheTests(unittest.TestCase):
    def test_playhead_reuses_cache_pan_resize_source_invalidates(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QImage,QPainter
        from comfymax_audio_chunker.editor.waveform import Waveform
        app=QApplication.instance() or QApplication([])
        wave=Waveform(); wave.resize(400,200); wave.peaks=(np.zeros(1000),np.ones(1000),.01)
        image=QImage(400,200,QImage.Format_ARGB32); painter=QPainter(image)
        wave.draw_cached_samples(painter,100,80); original=wave._sample_path
        wave.position=3; wave.draw_cached_samples(painter,100,80); self.assertIs(wave._sample_path,original)
        wave.start=1; wave.draw_cached_samples(painter,100,80); self.assertIsNot(wave._sample_path,original)
        original=wave._sample_path; wave.resize(500,200); wave.draw_cached_samples(painter,100,80)
        self.assertIsNot(wave._sample_path,original)
        original=wave._sample_path; wave.peaks=(np.zeros(1000),np.zeros(1000),.01); wave.draw_cached_samples(painter,100,80)
        self.assertIsNot(wave._sample_path,original); painter.end(); wave.close()
