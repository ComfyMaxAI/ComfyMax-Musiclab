"""Versioned JSON model. Scores are evidence measures, not probabilities."""
import copy
import math
from dataclasses import asdict, dataclass

SCHEMA = 'comfymax.music.1'
ALGORITHM = '1.2'
NAMES = ('C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B')


@dataclass(frozen=True)
class Settings:
    analysis_rate: int = 22050
    hop_length: int = 512
    silence_db: float = -60.0
    relative_silence_db: float = -45.0
    min_similarity: float = 0.65
    min_margin: float = 0.035
    transition_penalty: float = 0.12
    top_candidates: int = 5
    harmonic_margin: float = 2.0
    extension_min_relative_energy: float = 0.35
    extension_min_score_gain: float = 0.02
    tempo_min_intervals: int = 4
    tempo_max_relative_mad: float = 0.08
    tempo_inlier_tolerance: float = 0.15
    tempo_min_inlier_fraction: float = 0.75
    tempo_min_bpm: float = 30.0
    tempo_max_bpm: float = 300.0

    extension_temporal_min_beats: int = 2
    extension_temporal_min_fraction: float = 0.75
    extension_temporal_window_beats: int = 4
    extension_single_beat_strong_energy: float = 0.9
    extension_single_beat_strong_gain: float = 0.10
    chord_change_min_beats: int = 2
    chord_change_min_score_advantage: float = 0.08
    chord_change_single_beat_advantage: float = 0.20
    unknown_bridge_max_beats: int = 1
    unknown_bridge_min_similarity: float = 0.55
    unknown_bridge_max_conflict: float = 0.10
    bar_min_consistency: float = 0.75
    tempo_span_intervals: int = 8
    tempo_span_max_disagreement: float = 0.08

    def validate(self):
        if self.analysis_rate != 22050 or self.hop_length != 512:
            raise ValueError('Phase 1 supports analysis_rate=22050 and hop_length=512.')
        for value in asdict(self).values():
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('Music settings must be finite numbers.')
        if not -120 <= self.silence_db <= 0 or not -120 <= self.relative_silence_db <= 0:
            raise ValueError('Invalid silence threshold.')
        if not 0 <= self.min_similarity <= 1 or not 0 <= self.min_margin <= 1:
            raise ValueError('Invalid chord threshold.')
        if not 0 <= self.transition_penalty <= 1 or not 1 <= self.harmonic_margin <= 10:
            raise ValueError('Invalid smoothing/harmonic setting.')
        if type(self.top_candidates) is not int or not 2 <= self.top_candidates <= 24:
            raise ValueError('top_candidates must be between 2 and 24.')
        for value in (self.extension_min_relative_energy, self.extension_min_score_gain,
                      self.tempo_max_relative_mad, self.tempo_inlier_tolerance,
                      self.tempo_min_inlier_fraction):
            if not 0 < value <= 1:
                raise ValueError('Invalid extension/tempo threshold.')
        if type(self.tempo_min_intervals) is not int or self.tempo_min_intervals < 2:
            raise ValueError('tempo_min_intervals must be an integer >= 2.')
        if not 0 < self.tempo_min_bpm < self.tempo_max_bpm:
            raise ValueError('Invalid tempo range.')
        for name in ('extension_temporal_min_beats', 'extension_temporal_window_beats',
                     'chord_change_min_beats', 'unknown_bridge_max_beats', 'tempo_span_intervals'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError('Invalid temporal beat count: ' + name)
        if self.extension_temporal_window_beats < self.extension_temporal_min_beats:
            raise ValueError('Temporal window is shorter than minimum support.')
        for name in ('extension_temporal_min_fraction', 'extension_single_beat_strong_gain',
                     'chord_change_min_score_advantage', 'chord_change_single_beat_advantage',
                     'unknown_bridge_min_similarity', 'unknown_bridge_max_conflict',
                     'bar_min_consistency', 'tempo_span_max_disagreement'):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError('Invalid temporal threshold: ' + name)
        if self.extension_single_beat_strong_energy <= 0:
            raise ValueError('Invalid strong extension energy.')


def seconds_to_frame(seconds, rate):
    if not math.isfinite(seconds) or seconds < 0 or type(rate) is not int or rate <= 0:
        raise ValueError('Invalid time or sample rate.')
    return round(seconds * rate)


def frame_to_seconds(frame, rate):
    if type(frame) is not int or frame < 0 or type(rate) is not int or rate <= 0:
        raise ValueError('Invalid frame or sample rate.')
    return frame / rate


def chord(root=None, quality='unknown', bass=None):
    return dict(root_pc=root, quality=quality, bass_pc=bass)


def label(value):
    if value['root_pc'] is None:
        return value['quality']
    suffix = {'maj': '', 'min': 'm', 'min7': 'm7', 'min9': 'm9'}.get(value['quality'], value['quality'])
    result = NAMES[value['root_pc']] + suffix
    return result if value.get('bass_pc') is None else result + '/' + NAMES[value['bass_pc']]


def effective_chord(region):
    """Derived on demand: manual values never replace automatic evidence."""
    manual = region.get('manual') or {}
    return dict(label=manual.get('label') if manual.get('label') is not None else label(region),
                start_frame=manual.get('start_frame') if manual.get('start_frame') is not None else region['start_frame'],
                end_frame=manual.get('end_frame') if manual.get('end_frame') is not None else region['end_frame'])


def replacement(previous, result):
    """Refuse reanalysis with overrides until an explicit reconciliation UI exists."""
    if previous:
        if previous['key'].get('manual') is not None or previous['meter']['source'] == 'manual' or previous['first_downbeat']['source'] == 'manual':
            raise ValueError('Music has manual corrections. Reanalysis requires reconciliation; existing analysis was retained.')
        if any(any(v is not None for v in r.get('manual', {}).values()) for r in previous['regions']):
            raise ValueError('Music has manual corrections. Existing analysis was retained.')
    return copy.deepcopy(result)


def validate(data, timeline):
    """Fail closed before saving/recovering a malformed music document."""
    try:
        _validate(data, timeline)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError('Malformed music analysis.') from exc


def _validate(data, timeline):
    def require(ok, message):
        if not ok:
            raise ValueError('Invalid music analysis: ' + message)
    def score(value):
        return isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1
    def check_chord(c):
        require((c['root_pc'] is None and c['quality'] in ('N', 'unknown')) or
                (type(c['root_pc']) is int and 0 <= c['root_pc'] < 12 and isinstance(c['quality'], str)), 'chord')
        require(c.get('bass_pc') is None or (type(c['bass_pc']) is int and 0 <= c['bass_pc'] < 12), 'bass')
    require(data['schema_version'] == SCHEMA, 'schema')
    rate, total = timeline['sample_rate'], timeline['frames']
    require(data['timeline'] == dict(sample_rate=rate, frames=total), 'master clock mismatch')
    meta = data['analysis']
    for field in ('id', 'source_asset', 'backend', 'backend_version', 'algorithm_version'):
        require(isinstance(meta[field], str) and bool(meta[field]), field)
    Settings(**meta['settings']).validate()
    require(data['tempo']['bpm'] is None or (math.isfinite(data['tempo']['bpm']) and data['tempo']['bpm'] > 0), 'tempo')
    require(data['tempo']['beat_unit'] == '1/4', 'beat unit')
    tempo = data['tempo']
    # Additive schema extension: legacy Phase 1 tempo/settings remain valid.
    if any(k in tempo for k in ('librosa_bpm', 'grid_bpm', 'effective_bpm')):
        for field in ('librosa_bpm', 'grid_bpm', 'effective_bpm', 'median_interval'):
            value = tempo[field]
            require(value is None or (isinstance(value, (int, float)) and math.isfinite(value) and value > 0), field)
        require(tempo['interval_mad'] is None or (math.isfinite(tempo['interval_mad']) and tempo['interval_mad'] >= 0), 'interval MAD')
        require(score(tempo['stability']), 'tempo stability')
        require(type(tempo['interval_count']) is int and tempo['interval_count'] >= 0, 'interval count')
        require(tempo['source'] in ('grid', 'span', 'librosa', 'detector'), 'tempo source')
        if 'detector_bpm' in tempo:
            value = tempo['detector_bpm']
            require(value is None or (type(value) in (int, float) and math.isfinite(value) and value > 0), 'detector tempo')
        require(tempo['bpm'] == tempo['effective_bpm'] == tempo[tempo['source']+'_bpm'], 'effective tempo')
    if 'span_bpm' in tempo:
        require(tempo['span_bpm'] is None or (math.isfinite(tempo['span_bpm']) and tempo['span_bpm'] > 0), 'span tempo')
    key = data['key']
    require(key['root_pc'] is None or (type(key['root_pc']) is int and 0 <= key['root_pc'] < 12), 'key root')
    require(key['mode'] in ('major', 'minor', 'unknown') and score(key['score']), 'key')
    require(data['meter']['source'] in ('default', 'manual', 'auto'), 'meter provenance')
    require(type(data['meter']['numerator']) is int and data['meter']['numerator'] > 0 and
            data['meter']['denominator'] in (1, 2, 4, 8, 16, 32), 'meter')
    beats = data['beats']; ids = set(); last = -1
    for i, b in enumerate(beats):
        require(isinstance(b['id'], str) and b['id'] not in ids, 'beat ID')
        require(type(b['frame']) is int and last < b['frame'] < total, 'beat order')
        require(b['index'] == i + 1 and abs(b['seconds'] - b['frame']/rate) < 1e-9, 'beat time')
        ids.add(b['id']); last = b['frame']
    down = data['first_downbeat']
    require(down['source'] in ('default', 'manual', 'auto') and (down['beat_id'] is None or down['beat_id'] in ids), 'downbeat')
    provenance = meta.get('rhythm_backend')
    if provenance is not None:
        require(isinstance(provenance['requested_backend'], str), 'requested rhythm backend')
        require(provenance['selected_backend'] in ('librosa', 'beat-transformer'), 'selected rhythm backend')
        require(provenance['fallback_reason'] is None or isinstance(provenance['fallback_reason'], str), 'rhythm fallback')
        detected = provenance.get('detected_downbeats', [])
        last_down = -1
        for event in detected:
            require(type(event['frame']) is int and last_down < event['frame'] < total, 'detected downbeat order')
            require(abs(event['seconds']-event['frame']/rate) < 1e-9, 'detected downbeat clock')
            last_down = event['frame']
        beat_by_id = {b['id']: b for b in beats}
        detected_frames = {d['frame'] for d in detected}
        for event in provenance.get('aligned_downbeats', []):
            require(event['beat_id'] in ids and event['frame'] in detected_frames, 'aligned downbeat reference')
            require(event['beat_index'] == beat_by_id[event['beat_id']]['index'], 'aligned downbeat index')
            require(abs(event['seconds']-event['frame']/rate) < 1e-9, 'aligned downbeat clock')
    raw_ids = []; last = 0
    for row in data['raw_predictions']:
        require(isinstance(row['id'], str) and row['id'] not in raw_ids, 'prediction ID')
        require(type(row['start_frame']) is int and type(row['end_frame']) is int and
                row['start_frame'] == last < row['end_frame'] <= total, 'prediction coverage')
        for edge in ('start', 'end'):
            require(abs(row[edge+'_seconds'] - row[edge+'_frame']/rate) < 1e-9, 'prediction seconds')
        require(row['beat_id'] is None or row['beat_id'] in ids, 'beat reference')
        check_chord(row['raw']); require(score(row['score']) and score(row['margin']), 'raw score')
        require(len(row['chroma']) == 12 and all(isinstance(v,(int,float)) and math.isfinite(v) and v >= 0 for v in row['chroma']), 'chroma')
        for c in row['candidates']:
            check_chord(c); require(score(c['score']), 'candidate score')
            if 'extension_evidence' in c:
                e = c['extension_evidence']
                require(type(e['pitch_class']) is int and 0 <= e['pitch_class'] < 12, 'extension pitch')
                require(math.isfinite(e['relative_energy']) and e['relative_energy'] >= 0, 'extension energy')
                require(score(e['threshold']) and score(e['score_gain_threshold']), 'extension threshold')
                require(math.isfinite(e['score_gain']) and -1 <= e['score_gain'] <= 1, 'extension gain')
                require(type(e['accepted']) is bool, 'extension acceptance')
        raw_ids.append(row['id']); last = row['end_frame']
    require(last == total, 'complete prediction coverage')
    neural = 'chord_detections' in data
    if neural:
        from .neural_chords import validate_evidence
        validate_evidence(data)
    refs = []; region_ids = set(); last = 0
    by_id = {r['id']: r for r in data['raw_predictions']}
    for r in data['regions']:
        require(isinstance(r['id'],str) and r['id'] not in region_ids, 'region ID'); region_ids.add(r['id'])
        require(r['start_frame'] == last < r['end_frame'] <= total, 'region coverage')
        require(bool(r['raw_ids']) and all(v in by_id for v in r['raw_ids']), 'raw references')
        if not neural: require(r['start_frame'] == by_id[r['raw_ids'][0]]['start_frame'] and r['end_frame'] == by_id[r['raw_ids'][-1]]['end_frame'], 'region bounds')
        check_chord(r); require(r['score'] is None if neural else score(r['score']), 'region score')
        for edge in ('start', 'end'):
            require(abs(r[edge+'_seconds']-r[edge+'_frame']/rate)<1e-9, 'region seconds')
        manual = r['manual']
        require(manual['label'] is None or (isinstance(manual['label'],str) and bool(manual['label'])), 'manual label')
        effective = effective_chord(r)
        require(type(effective['start_frame']) is int and type(effective['end_frame']) is int and
                0 <= effective['start_frame'] < effective['end_frame'] <= total, 'manual bounds')
        refs.extend(r['raw_ids']); last = r['end_frame']
    require(last == total and (neural or refs == raw_ids), 'region/raw coverage')

    # Optional Phase 1.2 context is validated without migrating older documents.
    if 'bars' in data:
        from .structure import build_bars
        expected,disabled=build_bars(beats,total,data['meter'],down)
        require(isinstance(data['bars'],list) and len(data['bars'])==len(expected), 'bars')
        for actual,wanted in zip(data['bars'],expected):
            require(all(actual.get(k)==v for k,v in wanted.items()), 'bar clock/references/provenance')
            summary=actual.get('harmonic_summary')
            if summary is not None:
                require(score(summary['consistency']), 'bar consistency')
                for field in ('average_triad_score','median_triad_score','average_seventh_score','median_seventh_score'):
                    require(score(summary[field]), 'bar candidate score')
                require(all(type(f) is int and actual['start_frame']<f<actual['end_frame'] for f in summary['possible_internal_changes']), 'bar changes')
        require(data.get('bar_context_disabled_reason')==disabled, 'bar context state')
    def check_temporal(entries):
        require(isinstance(entries,list) and len(entries)<=2, 'temporal evidence size')
        for e in entries:
            n=e['total_beats']
            require(type(n) is int and n>0, 'temporal total')
            for field in ('supporting_beats','triad_supporting_beats','consecutive_support'):
                require(type(e[field]) is int and 0<=e[field]<=n, 'temporal count')
            require(score(e['supporting_fraction']) and abs(e['supporting_fraction']-e['supporting_beats']/n)<1e-9, 'temporal fraction')
            require(math.isfinite(e['median_relative_energy']) and e['median_relative_energy']>=0, 'temporal energy')
            for field in ('median_score_gain','maximum_score_gain'):
                require(math.isfinite(e[field]) and -1<=e[field]<=1, 'temporal gain')
            require(type(e['accepted']) is bool and isinstance(e['acceptance_reason'],str), 'temporal decision')
    for row in data['raw_predictions']:
        if 'initial_smoothed' in row: check_chord(row['initial_smoothed'])
        if 'temporal_extension_evidence' in row: check_temporal(row['temporal_extension_evidence'])
        resolution=row.get('unknown_resolution')
        if resolution is not None:
            require(row['raw']['quality']=='unknown' and resolution['original']=='unknown' and
                    isinstance(resolution['resolved_to'],str) and isinstance(resolution['reason'],str) and
                    score(resolution['confidence']), 'unknown resolution')
    for region in data['regions']:
        if 'triad_family' in region: check_chord(region['triad_family'])
        if 'temporal_extension_evidence' in region: check_temporal(region['temporal_extension_evidence'])
    for field in ('initial_regions', 'template_regions'):
        if field in data:
            initial=dict(data,regions=data[field])
            for extra in ('initial_regions','template_regions','chord_detections'): initial.pop(extra,None)
            _validate(initial,timeline)
