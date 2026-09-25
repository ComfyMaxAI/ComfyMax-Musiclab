/* Local abc2svg adapter. Original ABC is passed unchanged, never used as HTML. */
'use strict';
(() => {
  const paper = () => document.getElementById('paper');
  const tags = new Set(['svg','g','path','rect','line','polyline','polygon','circle',
    'ellipse','text','tspan','defs','clipPath','title','desc','use','style']);
  function safeStyle(css) {
    // Only the renderer's embedded local notation font may use a CSS URL.
    const withoutFont = css.replace(/url\("data:application\/octet-stream;base64,[A-Za-z0-9+/=]+"\)/g, '');
    if (/url\s*\(|@import|expression\s*\(|\\|<|>/i.test(withoutFont)) return '';
    return css;
  }
  function clean(svg) {
    for (const node of Array.from(svg.querySelectorAll('*'))) {
      if (!tags.has(node.localName)) { node.remove(); continue; }
      if (node.localName === 'style') node.textContent = safeStyle(node.textContent);
    }
    for (const node of [svg, ...svg.querySelectorAll('*')]) {
      for (const attr of Array.from(node.attributes)) {
        const name = attr.name.toLowerCase(), value = attr.value;
        if (name.startsWith('on') || name === 'src' ||
            (name === 'style' && safeStyle(value) !== value) ||
            ((name === 'href' || name === 'xlink:href') && !/^#[\w-]+$/.test(value)) ||
            (name !== 'style' && /url\s*\(/i.test(value) && !/^url\(#[\w-]+\)$/.test(value)))
          node.removeAttribute(attr.name);
      }
    }
    svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    return svg;
  }
  window.musiclab = {
    render(abc, options) {
      const start = performance.now();
      paper().replaceChildren();
      try {
        if (typeof abc2svg === 'undefined') throw Error('Bundled abc2svg renderer unavailable.');
        if (!/^\s*X:\s*\d+/m.test(abc) || !/^\s*K:\s*\S+/m.test(abc))
          throw Error('Malformed ABC: a tune index (X:) and key (K:) are required.');
        // No executable extension blocks, embedded markup or include files.
        // CSP also forbids eval; no module loader/read_file hooks are installed.
        if (/^\s*(?:%%|I:)\s*(?:begin(?:js|svg|ps|ml|html)|abc-include|js)\b/im.test(abc))
          throw Error('Executable, embedded markup or include directives are not supported. Inspect ABC.');
        const chunks = [], warnings = [], symbols = [];
        const renderer = new abc2svg.Abc({
          img_out: s => chunks.push(s),
          errmsg: s => warnings.push(String(s)),
          anno_start: (type, from, to, x, y, w, h, s) => {
            renderer.out_svg(`<g data-score-kind="${type}">`);
            // Renderer diagnostics, never an audio clock or canonical model.
            symbols.push({type, voice:s.v, meter:type==='meter' ? s.a_meter : undefined});
          },
          anno_stop: () => renderer.out_svg('</g>')
        });
        renderer.tosvg('viewer-options', `%%fullsvg ${options.fullsvg}\n%%pagewidth ${options.pagewidth}\n`);
        renderer.tosvg('score.abc', abc);
        const result = [];
        for (const chunk of chunks) {
          if (!chunk.trim().startsWith('<svg')) continue;
          const doc = new DOMParser().parseFromString(chunk, 'image/svg+xml');
          if (doc.querySelector('parsererror')) throw Error('Renderer produced invalid SVG.');
          result.push(new XMLSerializer().serializeToString(clean(doc.documentElement)));
        }
        if (!result.length || !symbols.some(s => ['note','rest','Zrest'].includes(s.type)))
          throw Error('ABC contains no renderable notes or rests.');
        window.musiclab.display(result);
        return JSON.stringify({svgs:result, warnings, symbols, render_ms:performance.now()-start});
      } catch (e) {
        paper().replaceChildren();
        return JSON.stringify({error:String(e.message || e)});
      }
    },
    display(svgs) {
      paper().replaceChildren();
      for (const text of svgs) {
        const svg = new DOMParser().parseFromString(text, 'image/svg+xml').documentElement;
        paper().append(document.importNode(clean(svg), true));
      }
    },
    size(fit, scale) {
      paper().style.width = fit ? 'calc(100% - 32px)' : `${1000 * scale}px`;
    },
    clear() { paper().replaceChildren(); }
  };
})();
