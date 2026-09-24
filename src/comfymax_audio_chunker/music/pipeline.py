"""Pure analysis orchestration: reads a MIX snapshot, never writes a project."""
from dataclasses import asdict
from datetime import datetime, timezone
import uuid
import time
import numpy as np
from . import rhythm, tonal, chords, smoothing
from .model import Settings, SCHEMA, ALGORITHM, label, validate


class Cancelled(Exception):
    pass


def analyze(wave, rate, checksum=None, settings=None, progress=lambda *_: None, cancelled=lambda: False,
            rhythm_config=None, chord_config=None):
    analysis_started = time.perf_counter()
    settings = settings or Settings(); settings.validate()
    def checkpoint(percent, message):
        if cancelled():
            raise Cancelled('Music analysis cancelled. Previous results retained.')
        progress(percent, message)
    checkpoint(0, 'Loading music backend (first run may compile Numba kernels)…')
    import librosa
    if type(rate) is not int or rate <= 0 or wave.ndim != 2 or not len(wave) or not np.all(np.isfinite(wave)):
        raise ValueError('Music analysis requires finite nonempty project audio.')
    total = len(wave)
    mono, selected_channel = tonal.select_channel(wave)
    y = librosa.resample(mono, orig_sr=rate, target_sr=settings.analysis_rate)
    # Short files need enough context for the lowest CQT bins; padding is analysis-only.
    y = np.pad(y, (0,max(0,4*settings.analysis_rate-len(y))))
    checkpoint(10, 'Estimating tempo and beat positions…')
    timing = rhythm.analyze_rhythm(y, settings, rate, total, source_audio=(mono, rate),
                                  config=rhythm_config,
                                  check_cancel=lambda: checkpoint(10, 'Estimating rhythm…'))
    beats = timing['beats']
    checkpoint(25, 'Separating harmonic and percussive content…')
    chroma, rms = tonal.features(y, settings, checkpoint)
    times = np.arange(chroma.shape[1]) * settings.hop_length / settings.analysis_rate
    valid = times < total / rate
    chroma, rms, times = chroma[:,valid], rms[valid], times[valid]
    floor = max(10**(settings.silence_db/20), float(np.max(rms))*10**(settings.relative_silence_db/20))
    checkpoint(65, 'Estimating key and scoring beat-aligned chords…')
    key = tonal.estimate_key(chroma, rms, floor)
    raw, score_rows = [], []
    for a,b,beat,kind in rhythm.intervals(beats,total):
        checkpoint(70, 'Scoring beat intervals…')
        indexes = np.flatnonzero((times >= a/rate) & (times < b/rate))
        if not len(indexes):
            indexes = np.array([int(np.argmin(abs(times-(a+b)/(2*rate))))])
        vector = np.mean(chroma[:,indexes],axis=1)
        energy = float(np.sqrt(np.mean(rms[indexes]**2)))
        value, score, margin, candidates, all_scores = chords.recognize(vector,energy,floor,settings)
        raw.append(dict(id=f'p{len(raw)+1}', beat_id=beat['id'] if beat else None,
                        beat_index=beat['index'] if beat else None, interval_kind=kind,
                        start_frame=a,end_frame=b,start_seconds=a/rate,end_seconds=b/rate,
                        raw=value,best_label=label(value),score=score,margin=margin,
                        candidates=candidates,chroma=vector.tolist(),harmonic_rms=energy))
        score_rows.append(all_scores)
    checkpoint(85, 'Stabilizing chords and merging regions…')
    sequence, scores = smoothing.smooth(raw,np.array(score_rows),settings.transition_penalty)
    result = dict(schema_version=SCHEMA,
                  analysis=dict(id=str(uuid.uuid4()),created_utc=datetime.now(timezone.utc).isoformat(),
                                source_asset='mix',source_sha256=checksum,backend='librosa',backend_version=librosa.__version__,
                                algorithm_version='2B',settings=asdict(settings),selected_channel=selected_channel,
                                harmonic_algorithm_version=ALGORITHM,
                                rhythm_backend=timing['provenance'],
                                score_kind='uncalibrated_cosine_similarity',key_score_kind='profile_correlation',
                                features=dict(kind='CQT chroma',storage='beat_aggregates',frame_matrices_persisted=False)),
                  timeline=dict(sample_rate=rate,frames=total),
                  tempo=timing['tempo'],key=key,
                  meter=timing['meter'],
                  first_downbeat=timing['first_downbeat'],beats=beats,raw_predictions=raw,
                  regions=smoothing.regions(raw,sequence,scores,rate),
                  warnings=timing['warnings']+['Scores are not probabilities. Key is a global profile estimate.',
                            'Major/minor triads and dominant/minor/major sevenths are recognized; N and unknown are explicit states.'])
    if len(beats)<2:
        result['warnings'].append('Too few beats for a reliable beat grid; unmetered/prefix/tail coverage is retained.')
    from .temporal import apply_structure
    result = apply_structure(result)
    checkpoint(92, 'Analyzing optional neural chords…')
    from .neural_chords import apply_backend
    result = apply_backend(result, mono, rate, chord_config,
                           check_cancel=lambda: checkpoint(92, 'Analyzing optional neural chords…'))
    result['analysis']['chord_backend']['timings']['total_analysis_seconds'] = time.perf_counter()-analysis_started
    validate(result,result['timeline'])
    checkpoint(100, 'Music analysis complete.')
    return result
