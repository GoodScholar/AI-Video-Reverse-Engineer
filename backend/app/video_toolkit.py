"""Local video tool adapters and project-scoped asset discovery."""
from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .video_upscale import UpscaleConfig

WORKER = Path(__file__).resolve().parents[1] / 'toolkit_worker' / 'run_tool.py'
KINDS = ('scenes', 'subtitles', 'mask', 'interpolate', 'upscale')
ARTIFACTS = {'scenes.json', 'subtitles.srt', 'subtitles.json', 'mask.mp4', 'overlay.mp4', 'tracking.json', 'output.mp4', 'metadata.json'}


class ToolkitError(Exception):
    pass


class ToolkitCancelled(Exception):
    pass


def safe_child(root: Path, *parts: str) -> Path:
    from .reference_video import validate_storage_id
    root = Path(os.path.abspath(root))
    if root.is_symlink():
        raise ValueError('unsafe root')
    current = root
    for part in parts:
        validate_storage_id(part)
        current /= part
        if current.is_symlink():
            raise ValueError('unsafe path')
    current.resolve().relative_to(root.resolve())
    return current


def validate_cuts(cuts, duration):
    if not math.isfinite(duration) or duration <= 0 or len(cuts) > 3000:
        raise ValueError('镜头时长或切点数量无效。')
    last = 0
    for cut in cuts:
        if isinstance(cut, bool) or not math.isfinite(cut) or not last < cut < duration:
            raise ValueError('切点必须严格递增，且位于视频起止时间之间。')
        last = cut
    return list(cuts)


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def asset_paths(root, project):
    """Server-owned IDs; callers never supply a filesystem path."""
    from .reference_media_storage import managed_reference_media_is_safe, resolve_reference_media_path
    root = Path(root)
    assets = {}
    pid = project.id
    base = safe_child(root, 'project-files', pid)
    media = project.referenceMedia
    if media and media.type == 'video' and managed_reference_media_is_safe(root, pid, media):
        path = resolve_reference_media_path(root, pid, media)
        assets[f'reference:{media.id}'] = (path, getattr(media, 'originalName', '参考视频'))
    for feature in ('upscale', 'toolkit'):
        directory = safe_child(base, feature)
        for record in directory.glob('*/state.json') if directory.exists() else []:
            try:
                rid = record.parent.name
                run = _read(safe_child(directory, rid, 'state.json'))
                if run.get('id') != rid or run.get('status') != 'completed':
                    continue
                if feature == 'toolkit' and 'output.mp4' not in run.get('artifacts', []):
                    continue
                path = safe_child(directory, rid, 'output.mp4')
                if path.is_file():
                    assets[f'{feature}:{rid}'] = (path, f'{"超分" if feature == "upscale" else "处理结果"} · {rid[:8]}')
            except (ValueError, OSError, TypeError, KeyError):
                continue
    pipeline_directory = safe_child(base, 'toolkit', 'pipelines')
    for record in pipeline_directory.glob('*/state.json') if pipeline_directory.exists() else []:
        try:
            pipeline_id = record.parent.name
            pipeline = _read(safe_child(pipeline_directory, pipeline_id, 'state.json'))
            if pipeline.get('id') != pipeline_id or pipeline.get('status') != 'completed':
                continue
            output = next((step for step in reversed(pipeline.get('steps', []))
                           if step.get('kind') in ('interpolate', 'upscale') and 'output.mp4' in step.get('artifacts', [])), None)
            if output is None:
                continue
            path = safe_child(pipeline_directory, pipeline_id, output['directory'], 'output.mp4')
            if path.is_file():
                assets[f'pipeline:{pipeline_id}'] = (path, f'自动处理结果 · {pipeline_id[:8]}')
        except (ValueError, OSError, TypeError, KeyError):
            continue
    for feature in ('reproduction', 'character-motion'):
        try:
            directory = safe_child(base, feature)
            state = _read(safe_child(directory, 'state.json'))
            for run in state.get('runs', []):
                if run.get('status') != 'completed':
                    continue
                for index, output in enumerate(run.get('outputs', [])):
                    name = output['filename']
                    if Path(name).suffix.lower() not in ('.mp4', '.webm', '.mov'):
                        continue
                    parts = ('runs', run['id'], 'outputs', name) if feature == 'character-motion' else (run['id'], name)
                    path = safe_child(directory, *parts)
                    if path.is_file():
                        assets[f'{feature}:{run["id"]}:{index}'] = (path, f'生成结果 · {run["id"][:8]} · {index + 1}')
        except (ValueError, OSError, TypeError, KeyError):
            continue
    return assets


