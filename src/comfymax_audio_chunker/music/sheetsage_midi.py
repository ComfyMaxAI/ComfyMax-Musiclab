"""SMF output adapter; source events are never changed. Mido is lazy-imported."""
from collections import defaultdict
from datetime import datetime, timezone
import importlib.util
import io
import math
import os
from pathlib import Path
import re
import tempfile

from .sheetsage_music import musical_events, suitable

PPQ = 960  # <= 0.4341 ms nearest-tick error at 72 BPM; native timestamp bins are 10 ms.
VERSION = '1'
CHANNELS = tuple(c for c in range(16) if c != 9)


def available(evidence):
    return importlib.util.find_spec('mido') is not None and suitable(evidence)


def filename(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .') or 'song'
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        name = '_'+name
    return name[:180]+'.mid'


def destination(path, root):
    target = Path(path)
    if not target.suffix:
        target = target.with_suffix('.mid')
    if target.suffix.lower() not in ('.mid', '.midi'):
        raise ValueError('Choose a .mid or .midi filename')
    target = target.resolve()
    root = Path(root).resolve()
    if any(target.is_relative_to((root/folder).resolve()) for folder in ('music_analysis','source','audio','cache','recovery')):
        raise ValueError('MIDI destination cannot replace project evidence or assets')
    return target


def _mido():
    try:
        import mido
        return mido
    except ImportError as exc:
        raise ValueError('MIDI export requires mido==1.3.3. Run setup.ps1 to install editor dependencies.') from exc


def build(events):
    mido = _mido()
    microseconds = round(60_000_000/events['bpm'])
    seconds_per_tick = microseconds / 1_000_000 / PPQ
    def tick(seconds):
        position = seconds/seconds_per_tick
        if not math.isfinite(position) or position > 0x0fffffff:
            raise ValueError('SheetSage timestamp exceeds supported SMF tick range')
        value = round(position)
        if value > 0x0fffffff:
            raise ValueError('SheetSage timestamp exceeds supported SMF tick range')
        return value
    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    conductor = mido.MidiTrack(); midi.tracks.append(conductor)
    conductor.append(mido.MetaMessage('track_name', name='SheetSage Conductor'))
    metadata = [(0, mido.MetaMessage('set_tempo', tempo=microseconds))]
    for item in events['meters']:
        metadata.append((tick(item['time']), mido.MetaMessage('time_signature', numerator=item['numerator'], denominator=item['denominator'])))
    for item in events['keys']:
        metadata.append((tick(item['time']), mido.MetaMessage('key_signature', key=item['key'])))
    last = 0
    for position, message in sorted(metadata, key=lambda pair:pair[0]):
        conductor.append(message.copy(time=position-last)); last = position
    track_ids = sorted(events['track_names'])
    if len(track_ids) > len(CHANNELS):
        raise ValueError('More than 15 melodic tracks: cannot assign independent MIDI channels without percussion')
    pools = {track:[CHANNELS[i]] for i, track in enumerate(track_ids)}
    free = list(CHANNELS[len(track_ids):])
    expected, summaries, adjustments = [], [], []
    for track_id in track_ids:
        track = mido.MidiTrack(); midi.tracks.append(track)
        track.append(mido.MetaMessage('track_name', name=events['track_names'][track_id]))
        pending = []
        active = {}
        notes = sorted((n for n in events['notes'] if n['track']==track_id), key=lambda n:(n['start'], n['source_index']))
        for note in notes:
            start = tick(note['start']); end = tick(note['end'])
            if end <= start:
                end = start + 1
                adjustments.append(dict(source_index=note['source_index'], reason='Positive sub-tick note extended to one tick'))
            pitch = note['pitch']
            # Same-pitch overlapping notes need separate channels: MIDI 1 has no note IDs.
            channel = next((c for c in pools[track_id] if active.get((c,pitch), -1) <= start), None)
            if channel is None:
                if not free:
                    raise ValueError('Overlapping same-pitch notes exceed 15 melodic channels; no notes were discarded')
                channel = free.pop(0); pools[track_id].append(channel)
            active[channel,pitch] = end
            ordinal = len(expected)
            expected.append(dict(track_index=len(midi.tracks)-1, channel=channel, pitch=pitch,
                                 start_tick=start, end_tick=end, start=note['start'], end=note['end'],
                                 velocity=note['velocity'], source_index=note['source_index']))
            pending.append((start, 1, ordinal, mido.Message('note_on', channel=channel, note=pitch, velocity=note['velocity'])))
            pending.append((end, 0, ordinal, mido.Message('note_off', channel=channel, note=pitch, velocity=0)))
        last = 0
        for position, _, _, message in sorted(pending, key=lambda x:x[:3]):
            track.append(message.copy(time=position-last)); last = position
        summaries.append(dict(source_track=track_id, name=track.name, note_count=len(notes), channels=pools[track_id]))
    buffer = io.BytesIO(); midi.save(file=buffer)
    return buffer.getvalue(), expected, metadata, summaries, adjustments, seconds_per_tick


def verify(payload, expected, metadata, summaries, seconds_per_tick):
    """Read serialized bytes, pair actual messages, and reconstruct seconds.

    Called on every export before publishing, not just in the test suite.
    """
    mido = _mido()
    if payload[:4] != b'MThd':
        raise ValueError('MIDI header missing')
    midi = mido.MidiFile(file=io.BytesIO(payload))
    if midi.type != 1 or midi.ticks_per_beat != PPQ or len(midi.tracks) != len(summaries)+1:
        raise ValueError('MIDI format/PPQ/track count verification failed')
    if [t.name for t in midi.tracks] != ['SheetSage Conductor']+[s['name'] for s in summaries]:
        raise ValueError('MIDI track names verification failed')
    position = 0; actual_meta = []
    for msg in midi.tracks[0]:
        position += msg.time
        if msg.type in ('set_tempo','time_signature','key_signature'):
            actual_meta.append((position,msg.copy(time=0)))
    if actual_meta != sorted(metadata,key=lambda p:p[0]):
        raise ValueError('MIDI tempo/meter/key verification failed')
    # Rebuild the clock from the read-back tempo event, not from source BPM.
    tempo_messages = [(t,m.tempo) for t,m in actual_meta if m.type=='set_tempo']
    if len(tempo_messages)!=1 or tempo_messages[0][0]!=0:
        raise ValueError('Expected one global tempo at tick zero')
    actual_seconds_per_tick = tempo_messages[0][1]/1_000_000/midi.ticks_per_beat
    observed = []
    for track_index, track in enumerate(midi.tracks[1:],1):
        position = 0; active = {}; ordered = []
        for msg in track:
            position += msg.time
            if msg.type not in ('note_on','note_off'):
                continue
            identity = (msg.channel,msg.note)
            if msg.type == 'note_on' and msg.velocity:
                if identity in active:
                    raise ValueError('Ambiguous overlapping MIDI note identity')
                note = dict(track_index=track_index, channel=msg.channel, pitch=msg.note,
                            start_tick=position, velocity=msg.velocity)
                active[identity] = note; ordered.append(note)
            else:
                if identity not in active:
                    raise ValueError('Unmatched MIDI note off')
                active.pop(identity)['end_tick'] = position
        if active:
            raise ValueError('MIDI contains hanging notes')
        observed.extend(ordered)
    if len(observed) != len(expected):
        raise ValueError('MIDI note count verification failed')
    start_errors, end_errors = [], []
    for source, actual in zip(expected, observed):
        if any(source[key] != actual[key] for key in actual):
            raise ValueError('MIDI pitch/order/channel/duration verification failed')
        start_error = abs(source['start']-actual['start_tick']*actual_seconds_per_tick)
        end_error = abs(source['end']-actual['end_tick']*actual_seconds_per_tick)
        # Nearest tick except explicitly extended sub-tick notes (at most 1.5 ticks).
        extended = round(source['start']/seconds_per_tick) == round(source['end']/seconds_per_tick)
        if start_error > seconds_per_tick*.5+1e-9 or end_error > seconds_per_tick*(1.5 if extended else .5)+1e-9:
            raise ValueError('MIDI timing error exceeds tick quantization bound')
        start_errors.append(start_error); end_errors.append(end_error)
    return dict(max_start_error_seconds=max(start_errors), mean_start_error_seconds=sum(start_errors)/len(start_errors),
                max_end_error_seconds=max(end_errors), mean_end_error_seconds=sum(end_errors)/len(end_errors),
                source_duration_seconds=max(n['end'] for n in expected),
                midi_note_duration_seconds=max(n['end_tick'] for n in observed)*actual_seconds_per_tick,
                midi_file_duration_seconds=midi.length, half_tick_seconds=seconds_per_tick/2,
                note_count=len(observed))


def export(evidence, root, path, *, overwrite=False):
    target = destination(path, root)
    if target.exists() and not overwrite:
        raise FileExistsError('MIDI file already exists; explicit overwrite confirmation is required')
    events = musical_events(evidence, root)
    payload, expected, metadata, tracks, adjustments, tick_seconds = build(events)
    measurements = verify(payload, expected, metadata, tracks, tick_seconds)
    handle, temporary = tempfile.mkstemp(prefix='.midi-export-', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(handle,'wb') as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        # Verify disk bytes too. Existing files stay intact if any check fails.
        verify(Path(temporary).read_bytes(), expected, metadata, tracks, tick_seconds)
        if overwrite:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)  # Atomic no-clobber publication, including races.
    finally:
        Path(temporary).unlink(missing_ok=True)
    return dict(exporter_version=VERSION, source='SheetSage2', exported_at=datetime.now(timezone.utc).isoformat(),
                filename=target.name, ppq=PPQ, format=1, tracks=tracks, note_count=len(expected),
                bpm=events['bpm'], meters=events['meters'], keys=events['keys'],
                source_sheet_sage_run=events['source_sheet_sage_run'], source_events_sha256=events['source_events_sha256'],
                tempo_source=events['tempo_source'], meter_source=events['meter_source'], key_source=events['key_source'],
                diagnostics=events['diagnostics']+adjustments, limitations=events['limitations'], timing=measurements)


def summary(result, path):
    tracks = '\n'.join(f"- {t['name']}: {t['note_count']} notes" for t in result['tracks'])
    meter = ', '.join(f"{m['numerator']}/{m['denominator']} @ {m['time']:.3f}s" for m in result['meters']) or 'not available'
    key = ', '.join(f"{k['key']} @ {k['time']:.3f}s" for k in result['keys']) or 'not available'
    return (f"MIDI exported successfully\n\nTracks:\n{tracks}\n\nTempo: {result['bpm']:g} BPM\n"
            f"Meter: {meter}\nKey: {key}\nDuration (last note): {result['timing']['midi_note_duration_seconds']:.3f} s\n"
            f"Diagnostics: {len(result['diagnostics'])} (saved in project export provenance)\n\nFile:\n{path}")
