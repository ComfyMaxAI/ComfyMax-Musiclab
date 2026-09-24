"""Additive SheetSage evidence. Never reads or alters the master beat/chord grid."""
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
import uuid

from .sheetsage_worker import PROTOCOL, sha256
from .sheetsage_runtime import APP_ROOT, managed_config

CONFIG = Path(__file__).resolve().parents[3]/'.cache'/'sheetsage-backend.json'


def strict_json(payload):
    def invalid_number(value):
        raise ValueError('Non-finite JSON number: '+value)
    return json.loads(payload, parse_constant=invalid_number)


def configuration():
    path = Path(os.environ.get('COMFYMAX_SHEETSAGE_CONFIG', CONFIG))
    if path.exists():
        result = strict_json(path.read_text(encoding='utf-8-sig'))
    elif 'COMFYMAX_SHEETSAGE_CONFIG' in os.environ:
        raise ValueError(f'SheetSage configuration not found: {path}')
    else:
        result = managed_config(APP_ROOT)
    if not isinstance(result, dict):
        raise ValueError('SheetSage configuration must be an object')
    for key in ('executable', 'model'):
        if isinstance(result.get(key),str) and not Path(result[key]).is_absolute():
            result[key] = str((APP_ROOT/result[key]).resolve())
        if not isinstance(result.get(key), str) or not Path(result[key]).is_file():
            raise ValueError(f'SheetSage {key} unavailable: {result.get(key)}')
    timeout = result.get('timeout_seconds', 1200)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 7200:
        raise ValueError('SheetSage timeout must be 1–7200 seconds')
    if result.get('backend', 'cuda') not in ('cpu', 'cuda', 'best'):
        raise ValueError('SheetSage backend must be cpu, cuda or best')
    for name, default, maximum in (('threads', 4, 256), ('max_tokens', 5120, 5120)):
        value = result.get(name, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'SheetSage {name} must be 1–{maximum}')
    return dict(dict(backend='cuda', threads=4, max_tokens=5120, timeout_seconds=1200), **result)


def availability():
    try:
        configuration()
        return True, 'Ready'
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).debug('SheetSage unavailable: %s', exc)
        return False, 'Not installed or incomplete — run Install_SheetSage.bat (check configuration if overridden).'


def relative_artifact(path):
    return (isinstance(path, str) and '\\' not in path and ':' not in path and
            not PurePosixPath(path).is_absolute() and '..' not in PurePosixPath(path).parts and
            path.startswith('music_analysis/sheetsage/'))


