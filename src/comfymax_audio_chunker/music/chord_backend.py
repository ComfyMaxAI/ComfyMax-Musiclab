"""Optional process boundary. No neural/legacy dependencies enter the editor."""
import json
import logging
import math
import os
from pathlib import Path, PureWindowsPath
import subprocess
import tempfile
import time

import numpy as np
import soundfile as sf

PROTOCOL = 'comfymax.chords.1'
CONFIG = Path(__file__).resolve().parents[3] / '.cache' / 'chord-backend.json'


class BackendUnavailable(RuntimeError):
    """Expected external configuration, process, or response failure."""


def configuration():
    path = Path(os.environ.get('COMFYMAX_CHORD_CONFIG', CONFIG))
    if not path.exists():
        if 'COMFYMAX_CHORD_CONFIG' in os.environ:
            raise BackendUnavailable(f'Chord configuration not found: {path}')
        return {'backend': 'template'}
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise BackendUnavailable(f'Cannot read chord configuration: {exc}') from exc
    if not isinstance(value, dict) or value.get('backend') not in ('template', 'chord-cnn-lstm'):
        raise BackendUnavailable('Configuration requires backend template or chord-cnn-lstm.')
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
        raise BackendUnavailable('Chord-CNN-LSTM command must be a nonempty argv list.')
    timeout = config.get('timeout_seconds', 1200)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 7200:
        raise BackendUnavailable('Worker timeout must be between 1 and 7200 seconds.')
    style = config.get('path_style', 'native')
    # Project-local exchange files are accessible to a configured WSL worker too.
    exchange = CONFIG.parent / 'chord-worker'
    try:
        exchange.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=exchange) as directory:
            audio_path = Path(directory) / 'selected-channel.wav'
            sf.write(audio_path, audio, rate, subtype='FLOAT')
            cancel_path = Path(directory) / 'cancel'
            request = dict(protocol=PROTOCOL, audio_path=worker_path(audio_path, style),
                           cancel_path=worker_path(cancel_path, style), timeout_seconds=timeout,
                           model_root=config.get('model_root'), checkpoint=config.get('checkpoint'),
                           device='cpu', launch_unix=time.time())
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
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
                            raise BackendUnavailable(f'Chord-CNN-LSTM timed out after {timeout}s.')
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
            if not isinstance(response.get('timings', {}), dict):
                raise BackendUnavailable('Invalid worker timing metadata.')
            response.setdefault('timings', {})['worker_roundtrip_seconds'] = time.monotonic()-started
            return response
    except OSError as exc:
        raise BackendUnavailable(f'Cannot run Chord-CNN-LSTM worker: {exc}') from exc

