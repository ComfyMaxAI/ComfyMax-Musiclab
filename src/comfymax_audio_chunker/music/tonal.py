"""Harmonic features and a 24-key profile baseline."""
import numpy as np
from .model import NAMES


def select_channel(wave):
    """Select the highest-energy channel: no stereo phase cancellation."""
    energy = np.mean(np.square(wave, dtype=np.float64), axis=0)
    index = int(np.argmax(energy))
    return np.ascontiguousarray(wave[:, index]), index


def features(y, settings, checkpoint=lambda *_: None):
    import librosa
    if float(np.max(np.abs(y))) <= 10**(settings.silence_db/20):
        count=1+len(y)//settings.hop_length
        checkpoint(45, 'Silence detected; retaining empty harmonic features.')
        return np.zeros((12,count)),np.zeros(count)
    harmonic, _ = librosa.effects.hpss(y, margin=(settings.harmonic_margin, 1.0))
    checkpoint(45, 'Harmonic preprocessing complete; calculating CQT chroma…')
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=settings.analysis_rate,
                                      hop_length=settings.hop_length, norm=None)
    rms = librosa.feature.rms(y=harmonic, frame_length=2048, hop_length=settings.hop_length)[0]
    n = min(chroma.shape[1], len(rms))
    return chroma[:, :n], rms[:n]


def estimate_key(chroma, rms, floor):
    active = rms > floor
    if not np.any(active):
        return dict(estimated='unknown', root_pc=None, mode='unknown', score=0.0, margin=0.0, manual=None)
    vector = np.mean(chroma[:, active], axis=1)
    vector = vector - vector.mean()
    norm = np.linalg.norm(vector)
    if norm < 1e-12:
        return dict(estimated='unknown', root_pc=None, mode='unknown', score=0.0, margin=0.0, manual=None)
    profiles = {'major': [6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88],
                'minor': [6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17]}
    scores = []
    for mode, values in profiles.items():
        profile = np.array(values); profile -= profile.mean(); profile /= np.linalg.norm(profile)
        for root in range(12):
            scores.append((float(np.dot(vector/norm, np.roll(profile, root))), root, mode))
    scores.sort(reverse=True)
    best, root, mode = scores[0]
    return dict(estimated=NAMES[root] + (' minor' if mode == 'minor' else ' major'), root_pc=root,
                mode=mode, score=max(0.0, min(1.0, best)), margin=best-scores[1][0], manual=None)
