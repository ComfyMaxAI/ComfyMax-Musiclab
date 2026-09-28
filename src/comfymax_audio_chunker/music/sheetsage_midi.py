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
from .model import effective_chord

PPQ = 960  # <= 0.4341 ms nearest-tick error at 72 BPM; native timestamp bins are 10 ms.
VERSION = '2'
CHANNELS = tuple(c for c in range(16) if c != 9)
CHORD_CHANNEL = 0
CHORD_VELOCITY = 72
CHORD_INTERVALS = {
    '': (0,4,7), 'maj': (0,4,7), 'm': (0,3,7), 'min': (0,3,7),
    '7': (0,4,7,10), 'm7': (0,3,7,10), 'min7': (0,3,7,10),
    'maj7': (0,4,7,11), 'dim': (0,3,6), 'aug': (0,4,8),
    'dim7': (0,3,6,9), 'hdim7': (0,3,6,10), 'sus2': (0,2,7),
    'sus4': (0,5,7), 'sus4(b7)': (0,5,7,10), '9': (0,4,7,10,14),
    'maj9': (0,4,7,11,14), 'm9': (0,3,7,10,14), 'min9': (0,3,7,10,14),
    '11': (0,4,7,10,14,17), '13': (0,4,7,10,14,17,21),
}
ROOTS={'C':0,'C#':1,'Db':1,'D':2,'D#':3,'Eb':3,'E':4,'F':5,'F#':6,'Gb':6,
       'G':7,'G#':8,'Ab':8,'A':9,'A#':10,'Bb':10,'B':11}


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


def chord_blocks(analysis):
    """Split existing final chord regions at every stored bar boundary."""
    if not isinstance(analysis,dict): return []
    rate=analysis.get('timeline',{}).get('sample_rate')
    bars=analysis.get('bars',[]); regions=analysis.get('regions',[])
    if type(rate) is not int or rate<=0 or not isinstance(bars,list) or not isinstance(regions,list): return []
    chords=[]
    for region in regions:
        if not isinstance(region,dict): continue
        value=effective_chord(region)
        if (not isinstance(value.get('label'),str) or not value['label'] or
                type(value.get('start_frame')) is not int or type(value.get('end_frame')) is not int):
            continue
        chords.append(value)
    chords.sort(key=lambda item:(item['start_frame'],item['end_frame']))
    blocks=[]
    for bar in sorted((b for b in bars if isinstance(b,dict)),key=lambda item:item.get('start_frame',-1)):
        start,end=bar.get('start_frame'),bar.get('end_frame')
        if type(start) is not int or type(end) is not int or not 0<=start<end: continue
        current=[]
        for chord in chords:
            left=max(start,chord['start_frame']); right=min(end,chord['end_frame'])
            if left>=right: continue
            if current and current[-1]['label']==chord['label'] and current[-1]['end_frame']==left:
                current[-1]['end_frame']=right
            else:
                current.append(dict(label=chord['label'],start_frame=left,end_frame=right))
        blocks.extend(dict(label=item['label'],start=item['start_frame']/rate,end=item['end_frame']/rate)
                      for item in current)
    return blocks


def chord_pitches(label):
    """Map the chord vocabulary produced by MusicLab to root-position notes at C3."""
    if label in ('N','unknown'): return ()
    match=re.fullmatch(r'([A-G](?:#|b)?)([^/]*)(?:/[A-G](?:#|b)?)?',label)
    if not match or match.group(1) not in ROOTS or match.group(2) not in CHORD_INTERVALS:
        raise ValueError('Unsupported MusicLab chord label for MIDI export: '+label)
    root=48+ROOTS[match.group(1)]
    return tuple(root+interval for interval in CHORD_INTERVALS[match.group(2)])


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
    chord_events=[]
    chords=events.get('chords',[])
    if chords:
        track=mido.MidiTrack(); midi.tracks.append(track)
        track.append(mido.MetaMessage('track_name',name='Chords'))
        pending=[]
        for item in chords:
            start=tick(item['start']); end=max(start+1,tick(item['end'])); pitches=chord_pitches(item['label'])
            pending.append((start,1,-1,mido.MetaMessage('marker',text=item['label'])))
            for pitch in pitches:
                pending.append((start,2,pitch,mido.Message('note_on',channel=CHORD_CHANNEL,note=pitch,velocity=CHORD_VELOCITY)))
                pending.append((end,0,pitch,mido.Message('note_off',channel=CHORD_CHANNEL,note=pitch,velocity=0)))
            chord_events.append(dict(tick=start,end_tick=end,time=item['start'],end=item['end'],
                                     label=item['label'],pitches=pitches))
        last=0
        for position,_,_,message in sorted(pending,key=lambda item:item[:3]):
            track.append(message.copy(time=position-last)); last=position
    buffer = io.BytesIO(); midi.save(file=buffer)
    return buffer.getvalue(), expected, metadata, summaries, adjustments, seconds_per_tick, chord_events


