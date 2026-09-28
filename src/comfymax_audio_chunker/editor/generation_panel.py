"""YuE2 generation UI and asynchronous audio.cpp inference."""
import logging
from pathlib import Path
import secrets
import shutil
import subprocess
import wave

from PySide6.QtCore import QDir,Qt,QTemporaryDir,QThread,QTimer,Signal,QUrl
from PySide6.QtMultimedia import QAudioOutput,QMediaPlayer
from PySide6.QtWidgets import (QCheckBox,QComboBox,QDoubleSpinBox,QFormLayout,QGroupBox,
                               QFileDialog,QHBoxLayout,QLabel,QLineEdit,QMessageBox,QPlainTextEdit,
                               QPushButton,QScrollArea,QSlider,QSpinBox,QSplitter,QVBoxLayout,QWidget)
from .lyrics_panel import insert_section
from .settings_panel import load_settings,save_settings


LOG=logging.getLogger(__name__)
STYLE_PRESETS={
    'Pop':'Modern pop, catchy hooks, bright synths, punchy drums, polished production, expressive lead vocals',
    'Rock':'Energetic rock, distorted electric guitars, driving bass and drums, powerful lead vocals',
    'Soft Rock':'Soft rock, clean electric guitars, warm piano, steady drums, smooth emotive vocals',
    'Indie Pop':'Indie pop, bright guitars, melodic bass, crisp drums, warm intimate vocals, polished demo mix',
    'Indie Rock':'Indie rock, jangly guitars, driving live drums, textured bass, earnest lead vocals',
    'Acoustic':'Acoustic singer-songwriter, fingerpicked guitar, light percussion, intimate natural vocals',
    'Folk':'Contemporary folk, acoustic guitar, fiddle and mandolin, organic rhythm, warm storytelling vocals',
    'Country':'Modern country, acoustic guitar, pedal steel, steady drums, clear heartfelt vocals',
    'Blues':'Electric blues, expressive guitar, shuffle groove, Hammond organ, soulful gritty vocals',
    'Soul':'Classic soul, warm Rhodes, brass accents, deep bass groove, passionate lead vocals',
    'R&B':'Contemporary R&B, smooth keys, deep bass, crisp restrained beats, silky expressive vocals',
    'Funk':'Funk, syncopated bass, wah guitar, tight drums, brass stabs, energetic vocals',
    'Disco':'Disco, four-on-the-floor groove, octave bass, strings, rhythmic guitar, glamorous vocals',
    'Jazz':'Modern jazz, piano trio, walking bass, brushed drums, rich harmony, relaxed vocal phrasing',
    'Swing':'Big band swing, brass and saxophones, walking bass, swinging drums, lively crooner vocals',
    'Reggae':'Roots reggae, offbeat guitar, deep bass, relaxed drums, warm laid-back vocals',
    'Ska':'Upbeat ska, offbeat guitar, punchy brass, walking bass, fast drums, spirited vocals',
    'Latin Pop':'Latin pop, bright guitars, syncopated percussion, modern pop beat, passionate vocals',
    'Salsa':'Salsa, piano montuno, brass section, congas and timbales, energetic call-and-response vocals',
    'Bachata':'Bachata, bright requinto guitar, syncopated bass, bongos, romantic intimate vocals',
    'Spanish Rumba':'Spanish rumba, lively rhythmic groove, Spanish guitar, hand percussion, lush strings, warm male vocals',
    'Flamenco':'Flamenco, virtuosic Spanish guitar, hand claps, cajón, dramatic passionate vocals',
    'Bossa Nova':'Bossa nova, nylon-string guitar, soft brushed drums, gentle bass, intimate airy vocals',
    'EDM':'Festival EDM, massive synth leads, driving kick, dramatic builds and drops, polished production',
    'House':'House, four-on-the-floor beat, deep bass, rhythmic piano chords, soulful vocal hooks',
    'Dance Pop':'Dance pop, bright synths, pulsing bass, energetic beat, catchy polished vocals',
    'Synthwave':'Synthwave, retro analog synths, gated drums, pulsing arpeggios, cinematic neon atmosphere',
    'Hip-Hop':'Modern hip-hop, heavy drums, deep bass, atmospheric samples, confident rhythmic vocals',
    'Trap':'Trap, booming 808 bass, rapid hi-hats, dark synth textures, melodic rap vocals',
    'Ballad':'Emotional ballad, piano and strings, gentle build, spacious production, heartfelt lead vocals',
    'Cinematic':'Cinematic, sweeping strings, deep percussion, evolving orchestral textures, dramatic atmosphere',
    'Orchestral':'Orchestral, full symphony orchestra, rich strings and brass, dynamic percussion, grand arrangement',
}


