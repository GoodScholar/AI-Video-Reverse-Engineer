"""Thin offline adapter to upstream MLX-Audio; reuse one model in an isolated process."""
from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import RLock

from .preproduction import safe_child
from .reference_video import validate_storage_id

_VOICE_CATALOG_PATH = Path(__file__).with_name('voice_catalog.json')
_PRESET_VOICES = json.loads(_VOICE_CATALOG_PATH.read_text(encoding='utf-8'))
_PRESET_VOICE_IDS = frozenset(voice['id'] for voice in _PRESET_VOICES)


class ProjectVoiceStore:
    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self._lock = RLock()

    def path(self, project_id, *parts):
        return safe_child(self.root, 'project-files', project_id, 'voices', *parts)

    def load(self, project_id):
        path = self.path(project_id, 'catalog.json')
        if not path.exists():
            return {'schemaVersion': 1, 'voices': []}
        if path.is_symlink() or not path.is_file():
            raise OSError('项目音色目录无效')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('schemaVersion') != 1 or not isinstance(value.get('voices'), list):
            raise ValueError('项目音色目录无效')
        seen = set()
        required = {'id', 'name', 'description', 'useCases', 'referenceText'}
        for voice in value['voices']:
            if (not isinstance(voice, dict) or set(voice) != required
                    or any(not isinstance(voice.get(key), str) or not voice[key].strip() for key in required)
                    or validate_storage_id(voice['id']) in seen):
                raise ValueError('项目音色记录无效')
            seen.add(voice['id'])
        return value

    def save(self, project_id, value):
        path = self.path(project_id, 'catalog.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.voices-', suffix='.part')
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def install(self, project_id, voice_id, source, *, name, description, use_cases, reference_text):
        voice_id = validate_storage_id(voice_id)
        if voice_id in _PRESET_VOICE_IDS:
            raise ValueError('项目音色标识不能与预置音色重复')
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise ValueError('参考音频不可用')
        record = {
            'id': voice_id,
            'name': name.strip(),
            'description': description.strip(),
            'useCases': use_cases.strip(),
            'referenceText': reference_text.strip(),
        }
        if any(not record[key] for key in ('name', 'description', 'useCases', 'referenceText')):
            raise ValueError('项目音色信息不完整')
        with self._lock:
            state = self.load(project_id)
            if any(voice['id'] == voice_id for voice in state['voices']):
                raise ValueError('项目音色标识重复')
            target = self.path(project_id, voice_id, 'reference.wav')
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError('项目音色文件已存在')
            descriptor, temporary_name = tempfile.mkstemp(dir=target.parent, prefix='.reference-', suffix='.part')
            os.close(descriptor)
            temporary = Path(temporary_name)
            published = False
            try:
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
                published = True
                state['voices'].append(record)
                self.save(project_id, state)
            except BaseException:
                temporary.unlink(missing_ok=True)
                if published:
                    target.unlink(missing_ok=True)
                raise
        return record

    def get(self, project_id, voice_id):
        validate_storage_id(voice_id)
        try:
            return next(voice for voice in self.load(project_id)['voices'] if voice['id'] == voice_id)
        except StopIteration:
            raise KeyError('项目音色不存在') from None

    def sample(self, project_id, voice_id):
        self.get(project_id, voice_id)
        path = self.path(project_id, voice_id, 'reference.wav')
        if path.is_symlink() or not path.is_file():
            raise OSError('项目音色参考音频不可用')
        return path


