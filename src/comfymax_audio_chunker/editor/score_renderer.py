"""Optional, asynchronous local SVG renderer. Chromium owns engraving work."""
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, Signal, QTimer, QUrl, Qt, QCoreApplication

VERSION = 'abc2svg-1.22.1/musiclab-1'
OPTIONS = dict(pagewidth=950, fullsvg='musiclab')
ASSETS = Path(__file__).with_name('score_assets')


def cache_key(document, options=None, version=VERSION):
    return hashlib.sha256(json.dumps([document.sha256, version, options or OPTIONS],
                                    sort_keys=True).encode()).hexdigest()


class RenderCache:
    """Bounded in-memory derivatives; no user music is written to disk."""
    def __init__(self, max_bytes=16_000_000):
        self.items = OrderedDict()
        self.max_bytes = max_bytes

    def get(self, key):
        value = self.items.get(key)
        if value is not None:
            self.items.move_to_end(key)
        return value

    def put(self, key, value):
        self.items[key] = value
        self.items.move_to_end(key)
        while len(self.items) > 4 or sum(len(json.dumps(v).encode()) for v in self.items.values()) > self.max_bytes:
            self.items.popitem(last=False)


def combined_svg(svgs):
    """Stack the renderer's vector systems; no interpretation of notation."""
    ns = 'http://www.w3.org/2000/svg'
    ET.register_namespace('', ns)
    root = ET.Element('{'+ns+'}svg', version='1.1')
    y, width = 0., 0.
    for index, text in enumerate(svgs):
        svg = ET.fromstring(text)
        box = svg.get('viewBox')
        w, h = ([float(v) for v in box.split()][2:] if box else
                [float(svg.get('width', '0')), float(svg.get('height', '0'))])
        if w <= 0 or h <= 0:
            raise ValueError('Renderer returned invalid SVG dimensions')
        # SVG fragment identifiers must remain unique in the combined document.
        ids = {n.get('id'): f'system{index}-{n.get("id")}' for n in svg.iter() if n.get('id')}
        for node in svg.iter():
            for name, value in list(node.attrib.items()):
                if name == 'id':
                    node.set(name, ids[value])
                else:
                    for old, new in ids.items():
                        value = value.replace(f'url(#{old})', f'url(#{new})')
                        if value == '#'+old: value = '#'+new
                    node.set(name, value)
        svg.set('x', '0'); svg.set('y', str(y))
        svg.set('width', str(w)); svg.set('height', str(h))
        root.append(svg); y += h; width = max(width, w)
    if not y:
        raise ValueError('No rendered score to export')
    root.set('viewBox', f'0 0 {width} {y}')
    root.set('width', str(width)); root.set('height', str(y))
    ET.SubElement(root, '{'+ns+'}metadata').text = (ASSETS / 'OFL.font.txt').read_text(encoding='utf-8')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


