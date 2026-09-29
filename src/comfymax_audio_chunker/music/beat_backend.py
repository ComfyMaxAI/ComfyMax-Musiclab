"""Optional process boundary. No neural/legacy dependencies enter the editor."""
import json
import hashlib
import logging
import math
import os
from pathlib import Path, PureWindowsPath
import subprocess
import tempfile
import sys

import numpy as np
import soundfile as sf

PROTOCOL = 'comfymax.rhythm.1'
CONFIG = Path(__file__).resolve().parents[3] / '.cache' / 'rhythm-backend.json'
ROOT = Path(__file__).resolve().parents[3]
MANAGED_MODEL = ROOT / 'engines' / 'beat-transformer' / 'model'
MANAGED_CHECKPOINT = MANAGED_MODEL / 'checkpoint' / 'fold_4_trf_param.pt'
WORKER = Path(__file__).with_name('beat_worker.py')
MANAGED_FILES = {
    MANAGED_CHECKPOINT: 'b76033014dd07d12307743b92337b7dffadf1f20a6ccd5a1edb03276e99a4512',
    MANAGED_MODEL / 'code' / 'DilatedTransformer.py': '54f8e13f93ff02f5070ff11095929264157275ab9975f06b233a373539873ec8',
    MANAGED_MODEL / 'code' / 'DilatedTransformerLayer.py': '87bdb9e11da791378c2aaa8ade815e6ea9eec2155dd700402b6561dbb8e2330b',
}


class BackendUnavailable(RuntimeError):
    """Expected external configuration, process, or response failure."""


def validate_managed_runtime():
    """Fail clearly if a bundled model file is missing or was corrupted."""
    for path, expected in MANAGED_FILES.items():
        if not path.is_file():
            raise BackendUnavailable(f'MusicLab Beat-Transformer file is missing: {path}')
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise BackendUnavailable(f'MusicLab Beat-Transformer checksum mismatch: {path}')


def configuration():
    path = Path(os.environ.get('COMFYMAX_RHYTHM_CONFIG', CONFIG))
    if not path.exists():
        if 'COMFYMAX_RHYTHM_CONFIG' in os.environ:
            raise BackendUnavailable(f'Rhythm configuration not found: {path}')
        value = {'backend': 'beat-transformer'}
    else:
        try:
            value = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            raise BackendUnavailable(f'Cannot read rhythm configuration: {exc}') from exc
    if not isinstance(value, dict) or value.get('backend') not in ('librosa', 'beat-transformer'):
        raise BackendUnavailable('Configuration requires backend librosa or beat-transformer.')
    if value['backend'] == 'librosa':
        return {'backend': 'librosa'}
    # Beat-Transformer is a MusicLab-managed component.  Deliberately discard
    # legacy machine-specific command/model fields (including WSL paths).
    validate_managed_runtime()
    return dict(backend='beat-transformer', command=[sys.executable, str(WORKER)],
                path_style='native', model_root=str(MANAGED_MODEL),
                checkpoint=str(MANAGED_CHECKPOINT), timeout_seconds=1200)


def worker_path(path, style):
    if style == 'native':
        return str(path)
    if style != 'wsl':
        raise BackendUnavailable('path_style must be native or wsl.')
    p = PureWindowsPath(path)
    if len(p.drive) != 2 or p.drive[1] != ':':
        raise BackendUnavailable('WSL transport requires a local Windows drive path.')
    return '/mnt/' + p.drive[0].lower() + '/' + '/'.join(p.parts[1:])


