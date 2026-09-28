"""Safe subprocess boundary for the bundled abcMIDI abc2abc utility."""
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXECUTABLE = ROOT / 'engines' / 'abcmidi' / 'abc2abc.exe'
MAX_SEMITONES = 12


class ABCTransposeError(ValueError):
    pass


_PITCH_CLASSES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
_SHARP_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
_FLAT_NAMES = ('C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B')
_FLAT_KEYS = {'F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb', 'Cb',
              'Dm', 'Gm', 'Cm', 'Fm', 'Bbm', 'Ebm', 'Abm'}
_SHARP_KEYS = {'G', 'D', 'A', 'E', 'B', 'F#', 'C#',
               'Em', 'Bm', 'F#m', 'C#m', 'G#m', 'D#m', 'A#m'}
_TOKEN = re.compile(r'\[([KV]):([^\]]+)\]|"([^"\r\n]*)"')
_CHORD = re.compile(r'^([A-G])([#b]*)([^/]*)(?:/([A-G])([#b]*))?$')
_VOICE_FIELD = re.compile(r'(?m)(^[ \t]*V:|\[V:)([ \t]*)')
_KEY_FIELD = re.compile(r'(?m)(^[ \t]*K:|\[K:)([ \t]*)([^\s\]]+)')


def _key_preference(value):
    token = value.strip().split()[0] if value.strip() else ''
    match = re.match(r'^([A-G](?:#|b)?)(.*)$', token)
    if not match:
        return None
    root, mode = match.groups()
    lower_mode = mode.lower()
    minor = lower_mode in ('m', 'min', 'minor') or lower_mode.startswith('aeo')
    name = root + ('m' if minor else '')
    if name in _FLAT_KEYS or 'b' in root:
        return 'flat'
    if name in _SHARP_KEYS or '#' in root:
        return 'sharp'
    return None


def _normal_root(letter, accidentals, preference):
    if not accidentals:
        return letter
    pitch = (_PITCH_CLASSES[letter] + accidentals.count('#') - accidentals.count('b')) % 12
    if preference == 'flat':
        return _FLAT_NAMES[pitch]
    if preference == 'sharp':
        return _SHARP_NAMES[pitch]
    if len(accidentals) > 1:
        names = _FLAT_NAMES if accidentals.startswith('b') else _SHARP_NAMES
        return names[pitch]
    return letter + accidentals


def _chord_suffix(value):
    """Exclude quoted annotations while accepting common and extended chord qualities."""
    if not value:
        return True
    lower = value.lower()
    return (value[0].isdigit() or value[0] in '+-(°øΔ' or
            lower.startswith(('m', 'min', 'major', 'maj', 'dim', 'aug', 'sus', 'add', 'no')))


def _normalize_chord(value, preference):
    match = _CHORD.fullmatch(value)
    if not match or not _chord_suffix(match.group(3)):
        return value
    letter, accidentals, suffix, bass_letter, bass_accidentals = match.groups()
    result = _normal_root(letter, accidentals, preference) + suffix
    if bass_letter:
        result += '/' + _normal_root(bass_letter, bass_accidentals, preference)
    return result


def normalize_chord_symbols(text):
    """Normalize only quoted chord roots, following global and per-voice key context."""
    global_preference = None
    voice_preferences = {}
    current_voice = None
    header = True
    result = []
    for line in text.splitlines(keepends=True):
        field = re.match(r'^\s*([KV]):\s*(\S+)', line)
        if field:
            kind, value = field.groups()
            if kind == 'V':
                if not header:
                    current_voice = value
            else:
                preference = _key_preference(value)
                if header or current_voice is None:
                    global_preference = preference
                else:
                    voice_preferences[current_voice] = preference
                header = False

        def replace(match):
            nonlocal current_voice, global_preference
            kind, value, quoted = match.groups()
            if kind == 'V':
                current_voice = value.strip().split()[0]
                return match.group(0)
            if kind == 'K':
                preference = _key_preference(value)
                if current_voice is None:
                    global_preference = preference
                else:
                    voice_preferences[current_voice] = preference
                return match.group(0)
            preference = voice_preferences.get(current_voice, global_preference)
            return '"' + _normalize_chord(quoted, preference) + '"'

        result.append(_TOKEN.sub(replace, line))
    return ''.join(result)


def restore_sheetsage_format(source, transposed):
    """Restore only source V:/K: conventions and newline style after abc2abc."""
    voice_spaces = [match.group(2) for match in _VOICE_FIELD.finditer(source)]
    source_keys = [(match.group(2), match.group(3)) for match in _KEY_FIELD.finditer(source)]
    voice_index = 0
    key_index = 0

    def voice(match):
        nonlocal voice_index
        spacing = voice_spaces[voice_index] if voice_index < len(voice_spaces) else (voice_spaces[0] if voice_spaces else match.group(2))
        voice_index += 1
        return match.group(1) + spacing

    def key(match):
        nonlocal key_index
        spacing, original = (source_keys[key_index] if key_index < len(source_keys)
                             else (match.group(2), ''))
        key_index += 1
        value = match.group(3)
        original_has_maj = bool(re.fullmatch(r'[A-G](?:#|b)?maj', original, re.IGNORECASE))
        if not original_has_maj:
            ordinary_major = re.fullmatch(r'([A-G](?:#|b)?)maj', value, re.IGNORECASE)
            if ordinary_major:
                value = ordinary_major.group(1)
        return match.group(1) + spacing + value

    restored = _VOICE_FIELD.sub(voice, transposed)
    restored = _KEY_FIELD.sub(key, restored)
    newline = '\r\n' if '\r\n' in source else ('\r' if '\r' in source else '\n')
    terminal = source.endswith(('\n', '\r'))
    restored = newline.join(restored.splitlines())
    return restored + newline if terminal else restored


def transpose_abc(text, semitones, executable=DEFAULT_EXECUTABLE):
    """Return rewritten ABC from abc2abc without touching a project artifact."""
    if type(text) is not str or not text.strip():
        raise ABCTransposeError('Working ABC is empty.')
    if type(semitones) is not int or semitones == 0 or abs(semitones) > MAX_SEMITONES:
        raise ABCTransposeError('Transpose amount must be an integer from -12 to +12, excluding zero.')
    executable = Path(executable).resolve()
    if not executable.is_file():
        raise ABCTransposeError('abc2abc is unavailable. Run Install.bat to install the ABC transposition runtime.')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        with tempfile.TemporaryDirectory(prefix='ComfyMax-MusicLab-abc2abc-') as directory:
            source = Path(directory) / 'working.abc'
            source.write_text(normalize_chord_symbols(text), encoding='utf-8', newline='')
            result = subprocess.run(
                [str(executable), str(source), '-t', str(semitones)],
                cwd=directory, stdin=subprocess.DEVNULL, capture_output=True,
                timeout=15, creationflags=flags,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ABCTransposeError(f'abc2abc could not transpose the Working ABC: {exc}') from exc
    stderr = result.stderr.decode('utf-8', errors='replace').strip()
    if result.returncode:
        detail = stderr or f'exit code {result.returncode}'
        raise ABCTransposeError('abc2abc failed: ' + detail)
    try:
        output = result.stdout.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ABCTransposeError('abc2abc returned non-UTF-8 output.') from exc
    if stderr:
        raise ABCTransposeError('abc2abc reported an error: ' + stderr)
    if not output.strip() or not any(line.startswith('K:') for line in output.splitlines()):
        raise ABCTransposeError('abc2abc returned invalid ABC without a key signature.')
    return restore_sheetsage_format(text, normalize_chord_symbols(output))
