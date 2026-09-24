"""Export Phase 2B evidence and timestamped four-way reference comparisons."""
import argparse
import hashlib
import json
from pathlib import Path
import soundfile as sf
from .pipeline import analyze
from .model import label


def comparison(data):
    evidence=data.get('chord_detections')
    if not evidence: return 'Neural backend unavailable: '+str(data['analysis']['chord_backend']['fallback_reason'])+'\n'
    rate=data['timeline']['sample_rate']
    tracks=[data['template_regions'],evidence['normalized_segments'],evidence['aligned_segments'],data['regions']]
    bounds=sorted({0,data['timeline']['frames'],*[r[k] for rows in tracks for r in rows for k in ('start_frame','end_frame')]})
    lines=['# Timestamped chord comparison','',
        'Each row is a half-open master-clock interval. Raw CNN labels remain unmodified; final labels use the explicit mapping.',
        '', '| Start (s) | End (s) | Template final | CNN raw | CNN aligned | Phase 2B final |',
        '|---:|---:|---|---|---|---|']
    for a,b in zip(bounds,bounds[1:]):
        values=[]
        for i,rows in enumerate(tracks):
            r=next((r for r in rows if r['start_frame']<=a<r['end_frame']),None)
            values.append((r['original_label'] if i in (1,2) else label(r)) if r else 'unobserved')
        lines.append(f'| {a/rate:.6f} | {b/rate:.6f} | '+' | '.join(values)+' |')
    lines+=['','## Neural boundaries','', '| Raw start | Aligned start | Model label | Start reason | Start beat | End reason |','|---:|---:|---|---|---|---|']
    for r in evidence['aligned_segments']:
        a=r['alignment']['start']; b=r['alignment']['end']
        lines.append(f"| {r['original_start_seconds']:.6f} | {r['start_seconds']:.6f} | {r['original_label']} | {a['reason']} | {a['beat_id'] or '—'} | {b['reason']} |")
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wav',type=Path); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--chord-config',type=Path); parser.add_argument('--rhythm-config',type=Path)
    args=parser.parse_args()
    def config(path): return json.loads(path.read_text(encoding='utf-8-sig')) if path else None
    wave,rate=sf.read(args.wav,dtype='float32',always_2d=True)
    data=analyze(wave,rate,hashlib.sha256(args.wav.read_bytes()).hexdigest(),
        rhythm_config=config(args.rhythm_config),chord_config=config(args.chord_config))
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'music_analysis.json').write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')
    (args.output/'comparison.md').write_text(comparison(data),encoding='utf-8')
    print(json.dumps(dict(rhythm=data['analysis']['rhythm_backend']['selected_backend'],
        chord_backend=data['analysis']['chord_backend'],regions=len(data['regions']),
        sequence=[label(r) for r in data['regions']]),indent=2))


if __name__=='__main__': main()
