"""Thin offline adapter to upstream MLX-Audio; reuse one model in an isolated process."""
from __future__ import annotations

import json
import os
import selectors
import subprocess
from pathlib import Path
from threading import RLock


class LocalVoiceService:
    def __init__(self, root=None, python=None, model=None):
        self.root = Path(root or os.environ.get('AIVRE_TTS_ROOT', Path.home() / '.cache/aivre/local-qwen-tts-test'))
        self.python = Path(python or os.environ.get('AIVRE_TTS_PYTHON', self.root / '.venv/bin/python'))
        self.model = Path(model or os.environ.get('AIVRE_TTS_MODEL', self.root / 'model'))
        try:
            manifest = json.loads((self.root / 'model-manifest.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            manifest = {}
        self.model_revision = manifest.get('revision', 'unverified-local-directory') if self.model.resolve() == (self.root / 'model').resolve() and manifest.get('verified') else 'unverified-local-directory'
        self._process = None
        self._lock = RLock()
        self.voices = json.loads(Path(__file__).with_name('voice_catalog.json').read_text(encoding='utf-8'))

    def catalog(self):
        try:
            config = json.loads((self.model / 'config.json').read_text(encoding='utf-8'))
            compatible = (config.get('model_type') == 'qwen3_tts' and config.get('tts_model_type') == 'custom_voice'
                          and config.get('tts_model_size') == '0b6' and config.get('quantization', {}).get('bits') == 8)
        except (OSError, ValueError, AttributeError):
            compatible = False
        available = compatible and self.python.is_file() and (self.model / 'model.safetensors').is_file()
        return {'available': available, 'modelRevision':self.model_revision, 'message': '本地 Qwen3-TTS 已就绪；首次生成需要加载模型。' if available else
                '本地配音环境未就绪或模型不匹配，请按 README 配置 Qwen 0.6B CustomVoice 8bit；不会自动下载。',
                'voices': [{**{k: v for k, v in voice.items() if k != 'sample'},
                            'sampleUrl': f"/api/voices/{voice['id']}/sample" if self.sample(voice['id']) else None} for voice in self.voices]}

    def sample(self, voice_id):
        voice = next((v for v in self.voices if v['id'] == voice_id), None)
        if voice is None:
            return None
        path = self.root / 'samples' / voice['sample']
        return path if path.is_file() and not path.is_symlink() else None

    def synthesize(self, texts, voice, directory):
        if voice not in {v['id'] for v in self.voices} or not self.catalog()['available']:
            raise ValueError('本地配音环境或所选音色不可用，请检查 README 中的目录设置。')
        with self._lock:
            try:
                if self._process is None or self._process.poll() is not None:
                    worker = Path(__file__).parents[1] / 'voice_worker/worker.py'
                    self._process = subprocess.Popen([str(self.python), '-u', str(worker), str(self.model.resolve())],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        text=True, encoding='utf-8', env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                                                        'HF_HUB_DISABLE_TELEMETRY': '1'})
                request = {'texts': texts, 'voice': voice, 'directory': str(directory.resolve())}
                self._process.stdin.write(json.dumps(request, ensure_ascii=False)+'\n')
                self._process.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(self._process.stdout, selectors.EVENT_READ)
                    if not selector.select(300):
                        raise TimeoutError('本地配音超时，请缩短脚本后重试。')
                result = json.loads(self._process.stdout.readline())
                if result.get('status') != 'completed':
                    raise ValueError('本地配音生成失败，请检查环境与脚本后重试。')
                return [directory / f'{i}.wav' for i in range(len(texts))]
            except (OSError, ValueError, TimeoutError) as error:
                self.close()
                raise ValueError(str(error) if isinstance(error, TimeoutError) else '本地配音生成失败，请检查环境与脚本后重试。') from None

    def close(self):
        with self._lock:
            if self._process is not None:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._process.kill(); self._process.wait()
                for stream in (self._process.stdin, self._process.stdout):
                    if stream:
                        stream.close()
                self._process = None


def create_voice_router(service):
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import FileResponse
    router = APIRouter(prefix='/api/voices')

    @router.get('')
    def catalog():
        return service.catalog()

    @router.get('/{voice_id}/sample')
    def sample(voice_id: str):
        path = service.sample(voice_id)
        if path is None:
            raise HTTPException(404, detail={'code':'voice_sample_missing','message':'此音色尚无本地试听，请检查试听目录。'})
        return FileResponse(path, media_type='audio/wav')
    return router
