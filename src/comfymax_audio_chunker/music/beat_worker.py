"""Standalone CPU worker: JSON stdin/stdout; optional dependencies stay here.

Loads only trusted upstream Beat-Transformer model code/checkpoint. No application
services, CUDA, Demucs, or synthetic fallback downbeats are used.
"""
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import traceback

PROTOCOL = 'comfymax.rhythm.1'


def run(request):
    if request.get('protocol') != PROTOCOL or request.get('device') != 'cpu':
        raise ValueError('Unsupported protocol or device; Phase 2A is CPU only.')
    import collections
    import collections.abc
    import numpy as np
    # Compatibility shims are confined to this short-lived legacy process.
    collections.MutableSequence = collections.abc.MutableSequence
    np.float = float
    np.int = int
    import librosa
    import torch
    from scipy import signal

    root = Path(request['model_root'])
    checkpoint = Path(request['checkpoint'])
    if not (root / 'code' / 'DilatedTransformer.py').is_file() or not checkpoint.is_file():
        raise FileNotFoundError('Beat-Transformer code or checkpoint missing.')
    sys.path.insert(0, str(root / 'code'))
    from DilatedTransformer import Demixed_DilatedTransformerModel
    torch.set_num_threads(4)
    torch.manual_seed(0)
    audio, _ = librosa.load(request['audio_path'], sr=44100, mono=True)
    if len(audio) < 4096 or not np.all(np.isfinite(audio)):
        raise ValueError('Audio is too short or nonfinite.')
    # Explicit approximation of the five instrument channels, not stem separation.
    # Preserve the local research preprocessing without importing its application.
    harmonic, percussive = librosa.effects.hpss(audio, margin=3.0)
    b, a = signal.butter(5, 1000 / 22050, btype='low')
    views = [audio, librosa.effects.preemphasis(audio, coef=.97), percussive,
             harmonic, signal.filtfilt(b, a, audio)]
    mel = librosa.filters.mel(sr=44100, n_fft=4096, n_mels=128, fmin=30, fmax=11000).T
    specs = []
    for view in views:
        power = np.abs(librosa.stft(view, n_fft=4096, hop_length=1024))**2
        specs.append(librosa.power_to_db(power.T @ mel, ref=np.max))
    spec = np.stack(specs).astype('float32')
    model = Demixed_DilatedTransformerModel(attn_len=5, instr=5, ntoken=2,
        dmodel=256, nhead=8, d_hid=1024, nlayers=9, norm_first=True)
    state = torch.load(checkpoint, map_location='cpu', weights_only=True)
    model.load_state_dict(state['state_dict'])
    model.eval()
    # Limit memory; overlapping context is discarded, never counted as extra time.
    # One global DBN decode avoids restarting bar phase at chunk boundaries.
    length = spec.shape[1]
    activation = np.empty((length, 2), dtype='float32')
    with torch.inference_mode():
        for start in range(0, length, 3000):
            end = min(length, start + 3000)
            left, right = max(0, start - 1024), min(length, end + 1024)
            x = torch.from_numpy(spec[:, left:right]).unsqueeze(0)
            logits, _ = model(x)
            activation[start:end] = torch.sigmoid(logits[0, start-left:end-left]).numpy()
    fps = 44100 / 1024
    beat = activation[:, 0]
    down = activation[:, 1]
    # Decode the model activations with MusicLab's installed librosa runtime.
    # This keeps the neural model/checkpoint unchanged while avoiding the old
    # madmom Python 3.10/WSL environment owned by another application.
    _, beat_frames = librosa.beat.beat_track(onset_envelope=beat, sr=44100,
        hop_length=1024, bpm=None, start_bpm=120., tightness=100., trim=False,
        sparse=True)
    beat_frames = np.asarray(beat_frames, dtype=int)
    beats = librosa.frames_to_time(beat_frames, sr=44100, hop_length=1024)
    # Downbeats come only from local maxima in the model's downbeat output.
    # Do not synthesize a bar grid by selecting every third/fourth beat.
    down_frames, properties = signal.find_peaks(down, height=.2, prominence=.03,
        distance=max(1, int(fps * 60 / 215 * 2)))
    downbeats = librosa.frames_to_time(down_frames, sr=44100, hop_length=1024)
    strengths = properties.get('peak_heights', np.array([], dtype=float))
    strength = float(np.median(strengths)) if len(strengths) else 0.
    duration = len(audio)/44100
    beats = beats[(beats >= 0) & (beats < duration)]
    downbeats = downbeats[(downbeats >= 0) & (downbeats < duration)]
    # No probability claim: this is an explicit conservative activation gate.
    reliable = len(downbeats) >= 3 and strength >= .2
    meter = None
    if reliable:
        # Meter metadata is accepted only when independently detected downbeat
        # peaks consistently span three or four decoded beats.
        nearest = np.searchsorted(beats, downbeats)
        nearest = np.clip(nearest, 0, max(0, len(beats)-1))
        if len(beats):
            left = np.maximum(nearest-1, 0)
            nearest = np.where(np.abs(beats[left]-downbeats) < np.abs(beats[nearest]-downbeats), left, nearest)
        counts = np.diff(nearest)
        if len(counts) >= 2:
            candidate = int(round(float(np.median(counts))))
            if candidate in (3, 4) and np.mean(counts == candidate) >= .75:
                meter = dict(numerator=candidate, denominator=4)
    return dict(protocol=PROTOCOL, success=True, beats=beats.tolist(),
        downbeats=downbeats.tolist(), bpm=float(60/np.median(np.diff(beats))) if len(beats)>1 else None,
        beat_unit='1/4', meter=meter, meter_reliable=meter is not None,
        downbeats_reliable=bool(reliable), metadata=dict(name='Beat-Transformer',
        worker_version='2A.1', checkpoint=checkpoint.name,
        checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        architecture_sha256=hashlib.sha256((root/'code'/'DilatedTransformer.py').read_bytes()).hexdigest(),
        torch=torch.__version__, librosa=librosa.__version__, numpy=np.__version__,
        decoder='musiclab-model-peak-v1', device='cpu', preprocessing='hpss-five-view-v1 (not demixed stems)',
        fps=fps, decoded_meter_candidates=[3,4], downbeat_median_activation=strength,
        reliability_threshold=.2, decoded_downbeat_count=len(downbeats),
        chunk_core_frames=3000, chunk_context_frames=1024))


def watch_request(request):
    """End inference even when a remote launcher cannot forward termination."""
    import os
    import threading
    import time
    path = request.get('cancel_path')
    timeout = request.get('timeout_seconds', 1200)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 7200:
        raise ValueError('Invalid worker deadline.')
    started = time.monotonic()
    def watch():
        while True:
            if path and Path(path).exists():
                os._exit(130)
            if time.monotonic() - started >= timeout:
                os._exit(124)
            time.sleep(.2)
    threading.Thread(target=watch, daemon=True).start()


def main():
    try:
        request = json.load(sys.stdin)
        watch_request(request)
        with contextlib.redirect_stdout(sys.stderr):
            response = run(request)
        json.dump(response, sys.stdout, allow_nan=False)
    except Exception as exc:
        # Process boundary: preserve traceback, never hide application programming errors.
        traceback.print_exc(file=sys.stderr)
        json.dump(dict(protocol=PROTOCOL, success=False,
                       error=f'{type(exc).__name__}: {exc}'), sys.stdout)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
