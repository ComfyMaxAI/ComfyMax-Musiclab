"""Pinned optional audio.cpp distribution; no model imports or GUI dependencies."""
from pathlib import Path
import json
from .sheetsage_worker import sha256

APP_ROOT = Path(__file__).resolve().parents[3]
VERSION = '0.8.1'
EXE_SHA = 'dc92dbd6ea4763cd298f03cc487c8fc5c0ba2c198abfa3b5e075af8700ec428c'
MODEL_SHA = '52bb5846c452037d39931aa8050885b6c751b9c7afcc8ef6d6d3067d241731a4'
MODEL_SIZE = 2708224512
MODEL_REVISION = '6dd648b0acf1ba9e662d8509a3eabcb201911c2c'
MODEL_URL = f'https://huggingface.co/audio-cpp/SheetSage2-GGUF/resolve/{MODEL_REVISION}/sheetsage2-orig.gguf'
ARCHIVES = (
 ('audio-v0.8.1-bin-windows-x64-cuda13.3.zip','aad5dffe4398b325018cf38e58e6555998ef97948a72ac7582de46e91155ca24'),
 ('audio-v0.8.1-cudart-windows-x64-cuda13.3.zip','5c0a8b1022500b2df2062b7215584408ad07d43358f38a1a3ace0ac6daa441a9'),
)
RELEASE_URL = 'https://github.com/0xShug0/audio.cpp/releases/download/v0.8.1/'


def locations(root=APP_ROOT):
    root=Path(root).resolve()
    return root/'engines'/'sheetsage'/'runtime',root/'models'/'sheetsage2'/'sheetsage2-orig.gguf'


def runtime_files(runtime, full=False):
    """Sizes for cheap UI discovery; full hashes before use and during setup."""
    try:
        return _runtime_files(runtime,full)
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError('Malformed SheetSage installation manifest; run Install_SheetSage.bat') from exc


def _runtime_files(runtime, full):
    runtime=Path(runtime)
    manifest=json.loads((runtime/'installation.json').read_text(encoding='utf8'))
    if manifest.get('version')!=VERSION or manifest.get('archives')!=[list(a) for a in ARCHIVES]:
        raise ValueError('Unknown SheetSage runtime installation; run Install_SheetSage.bat')
    files=manifest.get('files',{})
    required={'audiocpp_cli.exe','ggml.dll','ggml-base.dll','ggml-cuda.dll','cudart64_13.dll','cublas64_13.dll','cublasLt64_13.dll','cufft64_12.dll','model_specs/sheetsage2.json','LICENSE'}
    if not required.issubset(files): raise ValueError('Incomplete SheetSage runtime manifest')
    if files['audiocpp_cli.exe'].get('sha256')!=EXE_SHA: raise ValueError('Unexpected SheetSage executable identity')
    for name,identity in files.items():
        path=(runtime/name).resolve()
        if not path.is_relative_to(runtime.resolve()) or not path.is_file() or path.stat().st_size!=identity['bytes']:
            raise ValueError('Incomplete SheetSage runtime: '+name)
        if full and sha256(path)!=identity['sha256']: raise ValueError('SheetSage checksum mismatch: '+name)
    return manifest


def managed_config(root=APP_ROOT):
    runtime,model=locations(root)
    runtime_files(runtime)
    if not model.is_file() or model.stat().st_size!=MODEL_SIZE:
        raise ValueError('SheetSage model missing/incomplete')
    return dict(executable=str(runtime/'audiocpp_cli.exe'),model=str(model),managed=True,
                expected_executable_sha256=EXE_SHA,expected_model_sha256=MODEL_SHA)