def infer(audio, rate, config, check_cancel=lambda: None):
    command = config.get('command')
    if not isinstance(command, list) or not command or not all(isinstance(s, str) and s for s in command):
        raise BackendUnavailable('Beat-Transformer command must be a nonempty argv list.')
    timeout = config.get('timeout_seconds', 1200)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 7200:
        raise BackendUnavailable('Worker timeout must be between 1 and 7200 seconds.')
    style = config.get('path_style', 'native')
    # Project-local exchange files are accessible to a configured WSL worker too.
    exchange = CONFIG.parent / 'rhythm-worker'
    try:
        exchange.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=exchange) as directory:
            audio_path = Path(directory) / 'selected-channel.wav'
            sf.write(audio_path, audio, rate, subtype='FLOAT')
            cancel_path = Path(directory) / 'cancel'
            request = dict(protocol=PROTOCOL, audio_path=worker_path(audio_path, style),
                           cancel_path=worker_path(cancel_path, style), timeout_seconds=timeout,
                           model_root=config.get('model_root'), checkpoint=config.get('checkpoint'),
                           device='cpu')
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            import time
            started = time.monotonic()
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                  errors='replace', creationflags=flags) as process:
                try:
                    payload = json.dumps(request)
                    while True:
                        check_cancel()
                        remaining = timeout - (time.monotonic() - started)
                        if remaining <= 0:
                            raise BackendUnavailable(f'Beat-Transformer timed out after {timeout}s.')
                        try:
                            stdout, stderr = process.communicate(payload, timeout=min(.25, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            payload = None
                    if process.returncode:
                        raise BackendUnavailable(f'Worker exited {process.returncode}: {stderr[-4000:]}')
                finally:
                    if process.poll() is None:
                        # The sentinel reaches the worker even if a WSL launcher
                        # does not propagate Windows process termination.
                        cancel_path.touch()
                        try:
                            process.communicate(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate()
            try:
                response = json.loads(stdout)
            except ValueError as exc:
                raise BackendUnavailable(f'Worker returned invalid JSON: {stdout[-500:]} {stderr[-500:]}') from exc
            if not isinstance(response, dict) or response.get('protocol') != PROTOCOL:
                raise BackendUnavailable('Worker response protocol mismatch.')
            if response.get('success') is not True:
                raise BackendUnavailable(str(response.get('error', 'Worker inference failed.')))
            return response
    except OSError as exc:
        raise BackendUnavailable(f'Cannot run Beat-Transformer worker: {exc}') from exc


def normalize(response, duration):
    """Validate external evidence; never repair an invalid grid by inventing beats."""
    try:
        json.dumps(response, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BackendUnavailable('Worker response is not finite JSON.') from exc
    def times(name):
        value = response.get(name, [])
        if not isinstance(value, list) or any(type(t) not in (int, float) or
                not math.isfinite(t) or not 0 <= t < duration for t in value):
            raise BackendUnavailable(f'Invalid {name} timestamps.')
        if any(b <= a for a, b in zip(value, value[1:])):
            raise BackendUnavailable(f'{name} timestamps are not strictly increasing.')
        return value
    beats, downbeats = times('beats'), times('downbeats')
    bpm = response.get('bpm')
    if len(beats) < 2 or (bpm is not None and (type(bpm) not in (int, float) or
                                            not math.isfinite(bpm) or bpm <= 0)):
        raise BackendUnavailable('Insufficient beats or invalid BPM.')
    if response.get('beat_unit') != '1/4':
        raise BackendUnavailable('Worker must return quarter-note beat units.')
    metadata = response.get('metadata', {})
    if not isinstance(metadata, dict):
        raise BackendUnavailable('Invalid model metadata.')
    try:
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BackendUnavailable('Model metadata is not finite JSON.') from exc
    meter = response.get('meter')
    # This worker decodes simple quarter-note meters. Do not relabel compound pulses.
    valid_meter = (isinstance(meter, dict) and type(meter.get('numerator')) is int and
                   2 <= meter['numerator'] <= 12 and type(meter.get('denominator')) is int and
                   meter['denominator'] == 4 and response.get('meter_reliable') is True)
    return dict(beats=beats, downbeats=downbeats, bpm=bpm,
                meter=dict(numerator=meter['numerator'], denominator=4) if valid_meter else None,
                downbeats_reliable=response.get('downbeats_reliable') is True,
                metadata=metadata, reported_meter=meter)


def warn(reason):
    logging.getLogger(__name__).warning('Beat-Transformer fallback: %s', reason)
