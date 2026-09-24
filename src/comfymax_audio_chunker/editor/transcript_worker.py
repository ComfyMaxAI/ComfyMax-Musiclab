"""Reuse the Stage 1 Whisper transcription function in a cancellable process."""
import json
import sys
import time
from types import SimpleNamespace
from pathlib import Path
from importlib.metadata import version

from ..cli import transcribe, sha256
from .transcript import MODEL, SCHEMA, LANGUAGES, make_result


def run(request):
    if request.get('protocol')!=SCHEMA: raise ValueError('Transcript protocol mismatch')
    selected=request.get('language')
    if selected not in dict(LANGUAGES).values(): raise ValueError('Unsupported transcript language')
    started=time.monotonic(); path=Path(request['path'])
    checksum=sha256(path)
    if checksum!=request['source']['sha256']: raise ValueError('Transcript source checksum mismatch')
    native=[]
    args=SimpleNamespace(model=MODEL,offline=True,language=selected,initial_prompt=None)
    segments,_,language=transcribe(path,args,request['duration'],request['active'],
                                  word_timestamps=False,raw_output=native)
    valid=[s for s in segments if s['end']>s['start']]
    provenance=dict(backend='faster-whisper',version=version('faster-whisper'),model=MODEL,
                    device='cpu',compute_type='int8',local_files_only=True,word_timestamps=False,
                    requested_language=selected,task='transcribe',multilingual=False,initial_prompt=None,
                    language=language,seconds=time.monotonic()-started,discarded_zero_length=len(segments)-len(valid),
                    diagnostics=segments)
    return make_result(valid,native,provenance,request['source'],request['duration'])


if __name__=='__main__':
    try:
        response=dict(protocol=SCHEMA,success=True,transcript=run(json.load(sys.stdin)))
    except Exception as exc:
        response=dict(protocol=SCHEMA,success=False,error=f'{type(exc).__name__}: {exc}')
    print(json.dumps(response,allow_nan=False))
