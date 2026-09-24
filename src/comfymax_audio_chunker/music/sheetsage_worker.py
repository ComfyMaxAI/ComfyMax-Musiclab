"""Stdlib-only JSON worker around the installed audio.cpp SheetSage2 runtime."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

PROTOCOL = 'comfymax.sheetsage.1'
VERSION = '1'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def run(request):
    if request.get('protocol') != PROTOCOL:
        raise ValueError('SheetSage protocol mismatch')
    started = time.monotonic()
    startup = max(0, time.time()-request['launch_unix'])
    config = request['config']
    exe, model, audio = map(Path, (config['executable'], config['model'], request['audio_path']))
    if config.get('managed'):
        from comfymax_audio_chunker.music.sheetsage_runtime import runtime_files
        runtime_files(exe.parent, full=True)
    output = Path(request['output_dir']); output.mkdir(parents=True, exist_ok=True)
    native = output/'native'; native.mkdir()
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    version = subprocess.run([str(exe), '--version'], capture_output=True, text=True,
                             timeout=20, creationflags=flags, check=True).stdout.strip()
    provenance = dict(backend='sheetsage2', model_name=model.name, model_version=None,
                      model_path=str(model.resolve()), runtime_location='managed' if config.get('managed') else 'explicit override',
                      model_sha256=sha256(model), executable=str(exe), executable_sha256=sha256(exe),
                      runtime_version=version, worker_version=VERSION, environment=platform.platform(),
                      interpreter=sys.executable, requested_backend=config.get('backend', 'cuda'),
                      source_sha256=sha256(audio), source_asset='mix', config=config)
    for name in ('model','executable'):
        expected=config.get('expected_'+name+'_sha256')
        if expected and provenance[name+'_sha256']!=expected:
            raise ValueError('SheetSage '+name+' checksum mismatch; run Install_SheetSage.bat')
    command = [str(exe), '--task', 'midi', '--family', 'sheetsage2', '--model', str(model),
               '--backend', config.get('backend', 'cuda'), '--audio', str(audio),
               '--out-dir', str(native), '--log-file', str(output/'runtime.log'), '--metrics',
               '--threads', str(config.get('threads', 4)), '--max-tokens', str(config.get('max_tokens', 5120))]
    spec = exe.parent/'model_specs'/'sheetsage2.json'
    if spec.is_file():
        provenance['model_spec_sha256'] = sha256(spec)
        (output/'model-spec.json').write_bytes(spec.read_bytes())
    (output/'configuration.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    launched = time.monotonic()
    with (output/'stdout.log').open('wb') as stdout, (output/'stderr.log').open('wb') as stderr:
        with subprocess.Popen(command, cwd=exe.parent, stdout=stdout, stderr=stderr, creationflags=flags) as process:
            try:
                while process.poll() is None:
                    if Path(request['cancel_path']).exists():
                        raise RuntimeError('SheetSage cancelled')
                    if time.monotonic()-started > config.get('timeout_seconds', 1200):
                        raise TimeoutError('SheetSage timed out')
                    time.sleep(.1)
                if process.returncode:
                    raise RuntimeError(f'audio.cpp exited {process.returncode}; see stderr.log')
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
    elapsed = time.monotonic()-launched
    if not (native/'score.abc').is_file() or not (native/'score.abc').stat().st_size:
        raise ValueError('SheetSage produced no ABC artifact')
    log = (output/'runtime.log').read_text(encoding='utf-8', errors='replace') if (output/'runtime.log').exists() else ''
    timings = {name.removesuffix('_ms')+'_seconds': float(ms)/1000
               for name, ms in re.findall(r'\[TIMING[^\]]*\]\s+(\S+)\s+([\d.]+)', log)}
    timings.update(cli_wall_seconds=elapsed, worker_total_seconds=time.monotonic()-started,
                   setup_and_identity_seconds=launched-started,
                   worker_process_startup_seconds=startup,
                   model_loading_seconds=None)
    provenance['device_log'] = (output/'stderr.log').read_text(encoding='utf-8', errors='replace')[:4000]
    provenance['timings'] = timings
    return dict(protocol=PROTOCOL, success=True, provenance=provenance,
                artifacts=[p.relative_to(output).as_posix() for p in sorted(output.rglob('*')) if p.is_file()])


if __name__ == '__main__':
    try:
        response = run(json.load(sys.stdin))
    except Exception as exc:
        response = dict(protocol=PROTOCOL, success=False, error=f'{type(exc).__name__}: {exc}')
    print(json.dumps(response, allow_nan=False))
