"""Viterbi stabilization; raw evidence is never changed."""
import numpy as np
from .chords import templates, QUALITIES
from .model import chord


def smooth(raw, scores, penalty):
    vocabulary, _ = templates()
    count = len(vocabulary)
    vocabulary += [chord(quality='N'), chord()]
    states = len(vocabulary)
    emissions = np.zeros((len(raw), states))
    emissions[:, :count] = np.where(scores > 0, scores, -1e6)
    for i, row in enumerate(raw):
        if row['raw']['quality'] == 'N':
            emissions[i] = -1e6; emissions[i,count] = 0
        elif row['raw']['quality'] == 'unknown':
            # Unknown competes with tonal states; neighbors may resolve a weak tie.
            emissions[i,count] = -1e6
            emissions[i,count+1] = max(scores[i]) + penalty / 2
        else:
            emissions[i,count:] = -1e6
    transitions = np.full((states,states), -penalty)
    # An evidenced extension changes less than a root/triad change. Two half
    # penalties allow a strong one-beat seventh (cosine gain ~0.134) to survive.
    for i, a in enumerate(vocabulary[:count]):
        for j, b in enumerate(vocabulary[:count]):
            if a['root_pc'] == b['root_pc'] and (
                QUALITIES[a['quality']][1] == b['quality'] or
                QUALITIES[b['quality']][1] == a['quality']):
                transitions[i,j] = -penalty / 2
    np.fill_diagonal(transitions, 0)
    back = np.zeros((len(raw),states), dtype=int); cost = emissions[0].copy()
    for i in range(1,len(raw)):
        options = cost[:,None] + transitions
        back[i] = np.argmax(options, axis=0)
        cost = emissions[i] + np.max(options, axis=0)
    state = int(np.argmax(cost)); path = [state]
    for i in range(len(raw)-1,0,-1):
        state = int(back[i,state]); path.append(state)
    path.reverse()
    return [dict(vocabulary[i]) for i in path], [float(scores[n,i]) if i<count else 0.0 for n,i in enumerate(path)]


def regions(raw, sequence, selected_scores, rate):
    result = []
    for row, value, score in zip(raw, sequence, selected_scores):
        if result and all(result[-1][k] == value[k] for k in ('root_pc','quality','bass_pc')):
            current = result[-1]; current['end_frame'] = row['end_frame']; current['end_seconds'] = row['end_frame']/rate
            current['raw_ids'].append(row['id']); current['_scores'].append(score)
        else:
            result.append(dict(id=f'c{len(result)+1}', **value, start_frame=row['start_frame'], end_frame=row['end_frame'],
                               start_seconds=row['start_frame']/rate, end_seconds=row['end_frame']/rate,
                               raw_ids=[row['id']], _scores=[score], manual=dict(label=None,start_frame=None,end_frame=None)))
    for row in result:
        row['score'] = float(np.mean(row.pop('_scores')))
    return result
