"""Persistent, application-wide Yue2 style presets and their editor page."""
import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget)
from .settings_panel import settings_file


DEFAULT_STYLE_PRESETS={
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


def presets_file():
    return settings_file().with_name('style-presets.json')


class StylePresetStore(QObject):
    changed=Signal()

    def __init__(self,path=None,parent=None):
        super().__init__(parent); self.path=Path(path) if path is not None else presets_file()
        self.presets=self._load()

    def _load(self):
        if not self.path.is_file(): return dict(DEFAULT_STYLE_PRESETS)
        try:
            payload=json.loads(self.path.read_text(encoding='utf-8'))
            rows=payload.get('presets') if isinstance(payload,dict) else None
            if not isinstance(rows,list): raise ValueError('Invalid style presets file.')
            result={}
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('name'),str) or not row['name'].strip() or not isinstance(row.get('prompt'),str):
                    raise ValueError('Invalid style preset entry.')
                name=row['name'].strip()
                if name in result: raise ValueError('Duplicate style preset name.')
                result[name]=row['prompt']
            return result
        except (OSError,UnicodeError,json.JSONDecodeError,ValueError):
            return dict(DEFAULT_STYLE_PRESETS)

    def names(self): return list(self.presets)
    def prompt(self,name): return self.presets.get(name)

    def save(self,name,prompt,original_name=None):
        name=name.strip()
        if not name: raise ValueError('Preset Name may not be empty.')
        duplicate=next((key for key in self.presets if key.casefold()==name.casefold()),None)
        if duplicate is not None and duplicate!=original_name:
            raise ValueError(f'A style preset named "{name}" already exists.')
        if original_name is not None and original_name not in self.presets:
            raise ValueError('The selected style preset no longer exists.')
        if original_name and original_name!=name:
            updated={}
            for key,value in self.presets.items(): updated[name if key==original_name else key]=prompt if key==original_name else value
            self.presets=updated
        else: self.presets[name]=prompt
        self._write(); self.changed.emit()

    def delete(self,name):
        if name not in self.presets: raise ValueError('The selected style preset no longer exists.')
        del self.presets[name]; self._write(); self.changed.emit()

    def _write(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        payload={'presets':[{'name':name,'prompt':prompt} for name,prompt in self.presets.items()]}
        temporary=self.path.with_name(self.path.name+'.tmp')
        temporary.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8')
        os.replace(temporary,self.path)


class StylePresetsPanel:
    def build_style_presets(self):
        if not hasattr(self,'style_preset_store'):
            self.style_preset_store=StylePresetStore(getattr(self,'style_presets_path',None))
        self.style_presets_pane=QWidget(); layout=QVBoxLayout(self.style_presets_pane)
        split=QSplitter(); layout.addWidget(split,1)
        self.style_preset_list=QListWidget(); split.addWidget(self.style_preset_list)
        editor=QWidget(); fields=QVBoxLayout(editor); split.addWidget(editor); split.setSizes([260,700])
        fields.addWidget(QLabel('Preset Name'))
        self.style_preset_name=QLineEdit(); fields.addWidget(self.style_preset_name)
        fields.addWidget(QLabel('Style Prompt'))
        self.style_preset_prompt=QPlainTextEdit(); fields.addWidget(self.style_preset_prompt,1)
        buttons=QHBoxLayout()
        for label,slot in (('New Preset',self.new_style_preset),('Save',self.save_style_preset),
                           ('Save As New',self.save_style_preset_as_new),('Delete',self.delete_style_preset)):
            button=QPushButton(label); button.clicked.connect(slot); buttons.addWidget(button)
        buttons.addStretch(); fields.addLayout(buttons)
        self.selected_style_preset=None
        self.style_preset_list.currentTextChanged.connect(self.select_style_preset)
        self.style_preset_store.changed.connect(self.refresh_style_presets)
        self.refresh_style_presets()

    def refresh_style_presets(self,preferred=None):
        preferred=preferred or self.selected_style_preset
        self.style_preset_list.blockSignals(True); self.style_preset_list.clear()
        self.style_preset_list.addItems(self.style_preset_store.names()); self.style_preset_list.blockSignals(False)
        names=self.style_preset_store.names()
        if preferred in names: self.style_preset_list.setCurrentRow(names.index(preferred))
        elif names: self.style_preset_list.setCurrentRow(0)
        else: self.new_style_preset()

    def select_style_preset(self,name):
        if not name: return
        prompt=self.style_preset_store.prompt(name)
        if prompt is None: return
        self.selected_style_preset=name; self.style_preset_name.setText(name); self.style_preset_prompt.setPlainText(prompt)

    def new_style_preset(self):
        self.style_preset_list.clearSelection(); self.style_preset_list.setCurrentRow(-1)
        self.selected_style_preset=None; self.style_preset_name.clear(); self.style_preset_prompt.clear(); self.style_preset_name.setFocus()

    def _save_style_preset(self,original):
        try:
            name=self.style_preset_name.text().strip()
            self.style_preset_store.save(name,self.style_preset_prompt.toPlainText(),original)
            self.selected_style_preset=name; self.refresh_style_presets(name)
        except (OSError,ValueError) as exc: QMessageBox.warning(self.style_presets_pane,'Cannot save style preset',str(exc))

    def save_style_preset(self): self._save_style_preset(self.selected_style_preset)
    def save_style_preset_as_new(self): self._save_style_preset(None)

    def delete_style_preset(self):
        name=self.selected_style_preset
        if not name: QMessageBox.warning(self.style_presets_pane,'Cannot delete style preset','Select a style preset first.'); return
        if QMessageBox.question(self.style_presets_pane,'Delete style preset',f'Delete style preset "{name}"?',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        try:
            self.selected_style_preset=None; self.style_preset_store.delete(name)
        except (OSError,ValueError) as exc: QMessageBox.warning(self.style_presets_pane,'Cannot delete style preset',str(exc))