class GenerationTask(QThread):
    ready=Signal(object); failed=Signal(str)
    def __init__(self,operation,parent=None):
        super().__init__(parent); self.operation=operation
    def run(self):
        try: self.ready.emit(self.operation())
        except Exception as exc:
            LOG.exception('YuE2 generation failed')
            self.failed.emit(str(exc))


def parse_nvidia_smi(text):
    line=next((line.strip() for line in text.splitlines() if line.strip()),'')
    fields=[field.strip() for field in line.split(',')]
    if len(fields)!=4: raise ValueError('Unexpected nvidia-smi output')
    name,used,total,utilization=fields
    return dict(name=name,used_mib=float(used),total_mib=float(total),utilization=int(float(utilization)))


class GpuQueryTask(QThread):
    ready=Signal(object); failed=Signal()
    def run(self):
        try:
            result=subprocess.run(['nvidia-smi','--query-gpu=name,memory.used,memory.total,utilization.gpu',
                '--format=csv,noheader,nounits'],capture_output=True,text=True,encoding='utf-8',errors='replace',
                timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode: raise RuntimeError(result.stderr.strip() or 'nvidia-smi failed')
            self.ready.emit(parse_nvidia_smi(result.stdout))
        except (OSError,ValueError,subprocess.SubprocessError): self.failed.emit()


class GenerationPanel:
    def build_generation(self):
        stored=load_settings(getattr(self,'generation_settings_path',None))
        stored.update(getattr(self,'model_settings',{})); self.model_settings=stored
        self.generation_pane=QWidget(); layout=QVBoxLayout(self.generation_pane)
        columns=QSplitter(Qt.Horizontal); layout.addWidget(columns,1)
        left_scroll=QScrollArea(); left_scroll.setWidgetResizable(True)
        left=QWidget(); controls=QVBoxLayout(left); left_scroll.setWidget(left); columns.addWidget(left_scroll)
        output_column=QWidget(); output_layout=QVBoxLayout(output_column); output_layout.setContentsMargins(0,0,0,0)
        output=QGroupBox('Output'); out=QVBoxLayout(output); output_layout.addWidget(output); output_layout.addStretch()
        columns.addWidget(output_column); columns.setSizes([720,420])

        self.generation_model=QLabel('Model: Not configured'); controls.addWidget(self.generation_model)
        self.audiocpp_status=QLabel('audio.cpp: Not available'); controls.addWidget(self.audiocpp_status)
        controls.addWidget(QLabel('Lyrics'))
        sections=QHBoxLayout()
        for name in ('Intro','Verse','Chorus','Pre-Chorus','Bridge','Instrumental','Solo','Outro'):
            button=QPushButton(name); button.clicked.connect(lambda checked=False,n=name:insert_section(self.generation_lyrics,n)); sections.addWidget(button)
        sections.addStretch(); controls.addLayout(sections)
        self.generation_lyrics=QPlainTextEdit(); self.generation_lyrics.setMinimumHeight(180)
        self.generation_lyrics.textChanged.connect(lambda:self._generation_edited('lyrics')); controls.addWidget(self.generation_lyrics)
        form=QFormLayout(); controls.addLayout(form)
        self.generation_style=QLineEdit(); self.generation_style.setPlaceholderText('English indie pop, bright acoustic guitar, warm lead vocal, polished demo mix')
        form.addRow('Style:',self.generation_style)
        self.generation_style_preset=QComboBox(); self.generation_style_preset.addItem('Custom')
        self.generation_style_preset.addItems(STYLE_PRESETS)
        self._applying_style_preset=False
        self.generation_style_preset.currentTextChanged.connect(self.apply_style_preset)
        self.generation_style.textEdited.connect(self.style_manually_edited)
        form.addRow('Style preset:',self.generation_style_preset)
        seed_row=QWidget(); seed_layout=QHBoxLayout(seed_row); seed_layout.setContentsMargins(0,0,0,0)
        self.generation_seed=QSpinBox(); self.generation_seed.setRange(0,2_147_483_647)
        random_seed=QPushButton('Random'); random_seed.clicked.connect(self.randomize_generation_seed)
        seed_layout.addWidget(self.generation_seed,1); seed_layout.addWidget(random_seed); form.addRow('Seed:',seed_row)
        primary=QWidget(); self.generation_primary_row=QHBoxLayout(primary); self.generation_primary_row.setContentsMargins(0,0,0,0)
        route_column=QVBoxLayout(); route_column.addWidget(QLabel('Planning route'))
        self.generation_route=QComboBox(); self.generation_route.addItems(['Off','Melody','Full']); route_column.addWidget(self.generation_route)
        guidance_column=QVBoxLayout(); guidance_column.addWidget(QLabel('Semantic Guidance'))
        guidance_row=QHBoxLayout(); self.generation_guidance=QSlider(Qt.Horizontal)
        self.generation_guidance.setObjectName('semanticGuidance'); self.generation_guidance.setRange(0,500)
        self.generation_guidance.setSingleStep(1); self.generation_guidance.setPageStep(10)
        guidance=float(self.model_settings.get('yue2_semantic_guidance',1.5))
        self.generation_guidance.setValue(round(max(0.0,min(5.0,guidance))*100))
        self.generation_guidance_value=QLabel(); self._update_generation_guidance(self.generation_guidance.value())
        self.generation_guidance.valueChanged.connect(self._update_generation_guidance)
        self.generation_guidance.valueChanged.connect(lambda value:self._save_generation_setting('yue2_semantic_guidance',value/100))
        guidance_row.addWidget(self.generation_guidance,1); guidance_row.addWidget(self.generation_guidance_value); guidance_column.addLayout(guidance_row)
        nar_column=QVBoxLayout(); nar_column.addWidget(QLabel('NAR Steps'))
        self.generation_nar_steps=QSpinBox(); self.generation_nar_steps.setObjectName('narSteps'); self.generation_nar_steps.setRange(1,64)
        self.generation_nar_steps.setValue(max(1,min(64,int(self.model_settings.get('yue2_nar_steps',16)))))
        self.generation_nar_steps.valueChanged.connect(lambda value:self._save_generation_setting('yue2_nar_steps',value)); nar_column.addWidget(self.generation_nar_steps)
        self.generation_primary_row.addLayout(route_column,2); self.generation_primary_row.addLayout(guidance_column,5)
        self.generation_primary_row.addLayout(nar_column,1); controls.addWidget(primary)
        self.generation_use_abc=QCheckBox('Use MusicLab ABC score'); controls.addWidget(self.generation_use_abc)
        self.generation_abc_toggle,self.generation_abc_box=self._collapsible(controls,'ABC Preview')
        self.generation_abc=QPlainTextEdit(); self.generation_abc.setPlaceholderText('No MusicLab ABC score available.')
        self.generation_abc.textChanged.connect(lambda:self._generation_edited('abc')); self.generation_abc_box.addWidget(self.generation_abc)
        self.semantic_controls=self._sampling_section(controls,'Semantic sampling','semantic')
        self.abc_sampling_controls=self._sampling_section(controls,'ABC sampler sampling','abc')
        controls.addStretch()
        self.generation_generate=QPushButton('Generate'); self.generation_generate.clicked.connect(self.generate_music); controls.addWidget(self.generation_generate)

        self.generation_output_message=QLabel('Generated audio will appear here.'); self.generation_output_message.setWordWrap(True); out.addWidget(self.generation_output_message)
        self.generation_output_status=QLabel('No generated audio.'); out.addWidget(self.generation_output_status)
        timeline=QHBoxLayout(); self.generation_time=QLabel('0:00 / 0:00')
        self.generation_seek=QSlider(Qt.Horizontal); self.generation_seek.setRange(0,0); self.generation_seek.setEnabled(False)
        timeline.addWidget(self.generation_time); timeline.addWidget(self.generation_seek,1); out.addLayout(timeline)
        player=QHBoxLayout(); self.generation_play=QPushButton('Play'); self.generation_play.setEnabled(False)
        self.generation_save=QPushButton('Save Audio'); self.generation_save.setEnabled(False)
        self.generation_play.clicked.connect(self.toggle_generation_audio)
        self.generation_save.clicked.connect(self.save_generation_audio)
        player.addWidget(self.generation_play); player.addStretch(); player.addWidget(self.generation_save); out.addLayout(player)
        self.generation_lyrics_source=None; self.generation_abc_source=None
        self.generation_lyrics_dirty=False; self.generation_abc_dirty=False
        self.generation_output_path=None; self.generation_job=None
        self.generation_preview_dir=QTemporaryDir(str(Path(QDir.tempPath())/'ComfyMax-MusicLab-YuE2-XXXXXX'))
        if not self.generation_preview_dir.isValid(): raise RuntimeError('Could not create temporary audio preview directory.')
        self.generation_audio_output=QAudioOutput(self.generation_pane)
        self.generation_player=QMediaPlayer(self.generation_pane); self.generation_player.setAudioOutput(self.generation_audio_output)
        self.generation_position=0; self.generation_duration=0
        self.generation_player.playbackStateChanged.connect(
            lambda state:self.generation_play.setText('Pause' if state==QMediaPlayer.PlayingState else 'Play'))
        self.generation_player.positionChanged.connect(self._generation_position_changed)
        self.generation_player.durationChanged.connect(self._generation_duration_changed)
        self.generation_seek.sliderMoved.connect(self.seek_generation_audio)
        out.addWidget(QLabel('GPU'))
        self.generation_gpu=QLabel('GPU information unavailable'); self.generation_gpu.setWordWrap(True); out.addWidget(self.generation_gpu)
        self.generation_gpu_job=None; self.generation_gpu_timer=QTimer(self.generation_pane)
        self.generation_gpu_timer.setInterval(1000); self.generation_gpu_timer.timeout.connect(self.update_gpu_info)
        self.generation_gpu_timer.start()
        self.generation_pane.destroyed.connect(self._cleanup_generation_preview)

    @staticmethod
    def _collapsible(parent,title):
        toggle=QPushButton(title); toggle.setCheckable(True); toggle.setChecked(False); parent.addWidget(toggle)
        body=QVBoxLayout(); container=QWidget(); container.setLayout(body); container.setVisible(False); parent.addWidget(container)
        toggle.toggled.connect(container.setVisible)
        return toggle,body

    def _sampling_section(self,parent,title,prefix):
        _,body=self._collapsible(parent,title); form=QFormLayout(); body.addLayout(form); controls={}
        defaults={'semantic':((1.0,.95,100,1.2,50,200,9000)),
                  'abc':((.7,.9,30,1.005,100,32,4096))}[prefix]
        for (key,label,minimum,maximum,decimals),value in zip((
            ('temperature',f'{title.split()[0]} temperature',0,5.0,3),
            ('top_p',f'{title.split()[0]} top-P',0,1.0,3),
            ('top_k',f'{title.split()[0]} top-K',1,1_000_000,0),
            ('repetition_penalty',f'{title.split()[0]} repetition penalty',.001,10.0,3),
            ('penalty_window',f'{title.split()[0]} penalty window',1,1_000_000,0),
            ('min_tokens',f'{title.split()[0]} min tokens',0,1_000_000,0),
            ('max_tokens',f'{title.split()[0]} max tokens',1,1_000_000,0)),defaults):
            widget=QDoubleSpinBox() if decimals else QSpinBox(); widget.setRange(minimum,maximum)
            if decimals: widget.setDecimals(decimals); widget.setSingleStep(.05)
            widget.setValue(value); form.addRow(label+':',widget); controls[key]=widget
        return controls

    def _generation_edited(self,kind):
        setattr(self,f'generation_{kind}_dirty',True)

    def randomize_generation_seed(self):
        self.generation_seed.setValue(secrets.randbelow(2_147_483_648))

    def _update_generation_guidance(self,value):
        self.generation_guidance_value.setText(f'{value/100:.2f}')

    def _save_generation_setting(self,key,value):
        self.model_settings[key]=value
        save_settings(self.model_settings,getattr(self,'generation_settings_path',None))

    def apply_style_preset(self,name):
        if name=='Custom': return
        self._applying_style_preset=True
        try:
            prompt=STYLE_PRESETS[name]; self.generation_style.setText(prompt)
            self.generation_style.setCursorPosition(len(prompt))
        finally: self._applying_style_preset=False

    def style_manually_edited(self,_text):
        if self._applying_style_preset or self.generation_style_preset.currentText()=='Custom': return
        self.generation_style_preset.blockSignals(True)
        self.generation_style_preset.setCurrentText('Custom')
        self.generation_style_preset.blockSignals(False)

    def reset_generation_inputs(self):
        self.generation_lyrics_source=None; self.generation_abc_source=None
        self.generation_lyrics_dirty=False; self.generation_abc_dirty=False
        self.refresh_generation_inputs()

    def refresh_generation_inputs(self):
        model=getattr(self,'model_settings',{}).get('yue2_main_path','')
        self.generation_model.setText('Model: '+(model or 'Not configured'))
        lyrics=self.lyrics_editor.toPlainText() if hasattr(self,'lyrics_editor') else ''
        if not self.generation_lyrics_dirty and lyrics!=self.generation_lyrics_source:
            self.generation_lyrics.blockSignals(True); self.generation_lyrics.setPlainText(lyrics); self.generation_lyrics.blockSignals(False)
            self.generation_lyrics_source=lyrics
        document=self.score_panel.working_document() if getattr(self,'score_panel',None) else None
        abc=document.text if document and document.payload else ''
        abc_changed=abc!=self.generation_abc_source
        if not self.generation_abc_dirty and abc_changed:
            self.generation_abc.blockSignals(True); self.generation_abc.setPlainText(abc); self.generation_abc.blockSignals(False)
            self.generation_abc_source=abc
            self.generation_use_abc.setChecked(bool(abc))

    def set_audiocpp_status(self,status):
        self.audiocpp_status.setText('audio.cpp: '+status)

    def generation_request(self):
        values=lambda controls:{key:widget.value() for key,widget in controls.items()}
        return dict(model=getattr(self,'model_settings',{}).get('yue2_main_path',''),
                    lyrics=self.generation_lyrics.toPlainText(),style=self.generation_style.text(),
                    seed=self.generation_seed.value(),planning_route=self.generation_route.currentText(),
                    use_musiclab_abc=self.generation_use_abc.isChecked(),abc=self.generation_abc.toPlainText(),
                    semantic_guidance=self.generation_guidance.value()/100,
                    nar_steps=self.generation_nar_steps.value(),
                    semantic_sampling=values(self.semantic_controls),abc_sampling=values(self.abc_sampling_controls))

    def generate_music(self):
        route=self.generation_route.currentText()
        if route=='Off' and self.generation_use_abc.isChecked():
            self.generation_output_status.setText('MusicLab ABC requires Planning route Melody or Full.')
            return
        runtime=getattr(self,'audiocpp_runtime',None)
        if runtime is None:
            self.generation_output_status.setText('audio.cpp runtime is not available.')
            return
        request=self.generation_request()
        abc=request['abc'] if request['use_musiclab_abc'] else ''
        planning={'Off':'off','Melody':'melody','Full':'full'}[route]
        semantic={f'semantic_{key}':widget.value() for key,widget in self.semantic_controls.items()}
        abc_sampling={f'abc_{key}':widget.value() for key,widget in self.abc_sampling_controls.items()} if abc else {}
        options={'guidance_scale':request['semantic_guidance'],'num_inference_steps':request['nar_steps'],
                 **semantic,**abc_sampling}
        self.generation_generate.setEnabled(False); self.generation_output_status.setText('Generating...')
        self.generation_output_status.setStyleSheet(
            'background-color:#dc2626;color:white;padding:5px 9px;border-radius:4px;font-weight:600;')
        self.generation_job=GenerationTask(
            lambda:runtime.generate_yue2(request['lyrics'],request['style'],abc,request['seed'],planning,
                                         self.generation_preview_dir.path(),options),self.generation_pane)
        self.generation_job.ready.connect(self.generation_finished)
        self.generation_job.failed.connect(self.generation_failed)
        self.generation_job.finished.connect(self.generation_job_finished)
        self.generation_job.start()

    def generation_finished(self,result):
        self.generation_output_status.setStyleSheet('')
        path=Path(result['path']); previous=self.generation_output_path
        self.generation_player.stop(); self.generation_player.setSource(QUrl())
        if previous and previous!=path:
            try: previous.unlink(missing_ok=True)
            except OSError: LOG.warning('Could not remove previous YuE2 preview: %s',previous,exc_info=True)
        self.generation_output_path=path
        duration=self._wave_duration(path)
        detail=f'{duration:.2f} s' if duration is not None else 'duration unavailable'
        self.generation_output_message.setText('Generated audio preview is ready.')
        self.generation_output_status.setText(f'Generated audio ready — {detail}')
        self.generation_player.setSource(QUrl.fromLocalFile(str(path)))
        self.generation_seek.setEnabled(True)
        self.generation_play.setEnabled(True); self.generation_save.setEnabled(True)

    def generation_failed(self,message):
        self.generation_output_status.setStyleSheet('')
        self.generation_output_status.setText('Generation failed: '+message)

    def generation_job_finished(self):
        self.generation_output_status.setStyleSheet('')
        self.generation_generate.setEnabled(True)
        job=self.generation_job; self.generation_job=None
        if job is not None: job.deleteLater()

    @staticmethod
    def _wave_duration(path):
        try:
            with wave.open(str(path),'rb') as audio: return audio.getnframes()/audio.getframerate()
        except (OSError,EOFError,wave.Error,ZeroDivisionError): return None

    def toggle_generation_audio(self):
        if self.generation_player.playbackState()==QMediaPlayer.PlayingState: self.generation_player.pause()
        else: self.generation_player.play()

    @staticmethod
    def _format_media_time(milliseconds):
        seconds=max(0,int(milliseconds)//1000)
        return f'{seconds//60}:{seconds%60:02d}'

    def _update_generation_time(self,position=None,duration=None):
        position=self.generation_position if position is None else position
        duration=self.generation_duration if duration is None else duration
        self.generation_time.setText(f'{self._format_media_time(position)} / {self._format_media_time(duration)}')

    def _generation_position_changed(self,position):
        self.generation_position=position
        if not self.generation_seek.isSliderDown(): self.generation_seek.setValue(position)
        self._update_generation_time(position=position)

    def _generation_duration_changed(self,duration):
        self.generation_duration=duration
        self.generation_seek.setRange(0,max(0,duration)); self._update_generation_time(duration=duration)

    def seek_generation_audio(self,position):
        self.generation_position=position; self.generation_player.setPosition(position); self._update_generation_time(position=position)

    def update_gpu_info(self):
        if self.generation_gpu_job is not None: return
        job=GpuQueryTask(self.generation_pane); self.generation_gpu_job=job
        job.ready.connect(self._gpu_ready); job.failed.connect(self._gpu_unavailable)
        job.finished.connect(lambda:self._gpu_finished(job)); job.start()

    def _gpu_ready(self,value):
        self.generation_gpu.setText(f"{value['name']} | VRAM {value['used_mib']/1024:.1f} / {value['total_mib']/1024:.1f} GB | GPU {value['utilization']}%")

    def _gpu_unavailable(self): self.generation_gpu.setText('GPU information unavailable')

    def _gpu_finished(self,job):
        if self.generation_gpu_job is job: self.generation_gpu_job=None
        job.deleteLater()

    def save_generation_audio(self):
        if not self.generation_output_path: return
        target,_=QFileDialog.getSaveFileName(self.generation_pane,'Save generated audio',
                                             self.generation_output_path.name,'Wave audio (*.wav)')
        if target: shutil.copy2(self.generation_output_path,target)

    def _cleanup_generation_preview(self,*_):
        self.generation_output_status.setStyleSheet('')
        self.generation_gpu_timer.stop()
        job=self.generation_gpu_job
        if job is not None and job.isRunning(): job.wait(3500)
        self.generation_gpu_job=None
        self.generation_player.stop(); self.generation_player.setSource(QUrl())
        self.generation_output_path=None
        if hasattr(self,'generation_preview_dir'): self.generation_preview_dir.remove()