def validate(value):
    if not isinstance(value, dict) or value.get('schema_version') != PROTOCOL or value.get('status') not in ('success', 'failed', 'unavailable'):
        raise ValueError('Invalid SheetSage evidence')
    if not isinstance(value.get('provenance'), dict) or not isinstance(value.get('warnings'), list) or not isinstance(value.get('raw_artifacts'), list):
        raise ValueError('Invalid SheetSage metadata')
    for artifact in value['raw_artifacts']:
        if (not isinstance(artifact, dict) or not relative_artifact(artifact.get('path')) or
                not isinstance(artifact.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', artifact['sha256'])):
            raise ValueError('Invalid SheetSage artifact reference')
    if value['status'] == 'success' and not isinstance(value.get('transcription'), dict):
        raise ValueError('Missing SheetSage transcription')
    history = value.get('previous_results', [])
    if not isinstance(history, list):
        raise ValueError('Invalid SheetSage history')
    for previous in history:
        if not isinstance(previous, dict) or 'previous_results' in previous:
            raise ValueError('SheetSage history must be flat')
        validate(previous)


def parse_transcription(abc, native_events=None):
    """Conservative parsing: ABC locations are textual, never invented seconds."""
    result = dict(source='SheetSage2', representation='parsed from native ABC/events',
                  tempo=[], meters=[], keys=[], voices=[], chords=[], sections=[], notes=[],
                  measures=None, beats=None, voice_track_mapping=None)
    voice = None
    header = True
    fields = {'Q': 'tempo', 'M': 'meters', 'K': 'keys'}
    for line_number, line in enumerate(abc.splitlines(), 1):
        location = dict(line=line_number, voice=voice)
        if line.startswith('V:'):
            declaration = line[2:].strip().split()
            if not declaration:
                raise ValueError('Empty ABC voice declaration')
            identifier = declaration[0]
            if not header:
                voice = identifier
            if 'name=' in line:
                result['voices'].append(dict(id=identifier, declaration=line[2:].strip(), line=line_number))
            continue
        for field, target in fields.items():
            if line.startswith(field+':'):
                result[target].append(dict(value=line[2:].strip(), **location))
            for match in re.finditer(r'\['+field+r':([^\]]+)\]', line):
                result[target].append(dict(value=match[1], column=match.start(), **location))
        if line.startswith('K:'):
            header = False
        if line.startswith('%') and not line.startswith('%%'):
            result['sections'].append(dict(value=line[1:].strip(), **location))
        if not re.match(r'^[A-Za-z]:|^%', line):
            for match in re.finditer(r'"([^"\n]+)"', line):
                result['chords'].append(dict(symbol=match[1], column=match.start(), **location))
    if native_events is not None:
        events = native_events.get('events')
        if not isinstance(events, list):
            raise ValueError('Malformed SheetSage native events')
        for index, event in enumerate(events):
            for note in event.get('notes', []):
                start, end = event.get('time'), note.get('end_time')
                result['notes'].append(dict(pitch=note.get('pitch'), start=start, end=end,
                    duration=end-start if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None,
                    track=note.get('track'), voice=None, measure=None, beat_position=None,
                    native_subbeat=event.get('global_subbeat', event.get('subbeat')),
                    source='SheetSage2 native events', event_index=index))
        result['native_event_count'] = len(events)
    result['missing'] = ['Measure/beat semantics and native track-to-ABC-voice mapping are not inferred.',
                         'ABC field locations have no inferred wall-clock timestamps.']
    return result


def analyze(audio_path, project_root, check_cancel=lambda: None, config=None):
    """Return failure evidence instead of invalidating the independent main analysis."""
    started = time.monotonic()
    value = dict(schema_version=PROTOCOL, status='unavailable', provenance=dict(backend='sheetsage2',
                 worker_version='1', interpreter=sys.executable, source_asset='mix'),
                 warnings=[], raw_artifacts=[], transcription=None)
    root = Path(project_root).resolve()
    output = None
    try:
        config = configuration() if config is None else config
        value['provenance']['config'] = config
        value['provenance']['source_sha256'] = sha256(audio_path)
        output = root/'music_analysis'/'sheetsage'/uuid.uuid4().hex
        output.mkdir(parents=True)
        value['status'] = 'failed'
        request = dict(protocol=PROTOCOL, config=config, audio_path=str(Path(audio_path).resolve()),
                       output_dir=str(output), cancel_path=str(output/'cancel'), launch_unix=time.time())
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with subprocess.Popen([sys.executable, str(Path(__file__).with_name('sheetsage_worker.py'))],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding='utf-8', errors='replace', creationflags=flags) as process:
            try:
                payload = json.dumps(request)
                while True:
                    check_cancel()
                    if time.monotonic()-started > config.get('timeout_seconds', 1200)+30:
                        raise TimeoutError('SheetSage worker timeout')
                    try:
                        stdout, stderr = process.communicate(payload, timeout=.2)
                        break
                    except subprocess.TimeoutExpired:
                        payload = None
                (output/'worker-stderr.log').write_text(stderr, encoding='utf-8')
                (output/'worker-response.json').write_text(stdout, encoding='utf-8')
                if process.returncode:
                    raise ValueError(f'SheetSage worker exited {process.returncode}')
            finally:
                if process.poll() is None:
                    (output/'cancel').touch()
                    try:
                        process.communicate(timeout=4)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.communicate()
        response = strict_json(stdout)
        if not isinstance(response, dict) or response.get('protocol') != PROTOCOL or response.get('success') is not True:
            raise ValueError('SheetSage worker failed: '+str(response.get('error') if isinstance(response, dict) else 'malformed response'))
        if not isinstance(response.get('provenance'), dict) or not isinstance(response.get('artifacts'), list):
            raise ValueError('Malformed SheetSage worker metadata')
        for name in response['artifacts']:
            if not isinstance(name, str) or not (output/name).resolve().is_relative_to(output) or not (output/name).is_file():
                raise ValueError('Unsafe/missing worker artifact')
        value['provenance'].update(response['provenance'])
        abc = (output/'native'/'score.abc').read_text(encoding='utf-8-sig')
        if not abc.lstrip().startswith('X:'):
            raise ValueError('Malformed SheetSage ABC')
        native_events = None
        events = output/'native'/'events.json'
        if events.exists():
            native_events = strict_json(events.read_text(encoding='utf-8'))
            if 'payload_hex' in native_events:
                payload = bytes.fromhex(native_events['payload_hex'])
                (output/'events-decoded.json').write_bytes(payload)
                native_events = strict_json(payload)
        value['transcription'] = parse_transcription(abc, native_events)
        value['status'] = 'success'
        if not any(p.suffix.lower() in ('.mid', '.midi') for p in (output/'native').iterdir()):
            value['warnings'].append('This runtime returned native ABC/events, no native MIDI. No MIDI was generated by the wrapper.')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, TimeoutError) as exc:
        value['warnings'].append(f'{type(exc).__name__}: {exc}')
    finally:
        if output is not None:
            for path in sorted(output.rglob('*')):
                if path.is_file() and path.name != 'cancel' and path.resolve().is_relative_to(output):
                    value['raw_artifacts'].append(dict(path=path.relative_to(root).as_posix(), sha256=sha256(path),
                        bytes=path.stat().st_size, origin='native audio.cpp' if path.parent.name == 'native' else 'wrapper/provenance'))
        value['provenance']['backend_roundtrip_seconds'] = time.monotonic()-started
    validate(value)
    return value
