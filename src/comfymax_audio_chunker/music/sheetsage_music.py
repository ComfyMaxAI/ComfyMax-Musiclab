"""Read-only adapter from preserved SheetSage evidence to musical events.

No inference, ABC parser, main-analysis grid or MIDI representation lives here.
The version-gated token semantics are from audio.cpp v0.8.1 session.cpp and
processing.cpp. Unknown runtimes retain neutral track labels and header metadata.
"""
import hashlib
import json
import math
from pathlib import Path
import re

KNOWN_EXE = 'dc92dbd6ea4763cd298f03cc487c8fc5c0ba2c198abfa3b5e075af8700ec428c'
KNOWN_MODEL = '52bb5846c452037d39931aa8050885b6c751b9c7afcc8ef6d6d3067d241731a4'


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def valid_note(note):
    return (isinstance(note, dict) and number(note.get('start')) and
            number(note.get('end')) and 0 <= note['start'] < note['end'] and
            type(note.get('pitch')) is int and 0 <= note['pitch'] <= 127 and
            type(note.get('track')) is int and note['track'] >= 0)


def tempo(evidence):
    entries = evidence.get('transcription', {}).get('tempo', [])
    # Only a single, explicitly global stored declaration is understood.
    if len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError('SheetSage global tempo missing or ambiguous; no substitute tempo is used.')
    entry = entries[0]
    match = re.fullmatch(r'1/4\s*=\s*(\d+(?:\.\d+)?)', str(entry.get('value', '')))
    if not match or entry.get('voice') is not None or 'column' in entry:
        raise ValueError('No reliable global SheetSage quarter-note tempo.')
    bpm = float(match[1])
    if not math.isfinite(bpm) or bpm <= 0 or not 1 <= 60_000_000 / bpm <= 0xffffff:
        raise ValueError('SheetSage tempo cannot be represented in Standard MIDI.')
    return bpm


def suitable(evidence):
    """Cheap UI preflight; export performs artifact and full content validation."""
    try:
        return (isinstance(evidence, dict) and evidence.get('status') == 'success' and
                bool(tempo(evidence)) and
                any(valid_note(n) for n in evidence['transcription'].get('notes', [])))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _artifact(root, item):
    from .sheetsage import relative_artifact
    if not isinstance(item, dict) or not relative_artifact(item.get('path')):
        raise ValueError('Unsafe SheetSage artifact reference')
    path = (Path(root) / item['path']).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('SheetSage artifact escapes project')
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != item.get('sha256'):
        raise ValueError('SheetSage events checksum mismatch')
    return payload


def _initial(transcription, field):
    entries = [e for e in transcription.get(field, []) if isinstance(e, dict)
               and e.get('voice') is None and 'column' not in e]
    return entries[0].get('value') if len(entries) == 1 else None


