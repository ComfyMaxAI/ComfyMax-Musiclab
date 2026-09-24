"""Neural chord evidence, conservative grid alignment and final-region projection.

Template scoring and temporal smoothing run unchanged in a parallel evidence view.
Decoded neural identities are never demoted by template uncertainty.
"""
import copy
import hashlib
import json
import math
import re
import time
import numpy as np
from .model import chord, seconds_to_frame
from . import chord_backend

QUALITIES={'maj','min','7','maj7','min7','dim','aug','dim7','hdim7','sus2','sus4',
           'sus4(b7)','9','maj9','min9','11','13'}
DEFAULT_ALIGNMENT=dict(tolerance_seconds=.08,downbeat_preference_seconds=.015,
                       max_beat_fraction=.2,min_grid_stability=.75)


def map_label(text):
    if text in ('N','N/C'): return chord(None,'N'), 'no_chord'
    if text in ('X','unknown'): return chord(), 'unknown_label'
    match=re.fullmatch(r'([A-G])([#b]?)(?::([^/]+))?(?:/([^/]+))?',text)
    if not match: return chord(), 'unsupported_label'
    letter,accidental,quality,bass=match.groups(); quality=quality or 'maj'
    if quality not in QUALITIES: return chord(), 'unsupported_quality'
    root=({'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}[letter]+{'':0,'#':1,'b':-1}[accidental])%12
    bass_pc=None
    if bass:
        degree=re.fullmatch(r'([b#]?)([1-7])',bass)
        note=re.fullmatch(r'([A-G])([b#]?)',bass)
        if degree:
            accidental,number=degree.groups()
            bass_pc=(root+[0,2,4,5,7,9,11][int(number)-1]+{'':0,'#':1,'b':-1}[accidental])%12
        elif note:
            bass_pc=({'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}[note[1]]+{'':0,'#':1,'b':-1}[note[2]])%12
        else: return chord(), 'unsupported_bass'
    return chord(root,quality,bass_pc), 'mapped'


def fingerprint(raw):
    return hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def alignment_settings(config):
    supplied=config.get('alignment',{})
    if not isinstance(supplied,dict):
        raise chord_backend.BackendUnavailable('Alignment settings must be an object.')
    settings=dict(DEFAULT_ALIGNMENT,**supplied)
    if set(settings)!=set(DEFAULT_ALIGNMENT) or any(type(v) not in (int,float) or not math.isfinite(v) for v in settings.values()):
        raise chord_backend.BackendUnavailable('Invalid chord alignment settings.')
    if not (0<=settings['tolerance_seconds']<=.25 and 0<=settings['downbeat_preference_seconds']<=settings['tolerance_seconds'] and
            0<settings['max_beat_fraction']<=.5 and 0<=settings['min_grid_stability']<=1):
        raise chord_backend.BackendUnavailable('Chord alignment settings outside supported bounds.')
    return settings


def normalize(response,rate,total):
    try:
        json.dumps(response,allow_nan=False)
        raw=response['segments']; metadata=response['metadata']
        if response.get('backend')!='chord-cnn-lstm' or not isinstance(raw,list) or not raw or not isinstance(metadata,dict):
            raise ValueError('Missing chord segments/model metadata.')
        result=[]; previous=0.; last_frame=0
        for i,event in enumerate(raw):
            a,b=event['start_seconds'],event['end_seconds']; label=event['label']
            if (type(a) not in (int,float) or type(b) not in (int,float) or not 0<=a<b or a<previous or
                    b>total/rate+.05 or a>=total/rate or not isinstance(label,str) or not label):
                raise ValueError('Invalid/overlapping chord segment.')
            start,end=seconds_to_frame(a,rate),min(total,seconds_to_frame(b,rate))
            if not last_frame<=start<end: raise ValueError('Sub-sample or overlapping chord interval.')
            mapped,status=map_label(label)
            result.append(dict(id=f'n{i+1}',original_label=label,original_start_seconds=a,original_end_seconds=b,
                start_frame=start,end_frame=end,start_seconds=start/rate,end_seconds=end/rate,
                mapping_status=status,**mapped))
            previous=b; last_frame=end
        return result
    except (KeyError,TypeError,ValueError,OverflowError) as exc:
        raise chord_backend.BackendUnavailable(f'Malformed chord worker evidence: {exc}') from exc


def align(segments,data,settings):
    rate=data['timeline']['sample_rate']; total=data['timeline']['frames']
    beats=data['beats']; tempo=data['tempo']
    reliable=(len(beats)>=5 and tempo.get('stability',0)>=settings['min_grid_stability'] and
              tempo.get('interval_count',0)>=4 and tempo.get('fallback_reason') is None)
    down_ids=set()
    if data['first_downbeat']['source']=='manual':
        down_ids={b['beat_ids'][0] for b in data.get('bars',[]) if b['beat_ids'] and not b['pickup']}
    elif data['first_downbeat']['source']=='auto':
        provenance=data['analysis'].get('rhythm_backend',{})
        if provenance.get('downbeats_usable'):
            down_ids={d['beat_id'] for d in provenance.get('aligned_downbeats',[])}
    grid=[dict(frame=b['frame'],beat_id=b['id'],kind='downbeat' if b['id'] in down_ids else 'beat') for b in beats] if reliable else []
    spacing=float(np.median(np.diff([b['frame'] for b in beats]))) if reliable else 0
    tolerance=min(round(settings['tolerance_seconds']*rate),settings['max_beat_fraction']*spacing)
    bounds=sorted({0,total,*[s[k] for s in segments for k in ('start_frame','end_frame')]})
    choices={}
    for original in bounds:
        pick=dict(original_frame=original,aligned_frame=original,snapped=False,beat_id=None,grid_kind=None,
                  reason='endpoint' if original in (0,total) else 'no_reliable_grid' if not reliable else 'outside_tolerance')
        if original not in (0,total):
            candidates=[g for g in grid if abs(g['frame']-original)<=tolerance]
            if candidates:
                nearest=min(candidates,key=lambda g:(abs(g['frame']-original),g['frame']))
                downs=[g for g in candidates if g['kind']=='downbeat' and abs(g['frame']-original)<=abs(nearest['frame']-original)+settings['downbeat_preference_seconds']*rate]
                chosen=min(downs,key=lambda g:abs(g['frame']-original)) if downs else nearest
                pick.update(aligned_frame=chosen['frame'],snapped=chosen['frame']!=original,beat_id=chosen['beat_id'],grid_kind=chosen['kind'],
                            reason='already_on_grid' if chosen['frame']==original else 'nearby_downbeat' if downs else 'nearby_beat')
        choices[original]=pick
    # Retain every event, including arbitrarily short mid-bar changes and N gaps.
    # Cancel conflicting moves jointly instead of deleting/collapsing segments.
    while True:
        conflicts=set()
        for a,b in zip(bounds,bounds[1:]):
            if choices[a]['aligned_frame']>=choices[b]['aligned_frame']: conflicts.update((a,b))
        if not conflicts: break
        for frame in conflicts:
            choices[frame]=dict(original_frame=frame,aligned_frame=frame,snapped=False,beat_id=None,grid_kind=None,reason='would_collapse_or_reorder_interval')
    result=[]
    for segment in segments:
        a,b=choices[segment['start_frame']],choices[segment['end_frame']]
        row=copy.deepcopy(segment)
        row.update(start_frame=a['aligned_frame'],end_frame=b['aligned_frame'],
                   start_seconds=a['aligned_frame']/rate,end_seconds=b['aligned_frame']/rate,
                   alignment=dict(start=copy.deepcopy(a),end=copy.deepcopy(b)))
        result.append(row)
    return result


def final_regions(aligned,data):
    rate=data['timeline']['sample_rate']; total=data['timeline']['frames']; result=[]; cursor=0
    def add(a,b,value,detection_id,reason):
        if a>=b: return
        ids=[p['id'] for p in data['raw_predictions'] if p['start_frame']<b and p['end_frame']>a]
        result.append(dict(id=f'c{len(result)+1}',**value,start_frame=a,end_frame=b,start_seconds=a/rate,end_seconds=b/rate,
            raw_ids=ids,score=None,score_kind='unavailable_neural_segment_confidence',
            manual=dict(label=None,start_frame=None,end_frame=None),backend='chord-cnn-lstm',
            detection_id=detection_id,stabilization_reason=[reason]))
    for row in aligned:
        add(cursor,row['start_frame'],chord(),None,'unobserved_neural_gap')
        add(row['start_frame'],row['end_frame'],{k:row[k] for k in ('root_pc','quality','bass_pc')},row['id'],
            'decoded_neural_identity_preserved' if row['mapping_status'] in ('mapped','no_chord') else row['mapping_status'])
        cursor=row['end_frame']
    add(cursor,total,chord(),None,'unobserved_neural_gap')
    return result


def project_evidence(data,detections):
    result=copy.deepcopy(data); tick=time.perf_counter()
    result['template_regions']=copy.deepcopy(result['regions'])
    evidence=copy.deepcopy(detections)
    evidence['aligned_segments']=align(evidence['normalized_segments'],result,evidence['alignment_settings'])
    result['chord_detections']=evidence
    result['regions']=final_regions(evidence['aligned_segments'],result)
    result['analysis']['chord_backend']['timings']['alignment_postprocessing_seconds']=time.perf_counter()-tick
    result['warnings']=[w for w in result['warnings'] if not w.startswith('Neural chords:')]
    result['warnings'].append('Neural chords: decoded identities preserved; template temporal/bar summaries are separate context, not neural confidence or overrides.')
    return result


def apply_backend(data,audio,rate,config=None,check_cancel=lambda:None):
    requested='template'; reason=None
    try:
        config=chord_backend.configuration() if config is None else config
        requested=config['backend']
        if requested not in ('template','chord-cnn-lstm'): raise ValueError('Unknown chord backend.')
        if requested=='chord-cnn-lstm':
            settings=alignment_settings(config)
            response=chord_backend.infer(audio,rate,config,check_cancel)
            normalized=normalize(response,rate,data['timeline']['frames'])
            evidence=dict(backend='chord-cnn-lstm',raw_segments=copy.deepcopy(response['segments']),
                raw_sha256=fingerprint(response['segments']),normalized_segments=normalized,alignment_settings=settings)
            result=copy.deepcopy(data)
            result['analysis']['chord_backend']=dict(requested_backend=requested,selected_backend=requested,
                fallback_reason=None,model=response['metadata'],timings=response.get('timings',{}),
                final_policy='decoded neural identity; bounded alignment; no template veto')
            return project_evidence(result,evidence)
    except chord_backend.BackendUnavailable as exc:
        reason=str(exc)
        import logging
        logging.getLogger(__name__).warning('Chord-CNN-LSTM fallback: %s',reason)
    result=copy.deepcopy(data)
    result['analysis']['chord_backend']=dict(requested_backend=requested,selected_backend='template',fallback_reason=reason,timings={})
    if reason: result['warnings'].append('Chord-CNN-LSTM unavailable; template result retained: '+reason)
    return result


def restructure(data,meter,first_downbeat):
    from .temporal import apply_structure
    from .model import validate
    validate(data,data['timeline'])
    base=copy.deepcopy(data); evidence=base.pop('chord_detections')
    base['regions']=base.pop('template_regions')
    base=apply_structure(base,meter,first_downbeat)
    result=project_evidence(base,evidence)
    validate(result,result['timeline'])
    return result


def _validate_evidence(data):
    """Validate evidence integrity, derived boundaries and canonical final regions."""
    if 'chord_detections' not in data: return
    e=data['chord_detections']; rate=data['timeline']['sample_rate']; total=data['timeline']['frames']
    def require(ok,message):
        if not ok: raise ValueError('Invalid neural chord evidence: '+message)
    require(data['analysis']['chord_backend']['selected_backend']=='chord-cnn-lstm','backend')
    require(e['raw_sha256']==fingerprint(e['raw_segments']),'raw evidence changed')
    normalized=normalize(dict(backend=e['backend'],segments=e['raw_segments'],metadata={}),rate,total)
    require(normalized==e['normalized_segments'],'normalization/clock')
    settings=alignment_settings(dict(alignment=e['alignment_settings']))
    aligned=align(normalized,data,settings)
    require(aligned==e['aligned_segments'],'alignment')
    expected=final_regions(aligned,data)
    require(len(expected)==len(data['regions']),'final region count')
    for actual,wanted in zip(data['regions'],expected):
        require(all(actual.get(k)==v for k,v in wanted.items() if k!='manual'),'final region projection')


def validate_evidence(data):
    try:
        _validate_evidence(data)
    except chord_backend.BackendUnavailable as exc:
        raise ValueError('Invalid neural evidence: '+str(exc)) from exc
