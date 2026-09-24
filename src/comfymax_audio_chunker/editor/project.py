"""Portable project document, independent of UI and playback."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from datetime import datetime, timezone

import soundfile as sf
from filelock import FileLock, Timeout


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def inside(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Project asset path escapes its folder')
    return path


def playable(phrase, duration):
    a, b = phrase.get('start'), phrase.get('end')
    return (isinstance(a, (float, int)) and isinstance(b, (float, int)) and
            math.isfinite(a) and math.isfinite(b) and 0 <= a < b <= duration)


def atomic_json(path, data):
    payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
    handle, temporary = tempfile.mkstemp(prefix='.saving-', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_state(data):
    """Validate recovery candidates before any one can replace a good save."""
    if not isinstance(data, dict) or data.get('schema_version') != 'comfymax.editor.1':
        raise ValueError('Unsupported editor project version')
    if not isinstance(data.get('project_id'), str) or not isinstance(data.get('revision'), int) or data['revision'] < 0:
        raise ValueError('Invalid project identity or revision')
    for key in ('analysis', 'assets', 'timeline', 'settings', 'view', 'source'):
        if not isinstance(data.get(key), dict):
            raise ValueError(f'Missing project {key}')
    if not isinstance(data.get('title'), str):
        raise ValueError('Missing project title')
    for descriptor in [data['analysis']] + list(data['assets'].values()):
        if not isinstance(descriptor, dict) or any(not isinstance(descriptor.get(key), str) for key in ('path','sha256')):
            raise ValueError('Invalid project asset descriptor')
    if not isinstance(data.get('reference_lyrics', ''), str):
        raise ValueError('Invalid reference lyrics')
    if not isinstance(data.get('phrases'), list):
        raise ValueError('Invalid phrase list')
    identifiers = set()
    for phrase in data['phrases']:
        if not isinstance(phrase.get('id'), str) or phrase['id'] in identifiers:
            raise ValueError('Invalid or duplicate phrase ID')
        identifiers.add(phrase['id'])
        if not isinstance(phrase.get('original_text'), str):
            raise ValueError('Invalid original transcript')
        if phrase.get('corrected_text') is not None and not isinstance(phrase['corrected_text'], str):
            raise ValueError('Invalid corrected text')
        if phrase.get('review_status') not in ('unreviewed', 'reviewed'):
            raise ValueError('Invalid review status')
        if phrase.get('word_alignment_status') not in ('original_estimates','phrase_only'):
            raise ValueError('Invalid word alignment status')
        for key in ('start', 'end', 'imported_start', 'imported_end', 'source_segment_ids', 'source_word_ids'):
            if key not in phrase:
                raise ValueError(f'Missing phrase {key}')
    frames, rate = data['timeline'].get('frames'), data['timeline'].get('sample_rate')
    if not isinstance(frames, int) or not isinstance(rate, int) or min(frames, rate) <= 0:
        raise ValueError('Invalid project audio timeline')
    duration = data['timeline'].get('duration')
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or abs(duration-frames/rate) > 1/rate:
        raise ValueError('Invalid project duration')
    from .scenes import validate_cuts
    if 'transcript' in data:
        from .transcript import validate as validate_transcript
        validate_transcript(data['transcript'],duration)
    validate_cuts(data.get('chunk_boundaries',[]),frames)
    if 'marker_editor' in data:
        from .markers import validate_marker_state
        validate_marker_state(data['marker_editor'],frames)
    if 'music_analysis' in data:
        from ..music.model import validate as validate_music
        validate_music(data['music_analysis'], data['timeline'])
        if 'sheet_sage' in data['music_analysis']:
            from ..music.sheetsage import validate as validate_sheet
            validate_sheet(data['music_analysis']['sheet_sage'])
        if data['music_analysis']['analysis']['source_sha256'] != data['assets']['mix']['sha256']:
            raise ValueError('Music analysis does not match the project MIX checksum')
    if not isinstance(data.get('scenes_initialized',False),bool):
        raise ValueError('Invalid scene initialization flag')
    from .lyrics_workflow import validate_history
    validate_history(data)
    review=data.get('lyrics_review')
    if review is not None and (not isinstance(review,dict) or not isinstance(review.get('fingerprint'),str) or len(review['fingerprint'])!=64):
        raise ValueError('Invalid lyrics review checkpoint')
    for key in ('before', 'after'):
        if not isinstance(data['settings'].get(key), (float,int)) or not 0 <= data['settings'][key] <= 3:
            raise ValueError('Invalid playback context setting')
    if not isinstance(data['settings'].get('loop'),bool) or data['settings'].get('source') not in data['assets']:
        raise ValueError('Invalid playback settings')
    for key in ('position','start','span'):
        value=data['view'].get(key)
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
            raise ValueError('Invalid saved timeline view')


class Document:
    def __init__(self, root, data, analysis, lock, recovered=False):
        self.root, self.data, self.analysis, self.lock = root, data, analysis, lock
        self.recovered = recovered
        self.recovery_source = None

    @property
    def duration(self):
        return self.data['timeline']['duration']

    @property
    def phrases(self):
        return self.data['phrases']

    def save(self):
        if not self.lock or not self.lock.is_locked:
            raise ValueError('Project is not locked for writing')
        target = self.root / 'project.json'
        new = copy.deepcopy(self.data)
        new['revision'] += 1
        new['saved_utc'] = datetime.now(timezone.utc).isoformat()
        validate_state(new)
        # Never replace last-good recovery with a corrupt primary file.
        if target.exists():
            try:
                previous = json.loads(target.read_text(encoding='utf-8'))
                validate_state(previous)
                if previous.get('project_id') == new['project_id']:
                    atomic_json(self.root / 'recovery' / 'last-good.json', previous)
            except (ValueError, UnicodeError, KeyError, TypeError):
                pass
        # Write a complete recoverable revision before replacing the primary.
        # A crash/failure between these two replaces must not lose this revision.
        atomic_json(self.root / 'recovery' / 'pending.json', new)
        atomic_json(target, new)
        self.data = new
        self.recovered = False
        self.recovery_source = None

    def set_correction_state(self, phrase_id, state):
        if set(state) != {'corrected_text', 'review_status', 'word_alignment_status'}:
            raise ValueError('Correction commands may only change text, review and alignment status')
        phrase = next(p for p in self.phrases if p['id'] == phrase_id)
        phrase.update(copy.deepcopy(state))

    def discard_pending(self):
        # Only the designated checkpoint, never primary/source/audio, is removed.
        (self.root / 'recovery' / 'pending.json').unlink(missing_ok=True)

    def save_as(self, destination):
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError('Choose a new project folder; existing folders are never overwritten.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.comfymax-save-as-', dir=destination.parent))
        try:
            for name in ('source', 'audio', 'cache', 'recovery'):
                (stage / name).mkdir()
            new = copy.deepcopy(self.data)
            new['project_id'], new['revision'] = str(uuid.uuid4()), 1
            new['saved_utc'] = datetime.now(timezone.utc).isoformat()
            validate_state(new)
            paths = [new['analysis']['path']] + [a['path'] for a in new['assets'].values()]
            sheet = new.get('music_analysis', {}).get('sheet_sage')
            if sheet:
                for result in [sheet] + sheet.get('previous_results', []):
                    for artifact in result['raw_artifacts']:
                        if digest(inside(self.root, artifact['path'])) != artifact['sha256']:
                            raise ValueError('SheetSage artifact checksum mismatch')
                        paths.append(artifact['path'])
            for path in paths:
                dest = inside(stage, path); dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(inside(self.root, path), dest)
            atomic_json(stage / 'project.json', new)
            atomic_json(stage / 'recovery' / 'last-good.json', new)
            stage.rename(destination)
        except BaseException:
            if stage.exists(): shutil.rmtree(stage)
            raise
        return type(self).open(destination)

    def close(self):
        if self.lock:
            self.lock.release()
            self.lock = None

    @classmethod
    def open(cls, path):
        path = Path(path).resolve()
        root = path if path.is_dir() else path.parent
        lock = FileLock(str(root / '.editor.lock'), timeout=0, thread_local=False)
        try:
            lock.acquire()
        except Timeout as exc:
            raise ValueError('This project is already open in another editor. Close it there first.') from exc
        try:
            candidates = []
            for priority, name in enumerate(('project.json', 'recovery/pending.json', 'recovery/last-good.json')):
                try:
                    state = json.loads((root / name).read_text(encoding='utf-8'))
                    validate_state(state)
                    snap = inside(root, state['analysis']['path'])
                    if digest(snap) != state['analysis']['sha256']:
                        raise ValueError('Analysis snapshot checksum mismatch')
                    candidates.append((state['revision'], -priority, name, state))
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    continue
            if not candidates:
                raise ValueError('No valid project or recovery copy. Restore the project backup and its original analysis snapshot.')
            # The primary/last-good identity anchors recovery; ignore unrelated checkpoints.
            primary = next((c for c in candidates if c[2] == 'project.json'), None)
            anchor = primary or next((c for c in candidates if c[2] == 'recovery/last-good.json'), candidates[0])
            candidates = [c for c in candidates if c[3]['project_id'] == anchor[3]['project_id']
                          and c[3]['analysis'] == anchor[3]['analysis']]
            _, _, selected_path, data = max(candidates, key=lambda c: (c[0], c[1]))
            recovered = selected_path != 'project.json'
            snap = inside(root, data['analysis']['path'])
            if digest(snap) != data['analysis']['sha256']:
                raise ValueError('The original analysis snapshot changed. Restore source/analysis.json from the Stage 1 run.')
            analysis = json.loads(snap.read_text(encoding='utf-8'))
            frames, rate = data['timeline']['frames'], data['timeline']['sample_rate']
            if not isinstance(frames, int) or not isinstance(rate, int) or frames <= 0 or rate <= 0:
                raise ValueError('Invalid project audio timeline')
            if abs(data['timeline']['duration'] - frames / rate) > 1 / rate:
                raise ValueError('Project duration does not match its audio timeline')
            if 'mix' not in data['assets']:
                raise ValueError('The full mix playback proxy is missing')
            for key, asset in data['assets'].items():
                audio = inside(root, asset['path'])
                if not audio.exists():
                    raise ValueError(f'Missing {key} audio. Restore {audio} from the original Stage 1 run, or import that run into a new project.')
                info = sf.info(audio)
                if info.frames != frames or info.samplerate != rate or info.channels != 2:
                    raise ValueError(f'{key} audio does not match the project timeline')
                if digest(audio) != asset['sha256']:
                    raise ValueError(f'{key} audio has changed. Restore the original proxy or import a new project.')
            settings = data['settings']
            for key in ('before', 'after'):
                if not 0 <= settings[key] <= 3:
                    raise ValueError('Invalid playback context setting')
            if len({p['id'] for p in data['phrases']}) != len(data['phrases']):
                raise ValueError('Duplicate phrase IDs in project')
            document = cls(root, data, analysis, lock, recovered)
            document.recovery_source = selected_path if recovered else None
            return document
        except BaseException:
            lock.release()
            raise

    @classmethod
    def create(cls, analysis_path, destination):
        source = Path(analysis_path).resolve()
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError('Choose a new project folder; existing folders are never overwritten.')
        original = source.read_bytes()
        analysis = json.loads(original.decode('utf-8'))
        if analysis.get('stage') != 1 or analysis.get('schema_version') != '1.0':
            raise ValueError('Choose a ComfyMax Stage 1 analysis.json file')
        timeline = analysis['timeline']
        frames, rate = timeline['analysis_frames'], timeline['analysis_sample_rate']
        if not isinstance(frames, int) or not isinstance(rate, int) or min(frames, rate) <= 0:
            raise ValueError('Invalid Stage 1 timeline')
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.comfymax-import-', dir=destination.parent))
        try:
            for name in ('source', 'audio', 'cache', 'recovery'):
                (stage / name).mkdir()
            (stage / 'source' / 'analysis.json').write_bytes(original)
            assets = {}
            for key, field in [('mix', 'analysis_mix'), ('vocals', 'vocals')]:
                name = analysis.get('artifacts', {}).get(field)
                if not name:
                    if key == 'mix':
                        raise ValueError('Stage 1 analysis has no full mix proxy')
                    continue
                src = inside(source.parent, name)
                if not src.exists() and key == 'vocals':
                    continue
                info = sf.info(src)
                if info.frames != frames or info.samplerate != rate or info.channels != 2:
                    raise ValueError(f'{field} must match the Stage 1 stereo timeline')
                relative = f'audio/{field}.wav'
                shutil.copyfile(src, stage / relative)
                assets[key] = dict(path=relative, sha256=digest(stage / relative))
            phrases = []
            for s in analysis.get('segments', []):
                phrases.append(dict(id=str(uuid.uuid4()), source_segment_ids=[s['id']],
                    source_word_ids=[w['id'] for w in analysis.get('words', []) if w['segment_id'] == s['id']],
                    imported_start=s.get('start'), imported_end=s.get('end'), start=s.get('start'), end=s.get('end'),
                    original_text=s.get('text', ''), corrected_text=None, origin='imported_segment',
                    review_status='unreviewed', dismissed=False, word_alignment_status='original_estimates',
                    parent_phrase_ids=[], suspect=bool(s.get('suspect', False))))
            data = dict(schema_version='comfymax.editor.1', project_id=str(uuid.uuid4()), revision=0,
                title=Path(analysis['source']['path']).stem, source=copy.deepcopy(analysis['source']),
                analysis=dict(path='source/analysis.json', sha256=hashlib.sha256(original).hexdigest()),
                timeline=dict(origin='first decoded audio sample', frames=frames, sample_rate=rate, duration=frames/rate),
                assets=assets, phrases=phrases, reference_lyrics='', chunk_boundaries=[],
                settings=dict(before=.5, after=.5, loop=False, source='mix', volume=.7),
                view=dict(position=0., start=0., span=min(30., frames/rate), selected_id=None))
            atomic_json(stage / 'project.json', data)
            atomic_json(stage / 'recovery' / 'last-good.json', data)
            stage.rename(destination)
        except BaseException:
            # Only our unique temporary import directory is eligible for cleanup.
            if stage.exists():
                shutil.rmtree(stage)
            raise
        return cls.open(destination)
