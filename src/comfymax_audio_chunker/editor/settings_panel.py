"""Application-wide model locations; no runtime or model loading happens here."""
import json
import math
import os
from pathlib import Path
import shutil
import time

from PySide6.QtCore import QStandardPaths,QThread,Signal
from PySide6.QtWidgets import (QFileDialog,QFormLayout,QGroupBox,QHBoxLayout,QLabel,
                               QComboBox,QLineEdit,QMenu,QMessageBox,QPushButton,QVBoxLayout,QWidget)

from .transcript import MODEL as WHISPER_MODEL
from .audiocpp_runtime import YUE2_MAIN,YUE2_MODEL_ROOT,YUE2_VAE


SETTINGS_KEYS=('sheet_sage_path','yue2_main_path','yue2_vae_path','whisper_model')
GENERATION_DEFAULTS={'yue2_semantic_guidance':1.5,'yue2_nar_steps':16,
    'yue2_ar_lora':'','yue2_ar_lora_scale':1.0,'yue2_nar_lora':'','yue2_nar_lora_scale':1.0}
CONFIG_KEYS=SETTINGS_KEYS+tuple(GENERATION_DEFAULTS)
YUE2_PACKAGES={
    'main':(
        ('yue2_main_q4_0','YuE2 3B Main Q4_0','yue2-3b-q4_0.gguf'),
        ('yue2_main_q8_0','YuE2 3B Main Q8_0','yue2-3b-q8_0.gguf'),
        ('yue2_main_bf16','YuE2 3B Main BF16','yue2-3b-bf16.gguf')),
    'vae':(
        ('yue2_vae_f16','YuE2 VAE F16','yue2-vae-f16.gguf'),
        ('yue2_vae_f32','YuE2 VAE F32','yue2-vae-f32.gguf')),
}
YUE2_PACKAGE_DIRECTORY='Yue2-3B-GGUF'


class ModelDownloadTask(QThread):
    ready=Signal(str,str); failed=Signal(str); progress=Signal(str)
    def __init__(self,runtime,package_id,filename,parent=None):
        super().__init__(parent); self.runtime=runtime; self.package_id=package_id; self.filename=filename
    def run(self):
        staging=YUE2_MODEL_ROOT/'.downloads'; package=staging/YUE2_PACKAGE_DIRECTORY
        previous=''
        try:
            self.runtime.ensure_ready()
            root=self.runtime.models_root(); previous=str(root.get('models_root',''))
            staging.mkdir(parents=True,exist_ok=True)
            self.runtime.set_models_root(staging)
            self.runtime.install_model_package(self.package_id)
            deadline=time.monotonic()+21600
            while time.monotonic()<deadline:
                statuses=self.runtime.model_install_status()
                status=next((entry for entry in statuses if entry.get('id')==self.package_id),None)
                if status:
                    state=status.get('state',''); message=status.get('message') or state
                    percent=status.get('progress_percent',-1)
                    self.progress.emit(f'{message}'+(f' ({percent:.0f}%)' if isinstance(percent,(int,float)) and percent>=0 else ''))
                    if state=='complete': break
                    if state in ('failed','cancelled','cleaned'): raise RuntimeError(message)
                time.sleep(.5)
            else: raise RuntimeError('Model download timed out.')
            source=package/self.filename
            if not source.is_file(): raise RuntimeError(f'Download completed but {self.filename} is missing.')
            YUE2_MODEL_ROOT.mkdir(parents=True,exist_ok=True)
            for relative in ('sidecars/yue2-model-config.json','sidecars/yue2-generation-config.json',
                             'sidecars/yue2-qwen.tiktoken','sidecars/yue2-vae-config.json',self.filename,
                             f'.audiocpp-package-{self.package_id}.json'):
                item=package/relative
                if item.is_file():
                    target=YUE2_MODEL_ROOT/relative; target.parent.mkdir(parents=True,exist_ok=True)
                    if target.exists(): target.unlink()
                    shutil.move(str(item),str(target))
            for folder in (package/'sidecars',package,staging):
                try: folder.rmdir()
                except OSError: pass
            self.ready.emit(self.package_id,str(YUE2_MODEL_ROOT/self.filename))
        except Exception as exc: self.failed.emit(str(exc))
        finally:
            try: self.runtime.set_models_root(previous)
            except Exception: pass


