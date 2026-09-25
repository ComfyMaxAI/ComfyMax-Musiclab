"""Optional, asynchronous local SVG renderer. Chromium owns engraving work."""
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, Signal, QTimer, QUrl, Qt

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
        self.broken = False
        self.pending = None
        self.generation = 0
        self.fit, self.scale = True, 1.
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(15000)
        self.timeout.timeout.connect(lambda: self._fail('Score rendering timed out. ABC remains available.'))
        self.timeout.start()
        self.view.load(entry)

    def _loaded(self, ok):
        if not ok:
            self._fail('Cannot load the local score renderer.'); return
        self.loaded = True
        self.timeout.stop()
        if self.pending:
            self.render(self.pending)

    def _fail(self, message):
        self.timeout.stop()
        self.generation += 1
        self.broken = True
        self.failed.emit(message)

    def clear(self):
        self.pending = None
        self.generation += 1
        self.timeout.stop()
        if self.loaded:
            self.page.runJavaScript('musiclab.clear()')

    def sizing(self, fit, scale):
        self.fit, self.scale = fit, scale
        if self.loaded:
            self.page.runJavaScript(f'musiclab.size({json.dumps(fit)}, {scale})')

    def render(self, document):
        self.pending = document
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
                self.ready.emit({**result, 'cached': cached is not None,
                                 'elapsed_ms': (time.perf_counter()-start)*1000})
            except (ValueError, TypeError) as exc:
                self._fail('Score rendering failed: '+str(exc))

        if cached is not None:
            self.page.runJavaScript('musiclab.display('+json.dumps(cached['svgs'])+')', complete)
        else:
            self.page.runJavaScript('musiclab.render('+json.dumps(document.text)+','+json.dumps(OPTIONS)+')', complete)
