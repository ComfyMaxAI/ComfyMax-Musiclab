"""Independent segment-level lyrics; importing this module never loads Whisper."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import uuid

SCHEMA = 'comfymax.transcript.1'
MODEL = 'small'
LANGUAGES = (('Auto',None),('Spanish','es'),('English','en'),('Dutch','nl'),('French','fr'),('German','de'))
EVIDENCE_FIELDS = ('avg_logprob','no_speech_prob','compression_ratio','temperature')
# Review heuristics, not calibrated confidence. No filtering or text modification.
REVIEW_THRESHOLDS = dict(avg_logprob=-1.0,no_speech_prob=.8,no_speech_logprob=-.7,compression_ratio=2.4)


def review_reasons(evidence):
    reasons=[]
    for key in ('avg_logprob','no_speech_prob','compression_ratio'):
        threshold=REVIEW_THRESHOLDS[key]
        value=evidence.get(key)
        if type(value) not in (int,float) or not math.isfinite(value): continue
        if key=='no_speech_prob':
            # Singing often scores as "no speech" despite a good decode.
            logprob=evidence.get('avg_logprob')
            if type(logprob) not in (int,float) or not math.isfinite(logprob) or logprob>=REVIEW_THRESHOLDS['no_speech_logprob']:
                continue
        if (value<threshold if key=='avg_logprob' else value>threshold):
            reasons.append(f'{key}={value:.3f} ({"<" if key=="avg_logprob" else ">"} {threshold})')
            if key=='no_speech_prob': reasons[-1]+=f" with avg_logprob={logprob:.3f} < {REVIEW_THRESHOLDS['no_speech_logprob']}"
    return reasons


def segment_evidence(value):
    """Read-only lookup, including older transcripts without normalized metadata."""
    native=value['raw'].get('whisper_segments',[])
    diagnostics=value.get('provenance',{}).get('diagnostics',[])
    lookup={}
    for s in native+diagnostics:
        key=(s.get('start'),s.get('end'),s.get('text','').strip())
        lookup.setdefault(key,{}).update({k:s[k] for k in EVIDENCE_FIELDS if k in s})
    result=[]
    for s in value['raw']['segments']:
        evidence=dict(lookup.get((s['start'],s['end'],s['text'].strip()),{}))
        evidence.update({k:s[k] for k in EVIDENCE_FIELDS if k in s})
        result.append(evidence)
    return result
CACHE = Path(__file__).resolve().parents[3]/'.cache'/'whisper'


def availability():
    if importlib.util.find_spec('faster_whisper') is None:
        return False, 'Whisper unavailable; saved lyrics remain readable.'
    if not any((p/'model.bin').is_file() for p in (CACHE/f'models--Systran--faster-whisper-{MODEL}'/'snapshots').glob('*')):
        return False, f'Cached Whisper {MODEL} unavailable; no model will be downloaded.'
    return True, f'Whisper {MODEL}, local cache, CPU/int8; VOCALS → MIX'


def source_audio(data):
    key = 'vocals' if 'vocals' in data['assets'] else 'mix'
    return key, data['assets'][key]


def from_existing(data):
    if 'transcript' in data:
        return copy.deepcopy(data['transcript'])
    raw, edited = [], []
    for phrase in data.get('phrases', []):
        a,b = phrase.get('start'),phrase.get('end')
        if type(a) not in (float,int) or type(b) not in (float,int) or not 0 <= a < b <= data['timeline']['duration']:
            continue
        row = dict(id=phrase['id'], start=a, end=b, text=phrase['original_text'], source='imported transcript')
        raw.append(row); edited.append(dict(row, text=phrase['corrected_text'] if phrase.get('corrected_text') is not None else row['text']))
    return dict(schema_version=SCHEMA, source_audio=None, provenance=dict(backend='imported project phrases'),
                raw=dict(segments=raw), lyrics=dict(segments=edited, edited=edited!=raw, saved_at=None))


def validate(value, duration):
    if not isinstance(value,dict) or value.get('schema_version') != SCHEMA:
        raise ValueError('Invalid transcript schema')
    try:
        raw, lyrics = value['raw']['segments'], value['lyrics']['segments']
        if not isinstance(raw,list) or not isinstance(lyrics,list) or len(raw)!=len(lyrics):
            raise ValueError('Invalid transcript segment lists')
        identifiers=set()
        for r,e in zip(raw,lyrics):
            if not isinstance(r['id'],str) or not r['id'] or r['id'] in identifiers:
                raise ValueError('Invalid transcript segment ID')
            identifiers.add(r['id'])
            if any(type(r[k]) not in (int,float) or not math.isfinite(r[k]) for k in ('start','end')) or not 0<=r['start']<r['end']<=duration:
                raise ValueError('Invalid transcript timing')
            if any(e[k]!=r[k] for k in ('id','start','end','source')):
                raise ValueError('Lyrics editing cannot change timing/source/ID')
            if not isinstance(r['text'],str) or not isinstance(e['text'],str) or not isinstance(r['source'],str):
                raise ValueError('Invalid transcript text/source')
        if not isinstance(value['provenance'],dict): raise ValueError('Invalid transcript provenance')
        json.dumps(value,allow_nan=False)
        for old in value.get('history',[]):
            if 'history' in old: raise ValueError('Transcript history must be flat')
            validate(old,duration)
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError('Malformed transcript') from exc


def make_result(segments, native, provenance, source, duration):
    run = uuid.uuid4().hex
    rows=[dict(id=f'{run}:{i}',start=s['start'],end=s['end'],text=s['text'],source='Whisper')
          for i,s in enumerate(segments)]
    for row,segment in zip(rows,segments):
        row.update({k:segment[k] for k in EVIDENCE_FIELDS if k in segment})
    value=dict(schema_version=SCHEMA, source_audio=source, provenance=provenance,
               raw=dict(segments=rows, whisper_segments=native),
               lyrics=dict(segments=copy.deepcopy(rows),edited=False,saved_at=None))
    validate(value,duration)
    return value