def settings_file():
    root=Path(QStandardPaths.writableLocation(QStandardPaths.GenericConfigLocation))
    return root/'ComfyMax-MusicLab'/'settings.json'


def _detected_sheet_sage_path():
    try:
        from ..music.sheetsage import configuration
        return configuration()['model']
    except (OSError,ValueError,KeyError):
        return ''


def load_settings(path=None):
    path=Path(path) if path is not None else settings_file()
    values=dict(sheet_sage_path='',yue2_main_path='',yue2_vae_path='',whisper_model=WHISPER_MODEL,
                **GENERATION_DEFAULTS)
    if path.is_file():
        try:
            stored=json.loads(path.read_text(encoding='utf-8'))
            if isinstance(stored,dict):
                for key in SETTINGS_KEYS:
                    if isinstance(stored.get(key),str): values[key]=stored[key]
                for kind in ('ar','nar'):
                    key='yue2_'+kind+'_lora'
                    if isinstance(stored.get(key),str): values[key]=stored[key]
                    scale=stored.get(key+'_scale')
                    if type(scale) in (int,float) and math.isfinite(scale):
                        values[key+'_scale']=max(0.0,min(2.0,float(scale)))
                guidance=stored.get('yue2_semantic_guidance')
                steps=stored.get('yue2_nar_steps')
                if isinstance(guidance,(int,float)) and not isinstance(guidance,bool):
                    values['yue2_semantic_guidance']=float(guidance)
                if isinstance(steps,int) and not isinstance(steps,bool): values['yue2_nar_steps']=steps
        except (OSError,UnicodeError,json.JSONDecodeError):
            pass
    if not values['sheet_sage_path']: values['sheet_sage_path']=_detected_sheet_sage_path()
    if not values['yue2_main_path'] and (YUE2_MODEL_ROOT/YUE2_MAIN).is_file():
        values['yue2_main_path']=str(YUE2_MODEL_ROOT/YUE2_MAIN)
    if not values['yue2_vae_path'] and (YUE2_MODEL_ROOT/YUE2_VAE).is_file():
        values['yue2_vae_path']=str(YUE2_MODEL_ROOT/YUE2_VAE)
    return values


def save_settings(values,path=None):
    path=Path(path) if path is not None else settings_file()
    payload={key:str(values.get(key,'')) for key in SETTINGS_KEYS}
    payload.update(yue2_semantic_guidance=float(values.get('yue2_semantic_guidance',1.5)),
                   yue2_nar_steps=int(values.get('yue2_nar_steps',16)))
    for kind in ('ar','nar'):
        key='yue2_'+kind+'_lora'
        payload[key]=str(values.get(key,'') or '')
        payload[key+'_scale']=float(values.get(key+'_scale',1.0))
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8')
    os.replace(temporary,path)