class ScoreRenderer(QObject):
    ready = Signal(object)
    failed = Signal(str)
    pdf_finished = Signal(str)
    pdf_failed = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        # Import only when a score is requested. Basic editing needs no WebEngine.
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor
        from PySide6.QtWebEngineWidgets import QWebEngineView

        entry = QUrl.fromLocalFile(str(ASSETS / 'viewer.html'))
        allowed = {str((ASSETS / n).resolve()).lower() for n in
                   ('viewer.html', 'viewer.js', 'abc2svg-1.js')}

        class LocalOnly(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info):
                url = info.requestUrl()
                font = url.scheme() == 'data' and info.resourceType() == info.ResourceType.ResourceTypeFontResource
                info.block(not (font or (url.isLocalFile() and str(Path(url.toLocalFile()).resolve()).lower() in allowed)))

        class Page(QWebEnginePage):
            def acceptNavigationRequest(self, url, kind, main):
                return main and url == entry

        for name in ('viewer.html', 'viewer.js', 'abc2svg-1.js'):
            if not (ASSETS / name).is_file():
                raise RuntimeError('Bundled Score Viewer assets unavailable: '+name)
        self.view = QWebEngineView(parent)
        self.view.setContextMenuPolicy(Qt.NoContextMenu)
        self.profile = QWebEngineProfile(self.view)  # off-the-record, no persistent browser storage
        self.blocker = LocalOnly(self.profile)
        self.profile.setUrlRequestInterceptor(self.blocker)
        self.profile.downloadRequested.connect(lambda download: download.cancel())
        self.page = Page(self.profile, self.view)
        self.view.setPage(self.page)
        for attr in ('LocalContentCanAccessRemoteUrls', 'LocalContentCanAccessFileUrls',
                     'JavascriptCanOpenWindows', 'JavascriptCanAccessClipboard', 'PluginsEnabled',
                     'FullScreenSupportEnabled', 'WebGLEnabled', 'LocalStorageEnabled'):
            self.page.settings().setAttribute(getattr(QWebEngineSettings, attr), False)
        # Local trusted scripts must load; the interceptor restricts exact filenames.
        self.page.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        self.page.renderProcessTerminated.connect(lambda *_: self._fail('Score renderer process stopped. Reopen Score to retry.'))
        self.view.loadFinished.connect(self._loaded)
        self.cache = RenderCache()
        self.loaded = False
        self.closed = False
        self.broken = False
        self.pending = None
        self.generation = 0
        self.score_ready = False
        self.pdf_job = None
        self.pdf_files = {}
        self.page.pdfPrintingFinished.connect(self._pdf_complete)
        self.pdf_timeout = QTimer(self)
        self.pdf_timeout.setSingleShot(True)
        self.pdf_timeout.setInterval(30000)
        self.pdf_timeout.timeout.connect(lambda: self._pdf_error('PDF export timed out.'))
        self.fit, self.scale = True, 1.
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(15000)
        self.timeout.timeout.connect(lambda: self._fail('Score rendering timed out. ABC remains available.'))
        self.timeout.start()
        if parent is not None:
            parent.destroyed.connect(self.close)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.close)
        self.view.load(entry)

    def close(self):
        """Release Chromium resources synchronously, on the owning GUI thread.

        Deferred deletion alone is insufficient once the event loop is exiting.
        The profile must outlive its page, regardless of QObject child order.
        """
        if self.closed:
            return
        from shiboken6 import delete
        self.closed = True
        self.pdf_job = None
        self.pdf_timeout.stop()
        self.generation += 1  # Page destruction may complete pending JS callbacks.
        self.pending = None
        self.loaded = False
        self.timeout.stop()
        self.page.blockSignals(True)
        self.view.blockSignals(True)
        self.view.stop()
        # Detach ownership before setPage(None), so deletion is explicit.
        self.page.setParent(None)
        self.view.setPage(None)
        delete(self.page)
        self.page = None
        for directory, _, _ in self.pdf_files.values():
            directory.remove()
        self.pdf_files.clear()
        self.profile.setUrlRequestInterceptor(None)
        delete(self.profile)
        self.profile = None
        self.blocker = None
        delete(self.view)
        self.view = None
        self.cache.items.clear()

    def _loaded(self, ok):
        if self.closed:
            return
        if not ok:
            self._fail('Cannot load the local score renderer.'); return
        self.loaded = True
        self.timeout.stop()
        if self.pending:
            self.render(self.pending)

    def _fail(self, message):
        if self.closed:
            return
        self.timeout.stop()
        self.generation += 1
        self.broken = True
        self.score_ready = False
        self.failed.emit(message)

    def clear(self):
        if self.closed:
            return
        self.pending = None
        self.score_ready = False
        self.generation += 1
        self.timeout.stop()
        if self.loaded:
            self.page.runJavaScript('musiclab.clear()')

    def sizing(self, fit, scale):
        if self.closed:
            return
        self.fit, self.scale = fit, scale
        if self.loaded:
            self.page.runJavaScript(f'musiclab.size({json.dumps(fit)}, {scale})')

    def render(self, document):
        if self.closed:
            return
        self.pending = document
        self.score_ready = False
        if not self.loaded:
            return
        self.generation += 1
        generation = self.generation
        key = cache_key(document)
        start = time.perf_counter()
        cached = self.cache.get(key)
        self.timeout.start()

        def complete(raw):
            if generation != self.generation:
                return  # A project/tab/source change superseded this callback.
            self.timeout.stop()
            try:
                result = cached if cached is not None else json.loads(raw)
                if not isinstance(result, dict) or result.get('error') or not result.get('svgs'):
                    raise ValueError(result.get('error', 'Renderer returned no score') if isinstance(result, dict) else 'Invalid renderer response')
                if cached is None:
                    self.cache.put(key, result)
                self.sizing(self.fit, self.scale)
                self.score_ready = True
                self.ready.emit({**result, 'cached': cached is not None,
                                 'elapsed_ms': (time.perf_counter()-start)*1000})
            except (ValueError, TypeError) as exc:
                self._fail('Score rendering failed: '+str(exc))

        if cached is not None:
            self.page.runJavaScript('musiclab.display('+json.dumps(cached['svgs'])+')', complete)
        else:
            self.page.runJavaScript('musiclab.render('+json.dumps(document.text)+','+json.dumps(OPTIONS)+')', complete)

    def _pdf_error(self, message):
        if self.closed:
            return
        self.pdf_timeout.stop()
        self.pdf_job = None
        self.pdf_failed.emit(message)

    def export_pdf(self, target):
        """Print the current DOM, asynchronously; never rerender or alter ABC."""
        if self.closed:
            return
        if self.pdf_job is not None:
            return
        if not self.score_ready:
            self._pdf_error('No valid rendered score available.'); return
        from PySide6.QtCore import QMarginsF, QTemporaryDir
        from PySide6.QtGui import QPageLayout, QPageSize
        if self.pdf_files:
            self._pdf_error('Previous PDF export is still finishing. Please retry shortly.'); return
        target = Path(target)
        if target.suffix.lower() != '.pdf':
            self._pdf_error('Choose a .pdf filename.'); return
        directory = QTemporaryDir()
        if not directory.isValid():
            self._pdf_error('Cannot create a temporary PDF file.'); return
        path = str(Path(directory.filePath('score.pdf')).resolve())
        self.pdf_files[path] = (directory, self.generation, target)
        self.pdf_job = path
        layout = QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait,
                             QMarginsF(15, 15, 15, 15), QPageLayout.Millimeter)
        self.pdf_timeout.start()
        try:
            # The bytes-callback overload crashes in PySide6 6.8.3 when a page
            # is destroyed mid-print. The file overload + Qt signal is safe.
            self.page.printToPdf(path, layout)
        except (RuntimeError, TypeError, ValueError) as exc:
            self.pdf_files.pop(path)[0].remove()
            self._pdf_error('PDF export failed: '+str(exc))

    def _pdf_complete(self, path, success):
        from PySide6.QtCore import QSaveFile, QIODevice
        path = str(Path(path).resolve())
        record = self.pdf_files.pop(path, None)
        if record is None:
            return
        directory, generation, target = record
        try:
            if self.closed or self.pdf_job != path:
                return
            self.pdf_timeout.stop()
            if generation != self.generation or not self.score_ready:
                self._pdf_error('Score changed during PDF export; export cancelled.'); return
            payload = Path(path).read_bytes() if success else b''
            if not payload.startswith(b'%PDF-'):
                self._pdf_error('The score renderer could not create a PDF.'); return
            output = QSaveFile(str(target))
            if not output.open(QIODevice.WriteOnly):
                self._pdf_error('Cannot save PDF: '+output.errorString()); return
            if output.write(payload) != len(payload):
                message = output.errorString()
                output.cancelWriting()
                self._pdf_error('Cannot write PDF: '+message); return
            if not output.commit():
                self._pdf_error('Cannot finish PDF: '+output.errorString()); return
            self.pdf_job = None
            self.pdf_finished.emit(str(target))
        except OSError as exc:
            self._pdf_error('Cannot read generated PDF: '+str(exc))
        finally:
            directory.remove()
