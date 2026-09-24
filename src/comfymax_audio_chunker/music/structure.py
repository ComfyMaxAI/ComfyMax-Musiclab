"""Optional meter context on the existing quarter-note beat clock."""
from fractions import Fraction
import numpy as np
from .chords import templates
from .model import label


def build_bars(beats, total, meter, downbeat):
    if downbeat.get('beat_id') is None:
        return [], 'first_downbeat_unknown'
    unit_count = Fraction(meter['numerator'] * 4, meter['denominator'])
    if unit_count.denominator != 1:
        return [], 'bar_not_aligned_to_quarter_beat_grid'
    width = int(unit_count)
    anchor = next((i for i,b in enumerate(beats) if b['id'] == downbeat['beat_id']), None)
    if anchor is None:
        raise ValueError('First downbeat must reference an existing beat.')
    bars = []
    def append(number, members, start, end, pickup, incomplete):
        if end <= start:
            return
        bars.append(dict(id=f'bar{len(bars)+1}', number=number,
                         beat_ids=[b['id'] for b in members], start_frame=start, end_frame=end,
                         pickup=pickup, incomplete=incomplete, source=downbeat['source'],
                         meter_source=meter['source'], beat_unit='1/4'))
    append(0, beats[:anchor], 0, beats[anchor]['frame'], True, True)
    typical = float(np.median(np.diff([b['frame'] for b in beats]))) if len(beats)>1 else None
    for offset in range(anchor, len(beats), width):
        members = beats[offset:offset+width]
        end = beats[offset+width]['frame'] if offset+width < len(beats) else total
        incomplete = len(members) < width
        if typical is None or end-members[-1]['frame'] < typical * .75:
            incomplete = True
        append(1+(offset-anchor)//width, members, members[0]['frame'], end, False, incomplete)
    return bars, None


def summarize_bars(bars, raw, scores, families):
    vocabulary, _ = templates()
    lookup = {r['beat_id']: i for i,r in enumerate(raw) if r.get('beat_id')}
    for bar in bars:
        indexes = [lookup[b] for b in bar['beat_ids'] if b in lookup]
        if not indexes:
            bar['harmonic_summary'] = None
            continue
        mean = np.mean(scores[indexes],axis=0); median = np.median(scores[indexes],axis=0)
        triad = int(np.argmax(mean[:24])); seventh = 24+int(np.argmax(mean[24:]))
        labels = [label(families[i]) for i in indexes]
        tonal = [v for v in labels if v not in ('N','unknown')]
        dominant = max(dict.fromkeys(tonal), key=tonal.count) if tonal else 'unknown'
        changes = [raw[indexes[k]]['start_frame'] for k in range(1,len(indexes)) if labels[k] != labels[k-1]]
        bar['harmonic_summary'] = dict(dominant_triad_family=dominant,
            strongest_triad_label=label(vocabulary[triad]), strongest_seventh_label=label(vocabulary[seventh]),
            average_triad_score=float(mean[triad]), median_triad_score=float(median[triad]),
            average_seventh_score=float(mean[seventh]), median_seventh_score=float(median[seventh]),
            consistency=labels.count(dominant)/len(labels), possible_internal_changes=changes)
