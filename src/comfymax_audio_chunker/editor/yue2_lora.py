"""Header-only discovery for audio.cpp 0.8.1's unfused YuE2 adapters.

Tensor names follow src/models/yue2/lora.cpp at f2b4937. Shape compatibility
with the loaded base weights is deliberately left to audio.cpp.
"""
import json
import math
from pathlib import Path
import re
import struct

LORA_ROOT=Path(__file__).resolve().parents[3]/'models'/'yue2'/'loras'
PROJECTION=re.compile(r'layers\.(\d+)\.(nar_)?(?:self_attn\.[qkvo]_proj|mlp\.(?:gate|up|down)_proj)\.lora_([AB])')
REPLACEMENTS={'vae2llm.weight','vae2llm.bias','llm2vae.weight','llm2vae.bias'}


def adapter_kind(path):
    """Reject malformed/fused/foreign layouts, without reading tensor payloads."""
    path=Path(path)
    if path.suffix!='.safetensors':
        raise ValueError('YuE2 requires an unfused .safetensors adapter.')
    try:
        with path.open('rb') as stream:
            size=path.stat().st_size
            raw=stream.read(8)
            if len(raw)!=8: raise ValueError('Missing SafeTensors header.')
            length=struct.unpack('<Q',raw)[0]
            if not 2<=length<=min(16*1024*1024,size-8):
                raise ValueError('Invalid SafeTensors header length.')
            header=json.loads(stream.read(length))
        if not isinstance(header,dict): raise ValueError('Invalid SafeTensors header.')
        pairs={}; kinds=set(); spans=[]
        for name,tensor in header.items():
            if name=='__metadata__': continue
            match=PROJECTION.fullmatch(name)
            if not match and name not in REPLACEMENTS:
                raise ValueError(f'Unsupported YuE2 tensor: {name} (use unfused adapters, not ComfyUI weights).')
            if not isinstance(tensor,dict): raise ValueError(f'Invalid tensor: {name}')
            shape=tensor.get('shape'); offsets=tensor.get('data_offsets'); dtype=tensor.get('dtype')
            if (not isinstance(shape,list) or not shape or
                    any(type(n)!=int or n<=0 for n in shape) or dtype not in ('F32','BF16','F16') or
                    not isinstance(offsets,list) or len(offsets)!=2 or
                    any(type(n)!=int for n in offsets)):
                raise ValueError(f'Invalid tensor metadata: {name}')
            start,end=offsets
            width=4 if dtype=='F32' else 2
            if not 0<=start<end<=size-8-length or end-start!=math.prod(shape)*width:
                raise ValueError(f'Invalid tensor data offsets: {name}')
            spans.append((start,end))
            if match:
                if len(shape)!=2: raise ValueError(f'LoRA tensor must be rank-2: {name}')
                kind='nar' if match[2] else 'ar'; kinds.add(kind)
                pairs.setdefault(name.rsplit('.lora_',1)[0],{})[match[3]]=shape
            else: kinds.add('nar')
        if not pairs or len(kinds)!=1:
            raise ValueError('Adapter must contain matching AR or NAR A/B pairs.')
        for name,pair in pairs.items():
            if set(pair)!= {'A','B'} or pair['A'][0]!=pair['B'][1]:
                raise ValueError(f'YuE2 LoRA is missing a valid A/B pair: {name}')
        cursor=0
        for start,end in sorted(spans):
            if start!=cursor: raise ValueError('Invalid overlapping or incomplete tensor data.')
            cursor=end
        if cursor!=size-8-length: raise ValueError('Unexpected SafeTensors trailing data.')
        return kinds.pop()
    except (OSError,UnicodeError,json.JSONDecodeError,ValueError) as exc:
        raise ValueError(f'{path.name}: {exc}') from exc


def scan_loras(root=LORA_ROOT):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    adapters={'ar':[],'nar':[]}; errors=[]
    for path in sorted(root.iterdir(),key=lambda p:p.name.casefold()):
        if path.is_file() and path.suffix=='.safetensors':
            try: adapters[adapter_kind(path)].append(path)
            except ValueError as exc: errors.append(str(exc))
    return adapters,errors


def lora_metadata(config=None):
    config=config or {}
    return {key:config.get(key,default) for key,default in
            (('ar_lora',None),('ar_lora_scale',1.0),('nar_lora',None),('nar_lora_scale',1.0))}


def session_lora_options(config=None):
    options={}
    for kind in ('ar','nar'):
        filename=(config or {}).get(kind+'_lora')
        if not filename: continue
        path=Path(filename).resolve()
        if not path.is_file(): raise ValueError(f'{kind.upper()} LoRA file no longer exists: {path}')
        actual=adapter_kind(path)
        if actual!=kind: raise ValueError(f'{path.name} is a {actual.upper()} adapter, not {kind.upper()}.')
        scale=float((config or {}).get(kind+'_lora_scale',1.0))
        if not math.isfinite(scale) or not 0<=scale<=2:
            raise ValueError(f'{kind.upper()} LoRA strength must be between 0.0 and 2.0.')
        options['yue2.'+kind+'_lora']=str(path)
        options['yue2.'+kind+'_lora_scale']=scale
    return options


def active_lora_options(model):
    """The server serializes option values as strings; accept official short aliases."""
    source=model.get('session_options',{}); result={}
    for kind in ('ar','nar'):
        name='yue2.'+kind+'_lora'; value=source.get(name,source.get(kind+'_lora'))
        if value:
            path=Path(value)
            if not path.is_absolute():
                model_path=Path(model.get('path',''))
                if not model_path.is_absolute():
                    model_path=LORA_ROOT.parents[2]/'engines'/'audiocpp'/model_path
                path=model_path/path
            result[name]=str(path.resolve())
            result[name+'_scale']=float(source.get(name+'_scale',source.get(kind+'_lora_scale',1.0)))
    return result
