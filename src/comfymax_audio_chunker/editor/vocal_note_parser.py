"""Small, source-preserving ABC parser for the Vocal note deletion editor.

This is deliberately not a general ABC renderer.  It identifies timed note and
rest tokens in the explicit ``V: Vocal`` voice and retains their exact source
ranges so edits never require serialising the rest of the tune.
"""
from dataclasses import dataclass
from fractions import Fraction
import re


_NOTE = re.compile(r"(?P<acc>\^{1,2}|_{1,2}|=)?(?P<letter>[A-Ga-g])(?P<oct>[,']*)(?P<len>\d+(?:/\d*)?|/+\d*)?")
_REST = re.compile(r"[zZxX](?P<len>\d+(?:/\d*)?|/+\d*)?")
_FIELD = re.compile(r"^\s*([A-Za-z]):\s*(.*)$")
_VOICE = re.compile(r"^([^\s]+)")


class VocalABCError(ValueError):
    pass


@dataclass
class VocalNote:
    start: Fraction
    duration: Fraction
    pitch: int
    abc_source_range: tuple[int, int]
    abc_token: str
    duration_text: str
    tied_from_previous: bool = False
    tied_to_next: bool = False
    tie_before_range: tuple[int, int] | None = None
    tie_after_range: tuple[int, int] | None = None

    @property
    def rest_token(self):
        return 'z' + self.duration_text


@dataclass
class VocalPart:
    abc: str
    notes: list[VocalNote]
    total_duration: Fraction
    tempo_bpm: Fraction = Fraction(120)

    def delete_notes(self, notes):
        """Replace selected source tokens only, preserving every other byte/char."""
        ranges = {note.abc_source_range: note.rest_token for note in notes}
        # A tie cannot terminate at or start from a rest.  Removing only the
        # adjacent hyphen(s) keeps both surviving notes and all spacing intact.
        for note in notes:
            if note.tie_before_range:
                ranges[note.tie_before_range] = ' '
            if note.tie_after_range:
                ranges[note.tie_after_range] = ' '
        result = self.abc
        for (start, end), replacement in sorted(ranges.items(), reverse=True):
            result = result[:start] + replacement + result[end:]
        return result


def _length(text):
    if not text:
        return Fraction(1)
    if '/' not in text:
        return Fraction(int(text), 1)
    if set(text) == {'/'}:
        return Fraction(1, 2 ** len(text))
    numerator, denominator = text.split('/', 1)
    return Fraction(int(numerator or 1), int(denominator or 2))


def _default_length(meter):
    # ABC 2.1 default: 1/8 when the meter is at least 3/4, otherwise 1/16.
    try:
        top, bottom = meter.split('/', 1)
        return Fraction(1, 8) if Fraction(int(top), int(bottom)) >= Fraction(3, 4) else Fraction(1, 16)
    except (ValueError, ZeroDivisionError):
        return Fraction(1, 8)


def _key_accidentals(key):
    tonic = re.match(r'([A-Ga-g])([#b]?)(.*)', key.strip())
    if not tonic:
        return {}
    name = tonic.group(1).upper() + tonic.group(2)
    mode = tonic.group(3).lower()
    if mode.startswith('m') and not mode.startswith(('mix', 'maj')):
        name = {'A': 'C', 'E': 'G', 'B': 'D', 'F#': 'A', 'C#': 'E', 'G#': 'B',
                'D#': 'F#', 'A#': 'C#', 'D': 'F', 'G': 'Bb', 'C': 'Eb',
                'F': 'Ab', 'Bb': 'Db', 'Eb': 'Gb'}.get(name, name)
    count = {'Cb': -7, 'Gb': -6, 'Db': -5, 'Ab': -4, 'Eb': -3, 'Bb': -2,
             'F': -1, 'C': 0, 'G': 1, 'D': 2, 'A': 3, 'E': 4, 'B': 5,
             'F#': 6, 'C#': 7}.get(name, 0)
    order = 'FCGDAEB' if count > 0 else 'BEADGCF'
    return {letter: (1 if count > 0 else -1) for letter in order[:abs(count)]}