def list_assets(root, project):
    from urllib.parse import quote
    return [{'id': key, 'label': label, 'url': f'/api/projects/{project.id}/toolkit/assets/{quote(key, safe="")}/video'}
            for key, (_, label) in asset_paths(root, project).items()]


@dataclass(frozen=True)
class ToolkitConfig:
    python: str
    sam_python: str
    sam_checkpoint: str
    sam_config: str
    whisper: str
    whisper_model: str
    rife: str
    rife_models: str
    ffmpeg: str = 'ffmpeg'
    ffprobe: str = 'ffprobe'

    @classmethod
    def local(cls, ffmpeg='ffmpeg', ffprobe='ffprobe'):
        worker_root = WORKER.parent
        return cls(os.environ.get('VIDEO_TOOLKIT_PYTHON', str(worker_root / '.venv/bin/python')),
                   os.environ.get('SAM2_PYTHON', str(worker_root / 'sam2-venv/bin/python')),
                   os.environ.get('SAM2_CHECKPOINT', ''), os.environ.get('SAM2_CONFIG', 'configs/sam2.1/sam2.1_hiera_t.yaml'),
                   os.environ.get('WHISPER_BINARY', 'whisper-cli'), os.environ.get('WHISPER_MODEL', ''),
                   os.environ.get('RIFE_BINARY', 'rife-ncnn-vulkan'), os.environ.get('RIFE_MODELS', ''), ffmpeg, ffprobe)

    def environment(self):
        media = bool(shutil.which(self.ffmpeg) and shutil.which(self.ffprobe))
        def file(value): return bool(value and Path(value).is_file())
        scenes = bool(shutil.which(self.python))
        if scenes:
            try:
                probe = subprocess.run([self.python, '-c', 'import importlib.util,sys;sys.exit(0 if importlib.util.find_spec("scenedetect") else 1)'], capture_output=True, timeout=5)
                scenes = probe.returncode == 0
            except (OSError, subprocess.SubprocessError):
                scenes = False
        values = {
            'scenes': (scenes, '配置 VIDEO_TOOLKIT_PYTHON，并安装 PySceneDetect/OpenCV。'),
            'subtitles': (bool(shutil.which(self.whisper)) and file(self.whisper_model), '配置 WHISPER_BINARY 和 WHISPER_MODEL（多语言模型）。'),
            'mask': (bool(shutil.which(self.sam_python)) and file(self.sam_checkpoint), '配置 SAM2_PYTHON、SAM2_CHECKPOINT 和 SAM2_CONFIG。'),
            'interpolate': (bool(shutil.which(self.rife)) and file(str(Path(self.rife_models)/'flownet.param')) and file(str(Path(self.rife_models)/'flownet.bin')), '配置 RIFE_BINARY 和 RIFE_MODELS，使用支持任意时间步的 RIFE v4 模型。'),
        }
        result = {key: {'available': bool(ready and media), 'message': '依赖文件已就绪，执行时验证模型。' if ready and media else instruction + ' 需 FFmpeg。'} for key, (ready, instruction) in values.items()}
        result['upscale'] = UpscaleConfig.local(self.ffmpeg, self.ffprobe).environment()
        return result


def execute_tool(source, directory, kind, params, config, cancel, progress):
    from dataclasses import asdict
    request = {'source': str(source), 'directory': str(directory), 'kind': kind, 'params': params, 'config': asdict(config)}
    request_path = directory / 'request.json'
    request_path.write_text(json.dumps(request), encoding='utf-8')
    python = config.python if kind == 'scenes' else config.sam_python if kind == 'mask' else sys.executable
    process = None
    try:
        with (directory / 'worker.log').open('wb') as log:
            process = subprocess.Popen([python, str(WORKER), str(request_path)], stdout=log, stderr=log, start_new_session=True)
            start = time.monotonic()
            while process.poll() is None:
                if cancel():
                    raise ToolkitCancelled()
                if time.monotonic() - start > 7200:
                    raise ToolkitError('处理超过两小时，已终止；请缩短素材后重试。')
                progress('processing', None)
                time.sleep(.25)
            if cancel():
                raise ToolkitCancelled()
            if process.returncode:
                error_path = directory / 'error.json'
                message = _read(error_path).get('message') if error_path.is_file() else None
                raise ToolkitError(message or '处理失败，请检查工具依赖、模型和显存。')
        result = _read(directory / 'result.json')
        names = result['artifacts']
        if not names or any(name not in ARTIFACTS or not safe_child(directory, name).is_file() or safe_child(directory,name).stat().st_size == 0 for name in names):
            raise ToolkitError('处理工具未生成有效产物。')
        return names
    finally:
        if process is not None and process.poll() is None:
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=5)
