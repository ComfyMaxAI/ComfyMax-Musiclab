# Bundled notation renderer

abc2svg 1.22.1, Copyright (C) 2014–2022 Jean-François Moine.
Library license: GNU Lesser General Public License version 3 or later.
See `COPYING.LESSER` and its referenced GPL terms in `COPYING`.

The embedded notation font is Copyright (c) 2018–2019 Jean-François Moine,
SIL Open Font License 1.1. See `OFL.font.txt`; its editable FontForge source
is included in the source archive. This separate font license permits embedding.

The unmodified `abc2svg-1.js` (including its embedded notation font) is loaded as
a separate local library. MusicLab's `viewer.js` is a separate adapter, not a
modification of that library. Users may replace the library to test modifications;
there is no signature lock, obfuscation or prohibition on reverse engineering
for debugging such modifications. A changed library requires clearing the
in-memory cache/restarting MusicLab and updating the renderer version constant.

`UPSTREAM.json` records the pinned npm package and upstream source provenance.
`abc2svg-1.22.1-core-source.zip` contains the corresponding upstream core/font
sources, notices, package metadata and build rules from tag v1.22.1. To rebuild
the core on a POSIX build system with ninja/samurai and standard shell utilities:
extract it and run `NOMIN=1 ninja abc2svg-1.js`. Minification is optional. The
included `font.js` is already generated; modifying the font additionally requires
FontForge and the `font` build target. No soundfonts, player or inference model
are bundled or used by MusicLab's viewer.

When redistributing MusicLab, retain this notice, both license texts, the separate
library, font license and corresponding source archive. Existing Qt/PySide licensing
requirements still apply to redistribution of that existing dependency.

Upstream: https://chiselapp.com/user/moinejf/repository/abc2svg
