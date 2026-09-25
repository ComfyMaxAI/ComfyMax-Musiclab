"""Notation source boundary. No inference, MIDI conversion or project mutation."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ScoreDocument:
    payload: bytes
    location: str
    error: str = ''

    @property
    def sha256(self):
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def text(self):
        return self.payload.decode('utf-8-sig', errors='replace')


class ScoreSource(Protocol):
    def read(self) -> ScoreDocument: ...


class SheetSageABCSource:
    def __init__(self, root, evidence):
        self.root = Path(root).resolve()
        self.evidence = evidence or {}

    def read(self):
        items = [a for a in self.evidence.get('raw_artifacts', [])
                 if isinstance(a, dict) and str(a.get('path', '')).endswith('/score.abc')]
        if not items:
            return ScoreDocument(b'', '', 'No SheetSage score available.')
        if len(items) != 1:
            return ScoreDocument(b'', '', 'Ambiguous SheetSage ABC artifacts.')
        item = items[0]
        relative = item['path']
        # Independent of the optional inference runtime and its Python modules.
        if ('\\' in relative or ':' in relative or '..' in relative.split('/') or
                not relative.startswith('music_analysis/sheetsage/')):
            return ScoreDocument(b'', '', 'Unsafe ABC artifact path.')
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            return ScoreDocument(b'', '', 'ABC artifact escapes the project.')
        try:
            if path.stat().st_size > 2_000_000:
                return ScoreDocument(b'', str(path), 'ABC exceeds the 2 MB viewer limit.')
            payload = path.read_bytes()
        except OSError as exc:
            return ScoreDocument(b'', str(path), f'Cannot read SheetSage ABC: {exc}')
        error = ''
        if hashlib.sha256(payload).hexdigest() != item.get('sha256'):
            error = 'ABC checksum mismatch. Original text is available in ABC; rendering disabled.'
        elif not payload.strip():
            error = 'SheetSage ABC is empty.'
        else:
            try:
                payload.decode('utf-8-sig')
            except UnicodeDecodeError:
                error = 'ABC is not valid UTF-8. Original bytes have not been changed.'
        return ScoreDocument(payload, str(path), error)
