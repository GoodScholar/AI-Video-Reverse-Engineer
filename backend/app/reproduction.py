"""Versioned reproduction plans; no credentials or provider payloads are persisted."""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .reference_video import validate_storage_id


class OutputSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    strategy: Literal['wan22_i2v', 'wan22_fun_control'] = 'wan22_i2v'
    width: int = Field(default=480, ge=256, le=1280)
    height: int = Field(default=832, ge=256, le=1280)
    frames: int = Field(default=81, ge=17, le=161)
    fps: int = Field(default=16, ge=8, le=24)
    seed: int = Field(default=42, ge=0, le=2**53 - 1)

    @field_validator('width', 'height')
    @classmethod
    def dimensions_are_aligned(cls, value):
        if value % 16:
            raise ValueError('生成尺寸必须为 16 的倍数。')
        return value

    @field_validator('frames')
    @classmethod
    def frames_are_aligned(cls, value):
        if (value - 1) % 4:
            raise ValueError('生成帧数必须为 4n+1，例如 81。')
        return value


class SavedPrompts(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    positiveZh: str = Field(min_length=1, max_length=12000)
    negativeZh: str = Field(min_length=1, max_length=12000)
    positiveEn: str = Field(min_length=1, max_length=12000)
    negativeEn: str = Field(min_length=1, max_length=12000)


class GenerationRun(BaseModel):
    id: str
    promptId: Optional[str] = None
    status: Literal['submitting', 'queued', 'running', 'completed', 'failed', 'unknown']
    createdAt: str
    error: Optional[str] = None
    outputs: list[dict] = Field(default_factory=list)
    revision: int
    sourceHash: str
    comfyUrl: str
    workflow: dict


class ReproductionState(BaseModel):
    revision: int = 0
    sourceHash: Optional[str] = None
    prompts: Optional[SavedPrompts] = None
    settings: OutputSettings = Field(default_factory=OutputSettings)
    comfyUrl: str = 'http://127.0.0.1:8188'
    runs: list[GenerationRun] = Field(default_factory=list)
    lastError: Optional[str] = None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def source_hash(project) -> str:
    media = project.referenceMedia
    task = project.semanticAnalysis
    result = task.result if task else None
    if hasattr(result, 'model_dump'):
        result = result.model_dump(mode='json')
    snapshot = {
        'media': media.id if media else None,
        'analysis': task.id if task else None,
        'result': result,
        'preprocessing': project.localPreprocessing.id if project.localPreprocessing else None,
        'depth': project.activeDepthCaptureId,
    }
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def analysis_ready(project) -> bool:
    task, media, preprocessing = project.semanticAnalysis, project.referenceMedia, project.localPreprocessing
    return bool(media and preprocessing and preprocessing.status == 'completed' and task
                and task.status == 'completed' and task.result
                and task.sourceReferenceMediaId == media.id
                and task.sourcePreprocessingId == preprocessing.id)


def current_depth(project):
    media = project.referenceMedia
    for capture in getattr(project, 'depthCaptures', []):
        if (media and capture.id == project.activeDepthCaptureId and capture.sourceReferenceVideoId == media.id
                and capture.status == 'completed' and capture.qualityAssessment
                and (capture.qualityAssessment.status == 'passed'
                     or capture.qualityAssessment.status == 'review_required' and capture.reviewConfirmedAt)):
            return capture
    return None


def default_settings(project):
    media = project.referenceMedia
    if not media:
        return OutputSettings(), []
    scale = 480 / min(media.width, media.height)
    scale = min(scale, 1280 / max(media.width, media.height))
    width = max(256, round(media.width * scale / 16) * 16)
    height = max(256, round(media.height * scale / 16) * 16)
    settings = OutputSettings(width=width, height=height,
                              strategy='wan22_fun_control' if current_depth(project) else 'wan22_i2v')
    notes = [f'生成尺寸调整为 {width}×{height}，适配模板尺寸要求。',
             '候选模板尚未在你的 ComfyUI 环境验证；首次建议使用 81 帧短片。']
    if media.type == 'video':
        notes.append(f'参考视频 {media.durationSeconds:.2f} 秒；默认生成 {settings.frames / settings.fps:.2f} 秒，避免长片直接占用过多显存。')
    return settings, notes


class ReproductionStore:
    def __init__(self, data_dir: Path):
        self.root = Path(os.path.abspath(data_dir))

    def path(self, project_id, *parts):
        for part in (project_id, *parts):
            validate_storage_id(part)
        path = self.root / 'project-files' / project_id / 'reproduction'
        for part in parts:
            path /= part
        current = self.root
        if current.is_symlink():
            raise OSError('复刻方案存储路径无效。')
        for part in path.relative_to(self.root).parts:
            current /= part
            if current.is_symlink():
                raise OSError('复刻方案存储路径无效。')
        return path

    def load(self, project):
        path = self.path(project.id, 'state.json')
        if not path.exists():
            settings, _ = default_settings(project)
            return ReproductionState(settings=settings)
        return ReproductionState.model_validate_json(path.read_text(encoding='utf-8'))

    def save(self, project_id, state):
        path = self.path(project_id, 'state.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.state-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as target:
                target.write(state.model_dump_json())
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
