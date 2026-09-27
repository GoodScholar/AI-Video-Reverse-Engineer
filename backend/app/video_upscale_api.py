"""Project-scoped, durable local super-resolution jobs."""
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.background import BackgroundTask

from .durable_runs import LOCAL_RUN_POLICY
from .person_controls import PersonArtifactStream, parse_single_byte_range, stream_person_artifact
from .reference_media_storage import managed_reference_media_is_safe, resolve_reference_media_path
from .reference_video import validate_storage_id
from .video_upscale import UpscaleConfig, UpscaleError, execute_upscale, target_dimensions


class StartUpscale(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sourceId: str = Field(min_length=1, max_length=200)
    outputResolution: Literal['1080p', '2k'] = '1080p'
    scale: Optional[Literal[2, 4]] = None

    @model_validator(mode='after')
    def reject_mixed_targets(self):
        if self.scale is not None and 'outputResolution' in self.model_fields_set:
            raise ValueError('不能同时指定倍率与目标清晰度。')
        return self


class UpscaleOutput(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frameRate: float = Field(gt=0)
    durationSeconds: float = Field(gt=0)


class UpscaleRun(BaseModel):
    id: str
    sourceId: str
    scale: Optional[Literal[2, 4]] = None
    outputResolution: Optional[Literal['1080p', '2k']] = None
    status: Literal['queued', 'running', 'completed', 'failed']
    stage: str
    progress: int = Field(ge=0, le=100)
    error: Optional[str] = None
    output: Optional[UpscaleOutput] = None
    createdAt: str


def create_upscale_router(data_dir, get_project, compute_queue, *, config=None, source_lock=None):
    config = config or UpscaleConfig.local()
    root = Path(data_dir).absolute()
    lock = RLock()
    router = APIRouter(prefix='/api/projects/{project_id}/upscale')

    def fail(message, status=409):
        raise HTTPException(status_code=status, detail={'code': 'video_upscale_error', 'message': message})

    def safe_path(project_id, *parts):
        try:
            for part in (project_id, *parts):
                validate_storage_id(part)
            path = root / 'project-files' / project_id / 'upscale'
            path = path.joinpath(*parts)
            current = root
            if current.is_symlink():
                raise ValueError('symlink')
            for part in path.relative_to(root).parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError('symlink')
            path.resolve().relative_to(root.resolve())
            return path
        except (ValueError, OSError):
            fail('超分产物路径不可用。', 404)

    def load(project_id, rid):
        try:
            path = safe_path(project_id, rid, 'state.json')
            run = UpscaleRun.model_validate_json(path.read_text()).model_dump()
            if run['id'] != rid:
                raise ValueError('id mismatch')
            return run
        except FileNotFoundError:
            fail('超分任务不存在。', 404)
        except (OSError, ValueError):
            fail('超分任务记录不可用。', 503)

    def save(project_id, run):
        path = safe_path(project_id, run['id'], 'state.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.state-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(run, handle, ensure_ascii=False, allow_nan=False)
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def runs(project_id):
        directory = safe_path(project_id)
        if not directory.exists():
            return []
        return sorted([load(project_id, p.parent.name) for p in directory.glob('*/state.json')],
                      key=lambda r: r['createdAt'], reverse=True)

    def clean_temporary(project_id, rid):
        directory = safe_path(project_id, rid)
        for name in ('input.mp4', 'silent.mp4', 'output.part.mp4'):
            safe_path(project_id, rid, name).unlink(missing_ok=True)
        scratch = safe_path(project_id, rid, 'frames')
        if scratch.exists():
            shutil.rmtree(scratch)

    # Reconcile once during service initialization, never during a status read.
    base = root / 'project-files'
    if base.is_dir() and not base.is_symlink():
        for state in base.glob('*/upscale/*/state.json'):
            pid, rid = state.parents[2].name, state.parent.name
            try:
                run = load(pid, rid)
                recovery = LOCAL_RUN_POLICY.recover_after_restart(run['status'])
                if recovery.reason == 'interrupted':
                    run.update(status=recovery.status, error='服务重启导致超分任务中断，请重新开始。')
                    save(pid, run)
                    clean_temporary(pid, rid)
            except (OSError, HTTPException):
                continue

    def process(project_id, rid):
        def update(stage, percent):
            with lock:
                run = load(project_id, rid)
                run.update(status='running', stage=stage, progress=percent)
                save(project_id, run)
        try:
            update('preparing', 0)
            with lock:
                run = load(project_id, rid)
            directory = safe_path(project_id, rid)
            output = execute_upscale(directory / 'input.mp4', directory, run['outputResolution'] or run['scale'], config, update)
            with lock:
                run = load(project_id, rid)
                run.update(status='completed', stage='completed', progress=100, output=output)
                save(project_id, run)
        except Exception as error:
            with lock:
                run = load(project_id, rid)
                run.update(status='failed', error=str(error) if isinstance(error, UpscaleError) else '超分任务失败，请检查本地环境后重试。')
                save(project_id, run)
        finally:
            clean_temporary(project_id, rid)

    @router.get('')
    def get_status(project_id: str):
        get_project(project_id)
        with lock:
            return {'environment': config.environment(), 'runs': runs(project_id)}

    @router.post('', status_code=202)
    def start(project_id: str, body: StartUpscale):
        with lock, source_lock if source_lock is not None else nullcontext():
            project = get_project(project_id)
            reference = project.referenceMedia
            if reference is None or reference.type != 'video' or reference.id != body.sourceId:
                fail('请刷新页面并选择当前参考视频。')
            if not config.environment()['available']:
                fail(config.environment()['message'])
            if any(LOCAL_RUN_POLICY.active(r['status']) for r in runs(project_id)):
                fail('此项目已有超分任务正在排队或运行。')
            try:
                target_dimensions(reference.width, reference.height, body.scale or body.outputResolution)
            except UpscaleError as error:
                fail(str(error))
            if not managed_reference_media_is_safe(root, project_id, reference):
                fail('参考视频文件不可用。')
            run = UpscaleRun(id=str(uuid4()), sourceId=body.sourceId, scale=body.scale,
                outputResolution=body.outputResolution if body.scale is None else None,
                status=LOCAL_RUN_POLICY.initial_status, stage='queued', progress=0, createdAt=datetime.now(timezone.utc).isoformat()).model_dump()
            directory = safe_path(project_id, run['id'])
            try:
                directory.mkdir(parents=True)
                shutil.copyfile(resolve_reference_media_path(root, project_id, reference), directory / 'input.mp4')
                save(project_id, run)
            except OSError:
                shutil.rmtree(directory, ignore_errors=True)
                fail('无法保存超分任务或输入快照，请检查磁盘空间。', 503)
            if not compute_queue.submit('video_upscale', project_id, lambda pid: process(pid, run['id'])):
                run.update(status=LOCAL_RUN_POLICY.fail(run['status']), error='本地计算队列不可用，请重试。')
                save(project_id, run); clean_temporary(project_id, run['id'])
                fail(run['error'], 503)
            return run

    @router.get('/{run_id}/video')
    def video(project_id: str, run_id: str, request: Request, download: bool = False):
        get_project(project_id)
        with lock:
            run = load(project_id, run_id)
            if run['status'] != 'completed':
                fail('超分结果尚未完成。')
            path = safe_path(project_id, run_id, 'output.mp4')
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            except OSError:
                fail('超分视频不存在。', 404)
        size = os.fstat(descriptor).st_size
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or not size:
            os.close(descriptor); fail('超分视频不可用。', 404)
        try:
            start, length, partial = parse_single_byte_range(request.headers.get('range'), size)
        except ValueError:
            os.close(descriptor)
            raise HTTPException(status_code=416, headers={'Content-Range': f'bytes */{size}'})
        stream = PersonArtifactStream(descriptor, start, length)
        headers = {'Accept-Ranges': 'bytes', 'Content-Length': str(length)}
        if partial:
            headers['Content-Range'] = f'bytes {start}-{start + length - 1}/{size}'
        if download:
            headers['Content-Disposition'] = f'attachment; filename="upscaled-{run_id}.mp4"'
        return StreamingResponse(stream_person_artifact(stream), status_code=206 if partial else 200,
            media_type='video/mp4', headers=headers, background=BackgroundTask(stream.aclose))

    return router