def musical_events(evidence, root):
    """Return a fresh derivative, preserving source indices and explicit skips."""
    if not isinstance(evidence, dict) or evidence.get('status') != 'success':
        raise ValueError('No successful stored SheetSage transcription')
    bpm = tempo(evidence)
    transcription = evidence['transcription']
    artifacts = evidence.get('raw_artifacts', [])
    decoded = next((a for a in artifacts if a['path'].endswith('/events-decoded.json')), None)
    raw = next((a for a in artifacts if a['path'].endswith('/native/events.json')), None)
    diagnostics = []
    native = None
    source_hash = None
    source_run = None
    if decoded or raw:
        # If both copies exist they must agree. Never silently use corrupted evidence.
        unpacked = None
        if raw:
            wrapper = json.loads(_artifact(root, raw))
            unpacked = json.loads(bytes.fromhex(wrapper['payload_hex'])) if 'payload_hex' in wrapper else wrapper
        native = json.loads(_artifact(root, decoded)) if decoded else unpacked
        if unpacked is not None and native != unpacked:
            raise ValueError('Raw and decoded SheetSage events disagree')
        artifact = decoded or raw
        source_hash = artifact['sha256']
        source_run = Path(artifact['path']).parts[2]
        if not isinstance(native, dict) or not isinstance(native.get('events'), list):
            raise ValueError('Malformed native SheetSage events container')
        candidates = []
        for i, event in enumerate(native['events']):
            if not isinstance(event, dict) or not isinstance(event.get('notes'), list):
                diagnostics.append(dict(event_index=i, reason='Malformed event/notes container', value=event))
                continue
            for j, note in enumerate(event['notes']):
                candidates.append(dict(start=event.get('time'), end=note.get('end_time'),
                                       pitch=note.get('pitch'), track=note.get('track'),
                                       event_index=i, note_index=j) if isinstance(note, dict) else
                                  dict(event_index=i, note_index=j, malformed=note))
    else:
        candidates = transcription.get('notes', [])
        if not isinstance(candidates, list):
            raise ValueError('Malformed normalized SheetSage notes')
        source_hash = hashlib.sha256(json.dumps(candidates, sort_keys=True, default=repr).encode()).hexdigest()
        diagnostics.append(dict(reason='Native artifact absent: using preserved normalized notes'))
    notes = []
    for index, note in enumerate(candidates):
        if valid_note(note):
            notes.append({**note, 'source_index': index, 'velocity': 80})
        else:
            diagnostics.append(dict(source_index=index, reason='Skipped invalid pitch, track or start/end duration', value=note))
    if not notes:
        raise ValueError('No valid SheetSage notes to export')
    provenance = evidence.get('provenance', {})
    known = (provenance.get('executable_sha256') == KNOWN_EXE and
             provenance.get('model_sha256') == KNOWN_MODEL)
    track_names = {t: ({0: 'Vocal Melody', 1: 'Instrumental Melody'}.get(t, f'SheetSage Track {t}')
                       if known else f'SheetSage Track {t}') for t in sorted({n['track'] for n in notes})}
    meters, keys = [], []
    if known and native:
        # 300-second pinned model: time_start 517, time_end 30517;
        # 192 meter + 256 eighth + 23 structure tokens => key_start 30988.
        major = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']
        minor = ['Cm', 'C#m', 'Dm', 'Ebm', 'Em', 'Fm', 'F#m', 'Gm', 'G#m', 'Am', 'Bbm', 'Bm']
        for i, event in enumerate(native['events']):
            if not isinstance(event, dict) or not number(event.get('time')) or event['time'] < 0:
                continue
            fields = event.get('tokens_by_field', {})
            if not isinstance(fields, dict):
                diagnostics.append(dict(event_index=i, reason='Malformed metadata tokens')); continue
            for field in ('rhythm', 'key'):
                tokens = fields.get(field, [])
                if not isinstance(tokens, list):
                    diagnostics.append(dict(event_index=i, reason='Malformed '+field+' tokens')); continue
                for token in tokens:
                    if type(token) is not int:
                        diagnostics.append(dict(event_index=i, reason='Noninteger metadata token')); continue
                    if field == 'rhythm' and 30517 <= token < 30709:
                        index = token - 30517
                        meters.append(dict(time=event['time'], numerator=index//6+1,
                                           denominator=[1,2,4,8,16,32][index%6]))
                    if field == 'key' and 30988 <= token < 31012:
                        keys.append(dict(time=event['time'], key=(major+minor)[token-30988]))
    meter_source = key_source = 'SheetSage native timed tokens (audio.cpp 0.8.1)'
    if not meters:
        value = _initial(transcription, 'meters')
        match = re.fullmatch(r'(\d+)/(\d+)', str(value))
        if match and 1 <= int(match[1]) <= 255 and int(match[2]) in (1,2,4,8,16,32,64,128):
            meters = [dict(time=0., numerator=int(match[1]), denominator=int(match[2]))]
        meter_source = 'preserved SheetSage initial declaration' if meters else 'unavailable'
    if not keys:
        value = _initial(transcription, 'keys')
        allowed = {'Cb','Gb','Db','Ab','Eb','Bb','F','C','G','D','A','E','B','F#','C#',
                   'Abm','Ebm','Bbm','Fm','Cm','Gm','Dm','Am','Em','Bm','F#m','C#m','G#m','D#m','A#m'}
        if value in allowed:
            keys = [dict(time=0., key=value)]
        key_source = 'preserved SheetSage initial declaration' if keys else 'unavailable'
    def changes(items):
        result = []
        for item in sorted(items, key=lambda x:x['time']):
            if not result or {k:v for k,v in result[-1].items() if k != 'time'} != {k:v for k,v in item.items() if k != 'time'}:
                result.append(item)
        return result
    # Diagnostics must remain persistable even for malformed non-finite input.
    diagnostics = json.loads(json.dumps(diagnostics, default=repr), parse_constant=lambda value: value)
    return dict(notes=notes, track_names=track_names, bpm=bpm, meters=changes(meters), keys=changes(keys),
                diagnostics=diagnostics, source_events_sha256=source_hash, source_sheet_sage_run=source_run,
                tempo_source='preserved SheetSage global quarter-note declaration',
                meter_source=meter_source, key_source=key_source,
                limitations=['No tempo changes inferred; notes retain absolute seconds.',
                            'ABC-only meter/key positions remain in the original evidence.',
                            'Fixed velocity 80; no detected dynamics or instrument programs.'])
