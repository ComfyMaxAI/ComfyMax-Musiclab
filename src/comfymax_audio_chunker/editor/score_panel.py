"""Original and editable Working ABC views, disconnected from the transport."""
import re
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QFileDialog, QMessageBox
from .score_source import ScoreDocument, SheetSageABCSource
from .abc_transpose import ABCTransposeError, MAX_SEMITONES, transpose_abc


class ScorePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.renderer = None
        self.source = None
        self.original_document = None
        self.document = None
        self.transpose_amount = 0
        self.result = None
        self.scale = 1.
        self.fit = True
        self.layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        for label, action in [('Zoom −', lambda: self.zoom(.8)), ('100%', lambda: self.zoom(reset=True)),
                              ('Zoom +', lambda: self.zoom(1.25)), ('Fit Width', self.fit_width)]:
            button = QPushButton(label); button.clicked.connect(action); bar.addWidget(button)
        self.size_label = QLabel('Fit Width'); bar.addWidget(self.size_label); bar.addStretch()
        self.export_button = QPushButton('Export SVG…'); self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_svg); bar.addWidget(self.export_button)
        self.pdf_button = QPushButton('Export PDF'); self.pdf_button.setEnabled(False)
        self.pdf_button.clicked.connect(self.export_pdf); bar.addWidget(self.pdf_button)
        self.layout.addLayout(bar)
        self.status = QLabel('No SheetSage score available.'); self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText); self.layout.addWidget(self.status)
        self.layout.addStretch()
        self.abc_view = QWidget()
        abc_layout = QVBoxLayout(self.abc_view); abc_layout.setContentsMargins(0, 0, 0, 0)
        transpose_bar = QHBoxLayout()
        for amount in (-2, -1):
            button = QPushButton(str(amount)); button.clicked.connect(lambda checked=False, n=amount: self.transpose(n)); transpose_bar.addWidget(button)
        reset = QPushButton('Reset'); reset.clicked.connect(self.reset_abc); transpose_bar.addWidget(reset)
        for amount in (1, 2):
            button = QPushButton(f'+{amount}'); button.clicked.connect(lambda checked=False, n=amount: self.transpose(n)); transpose_bar.addWidget(button)
        self.transpose_label = QLabel('Transpose: Original'); transpose_bar.addWidget(self.transpose_label)
        preview = QPushButton('Update Preview'); preview.clicked.connect(self.preview_working); transpose_bar.addWidget(preview)
        self.vocal_editor_button = QPushButton('Edit Vocal Notes')
        self.vocal_editor_button.clicked.connect(self.edit_vocal_notes); transpose_bar.addWidget(self.vocal_editor_button)
        transpose_bar.addStretch(); abc_layout.addLayout(transpose_bar)
        self.abc = QPlainTextEdit()
        self.abc.setPlaceholderText('No SheetSage score available.')
        abc_layout.addWidget(self.abc, 1)

    def edit_vocal_notes(self):
        document = self.working_document()
        if not document or document.error:
            self.status.setText(document.error if document else 'No SheetSage score available.')
            return
        from .vocal_note_editor import VocalNoteEditor
        try:
            dialog = VocalNoteEditor(self.abc.toPlainText(), self)
        except ValueError as exc:
            self.status.setText('Vocal Note Editor: ' + str(exc))
            return
        if dialog.exec() != dialog.Accepted or dialog.result_abc is None:
            return
        text = dialog.result_abc
        self.document = ScoreDocument(text.encode('utf-8'), 'Working ABC')
        self.abc.blockSignals(True); self.abc.setPlainText(text); self.abc.blockSignals(False)
        self.preview_working()

    def set_project(self, doc):
        self.source = (SheetSageABCSource(doc.root, doc.data.get('music_analysis', {}).get('sheet_sage'))
                       if doc else None)
        self.original_document = None
        self.document = None
        self.transpose_amount = 0
        self.transpose_label.setText('Transpose: Original')
        self.result = None
        self.export_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        self.abc.clear()
        self.status.setText('Open Score to view the preserved SheetSage ABC.' if doc else 'No SheetSage score available.')
        if self.renderer:
            self.renderer.clear()

    def load_source(self):
        if self.original_document is None:
            self.original_document = self.source.read() if self.source else None
            self.document = self.original_document
            self.abc.blockSignals(True)
            self.abc.setPlainText(self.document.text if self.document else '')
            self.abc.blockSignals(False)
        return self.document

    def working_document(self):
        if self.original_document is None:
            self.load_source()
        if not self.original_document:
            return None
        if self.original_document.error:
            return self.original_document
        text = self.abc.toPlainText()
        payload = text.encode('utf-8')
        if self.document and text == self.document.text:
            payload = self.document.payload
        return ScoreDocument(payload, 'Working ABC')

    def transpose(self, semitones):
        document = self.working_document()
        if not document or document.error:
            self.status.setText(document.error if document else 'No SheetSage score available.')
            return
        total = self.transpose_amount + semitones
        if abs(total) > MAX_SEMITONES:
            self.status.setText('Transpose range is limited to -12 through +12 semitones.')
            return
        try:
            text = transpose_abc(self.abc.toPlainText(), semitones)
        except ABCTransposeError as exc:
            self.status.setText('ABC transposition failed: ' + str(exc))
            return
        self.document = ScoreDocument(text.encode('utf-8'), 'Working ABC')
        self.abc.blockSignals(True); self.abc.setPlainText(text); self.abc.blockSignals(False)
        self.transpose_amount = total
        unit = 'semitone' if abs(total) == 1 else 'semitones'
        self.transpose_label.setText(f'Transpose: {total:+d} {unit}')
        self.preview_working()

    def reset_abc(self):
        if self.original_document is None:
            self.load_source()
        if not self.original_document:
            return
        self.document = self.original_document
        self.abc.blockSignals(True); self.abc.setPlainText(self.original_document.text); self.abc.blockSignals(False)
        self.transpose_amount = 0
        self.transpose_label.setText('Transpose: Original')
        self.preview_working()

    def preview_working(self):
        document = self.working_document()
        if not document or document.error:
            self.status.setText(document.error if document else 'No SheetSage score available.')
            return
        self.document = document
        self.render_document(document)

    def show_score(self):
        document = self.working_document()
        self.render_document(document)

    def render_document(self, document):
        self.result = None
        self.export_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        if not document or document.error:
            if self.renderer: self.renderer.clear()
            self.status.setText(document.error if document else 'No SheetSage score available.')
            return
        try:
            if self.renderer is not None and self.renderer.broken:
                self.close_renderer()
            if self.renderer is None:
                from .score_renderer import ScoreRenderer
                self.renderer = ScoreRenderer(self)
                if self.layout.itemAt(self.layout.count()-1).spacerItem():
                    self.layout.takeAt(self.layout.count()-1)
                self.layout.addWidget(self.renderer.view, 1)
                self.renderer.ready.connect(self.rendered)
                self.renderer.failed.connect(self.failed)
                self.renderer.pdf_finished.connect(self.pdf_saved)
                self.renderer.pdf_failed.connect(self.pdf_error)
            self.status.setText('Rendering Working ABC locally…')
            self.renderer.render(document)
        except (ImportError, RuntimeError, OSError) as exc:
            self.failed('Score renderer unavailable: '+str(exc))

    def rendered(self, result):
        self.result = result
        self.export_button.setEnabled(True)
        self.pdf_button.setEnabled(self.renderer is not None and self.renderer.pdf_job is None)
        warnings = [re.sub('<[^>]+>', '', w) for w in result.get('warnings', [])]
        self.status.setText(f"Working ABC • {len(result['svgs'])} systems • "
                            f"{'cached' if result['cached'] else 'rendered'} {result['elapsed_ms']:.0f} ms"
                            + (' • Renderer warnings: '+ ' | '.join(warnings[:3]) if warnings else ''))
        self.status.setToolTip('\n'.join(warnings) or self.document.location)

    def close_renderer(self):
        self.pdf_button.setEnabled(False)
        if self.renderer is not None:
            if self.renderer.view is not None:
                self.layout.removeWidget(self.renderer.view)
            self.renderer.close()
            self.renderer.deleteLater()
            self.renderer = None

    def closeEvent(self, event):
        self.close_renderer()
        super().closeEvent(event)

    def failed(self, message):
        self.result = None
        self.export_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        self.status.setText(message+' Working ABC remains unchanged.')

    def zoom(self, factor=1., reset=False):
        self.fit = False
        self.scale = 1. if reset else min(3., max(.4, self.scale*factor))
        self.apply_size()

    def fit_width(self):
        self.fit = True
        self.apply_size()

    def apply_size(self):
        self.size_label.setText('Fit Width' if self.fit else f'{self.scale:.0%}')
        if self.renderer: self.renderer.sizing(self.fit, self.scale)

    def export_svg(self):
        if not self.result or not self.document:
            return
        name, _ = QFileDialog.getSaveFileName(self, 'Export score as SVG',
                                             str(self.source.root.parent / 'score.svg'), 'SVG score (*.svg)')
        if not name: return
        try:
            target = Path(name).resolve()
            if target.suffix.lower() != '.svg' or target.is_relative_to(self.source.root):
                raise ValueError('Choose a .svg file outside the project folder to protect project artifacts.')
            from .score_renderer import combined_svg
            target.write_bytes(combined_svg(self.result['svgs']))
            self.status.setText('Exported vector score: '+str(target))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Score export failed', str(exc))

    def export_pdf(self):
        if not self.result or not self.document or not self.renderer or self.renderer.pdf_job is not None:
            return
        name, _ = QFileDialog.getSaveFileName(self, 'Export score as PDF',
                                             str(self.source.root.parent / 'score.pdf'), 'PDF score (*.pdf)')
        if not name:
            return
        target = Path(name).resolve()
        if not target.suffix:
            target = target.with_suffix('.pdf')
            if target.exists() and QMessageBox.question(self, 'Replace PDF?',
                    f'Replace the existing PDF?\n{target}', QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        if target.suffix.lower() != '.pdf' or target.is_relative_to(self.source.root):
            self.pdf_error('Choose a .pdf file outside the project folder to protect project artifacts.'); return
        self.pdf_button.setEnabled(False)
        self.status.setText('Exporting score as PDF…')
        self.renderer.export_pdf(target)

    def pdf_saved(self, path):
        self.pdf_button.setEnabled(bool(self.result and self.renderer and not self.renderer.closed))
        self.status.setText('Exported PDF score: '+path)

    def pdf_error(self, message):
        self.pdf_button.setEnabled(bool(self.result and self.renderer and not self.renderer.closed))
        self.status.setText('PDF export failed: '+message)
        QMessageBox.warning(self, 'PDF export failed', message)