class SettingsPanel:
    def build_settings(self):
        self.settings_pane=QWidget()
        layout=QVBoxLayout(self.settings_pane)
        self.model_settings=load_settings()
        self.settings_fields={}; self.settings_status={}; self.model_download_job=None
        self._settings_group(layout,'Music Analysis','SheetSage',[
            ('sheet_sage_path','Model/location path')])
        self._settings_group(layout,'Music Generation','YuE2',[
            ('yue2_main_path','Main model'),('yue2_vae_path','VAE')])
        self._yue2_downloads(layout)
        self._settings_group(layout,'Transcription','Whisper',[
            ('whisper_model','Model/config')])
        layout.addStretch()

    def _yue2_downloads(self,parent):
        group=QGroupBox('YuE2 model downloads'); box=QFormLayout(group)
        self.yue2_package_choices={}; self.yue2_download_buttons={}; self.yue2_download_status=QLabel('')
        for kind,label in (('main','Main model'),('vae','VAE')):
            row=QWidget(); line=QHBoxLayout(row); line.setContentsMargins(0,0,0,0)
            choice=QComboBox()
            for package_id,name,filename in YUE2_PACKAGES[kind]: choice.addItem(name,(package_id,filename))
            button=QPushButton('Download'); button.clicked.connect(lambda checked=False,k=kind:self.download_yue2(k))
            line.addWidget(choice,1); line.addWidget(button); box.addRow(label+':',row)
            self.yue2_package_choices[kind]=choice; self.yue2_download_buttons[kind]=button
        self.yue2_download_status.setWordWrap(True); box.addRow('',self.yue2_download_status); parent.addWidget(group)

    def download_yue2(self,kind):
        package_id,filename=self.yue2_package_choices[kind].currentData(); target=YUE2_MODEL_ROOT/filename
        if target.is_file():
            QMessageBox.information(self.settings_pane,'YuE2 model','This model is already installed.')
            self.yue2_download_status.setText(f'{filename} is already installed.'); return
        runtime=getattr(self,'audiocpp_runtime',None)
        if runtime is None:
            self.yue2_download_status.setText('audio.cpp runtime is not available.'); return
        if self.model_download_job is not None: return
        for button in self.yue2_download_buttons.values(): button.setEnabled(False)
        self.yue2_download_status.setText(f'Downloading {filename}…')
        job=ModelDownloadTask(runtime,package_id,filename,self.settings_pane); self.model_download_job=job
        job.progress.connect(self.yue2_download_status.setText)
        job.ready.connect(lambda package,path,k=kind:self._download_ready(k,package,path))
        job.failed.connect(lambda message:self.yue2_download_status.setText('Download failed: '+message))
        job.finished.connect(lambda:self._download_finished(job)); job.start()

    def _download_ready(self,kind,package_id,path):
        key='yue2_main_path' if kind=='main' else 'yue2_vae_path'
        self.set_model_path(key,path); self.yue2_download_status.setText(f'{package_id} installed.')

    def _download_finished(self,job):
        if self.model_download_job is job: self.model_download_job=None
        for button in self.yue2_download_buttons.values(): button.setEnabled(True)
        job.deleteLater()

    def _settings_group(self,parent,title,engine,rows):
        group=QGroupBox(title); box=QVBoxLayout(group); box.addWidget(QLabel(engine))
        form=QFormLayout(); box.addLayout(form)
        for key,label in rows:
            row=QWidget(); line=QHBoxLayout(row); line.setContentsMargins(0,0,0,0)
            editor=QLineEdit(self.model_settings[key]); editor.setAccessibleName(label)
            editor.editingFinished.connect(lambda k=key,e=editor:self.set_model_path(k,e.text()))
            browse=QPushButton('Browse'); menu=QMenu(browse)
            menu.addAction('Select model file…',lambda checked=False,k=key:self._choose_file(k))
            menu.addAction('Select model folder…',lambda checked=False,k=key:self._choose_folder(k))
            browse.setMenu(menu)
            status=QLabel(); line.addWidget(editor,1); line.addWidget(browse); line.addWidget(status)
            form.addRow(label+':',row)
            self.settings_fields[key]=editor; self.settings_status[key]=status; self._update_path_status(key)
        parent.addWidget(group)

    def _choose_file(self,key):
        value,_=QFileDialog.getOpenFileName(self.settings_pane,'Select model file',self.model_settings[key])
        if value: self.set_model_path(key,value)

    def _choose_folder(self,key):
        value=QFileDialog.getExistingDirectory(self.settings_pane,'Select model folder',self.model_settings[key])
        if value: self.set_model_path(key,value)

    def set_model_path(self,key,value):
        value=value.strip(); self.model_settings[key]=value
        self.settings_fields[key].setText(value); self._update_path_status(key)
        save_settings(self.model_settings)

    def _update_path_status(self,key):
        value=self.model_settings[key]
        self.settings_status[key].setText('Available' if value and Path(value).exists() else 'Not configured')
