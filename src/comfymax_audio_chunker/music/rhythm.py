"""Beat estimates mapped once to the authoritative master sample clock."""
import numpy as np
from .model import seconds_to_frame


def estimate_tempo(librosa_bpm, seconds, settings):
    """Median grid estimate; retain the original estimate and fallback reason."""
    original = float(librosa_bpm) if librosa_bpm is not None else None
    if original is not None and (not np.isfinite(original) or original <= 0):
        original = None
    times = np.asarray(seconds, dtype=float)
    intervals = np.diff(times)
    valid = bool(np.all(np.isfinite(times)) and np.all(intervals > 0))
    median = mad = grid = None
    stability = 0.0
    if valid and len(intervals):
        median = float(np.median(intervals))
        mad = float(np.median(np.abs(intervals - median)))
        grid = 60.0 / median
        if not np.isfinite(grid):
            grid = None
        stability = float(np.mean(np.abs(intervals - median) <= settings.tempo_inlier_tolerance * median))
    if not valid:
        reason = 'invalid_beat_grid'
    elif len(intervals) < settings.tempo_min_intervals:
        reason = 'too_few_intervals'
    elif grid is None or not settings.tempo_min_bpm <= grid <= settings.tempo_max_bpm:
        reason = 'implausible_grid_bpm'
    elif mad / median > settings.tempo_max_relative_mad or stability < settings.tempo_min_inlier_fraction:
        reason = 'unstable_intervals'
    else:
        reason = None
    span = None
    width = settings.tempo_span_intervals
    if valid and len(intervals) >= width:
        # Median over overlapping long spans resists an isolated missed beat.
        estimates = 60.0 * width / (times[width:] - times[:-width])
        candidate = float(np.median(estimates))
        if np.isfinite(candidate) and candidate > 0:
            span = candidate
    use_span = (reason is None and span is not None and
                settings.tempo_min_bpm <= span <= settings.tempo_max_bpm and
                abs(span - grid) / grid <= settings.tempo_span_max_disagreement)
    effective = span if use_span else grid if reason is None else original
    return dict(bpm=effective, librosa_bpm=original, grid_bpm=grid, effective_bpm=effective,
                span_bpm=span, span_intervals=width, beat_unit='1/4', median_interval=median, interval_mad=mad,
                stability=stability, interval_count=len(intervals),
                source='span' if use_span else 'grid' if reason is None else 'librosa', fallback_reason=reason)


def master_beats(seconds, rate, total):
    frames = sorted({seconds_to_frame(float(t), rate) for t in seconds if np.isfinite(t) and t >= 0})
    return [dict(id=f'b{i+1}', index=i+1, frame=f, seconds=f/rate)
            for i, f in enumerate(f for f in frames if f < total)]


def analyze(y, settings, rate, total):
    import librosa
    bpm, positions = librosa.beat.beat_track(y=y, sr=settings.analysis_rate,
                                           hop_length=settings.hop_length, units='time')
    value = float(np.asarray(bpm).reshape(-1)[0])
    beats = master_beats(positions, rate, total)
    return (value if value > 0 and beats else None), beats


