import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
from PySide6.QtCore import QSize, QBuffer, QIODevice
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication, QMessageBox
from comfymax_audio_chunker.editor.score_panel import ScorePanel
from comfymax_audio_chunker.editor.score_source import SheetSageABCSource
from test_score_viewer import SIMPLE, artifact

LONG = ('X:1\nT:PDF acceptance\nM:4/4\nL:1/4\nQ:1/4=111\nK:C\n'
        + '"C"C D E F|G A B c|\n'*32 + 'P:Complete ending\n"G7"C4|]\n')


class ScorePdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.panel = ScorePanel()
        self.panel.source = SheetSageABCSource(self.root/'project', artifact(self.root/'project'))

    def tearDown(self):
        if hasattr(self, 'pdf'):
            self.pdf.close()
        self.panel.close()
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self, predicate):
        end = time.monotonic()+20
        while not predicate() and time.monotonic()<end:
            self.app.processEvents(); time.sleep(.01)
        self.assertTrue(predicate(), self.panel.status.text())

    def render(self, text=SIMPLE):
        self.panel.source = SheetSageABCSource(self.root/'project', artifact(self.root/'project', text.encode()))
        self.panel.show_score()
        self.wait(lambda: self.panel.result is not None)
        return self.panel.renderer

    def test_full_vector_a4_pdf_independent_of_zoom_and_scroll(self):
        r = self.render(LONG)
        r.sizing(False, 2.5)
        r.page.runJavaScript('window.scrollTo(0,document.body.scrollHeight)')
        target = self.root/'score.pdf'
        before = self.panel.document.payload
        with patch('comfymax_audio_chunker.editor.score_panel.QFileDialog.getSaveFileName',return_value=(str(target),'')):
            self.panel.pdf_button.click()
        self.assertFalse(self.panel.pdf_button.isEnabled())
        self.wait(target.exists)
        self.assertTrue(self.panel.pdf_button.isEnabled())
        payload = target.read_bytes()
        self.assertTrue(payload.startswith(b'%PDF-'))
        self.assertNotIn(b'/Subtype /Image',payload)  # Native SVG paths/text, not screenshots.
        pdf = QPdfDocument(self.panel)
        self.pdf = pdf
        self.pdf_buffer = QBuffer(self.panel)
        self.pdf_buffer.setData(payload)
        self.pdf_buffer.open(QIODevice.ReadOnly)
        pdf.load(self.pdf_buffer)
        self.wait(lambda: pdf.status() == QPdfDocument.Status.Ready)
        self.assertGreaterEqual(pdf.pageCount(),2)
        text = '\n'.join(pdf.getAllText(i).text() for i in range(pdf.pageCount()))
        compact = ''.join(text.split())  # PDFium may separate positioned SVG glyphs.
        self.assertIn('PDFacceptance',compact)
        self.assertIn('Completeending',compact)
        for forbidden in ('Export PDF','Export SVG','Fit Width','Timeline','MusicLab'):
            self.assertNotIn(''.join(forbidden.split()),compact)
        for i in range(pdf.pageCount()):
            size = pdf.pagePointSize(i)
            # Chromium rounds print dimensions to its internal device grid.
            self.assertAlmostEqual(size.width(),595,delta=1.5)
            self.assertAlmostEqual(size.height(),842,delta=1.5)
        preview = pdf.render(0,QSize(595,842))
        # PDFium exposes unpainted paper margins as transparent; the score
        # background inside the 15 mm margins must be explicitly white.
        self.assertEqual(preview.pixelColor(50,50).getRgb(),(255,255,255,255))
        pdf.close()
        self.assertEqual(self.panel.document.payload,before)
        self.assertEqual(self.panel.source.read().payload,before)

    def test_disabled_without_score_cancel_and_default_extension(self):
        self.assertFalse(self.panel.pdf_button.isEnabled())
        with patch('comfymax_audio_chunker.editor.score_panel.QFileDialog.getSaveFileName') as dialog:
            self.panel.export_pdf();dialog.assert_not_called()
        self.render()
        with patch('comfymax_audio_chunker.editor.score_panel.QFileDialog.getSaveFileName',return_value=('','')):
            self.panel.export_pdf()
        self.assertTrue(self.panel.pdf_button.isEnabled())
        target = self.root/'extension'
        with patch('comfymax_audio_chunker.editor.score_panel.QFileDialog.getSaveFileName',return_value=(str(target),'')):
            self.panel.export_pdf()
        self.wait(target.with_suffix('.pdf').exists)
        with patch('comfymax_audio_chunker.editor.score_panel.QFileDialog.getSaveFileName',return_value=(str(target),'')),\
             patch('comfymax_audio_chunker.editor.score_panel.QMessageBox.question',return_value=QMessageBox.No),\
             patch.object(self.panel.renderer.page,'printToPdf') as printer:
            self.panel.export_pdf()
            printer.assert_not_called()
        self.panel.set_project(None)
        self.assertFalse(self.panel.pdf_button.isEnabled())

    def test_empty_print_result_reports_error_and_preserves_existing_file(self):
        r=self.render();target=self.root/'existing.pdf';target.write_bytes(b'previous PDF')
        with patch.object(r.page,'printToPdf',side_effect=lambda path,layout:r.page.pdfPrintingFinished.emit(path,False)),\
             patch('comfymax_audio_chunker.editor.score_panel.QMessageBox.warning') as warning:
            r.export_pdf(target)
        warning.assert_called_once()
        self.assertIn('could not create',self.panel.status.text())
        self.assertEqual(target.read_bytes(),b'previous PDF')
        self.assertTrue(self.panel.pdf_button.isEnabled())

    def test_write_failure_and_protected_project_destination(self):
        r=self.render()
        with patch('comfymax_audio_chunker.editor.score_panel.QMessageBox.warning') as warning:
            r.export_pdf(self.root/'missing-folder'/'score.pdf')
            self.wait(lambda:warning.called)
        self.assertIn('Cannot save',self.panel.status.text())
        with patch('comfymax_audio_chunker.editor.score_panel.QFileDialog.getSaveFileName',return_value=(str(self.root/'project'/'score.pdf'),'')),\
             patch('comfymax_audio_chunker.editor.score_panel.QMessageBox.warning') as warning:
            self.panel.export_pdf()
        warning.assert_called_once()
        self.assertFalse((self.root/'project'/'score.pdf').exists())

    def test_pending_export_cancelled_on_source_change_or_cleanup(self):
        for close in (False,True):
            r=self.render();callbacks=[];target=self.root/'cancelled.pdf'
            with patch.object(r.page,'printToPdf',side_effect=lambda callback,layout:callbacks.append(callback)):
                r.export_pdf(target)
                r.export_pdf(target)
            self.assertEqual(len(callbacks),1)
            with patch('comfymax_audio_chunker.editor.score_panel.QMessageBox.warning') as warning:
                if close:
                    self.panel.close_renderer()
                else:
                    self.panel.set_project(None)
                r._pdf_complete(callbacks[0],False)
                self.assertEqual(warning.called,not close)
            self.assertFalse(target.exists())

    def test_native_print_pending_during_cleanup(self):
        r=self.render();target=self.root/'closing.pdf';delivered=[]
        r.pdf_finished.connect(delivered.append);r.pdf_failed.connect(delivered.append)
        r.export_pdf(target)
        self.panel.close_renderer()
        self.app.processEvents()
        self.assertEqual(delivered,[])
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