def verify(payload, expected, metadata, summaries, seconds_per_tick, chord_events=None):
    """Read serialized bytes, pair actual messages, and reconstruct seconds.

    Called on every export before publishing, not just in the test suite.
    """
    mido = _mido()
    if payload[:4] != b'MThd':
        raise ValueError('MIDI header missing')
    midi = mido.MidiFile(file=io.BytesIO(payload))
    chord_events=chord_events or []
    if midi.type != 1 or midi.ticks_per_beat != PPQ or len(midi.tracks) != len(summaries)+1+bool(chord_events):
        raise ValueError('MIDI format/PPQ/track count verification failed')
    names=['SheetSage Conductor']+[s['name'] for s in summaries]+(['Chords'] if chord_events else [])
    if [t.name for t in midi.tracks] != names:
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
    melodic_tracks=midi.tracks[1:1+len(summaries)]
    for track_index, track in enumerate(melodic_tracks,1):
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
    if chord_events:
        position=0; markers=[]; active={}; notes=[]
        for msg in midi.tracks[-1]:
            position+=msg.time
            if msg.type=='marker': markers.append(dict(tick=position,label=msg.text))
            elif msg.type=='note_on' and msg.velocity:
                if msg.note in active: raise ValueError('Overlapping chord note')
                active[msg.note]=position; notes.append(dict(start_tick=position,pitch=msg.note))
            elif msg.type in ('note_on','note_off'):
                if msg.note not in active: raise ValueError('Unmatched chord note off')
                start=active.pop(msg.note)
                next(note for note in reversed(notes) if note['pitch']==msg.note and note['start_tick']==start)['end_tick']=position
        wanted_markers=[dict(tick=item['tick'],label=item['label']) for item in chord_events]
        wanted_notes=[dict(start_tick=item['tick'],pitch=pitch,end_tick=item['end_tick'])
                      for item in chord_events for pitch in item['pitches']]
        if markers!=wanted_markers: raise ValueError('MIDI chord marker verification failed')
        if active or notes!=wanted_notes: raise ValueError('MIDI chord note verification failed')
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


def export(evidence, root, path, *, overwrite=False, analysis=None):
    target = destination(path, root)
    if target.exists() and not overwrite:
        raise FileExistsError('MIDI file already exists; explicit overwrite confirmation is required')
    events = musical_events(evidence, root)
    events['chords']=chord_blocks(analysis)
    payload, expected, metadata, tracks, adjustments, tick_seconds, chords = build(events)
    measurements = verify(payload, expected, metadata, tracks, tick_seconds, chords)
    handle, temporary = tempfile.mkstemp(prefix='.midi-export-', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(handle,'wb') as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        # Verify disk bytes too. Existing files stay intact if any check fails.
        verify(Path(temporary).read_bytes(), expected, metadata, tracks, tick_seconds, chords)
        if overwrite:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)  # Atomic no-clobber publication, including races.
    finally:
        Path(temporary).unlink(missing_ok=True)
    return dict(exporter_version=VERSION, source='SheetSage2', exported_at=datetime.now(timezone.utc).isoformat(),
                filename=target.name, ppq=PPQ, format=1, tracks=tracks, note_count=len(expected),
                chord_event_type='marker', chord_event_count=len(chords),
                chord_note_count=sum(len(item['pitches']) for item in chords),
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