def analyze_rhythm(y, settings, rate, total, *, source_audio=None, config=None,
                   check_cancel=lambda: None):
    """Backend-neutral evidence; master_beats remains the only time conversion."""
    from . import beat_backend
    requested, reason = 'librosa', None
    try:
        config = beat_backend.configuration() if config is None else config
        requested = config.get('backend', 'librosa')
        if requested not in ('librosa', 'beat-transformer'):
            raise ValueError('Unknown rhythm backend: ' + str(requested))
        if requested == 'beat-transformer':
            audio, input_rate = source_audio if source_audio is not None else (y, settings.analysis_rate)
            response = beat_backend.infer(audio, input_rate, config, check_cancel)
            evidence = beat_backend.normalize(response, total / rate)
            # Reject extrapolated beats deep in a silent prefix. Allow 100 ms onset
            # uncertainty for the centered neural STFT; never add a beat at zero.
            width = max(1, round(input_rate * .01))
            samples = np.asarray(audio)[:round(total / rate * input_rate)]
            padded = np.pad(samples, (0, (-len(samples)) % width))
            rms = np.sqrt(np.mean(padded.reshape(-1, width).astype(float)**2, axis=1))
            floor = max(10**(settings.silence_db/20), float(np.max(rms))*10**(settings.relative_silence_db/20))
            active = np.flatnonzero(rms > floor)
            if not len(active):
                raise beat_backend.BackendUnavailable('No audible activity supports neural beats.')
            onset = active[0] * width / input_rate
            seconds = [t for t in evidence['beats'] if t >= onset - .1]
            beats = master_beats(seconds, rate, total)
            if len(beats) < 2:
                raise beat_backend.BackendUnavailable('No usable beat grid outside leading silence.')
            downbeats = master_beats(evidence['downbeats'], rate, total)
            tolerance = min(round(.07 * rate), .2 * np.median(np.diff([b['frame'] for b in beats])))
            matched = []
            for down in downbeats:
                nearest = min(beats, key=lambda b: abs(b['frame'] - down['frame']))
                if abs(nearest['frame'] - down['frame']) <= tolerance and nearest['id'] not in [b['beat_id'] for b in matched]:
                    matched.append(dict(frame=down['frame'], seconds=down['seconds'],
                                        beat_id=nearest['id'], beat_index=nearest['index']))
            usable = evidence['downbeats_reliable'] and len(matched) >= 3
            meter = dict(numerator=4, denominator=4, source='default')
            detected = evidence['meter']
            # Require at least two complete, agreeing bar observations.
            gaps = np.diff([d['beat_index'] for d in matched])
            if usable and detected and len(gaps) >= 2 and np.all(gaps == detected['numerator']):
                meter = dict(**detected, source='auto')
            first = dict(beat_id=matched[0]['beat_id'] if usable else None,
                         source='auto' if usable else 'default')
            tempo = estimate_tempo(evidence['bpm'], [b['seconds'] for b in beats], settings)
            tempo['detector_bpm'] = tempo.pop('librosa_bpm')
            tempo['librosa_bpm'] = None
            if tempo['source'] == 'librosa':
                tempo['source'] = 'detector'
            tempo['detector_backend'] = 'beat-transformer'
            warnings = []
            if not usable:
                warnings.append('Beat-Transformer supplied no reliable aligned downbeat sequence; first downbeat remains unknown.')
            if meter['source'] == 'default':
                warnings.append('Beat-Transformer meter unavailable, unsupported or inconsistent; 4/4 remains default metadata.')
            return dict(beats=beats, tempo=tempo, meter=meter, first_downbeat=first,
                        warnings=warnings, provenance=dict(requested_backend=requested,
                        selected_backend='beat-transformer', fallback_reason=None,
                        model=evidence['metadata'], detected_downbeats=downbeats,
                        aligned_downbeats=matched, detected_meter=detected,
                        reported_meter=evidence['reported_meter'], downbeats_usable=usable,
                        leading_activity_seconds=float(onset),
                        removed_prefix_beats=len(evidence['beats'])-len(seconds)))
    except beat_backend.BackendUnavailable as exc:
        reason = str(exc)
        if requested == 'librosa':
            requested = 'configuration-error'
        beat_backend.warn(reason)
    check_cancel()
    bpm, beats = analyze(y, settings, rate, total)
    return dict(beats=beats, tempo=estimate_tempo(bpm, [b['seconds'] for b in beats], settings),
                meter=dict(numerator=4, denominator=4, source='default'),
                first_downbeat=dict(beat_id=None, source='default'),
                warnings=[f'Beat-Transformer unavailable; using librosa: {reason}'] if reason else [],
                provenance=dict(requested_backend=requested, selected_backend='librosa',
                                fallback_reason=reason))


def intervals(beats, total):
    """Explicit prefix/tail; do not pretend an invented grid is detected beats."""
    by_frame = {b['frame']: b for b in beats}
    bounds = sorted({0, total, *by_frame})
    for a, b in zip(bounds, bounds[1:]):
        beat = by_frame.get(a)
        yield a, b, beat, ('beat' if beat and b in by_frame else 'tail' if beat else 'prefix' if beats else 'unmetered')
