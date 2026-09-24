"""Read-only reference analysis: python -m comfymax_audio_chunker.music.reference."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import soundfile as sf
from .pipeline import analyze


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wav',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--compare-librosa',action='store_true')
    args=parser.parse_args()
    wave,rate=sf.read(args.wav,dtype='float32',always_2d=True)
    checksum=hashlib.sha256(args.wav.read_bytes()).hexdigest()
    config=json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else None
    results={}
    modes=[('configured',config)]
    if args.compare_librosa: modes.append(('librosa',dict(backend='librosa')))
    summary={}
    for name,selected in modes:
        started=time.monotonic()
        result=analyze(wave,rate,checksum,rhythm_config=selected)
        results[name]=result
        first=next((b for b in result['beats'] if b['id']==result['first_downbeat']['beat_id']),None)
        provenance=result['analysis']['rhythm_backend']
        summary[name]=dict(selected_backend=provenance['selected_backend'],
            fallback_reason=provenance['fallback_reason'],beats=len(result['beats']),
            downbeats=len(provenance.get('detected_downbeats',[])),
            first_beat_seconds=result['beats'][0]['seconds'] if result['beats'] else None,
            first_downbeat=first, meter=result['meter'],tempo=result['tempo'],
            duration_seconds=len(wave)/rate,elapsed_seconds=time.monotonic()-started,
            selected_channel=result['analysis']['selected_channel'])
    args.output.mkdir(parents=True,exist_ok=True)
    for name,result in results.items():
        (args.output/f'{name}-music-analysis.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(summary,indent=2,allow_nan=False))
    return 0


if __name__=='__main__': raise SystemExit(main())
