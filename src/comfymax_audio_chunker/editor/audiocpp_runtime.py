"""Lifecycle for MusicLab's bundled, headless audio.cpp server."""
import json
import base64
import logging
from datetime import datetime
from pathlib import Path
import subprocess
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

from PySide6.QtCore import QObject,Signal


LOG=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[3]
YUE2_MODEL_ID='yue2-3b-q8'
YUE2_MODEL_ROOT=ROOT/'models'/'yue2'
YUE2_REGISTERED_PATH='../../models/yue2'
YUE2_MAIN='yue2-3b-q8_0.gguf'
YUE2_VAE='yue2-vae-f16.gguf'
YUE2_SIDECARS=('yue2-model-config.json','yue2-generation-config.json',
               'yue2-qwen.tiktoken','yue2-vae-config.json')


def validate_yue2_package(root=YUE2_MODEL_ROOT):
    root=Path(root)
    required=(root/YUE2_MAIN,root/YUE2_VAE,
              *(root/'sidecars'/name for name in YUE2_SIDECARS))
    return [path for path in required if not path.is_file()]


class AudioCppRuntime(QObject):
    status_changed=Signal(str)

    def __init__(self,parent=None,runtime_dir=None,health_url='http://127.0.0.1:18080/health'):
        super().__init__(parent)
        self.runtime_dir=Path(runtime_dir) if runtime_dir is not None else ROOT/'engines'/'audiocpp'
        self.executable=self.runtime_dir/'audiocpp_server.exe'
        self.config=self.runtime_dir/'server.json'
        self.health_url=health_url
        self.status='Not available'; self.process=None; self.owned=False
        self._thread=None; self._stopping=False; self._lock=threading.Lock()
        if parent is not None: parent.destroyed.connect(self.stop)

    def _set_status(self,value):
        self.status=value
        try: self.status_changed.emit(value)
        except RuntimeError: pass

    def health(self):
        try:
            with urlopen(self.health_url,timeout=.5) as response:
                value=json.loads(response.read().decode('utf-8'))
            return value if isinstance(value,dict) and value.get('status')=='ok' and value.get('backend') else None
        except (OSError,UnicodeError,json.JSONDecodeError,ValueError):
            return None

    def _json_request(self,path,body=None,timeout=120):
        data=None if body is None else json.dumps(body).encode('utf-8')
        request=Request(self.health_url.rsplit('/health',1)[0]+path,data=data,
                        headers={'Content-Type':'application/json'},
                        method='GET' if body is None else 'POST')
        try:
            with urlopen(request,timeout=timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            try: detail=exc.read().decode('utf-8',errors='replace').strip()
            except OSError: detail=''
            suffix=f': {detail}' if detail else ''
            raise RuntimeError(f'audio.cpp request failed: HTTP {exc.code}{suffix}') from exc
        except (URLError,OSError,UnicodeError,json.JSONDecodeError) as exc:
            raise RuntimeError(f'audio.cpp request failed: {exc}') from exc

    def models(self):
        return self._json_request('/v1/models?include_session_options=true').get('data',[])

    def models_root(self):
        return self._json_request('/v1/ui/models-root')

    def set_models_root(self,path=''):
        return self._json_request('/v1/ui/models-root',{'path':str(path)})

    def install_model_package(self,package_id,overwrite=False):
        return self._json_request('/v1/ui/models/install',{'id':package_id,'overwrite':bool(overwrite)})

    def model_install_status(self):
        return self._json_request('/v1/ui/models/install-status').get('data',[])

    def load_yue2(self):
        missing=validate_yue2_package()
        if missing:
            raise RuntimeError('Missing YuE2 component(s): '+', '.join(str(path) for path in missing))
        return self._json_request('/v1/models/load',{
            'id':YUE2_MODEL_ID,'path':YUE2_REGISTERED_PATH,'family':'yue2',
            'task':'gen','mode':'offline','load_options':{},
            'session_options':{'model_gguf':YUE2_MAIN,'vae_gguf':YUE2_VAE}},timeout=300)

    def unload_yue2(self):
        return self._json_request('/v1/models/unload',{'id':YUE2_MODEL_ID},timeout=120)

    def ensure_ready(self,timeout=15):
        if self.health(): return
        self.ensure_started()
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.health(): return
            if self.status in ('Error','Not available'):
                break
            time.sleep(.1)
        raise RuntimeError('audio.cpp runtime is not available.')

    def generate_yue2(self,lyrics,style,abc,seed,planning='off',output_dir=None,request_options=None):
        self.ensure_ready()
        models=self.models()
        model=next((entry for entry in models if entry.get('id')==YUE2_MODEL_ID),None)
        if model is None:
            raise RuntimeError(f'YuE2 model {YUE2_MODEL_ID} is not registered.')
        if not model.get('loaded'):
            self.load_yue2()
        options={'style':style,'cot':planning}
        if abc: options['abc']=abc
        if request_options: options.update(request_options)
        request={'lyrics':lyrics,'seed':int(seed),'options':options}
        debug_options={key:(f'<{len(value)} chars>' if key=='abc' else value) for key,value in options.items()}
        LOG.debug('YuE2 request seed=%s lyrics=<%d chars> options=%s',seed,len(lyrics),debug_options)
        response=self._json_request('/v1/tasks/run',{'model':YUE2_MODEL_ID,'request':request},timeout=3600)
        encoded=response.get('audio')
        if not isinstance(encoded,str) and isinstance(response.get('named_audio_outputs'),list):
            encoded=next((item.get('audio') for item in response['named_audio_outputs']
                          if isinstance(item,dict) and isinstance(item.get('audio'),str)),None)
        if not encoded:
            raise RuntimeError('YuE2 returned no audio output.')
        try: audio=base64.b64decode(encoded,validate=True)
        except (ValueError,TypeError) as exc: raise RuntimeError('YuE2 returned invalid audio data.') from exc
        if not audio: raise RuntimeError('YuE2 returned empty audio data.')
        destination=Path(output_dir) if output_dir is not None else ROOT/'outputs'/'music_generation'
        destination.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        path=destination/f'yue2-{stamp}.wav'
        path.write_bytes(audio)
        return {'path':path,'response':response,'request':request}

    def ensure_started(self):
        with self._lock:
            if self._stopping or self.status in ('Starting...','Ready') or (self._thread and self._thread.is_alive()): return
            self._set_status('Starting...')
            self._thread=threading.Thread(target=self._start_worker,name='MusicLab audio.cpp startup',daemon=True)
            self._thread.start()

    def _start_worker(self):
        if self._stopping: return
        if self.health():
            if self._stopping: return
            self.owned=False; self._set_status('Ready'); return
        if not self.executable.is_file() or not self.config.is_file():
            self._set_status('Not available'); return
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
        try:
            process=subprocess.Popen([str(self.executable),'--no-ui','--ui-management','--config',self.config.name],
                cwd=self.runtime_dir,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=flags)
            with self._lock:
                self.process=process; self.owned=True
            for _ in range(100):
                if self._stopping:
                    self._stop_owned(); return
                if process.poll() is not None:
                    self._set_status('Error'); return
                if self.health():
                    self._set_status('Ready'); return
                time.sleep(.1)
            self._stop_owned(); self._set_status('Error')
        except (OSError,ValueError):
            self._set_status('Error')

    def _stop_owned(self):
        process=self.process
        if not self.owned or process is None: return
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=2)
        self.process=None; self.owned=False

    def stop(self):
        self._stopping=True
        thread=self._thread
        if thread and thread.is_alive(): thread.join(timeout=2)
        self._stop_owned()