class LocalVoiceService:
    def __init__(self, root=None, python=None, model=None, *, data_dir=None, clone_model=None):
        self.root = Path(root or os.environ.get('AIVRE_TTS_ROOT', Path.home() / '.cache/aivre/local-qwen-tts-test'))
        self.python = Path(python or os.environ.get('AIVRE_TTS_PYTHON', self.root / '.venv/bin/python'))
        self.model = Path(model or os.environ.get('AIVRE_TTS_MODEL', self.root / 'model'))
        self.clone_model = Path(clone_model or os.environ.get(
            'AIVRE_TTS_CLONE_MODEL', Path.home() / '.cache/aivre/local-qwen-tts-clone/model'))
        self.project_voices = ProjectVoiceStore(data_dir) if data_dir is not None else None
        try:
            manifest = json.loads((self.root / 'model-manifest.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            manifest = {}
        self.model_revision = manifest.get('revision', 'unverified-local-directory') if self.model.resolve() == (self.root / 'model').resolve() and manifest.get('verified') else 'unverified-local-directory'
        self._processes = {}
        self._lock = RLock()
        self.voices = _PRESET_VOICES

    def _model_available(self, model, model_type):
        try:
            config = json.loads((model / 'config.json').read_text(encoding='utf-8'))
            compatible = (config.get('model_type') == 'qwen3_tts' and config.get('tts_model_type') == model_type
                          and config.get('tts_model_size') == '0b6' and config.get('quantization', {}).get('bits') == 8)
        except (OSError, ValueError, AttributeError):
            compatible = False
        return compatible and self.python.is_file() and (model / 'model.safetensors').is_file()

    def catalog(self, project_id=None):
        available = self._model_available(self.model, 'custom_voice')
        clone_available = self._model_available(self.clone_model, 'base')
        project_voices = [] if project_id is None or self.project_voices is None else self.project_voices.load(project_id)['voices']
        voices = [{**{k: v for k, v in voice.items() if k != 'sample'}, 'available': available,
                   'sampleUrl': f"/api/voices/{voice['id']}/sample" if self.sample(voice['id']) else None}
                  for voice in self.voices]
        voices.extend({
            'id': voice['id'], 'name': voice['name'], 'description': voice['description'],
            'useCases': voice['useCases'], 'group': 'project', 'available': clone_available,
            'sampleUrl': f"/api/projects/{project_id}/voices/{voice['id']}/sample",
        } for voice in project_voices)
        service_available = available or (clone_available and bool(project_voices))
        return {'available': service_available, 'modelRevision':self.model_revision, 'message': '本地 Qwen3-TTS 已就绪；首次生成需要加载模型。' if service_available else
                '本地配音环境未就绪或模型不匹配，请按 README 配置 Qwen 0.6B CustomVoice 8bit；不会自动下载。',
                'voices': voices}

    def sample(self, voice_id, *, project_id=None):
        if project_id is not None and self.project_voices is not None:
            try:
                return self.project_voices.sample(project_id, voice_id)
            except KeyError:
                pass
        voice = next((v for v in self.voices if v['id'] == voice_id), None)
        if voice is None:
            return None
        path = self.root / 'samples' / voice['sample']
        return path if path.is_file() and not path.is_symlink() else None

    def synthesize(self, texts, voice, directory, *, project_id=None):
        project_voice = None
        if project_id is not None and self.project_voices is not None:
            try:
                project_voice = self.project_voices.get(project_id, voice)
            except KeyError:
                pass
        if project_voice is not None:
            if not self._model_available(self.clone_model, 'base'):
                raise ValueError('本地音色克隆模型不可用，请检查 AIVRE_TTS_CLONE_MODEL。')
            model = self.clone_model
            request = {
                'texts': texts,
                'refAudio': str(self.project_voices.sample(project_id, voice).resolve()),
                'refText': project_voice['referenceText'],
                'directory': str(Path(directory).resolve()),
            }
        else:
            if voice not in {v['id'] for v in self.voices} or not self.catalog()['available']:
                raise ValueError('本地配音环境或所选音色不可用，请检查 README 中的目录设置。')
            model = self.model
            request = {'texts': texts, 'voice': voice, 'directory': str(Path(directory).resolve())}
        with self._lock:
            try:
                key = str(model.resolve())
                process = self._processes.get(key)
                if process is None or process.poll() is not None:
                    worker = Path(__file__).parents[1] / 'voice_worker/worker.py'
                    process = subprocess.Popen([str(self.python), '-u', str(worker), key],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        text=True, encoding='utf-8', env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                                                        'HF_HUB_DISABLE_TELEMETRY': '1'})
                    self._processes[key] = process
                process.stdin.write(json.dumps(request, ensure_ascii=False)+'\n')
                process.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    if not selector.select(300):
                        raise TimeoutError('本地配音超时，请缩短脚本后重试。')
                result = json.loads(process.stdout.readline())
                if result.get('status') != 'completed':
                    raise ValueError('本地配音生成失败，请检查环境与脚本后重试。')
                return [Path(directory) / f'{i}.wav' for i in range(len(texts))]
            except (OSError, ValueError, TimeoutError) as error:
                self.close()
                raise ValueError(str(error) if isinstance(error, TimeoutError) else '本地配音生成失败，请检查环境与脚本后重试。') from None

    def close(self):
        with self._lock:
            for process in self._processes.values():
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()
            self._processes = {}


def create_voice_router(service, get_project=None):
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import FileResponse
    router = APIRouter()

    @router.get('/api/voices')
    def catalog():
        return service.catalog()

    @router.get('/api/voices/{voice_id}/sample')
    def sample(voice_id: str):
        path = service.sample(voice_id)
        if path is None:
            raise HTTPException(404, detail={'code':'voice_sample_missing','message':'此音色尚无本地试听，请检查试听目录。'})
        return FileResponse(path, media_type='audio/wav')

    @router.get('/api/projects/{project_id}/voices')
    def project_catalog(project_id: str):
        if get_project is not None:
            get_project(project_id)
        return service.catalog(project_id)

    @router.get('/api/projects/{project_id}/voices/{voice_id}/sample')
    def project_sample(project_id: str, voice_id: str):
        if get_project is not None:
            get_project(project_id)
        try:
            path = service.sample(voice_id, project_id=project_id)
        except (KeyError, OSError, ValueError):
            path = None
        if path is None:
            raise HTTPException(404, detail={'code':'voice_sample_missing','message':'此项目音色的试听文件不可用。'})
        return FileResponse(path, media_type='audio/wav')
    return router
