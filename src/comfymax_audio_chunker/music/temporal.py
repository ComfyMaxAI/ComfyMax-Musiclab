"""Temporal context over immutable beat evidence; no audio extraction or key filter."""
import copy
from dataclasses import asdict
import numpy as np
from .chords import QUALITIES, templates, recognize
from .model import chord, label, Settings, validate
from .smoothing import smooth, regions
from .structure import build_bars, summarize_bars


def family(value):
    quality = value['quality']
    return chord(value['root_pc'], QUALITIES.get(quality, (None,None))[1] or quality)


def runs(values):
    start = 0
    for end in range(1,len(values)+1):
        if end == len(values) or values[end] != values[start]:
            yield start,end,values[start]
            start = end


def longest_support(flags):
    best = count = 0
    for flag in flags:
        count = count+1 if flag else 0
        best = max(best,count)
    return best


def stabilize(raw, scores, settings, bars):
    """Return initial/final states and compact metadata without mutating raw."""
    vocabulary, bank = templates()
    lookup = {(v['root_pc'],v['quality']):i for i,v in enumerate(vocabulary)}
    initial, _ = smooth(raw,scores,settings.transition_penalty)
    family_raw = [dict(r,raw=family(r['raw'])) for r in raw]
    family_scores = scores.copy()
    for j,value in enumerate(vocabulary[24:],24):
        parent=family(value); parent_index=lookup[parent['root_pc'],parent['quality']]
        family_scores[:,parent_index]=np.maximum(family_scores[:,parent_index],scores[:,j])
    family_scores[:,24:] = 0
    stable, _ = smooth(family_raw,family_scores,settings.transition_penalty)
    reasons = [[] for _ in raw]; resolutions = [None for _ in raw]
    def index(v): return lookup.get((v['root_pc'],v['quality']))
    def row_score(i,v):
        j=index(v)
        return float(scores[i,j]) if j is not None else 0.0
    # Retain already supported Phase 1.1 resolutions only with clear family
    # evidence or sustained neighboring support. Never turn a raw unknown into N.
    typical=float(np.median([r['end_frame']-r['start_frame'] for r in raw if r.get('beat_id')])) if any(r.get('beat_id') for r in raw) else 0
    family_path=copy.deepcopy(stable)
    for i,r in enumerate(raw):
        if r['raw']['quality'] != 'unknown': continue
        target=family_path[i]; j=index(target)
        neighbor_support=0
        for direction in (-1,1):
            k=i+direction
            while 0<=k<len(raw) and family_path[k]==target:
                neighbor_support+=1; k+=direction
        ordered=np.sort(family_scores[i,:24])
        clear=ordered[-1]-ordered[-2]>=settings.chord_change_single_beat_advantage
        reasonable=(j is not None and row_score(i,target)>=settings.unknown_bridge_min_similarity and
                    float(np.max(family_scores[i,:24]))-family_scores[i,j]<=settings.unknown_bridge_max_conflict)
        if (initial[i]['root_pc'] is not None and family(initial[i])==target and reasonable and
            (clear or neighbor_support>=settings.chord_change_min_beats) and typical and
            r['end_frame']-r['start_frame']<=typical*1.5):
            resolutions[i]=dict(original='unknown',resolved_to=label(target),
                                reason='supported_initial_viterbi_resolution',confidence=row_score(i,target))
            reasons[i].append('supported_initial_viterbi_resolution')
        else:
            stable[i]=chord()
    # Short family changes need an advantage over the stable neighboring family.
    before = copy.deepcopy(stable)
    for a,b,value in runs(before):
        if value['root_pc'] is None or b-a >= settings.chord_change_min_beats:
            continue
        neighbors = [before[k] for k in (a-1,b) if 0<=k<len(raw) and before[k]['root_pc'] is not None]
        if not neighbors: continue
        previous = max(neighbors,key=lambda v:float(np.mean([row_score(i,v) for i in range(a,b)])))
        advantage = float(np.mean([row_score(i,value)-row_score(i,previous) for i in range(a,b)]))
        if advantage < settings.chord_change_min_score_advantage:
            for i in range(a,b): stable[i]=dict(previous); reasons[i].append('short_change_hysteresis')
    for i,row in enumerate(family_raw):
        target=row['raw']
        if target['root_pc'] is not None and stable[i]['root_pc'] is not None and target!=stable[i]:
            if row_score(i,target)-row_score(i,stable[i])>=settings.chord_change_single_beat_advantage:
                stable[i]=dict(target); reasons[i].append('strong_single_beat_change')
    # Bar context may resolve a weak isolated minority, never a multi-beat change.
    summarize_bars(bars,raw,scores,stable)
    beat_rows = {r.get('beat_id'):i for i,r in enumerate(raw) if r.get('beat_id')}
    for bar in bars:
        summary=bar['harmonic_summary']
        if not summary or summary['consistency'] < settings.bar_min_consistency:
            continue
        target=next((v for v in vocabulary[:24] if label(v)==summary['dominant_triad_family']),None)
        if target is None: continue
        for beat in bar['beat_ids']:
            i=beat_rows.get(beat)
            if i is None or stable[i]['quality']=='N' or stable[i]==target: continue
            if stable[i]['quality']=='unknown':
                conflict=float(np.max(family_scores[i,:24]))-row_score(i,target)
                short=typical and raw[i]['end_frame']-raw[i]['start_frame']<=typical*1.5
                isolated=all(stable[k]['quality']!='unknown' for k in (i-1,i+1) if 0<=k<len(raw))
                if (short and isolated and row_score(i,target)>=settings.min_similarity and
                    conflict<settings.chord_change_min_score_advantage):
                    stable[i]=dict(target); reasons[i].append('bar_context_unknown')
                    resolutions[i]=dict(original='unknown',resolved_to=label(target),
                                        reason='isolated_gap_supported_by_bar_majority',confidence=row_score(i,target))
                continue
            same_neighbor = any(stable[k]==stable[i] for k in (i-1,i+1) if 0<=k<len(raw))
            if same_neighbor: continue
            advantage=row_score(i,stable[i])-row_score(i,target)
            if row_score(i,target)>=settings.unknown_bridge_min_similarity and advantage<settings.chord_change_min_score_advantage:
                stable[i]=dict(target); reasons[i].append('bar_context_weak_change')
    # Bridge only short unknown runs flanked by the same stable family.
    before=copy.deepcopy(stable)
    for a,b,value in runs(before):
        if value['quality']!='unknown' or b-a>settings.unknown_bridge_max_beats or a==0 or b==len(raw):
            continue
        target=before[a-1]
        if target['root_pc'] is None or target!=before[b]: continue
        if not typical or raw[b-1]['end_frame']-raw[a]['start_frame']>typical*1.5*settings.unknown_bridge_max_beats:
            continue
        support=[row_score(i,target) for i in range(a,b)]
        conflict=[float(np.max(family_scores[i,:24]))-support[i-a] for i in range(a,b)]
        if min(support)<settings.unknown_bridge_min_similarity or max(conflict)>settings.unknown_bridge_max_conflict:
            continue
        for i in range(a,b):
            stable[i]=dict(target); reasons[i].append('unknown_neighbor_bridge')
            resolutions[i]=dict(original='unknown', resolved_to=label(target),
                                reason='short_supported_gap_between_agreeing_families', confidence=support[i-a])
    summarize_bars(bars,raw,scores,stable)
    final=copy.deepcopy(stable); temporal=[[] for _ in raw]
    for a,b,triad in runs(stable):
        if triad['root_pc'] is None: continue
        root=triad['root_pc']; parent=triad['quality']; base_index=index(triad)
        members=[i for i in range(a,b) if raw[i].get('beat_id')]
        for quality,(offsets,parent_quality) in QUALITIES.items():
            if parent_quality!=parent: continue
            j=lookup[root,quality]; pc=(root+offsets[-1])%12
            energies=[]; gains=[]; flags=[]
            for i in members:
                vector=np.array(raw[i]['chroma']); norm=np.linalg.norm(vector)
                cos=bank @ (vector/norm) if norm>1e-12 else np.zeros(len(bank))
                base=float(np.mean(vector[[(root+n)%12 for n in QUALITIES[parent][0]]]))
                energy=float(vector[pc]/base) if base>1e-12 else 0.0
                gain=float(cos[j]-cos[base_index])
                energies.append(energy); gains.append(gain)
                flags.append(bool(scores[i,j]>0 and scores[i,j]>=np.max(scores[i])-settings.min_margin))
            n=len(members)
            if not n: continue
            stats=dict(triad_label=label(triad),seventh_label=label(vocabulary[j]),
                       total_beats=n,triad_supporting_beats=sum(scores[i,base_index]>=scores[i,j] for i in members),
                       supporting_beats=sum(flags),supporting_fraction=sum(flags)/n,
                       consecutive_support=longest_support(flags), median_relative_energy=float(np.median(energies)),
                       median_score_gain=float(np.median(gains)), maximum_score_gain=float(max(gains)))
            # Convert numpy scalars to plain JSON primitives.
            stats['triad_supporting_beats']=int(stats['triad_supporting_beats'])
            accepted={}; local_stats={}
            for left,right,supported in runs(flags):
                if not supported: continue
                length=right-left
                # Center a minimum-size context window on the candidate run;
                # include surrounding triad beats rather than only the extensions.
                width=max(settings.extension_temporal_window_beats,length)
                lo=max(0,left-(width-length)//2); hi=min(n,lo+width); lo=max(0,hi-width)
                fraction=sum(flags[lo:hi])/(hi-lo)
                med_energy=float(np.median(energies[lo:hi])); med_gain=float(np.median(gains[lo:hi]))
                sustained=(length>=settings.extension_temporal_min_beats and
                           fraction>=settings.extension_temporal_min_fraction and
                           float(np.median(energies[left:right]))>=settings.extension_min_relative_energy and
                           float(np.median(gains[left:right]))>=settings.extension_min_score_gain)
                for k in range(left,right):
                    i=members[k]
                    # Single-beat exception is deliberately dominant-only and
                    # requires a same-family preparation plus a fifth-down resolution.
                    coherent=(quality=='7' and i>0 and i+1<len(raw) and stable[i-1]==triad and
                              stable[i+1]['root_pc']==(root+5)%12 and stable[i+1]['quality'] in ('maj','min'))
                    exceptional=(length==1 and coherent and energies[k]>=settings.extension_single_beat_strong_energy and
                                 gains[k]>=settings.extension_single_beat_strong_gain)
                    accepted[i]='sustained_extension' if sustained else 'strong_resolving_dominant' if exceptional else None
                    local_stats[i]=dict(window_beats=hi-lo,window_supporting_fraction=fraction,
                                        window_median_relative_energy=med_energy,window_median_score_gain=med_gain)
            for k,i in enumerate(members):
                reason=accepted.get(i)
                entry=dict(stats, **local_stats.get(i,{}), accepted=bool(reason),
                           acceptance_reason=reason or 'insufficient_temporal_support')
                temporal[i].append(entry)
                if reason and (final[i]==triad or scores[i,j]>row_score(i,final[i])):
                    final[i]=dict(vocabulary[j]); reasons[i].append(reason)
    for i in range(len(raw)):
        if initial[i]!=final[i] and not reasons[i]: reasons[i].append('triad_family_stabilization')
    selected=[row_score(i,v) for i,v in enumerate(final)]
    for i,resolution in enumerate(resolutions):
        if resolution is not None:
            resolution['resolved_to']=label(final[i])
            resolution['confidence']=selected[i]
    return initial,final,selected,temporal,resolutions,reasons


def apply_structure(data, meter=None, first_downbeat=None):
    """Copy-on-write structural rerun; raw evidence and manual chords are protected."""
    if any(any(v is not None for v in r.get('manual',{}).values()) for r in data['regions']):
        raise ValueError('Manual chord overrides must be reconciled before structural smoothing.')
    if 'chord_detections' in data:
        from .neural_chords import restructure
        return restructure(data,meter,first_downbeat)
    result=copy.deepcopy(data)
    if meter is not None: result['meter']=copy.deepcopy(meter)
    if first_downbeat is not None: result['first_downbeat']=copy.deepcopy(first_downbeat)
    result.pop('bars',None)
    validate(result,result['timeline'])
    settings=Settings(**result['analysis']['settings']); settings.validate()
    raw=result['raw_predictions']; scores=[]
    # Reconstruct ephemeral scores from stored aggregates; raw/candidates never rewritten.
    for row in raw:
        scores.append(recognize(np.asarray(row['chroma']),1,0,settings)[4])
    scores=np.array(scores)
    bars,disabled=build_bars(result['beats'],result['timeline']['frames'],result['meter'],result['first_downbeat'])
    initial,final,selected,temporal,resolutions,reasons=stabilize(raw,scores,settings,bars)
    vocabulary,_=templates()
    result['initial_regions']=regions(raw,initial,[float(scores[i,vocabulary.index(v)]) if v['root_pc'] is not None else 0.0 for i,v in enumerate(initial)],result['timeline']['sample_rate'])
    result['regions']=regions(raw,final,selected,result['timeline']['sample_rate'])
    for i,row in enumerate(raw):
        row['initial_smoothed']=initial[i]
        row['temporal_extension_evidence']=temporal[i]
        row['unknown_resolution']=resolutions[i]
        row['stabilization_reason']=reasons[i]
    by_id={r['id']:i for i,r in enumerate(raw)}
    for region in result['regions']:
        indexes=[by_id[r] for r in region['raw_ids']]
        region['triad_family']=family(region)
        # One compact run summary per quality; beat-local decisions remain on raw rows.
        summaries={e['seventh_label']:e for i in indexes for e in temporal[i]}
        region['temporal_extension_evidence']=list(summaries.values())
        region['unknown_resolution']=[dict(raw_id=raw[i]['id'],**resolutions[i]) for i in indexes if resolutions[i]]
        region['stabilization_reason']=list(dict.fromkeys(reason for i in indexes for reason in reasons[i]))
    result['bars']=bars; result['bar_context_disabled_reason']=disabled
    result['warnings']=[w for w in result.get('warnings',[]) if not w.startswith(('Meter 4/4 is a default.','Structure:'))]
    meter=result['meter']; down=result['first_downbeat']
    result['warnings'].append(f"Structure: meter {meter['numerator']}/{meter['denominator']} ({meter['source']}); "
                              f"first downbeat {down['beat_id'] or 'unknown'} ({down['source']}); "
                              f"bar context {disabled or 'available'}.")
    result['analysis']['structural_algorithm_version']='1.2'
    result['analysis']['settings']=asdict(settings)
    validate(result,result['timeline'])
    return result
