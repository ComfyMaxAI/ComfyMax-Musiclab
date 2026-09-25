"""Real offline renderer tests: requires the optional PySide6 WebEngine component."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
from PySide6.QtWidgets import QApplication, QWidget
from comfymax_audio_chunker.editor.score_source import ScoreDocument, SheetSageABCSource
from comfymax_audio_chunker.editor.score_renderer import ScoreRenderer, RenderCache, cache_key, combined_svg
from comfymax_audio_chunker.editor.score_panel import ScorePanel

SIMPLE = 'X:1\nT:Simple\nM:4/4\nL:1/4\nQ:1/4=111\nK:C\n"C"C D E z|"G7"^F =F _B B,|c2-c2|]\n'
MULTI = '''X:1
T:Changes & voices
M:4/4
L:1/4
Q:1/4=111
V:Vocal clef=treble name="Vocal Melody"
V:Ins clef=treble name="Ins Melody"
V:Bass clef=bass name="Bass"
K:C
V:Vocal
"C"(C D) E z|"G7"G2-G2|
V:Ins
z4|"F"F A c2|
V:Bass
C,4|G,,4|
V:Vocal
K:Eb
M:3/4
"Eb"E G B|
V:Ins
K:Eb
M:3/4
"Ab"A c e|
V:Bass
K:Eb
M:3/4
E,3|
V:Vocal
K:E
M:2/4
"F#m"F A|
M:4/4
"B7"B2 "E"e2|]
V:Ins
K:E
M:2/4
z2|
M:4/4
E2 z2|]
V:Bass
K:E
M:2/4
F,2|
M:4/4
B,,2 E,2|]
'''


def artifact(root, payload=SIMPLE.encode()):
    p = root/'music_analysis/sheetsage/test/native/score.abc'
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(payload)
    return dict(schema_version='comfymax.sheetsage.1', status='success', provenance={}, warnings=[],
                transcription={}, raw_artifacts=[dict(path=p.relative_to(root).as_posix(),
                sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload), origin='test fixture')])


class SourceTests(unittest.TestCase):
    def test_byte_identical_no_runtime_or_midi(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); payload=b'\xef\xbb\xbf'+SIMPLE.replace('\n','\r\n').encode(); e=artifact(root,payload)
            before=copy.deepcopy(e)
            with patch('comfymax_audio_chunker.music.sheetsage.analyze', side_effect=AssertionError('inference')):
                a=SheetSageABCSource(root,e).read(); b=SheetSageABCSource(root,e).read()
            self.assertEqual(a,b); self.assertEqual(a.payload,payload); self.assertFalse(a.error)
            self.assertEqual(e,before); self.assertEqual((root/e['raw_artifacts'][0]['path']).read_bytes(),payload)

    def test_missing_empty_invalid_encoding_and_checksum(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            self.assertIn('No SheetSage',SheetSageABCSource(root,{}).read().error)
            for payload, error in [(b'','empty'),(b'\xff','UTF-8')]:
                self.assertIn(error,SheetSageABCSource(root,artifact(root,payload)).read().error)
            e=artifact(root); e['raw_artifacts'][0]['sha256']='0'*64
            d=SheetSageABCSource(root,e).read(); self.assertIn('checksum',d.error);self.assertEqual(d.text,SIMPLE)
            (root/e['raw_artifacts'][0]['path']).unlink()
            self.assertIn('Cannot read',SheetSageABCSource(root,e).read().error)

    def test_unsafe_and_ambiguous_artifacts(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);e=artifact(root)
            e['raw_artifacts']*=2
            self.assertIn('Ambiguous',SheetSageABCSource(root,e).read().error)
            for name in ('music_analysis/sheetsage/../../../score.abc','C:/score.abc'):
                e['raw_artifacts']=[dict(path=name)]
                self.assertIn('Unsafe',SheetSageABCSource(root,e).read().error)

    def test_cache_invalidation_content_version_options_and_bounds(self):
        d=ScoreDocument(SIMPLE.encode(),'same.abc'); key=cache_key(d)
        for other in (cache_key(ScoreDocument(b'other','same.abc')),cache_key(d,version='next'),cache_key(d,{'staffwidth':1})):
            self.assertNotEqual(key,other)
        self.assertEqual(key,cache_key(ScoreDocument(d.payload,'different.abc')))
        c=RenderCache(100);c.put('a',{'x':'1'});self.assertEqual(c.get('a'),{'x':'1'})
        c.put('b',{'x':'x'*101});self.assertIsNone(c.get('a'));self.assertIsNone(c.get('b'))


class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.host=QWidget();cls.renderer=ScoreRenderer(cls.host)
        cls.result=None;cls.failure=None
        cls.renderer.ready.connect(lambda x:setattr(cls,'result',x))
        cls.renderer.failed.connect(lambda x:setattr(cls,'failure',x))
        cls.wait(lambda: cls.renderer.loaded or cls.failure)
        if cls.failure: raise AssertionError(cls.failure)

    @classmethod
    def wait(cls,predicate):
        end=time.monotonic()+20
        while not predicate() and time.monotonic()<end:
            cls.app.processEvents();time.sleep(.01)
        if not predicate(): raise AssertionError('Timed out waiting for local renderer')

    @classmethod
    def tearDownClass(cls):
        cls.renderer.close();cls.host.close();cls.host.deleteLater();cls.app.processEvents()

    def render(self,text):
        type(self).result=None;type(self).failure=None
        self.renderer.render(ScoreDocument(text.encode(),'fixture'))
        self.wait(lambda:self.result is not None or self.failure is not None)
        return self.result

    def js(self,code):
        out=[];self.renderer.page.runJavaScript(code,out.append);self.wait(lambda:bool(out));return out[0]

    def test_valid_tempo_rests_accidentals_chords_and_ties(self):
        r=self.render(SIMPLE);self.assertIsNone(self.failure)
        text=''.join(r['svgs'])
        for token in ('Simple','111','G7','data-score-kind="rest"','data-score-kind="note"'):
            self.assertTrue(token in text,token)
        # SMuFL flat/natural/sharp glyphs in the renderer's embedded font.
        for glyph in ('\ue260','\ue261','\ue262'):
            self.assertTrue(glyph in text,repr(glyph))

    def test_multiple_voices_keys_meters_and_vector_export(self):
        r=self.render(MULTI);self.assertIsNone(self.failure);self.assertFalse(r['warnings'])
        text=''.join(r['svgs'])
        for token in ('Vocal Melody','Ins Melody','Bass','E♭','A♭','F♯m','B7'):
            self.assertTrue(token in text,token)
        self.assertGreater(text.count('data-score-kind="key"'),3)
        for voice in (0,1,2):
            meters = [s['meter'] for s in r['symbols'] if s['type']=='meter' and s['voice']==voice]
            self.assertEqual(meters,[[dict(top=n,bot='4')] for n in ('4','3','2','4')])
        exported=ET.fromstring(combined_svg(r['svgs']))
        self.assertEqual(len(exported.findall('{http://www.w3.org/2000/svg}svg')),len(r['svgs']))
        self.assertIn('SIL OPEN FONT LICENSE',ET.tostring(exported).decode())
        self.assertGreater(float(exported.get('height')),500)

    def test_cache_and_zoom_do_not_reengrave(self):
        self.render(SIMPLE);r=self.render(SIMPLE);self.assertTrue(r['cached'])
        before=self.js('document.querySelector("svg").getAttribute("viewBox")')
        self.renderer.sizing(False,2.)
        self.assertEqual(self.js('document.getElementById("paper").style.width'),'2000px')
        self.renderer.sizing(True,1.)
        self.assertEqual(before,self.js('document.querySelector("svg").getAttribute("viewBox")'))

    def test_malformed_and_renderer_exception(self):
        self.assertIsNone(self.render('not ABC'));self.assertIn('Malformed',self.failure)
        self.assertIsNone(self.render('X:1\nK:C\n'));self.assertIn('no renderable',self.failure)
        self.js('window.savedRender = abc2svg.Abc; abc2svg.Abc = function() {throw Error("injected failure")};')
        try:
            self.assertIsNone(self.render(SIMPLE+'% new uncached\n'));self.assertIn('injected failure',self.failure)
        finally:self.js('abc2svg.Abc=window.savedRender')

    def test_executable_includes_and_embedded_markup_rejected(self):
        for directive in ('%%beginjs\nwindow.pwned=1\n%%endjs', 'I:beginjs\nwindow.pwned=1\nI:endjs',
                          '%%beginsvg\n<script>window.pwned=1</script>\n%%endsvg', '%%abc-include secret.abc'):
            self.assertIsNone(self.render(SIMPLE+directive))
            self.assertIn('not supported',self.failure)
        self.assertEqual(self.js('typeof window.pwned'),'undefined')

    def test_untrusted_metadata_is_inert_and_network_denied(self):
        r=self.render(SIMPLE.replace('T:Simple','T:<img src="https://example.com" onerror="window.pwned=1">'))
        self.assertIsNotNone(r);self.assertEqual(self.js('typeof window.pwned'),'undefined')
        self.assertEqual(self.js('document.querySelectorAll("img,iframe,svg script").length'),0.)
        self.js('window.netResult="pending"; fetch("https://example.com").then(()=>window.netResult="bad").catch(()=>window.netResult="blocked")')
        self.wait(lambda:self.js('window.netResult')!='pending')
        self.assertEqual(self.js('window.netResult'),'blocked')

    def test_unavailable_assets_failure_is_local(self):
        with tempfile.TemporaryDirectory() as t,patch('comfymax_audio_chunker.editor.score_renderer.ASSETS',Path(t)):
            with self.assertRaisesRegex(RuntimeError,'unavailable'):ScoreRenderer(self.host)

    def test_cleanup_order_idempotence_pending_work_and_parent_destruction(self):
        from PySide6.QtCore import qInstallMessageHandler
        from shiboken6 import delete, isValid
        messages=[]; previous=qInstallMessageHandler(lambda kind,context,text:messages.append(text))
        try:
            for destroy_parent in (False, True):
                host=QWidget();renderer=ScoreRenderer(host);order=[];delivered=[]
                page,profile,view=renderer.page,renderer.profile,renderer.view
                for name,obj in [('page',page),('profile',profile),('view',view)]:
                    obj.destroyed.connect(lambda *args,n=name:order.append(n))
                renderer.ready.connect(delivered.append);renderer.failed.connect(delivered.append)
                renderer.render(ScoreDocument(SIMPLE.encode(),'pending'))
                if destroy_parent:
                    delete(host)
                else:
                    renderer.close();renderer.close()
                    renderer.clear();renderer.render(ScoreDocument(SIMPLE.encode(),'closed'))
                    self.assertFalse(renderer.timeout.isActive())
                    self.assertIsNone(renderer.pending)
                    delete(host)
                self.assertEqual(order,['page','profile','view'])
                self.assertFalse(any(isValid(o) for o in (page,profile,view)))
                self.app.processEvents();self.assertEqual(delivered,[])
            self.assertFalse(any('WebEnginePage still not deleted' in m for m in messages),messages)
        finally:
            qInstallMessageHandler(previous)

    def test_stale_callback_and_process_failure_do_not_publish_a_score(self):
        type(self).result=None;type(self).failure=None
        self.renderer.render(ScoreDocument((SIMPLE+'% stale').encode(),'fixture'))
        self.renderer.clear()
        end=time.monotonic()+.2
        while time.monotonic()<end:self.app.processEvents();time.sleep(.01)
        self.assertIsNone(self.result)
        self.renderer._fail('simulated renderer process stopped')
        self.assertTrue(self.renderer.broken);self.assertIn('process stopped',self.failure)


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_old_project_and_failure_keep_raw_abc(self):
        with tempfile.TemporaryDirectory() as t:
            p=ScorePanel();p.show_score();self.assertIn('No SheetSage',p.status.text())
            root=Path(t);p.source=SheetSageABCSource(root,artifact(root))
            with patch('comfymax_audio_chunker.editor.score_renderer.ScoreRenderer',side_effect=ImportError('not installed')):
                p.show_score()
            self.assertIn('unavailable',p.status.text());self.assertEqual(p.abc.toPlainText(),SIMPLE)
            self.assertTrue(p.abc.isReadOnly());self.assertFalse(p.export_button.isEnabled());p.close()

    def test_reopen_save_as_stored_score_without_runtime_no_mutation(self):
        import test_music
        from comfymax_audio_chunker.editor.project import Document
        test_music.MusicPipelineTests.setUpClass()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);doc=test_music.MusicPipelineTests().project(root)
            doc.data['music_analysis']=copy.deepcopy(test_music.MusicPipelineTests.result)
            doc.data['music_analysis']['analysis']['source_sha256']=doc.data['assets']['mix']['sha256']
            e=artifact(doc.root);doc.data['music_analysis']['sheet_sage']=e;doc.save()
            copied=doc.save_as(root/'copied.comfymax');copied.close();doc.close()
            for path in (root/'copied.comfymax',doc.root):
                reopened=Document.open(path);before=copy.deepcopy(reopened.data)
                with patch('comfymax_audio_chunker.music.sheetsage.analyze',side_effect=AssertionError('rerun')),\
                     patch('comfymax_audio_chunker.music.sheetsage.availability',return_value=(False,'runtime absent')):
                    p=ScorePanel();p.set_project(reopened);p.load_source()
                    self.assertEqual(p.document.payload,SIMPLE.encode());self.assertFalse(p.document.error)
                self.assertEqual(reopened.data,before);p.close();reopened.close()

    def test_integrated_tabs_do_not_mutate_analysis_or_transport(self):
        import test_music
        from comfymax_audio_chunker.editor.marker_app import MarkerEditor, open_audio
        test_music.MusicPipelineTests.setUpClass()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);doc=test_music.MusicPipelineTests().project(root)
            doc.data['music_analysis']=copy.deepcopy(test_music.MusicPipelineTests.result)
            doc.data['music_analysis']['analysis']['source_sha256']=doc.data['assets']['mix']['sha256']
            doc.data['music_analysis']['sheet_sage']=artifact(doc.root);doc.save();path=doc.root;doc.close()
            w=MarkerEditor();w.loaded(open_audio(path));before=copy.deepcopy(w.doc.data)
            position=w.transport.position();clock=w.transport
            with patch('comfymax_audio_chunker.music.sheetsage.analyze',side_effect=AssertionError('rerun')):
                w.views.setCurrentIndex(1)
                end=time.monotonic()+20
                while w.score_panel.result is None and time.monotonic()<end:
                    self.app.processEvents();time.sleep(.01)
                self.assertIsNotNone(w.score_panel.result,w.score_panel.status.text())
                w.score_panel.zoom(1.25);w.score_panel.fit_width()
                w.views.setCurrentIndex(2);self.assertEqual(w.score_panel.abc.toPlainText(),SIMPLE)
                w.views.setCurrentIndex(0)
            self.assertEqual(w.doc.data,before);self.assertIs(w.transport,clock)
            self.assertEqual(w.transport.position(),position);self.assertFalse(w.dirty)
            w.close()


if __name__ == '__main__':unittest.main()
