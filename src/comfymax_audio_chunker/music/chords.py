"""Pitch-class templates, independent of display spelling and global key."""
import numpy as np
from .model import chord, label

# Equal weights preserve Phase 1 triads. No root bias: chroma cannot reliably
# distinguish root strength from instrumentation, register, or inversion.
QUALITIES = {
    'maj': ((0, 4, 7), None),
    'min': ((0, 3, 7), None),
    '7': ((0, 4, 7, 10), 'maj'),
    'min7': ((0, 3, 7, 10), 'min'),
    'maj7': ((0, 4, 7, 11), 'maj'),
}


def templates():
    values, vectors = [], []
    for quality, (offsets, _) in QUALITIES.items():
        for root in range(12):
            v = np.zeros(12)
            v[[(root+i)%12 for i in offsets]] = 1 / np.sqrt(len(offsets))
            values.append(chord(root, quality)); vectors.append(v)
    return values, np.array(vectors)


def recognize(vector, rms, floor, settings):
    values, bank = templates()
    norm = np.linalg.norm(vector)
    similarities = np.clip(bank @ (vector / norm), 0, 1) if norm > 1e-12 else np.zeros(len(values))
    scores = similarities.copy()
    lookup = {(v['root_pc'], v['quality']): i for i, v in enumerate(values)}
    evidence = {}
    for i, value in enumerate(values):
        offsets, parent = QUALITIES[value['quality']]
        if parent is None:
            continue
        root = value['root_pc']; pc = (root + offsets[-1]) % 12
        base = float(np.mean(vector[[(root + n) % 12 for n in QUALITIES[parent][0]]]))
        relative = float(vector[pc] / base) if base > 1e-12 else 0.0
        gain = float(similarities[i] - similarities[lookup[root, parent]])
        accepted = relative >= settings.extension_min_relative_energy and gain >= settings.extension_min_score_gain
        evidence[i] = dict(pitch_class=pc, relative_energy=relative,
                           threshold=settings.extension_min_relative_energy, score_gain=gain,
                           score_gain_threshold=settings.extension_min_score_gain, accepted=bool(accepted))
        if not accepted:
            scores[i] = 0.0  # Ineligible for both raw selection and Viterbi.
    order = np.argsort(-scores, kind='stable')
    # Keep only top candidates; rejected extensions can still be inspected via
    # their cosine rank without persisting a full 60-state score matrix.
    debug_order = np.argsort(-similarities, kind='stable')[:settings.top_candidates]
    candidates = []
    for i in debug_order:
        item = dict(**values[i], label=label(values[i]), score=float(similarities[i]))
        if i in evidence:
            item['extension_evidence'] = evidence[i]
        candidates.append(item)
    best, second = order[:2]; margin = float(scores[best] - scores[second])
    if rms <= floor or norm <= 1e-12:
        raw = chord(quality='N')
    elif scores[best] < settings.min_similarity or margin < settings.min_margin:
        raw = chord()
    else:
        raw = values[best]
    return raw, float(scores[best]), margin, candidates, scores
