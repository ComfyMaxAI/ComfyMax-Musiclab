"""Standalone Chord-CNN-LSTM CPU process; one JSON request and response."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

PROTOCOL='comfymax.chords.1'


def run(request):
    if request.get('protocol')!=PROTOCOL or request.get('device')!='cpu':
        raise ValueError('Unsupported chord protocol/device.')
    os.environ['CUDA_VISIBLE_DEVICES']=''
    started=time.perf_counter()
    import numpy as np
    import torch
    import librosa
    root=Path(request['model_root'])
    if not (root/'chordnet_ismir_naive.py').is_file():
        raise FileNotFoundError('Chord-CNN-LSTM model code missing.')
    sys.path.insert(0,str(root))
    from mir.nn.train import NetworkBehavior
    # CPU even if a host has CUDA/MPS or research device auto-selection.
    NetworkBehavior._get_optimal_device=lambda self: torch.device('cpu')
    from chordnet_ismir_naive import ChordNet
    from extractors.xhmm_ismir import XHMMDecoder
    torch.set_num_threads(4)
    timings=dict(imports_seconds=time.perf_counter()-started)
    tick=time.perf_counter()
    nets=[]; weights=[]
    for i in range(5):
        path=root/'cache_data'/f'joint_chord_net_ismir_naive_v1.0_reweight(0.0,10.0)_s{i}.best.sdict'
        net=ChordNet(None)
        state=torch.load(path,map_location='cpu',weights_only=True)
        net.load_state_dict(state["net"]); net.eval()
        nets.append(net)
        weights.append(dict(name=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    timings['model_loading_seconds']=time.perf_counter()-tick
    tick=time.perf_counter()
    audio,_=librosa.load(request['audio_path'],sr=22050,mono=True)
    cqt=np.abs(librosa.hybrid_cqt(y=audio,sr=22050,bins_per_octave=36,
        fmin=librosa.note_to_hz('F#0'),n_bins=288,tuning=None,hop_length=512)).T.astype('float32')
    timings['preprocessing_seconds']=time.perf_counter()-tick
    tick=time.perf_counter(); predictions=[]
    with torch.inference_mode():
        x=torch.from_numpy(cqt)
        for net in nets: predictions.append(net.inference(x))
    probs=[np.mean([p[i] for p in predictions],axis=0) for i in range(6)]
    timings['inference_seconds']=time.perf_counter()-tick
    tick=time.perf_counter()
    dictionary=root/'data/submission_chord_list.txt'
    decoder=XHMMDecoder(template_file=str(dictionary))
    tags=decoder.decode(probs,np.ones(len(cqt),dtype=np.int8))
    segments=[]; start=0; duration=len(audio)/22050
    for end in range(1,len(tags)+1):
        if end==len(tags) or tags[end]!=tags[start]:
            a,b=start*512/22050,min(end*512/22050,duration)
            if a<b:
                segments.append(dict(start_seconds=a,end_seconds=b,label=tags[start],evidence=None))
            start=end
    timings['decoding_seconds']=time.perf_counter()-tick
    return dict(protocol=PROTOCOL,success=True,backend='chord-cnn-lstm',segments=segments,
        metadata=dict(name='Chord-CNN-LSTM',worker_version='2B.1',device='cpu',
        torch=torch.__version__,librosa=librosa.__version__,numpy=np.__version__,
        weights=weights,dictionary='submission',dictionary_sha256=hashlib.sha256(dictionary.read_bytes()).hexdigest(),
        decoder_sha256=hashlib.sha256((root/'extractors/xhmm_ismir.py').read_bytes()).hexdigest(),
        architecture_sha256=hashlib.sha256((root/'chordnet_ismir_naive.py').read_bytes()).hexdigest(),
        decoder_transition_penalty=decoder.diff_trans_penalty,decoder_initial_state='N',
        decoder_no_chord_multiplier=.5,model_folds=5,probability_aggregation='model-native five-fold mean',
        score_kind='unavailable: decoded segments have no calibrated chord confidence',
        sample_rate=22050,hop_length=512),timings=timings)


def main():
    try:
        request=json.load(sys.stdin)
        startup=max(0,time.time()-request['launch_unix'])
        # Reuse only the dependency-free cancellation watchdog, not the rhythm model.
        from beat_worker import watch_request
        watch_request(request)
        with contextlib.redirect_stdout(sys.stderr): response=run(request)
        response['timings']['worker_startup_seconds']=startup
        json.dump(response,sys.stdout,allow_nan=False)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        json.dump(dict(protocol=PROTOCOL,success=False,error=f'{type(exc).__name__}: {exc}'),sys.stdout)
        return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