def _pitch(match, key_accidentals, measure_accidentals):
    letter = match.group('letter')
    value = {'C': 60, 'D': 62, 'E': 64, 'F': 65, 'G': 67, 'A': 69, 'B': 71}[letter.upper()]
    if letter.islower():
        value += 12
    value += 12 * (match.group('oct').count("'") - match.group('oct').count(','))
    accidental = match.group('acc') or ''
    accidental_key = (letter.upper(), value // 12)
    if accidental.startswith('^'):
        adjustment = len(accidental)
    elif accidental.startswith('_'):
        adjustment = -len(accidental)
    elif accidental == '=':
        adjustment = 0
    else:
        adjustment = measure_accidentals.get(accidental_key, key_accidentals.get(letter.upper(), 0))
    if accidental:
        measure_accidentals[accidental_key] = adjustment
    value += adjustment
    return value


def _tuplet(spec):
    values = [int(value) for value in spec.split(':') if value]
    p = values[0]
    q = values[1] if len(values) > 1 else (3 if p in (2, 4, 8) else 2)
    r = values[2] if len(values) > 2 else p
    return Fraction(q, p), r


def _tempo(value):
    """Return quarter-note BPM for common ABC Q: forms."""
    match = re.search(r'(?:(\d+)\s*/\s*(\d+)\s*=\s*)?(\d+(?:\.\d+)?)', value)
    if not match:
        return None
    beat = Fraction(int(match.group(1)), int(match.group(2))) if match.group(1) else Fraction(1, 4)
    return Fraction(match.group(3)) * beat * 4


def parse_vocal_notes(abc):
    """Return notes from explicit Vocal sections with exact offsets and timing."""
    meter = '4/4'
    key = 'C'
    key_accidentals = {}
    measure_accidentals = {}
    unit = None
    current_voice = None
    body = False
    cursor = Fraction(0)
    maximum = Fraction(0)
    notes = []
    tuplet_factor, tuplet_left = Fraction(1), 0
    broken_next = Fraction(1)
    previous = None
    pending_tie = False
    tempo_bpm = Fraction(120)

    offset = 0
    for raw_line in abc.splitlines(keepends=True):
        line = raw_line.rstrip('\r\n')
        field = _FIELD.match(line)
        content_start = 0
        if field:
            name, value = field.groups()
            if name == 'M':
                meter = value.split()[0]
            elif name == 'L':
                try:
                    unit = _length(value.split()[0])
                except (ValueError, ZeroDivisionError):
                    pass
            elif name == 'K':
                key = value.split()[0]
                key_accidentals = _key_accidentals(key)
                measure_accidentals = {}
                body = True
            elif name == 'Q' and not body:
                parsed_tempo = _tempo(value)
                if parsed_tempo and parsed_tempo > 0:
                    tempo_bpm = parsed_tempo
            elif name == 'V':
                match = _VOICE.match(value)
                current_voice = match.group(1) if match else None
            offset += len(raw_line)
            continue
        if not body or current_voice != 'Vocal':
            offset += len(raw_line)
            continue

        base = unit or _default_length(meter)
        i = content_start
        while i < len(line):
            char = line[i]
            if char == '%':
                break
            if char == '"':
                end = line.find('"', i + 1)
                i = len(line) if end < 0 else end + 1
                continue
            if char in '!+':
                end = line.find(char, i + 1)
                i = len(line) if end < 0 else end + 1
                continue
            if char == '[':
                end = line.find(']', i + 1)
                if end >= 0 and ':' in line[i + 1:end]:
                    name, value = line[i + 1:end].split(':', 1)
                    if name.strip() == 'V':
                        match = _VOICE.match(value.strip())
                        current_voice = match.group(1) if match else None
                    elif name.strip() == 'L':
                        try: unit = _length(value.strip())
                        except (ValueError, ZeroDivisionError): pass
                    elif name.strip() == 'M':
                        meter = value.strip()
                    elif name.strip() == 'K':
                        key = value.strip().split()[0]
                        key_accidentals = _key_accidentals(key)
                        measure_accidentals = {}
                    i = end + 1
                    continue
                if end >= 0:
                    raise VocalABCError('Chord note groups in V: Vocal are not supported by the deletion editor.')
            if char == '{':
                raise VocalABCError('Grace-note groups in V: Vocal are not supported by the deletion editor.')
            if char == '&':
                raise VocalABCError('Overlaid voices in V: Vocal are not supported by the deletion editor.')
            if current_voice != 'Vocal':
                i += 1
                continue
            tuplet = re.match(r"\((\d+(?::\d*){0,2})", line[i:])
            if tuplet:
                tuplet_factor, tuplet_left = _tuplet(tuplet.group(1))
                i += tuplet.end()
                continue
            if char in '<>' and previous is not None:
                count = 1
                while i + count < len(line) and line[i + count] == char:
                    count += 1
                short = Fraction(1, 2 ** count)
                long = Fraction(2 ** (count + 1) - 1, 2 ** count)
                old = previous.duration
                previous.duration *= long if char == '>' else short
                cursor += previous.duration - old
                maximum = max(maximum, cursor)
                broken_next = short if char == '>' else long
                i += count
                continue
            if char == '-':
                if previous:
                    previous.tied_to_next = True
                    previous.tie_after_range = (offset + i, offset + i + 1)
                    pending_tie = True
                    pending_tie_range = (offset + i, offset + i + 1)
                i += 1
                continue
            if char == '|':
                measure_accidentals = {}
                i += 1
                continue
            note_match = _NOTE.match(line, i)
            rest_match = _REST.match(line, i) if not note_match else None
            match = note_match or rest_match
            if match:
                factor = tuplet_factor if tuplet_left else Fraction(1)
                duration = base * _length(match.group('len')) * factor * broken_next
                broken_next = Fraction(1)
                if tuplet_left:
                    tuplet_left -= 1
                    if not tuplet_left: tuplet_factor = Fraction(1)
                if note_match:
                    start, end = offset + match.start(), offset + match.end()
                    previous = VocalNote(cursor, duration, _pitch(match, key_accidentals, measure_accidentals),
                                         (start, end), match.group(0), match.group('len') or '', pending_tie,
                                         False, pending_tie_range if pending_tie else None, None)
                    notes.append(previous)
                else:
                    previous = None
                pending_tie = False
                pending_tie_range = None
                cursor += duration
                maximum = max(maximum, cursor)
                i = match.end()
                continue
            i += 1
        offset += len(raw_line)
    return VocalPart(abc, notes, maximum, tempo_bpm)
