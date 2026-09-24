"""Optional process boundary. No neural/legacy dependencies enter the editor."""
import json
import logging
import math
import os
from pathlib import Path, PureWindowsPath
import subprocess
import tempfile

import numpy as np
import soundfile as sf

PROTOCOL = 'comfymax.rhythm.1'
CONFIG = Path(__file__).resolve().parents[3] / '.cache' / 'rhythm-backend.json'


class BackendUnavailable(RuntimeError):
    """Expected external configuration, process, or response failure."""


def configuration():
    path = Path(os.environ.get('COMFYMAX_RHYTHM_CONFIG', CONFIG))
    if not path.exists():
        if 'COMFYMAX_RHYTHM_CONFIG' in os.environ:
            raise BackendUnavailable(f'Rhythm configuration not found: {path}')
        return {'backend': 'librosa'}
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise BackendUnavailable(f'Cannot read rhythm configuration: {exc}') from exc
    if not isinstance(value, dict) or value.get('backend') not in ('librosa', 'beat-transformer'):
        raise BackendUnavailable('Configuration requires backend librosa or beat-transformer.')
    return value


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
