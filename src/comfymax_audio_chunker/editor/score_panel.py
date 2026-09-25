"""Read-only notation views; deliberately disconnected from the transport."""
import re
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QFileDialog, QMessageBox
from .score_source import SheetSageABCSource


class ScorePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.renderer = None
        self.source = None
        self.document = None
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
        self.layout.addLayout(bar)
        self.status = QLabel('No SheetSage score available.'); self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText); self.layout.addWidget(self.status)
        self.layout.addStretch()
        self.abc = QPlainTextEdit(); self.abc.setReadOnly(True)
        self.abc.setPlaceholderText('No SheetSage score available.')

    def set_project(self, doc):
        self.source = (SheetSageABCSource(doc.root, doc.data.get('music_analysis', {}).get('sheet_sage'))
                       if doc else None)
        self.document = None
        self.result = None
        self.export_button.setEnabled(False)
        self.abc.clear()
        self.status.setText('Open Score to view the preserved SheetSage ABC.' if doc else 'No SheetSage score available.')
        if self.renderer:
            self.renderer.clear()

    def load_source(self):
        self.document = self.source.read() if self.source else None
        self.abc.setPlainText(self.document.text if self.document else '')
        return self.document

    def show_score(self):
        document = self.load_source()
        self.result = None
        self.export_button.setEnabled(False)
        if not document or document.error:
            if self.renderer: self.renderer.clear()
            self.status.setText(document.error if document else 'No SheetSage score available.')
            return
        try:
            if self.renderer is not None and self.renderer.broken:
                self.layout.removeWidget(self.renderer.view)
                self.renderer.view.hide()
                self.renderer.view.deleteLater()
                self.renderer.deleteLater()
                self.renderer = None
            if self.renderer is None:
                from .score_renderer import ScoreRenderer
                self.renderer = ScoreRenderer(self)
                if self.layout.itemAt(self.layout.count()-1).spacerItem():
                    self.layout.takeAt(self.layout.count()-1)
                self.layout.addWidget(self.renderer.view, 1)
                self.renderer.ready.connect(self.rendered)
                self.renderer.failed.connect(self.failed)
            self.status.setText('Rendering stored SheetSage ABC locally…')
            self.renderer.render(document)
        except (ImportError, RuntimeError, OSError) as exc:
            self.failed('Score renderer unavailable: '+str(exc))

    def rendered(self, result):
        self.result = result
        self.export_button.setEnabled(True)
        warnings = [re.sub('<[^>]+>', '', w) for w in result.get('warnings', [])]
        self.status.setText(f"SheetSage ABC • {len(result['svgs'])} systems • "
                            f"{'cached' if result['cached'] else 'rendered'} {result['elapsed_ms']:.0f} ms"
                            + (' • Renderer warnings: '+ ' | '.join(warnings[:3]) if warnings else ''))
        self.status.setToolTip('\n'.join(warnings) or self.document.location)

    def failed(self, message):
        self.result = None
        self.export_button.setEnabled(False)
        self.status.setText(message+' Original source remains in ABC.')

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
