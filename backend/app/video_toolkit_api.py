"""Durable, cancellable local tool jobs with immutable input snapshots."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

from .durable_runs import LOCAL_RUN_POLICY
from .person_controls import PersonArtifactStream, parse_single_byte_range, stream_person_artifact
from .video_toolkit import ARTIFACTS, ToolkitCancelled, ToolkitConfig, ToolkitError, asset_paths, execute_tool, list_assets, safe_child, validate_cuts


class ToolParams(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    language: Literal['auto', 'zh', 'en', 'ja', 'ko'] = 'auto'
    threshold: float = Field(default=3, ge=1, le=20)
    minSceneSeconds: float = Field(default=.5, ge=.1, le=10)
    fps: Literal[30, 60] = 60
    resolution: Literal['1080p', '2k'] = '1080p'
    points: list[list[float]] = Field(default_factory=list, max_length=32)
    @field_validator('points')
    @classmethod
    def points_valid(cls, points):
        if any(len(p) != 3 or not 0 <= p[0] <= 1 or not 0 <= p[1] <= 1 or p[2] not in (0,1) for p in points):
            raise ValueError('选点必须是归一化 x、y 与前景/背景标签。')
        return points


class StartTools(BaseModel):
    model_config = ConfigDict(extra='forbid')
    assetIds: list[str] = Field(min_length=1, max_length=12)
    kind: Literal['scenes', 'subtitles', 'mask', 'interpolate', 'upscale']
    params: ToolParams = Field(default_factory=ToolParams)


class EditCuts(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    revision: int = Field(ge=0)
    cuts: list[float] = Field(max_length=3000)


class PipelineStepRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['scenes', 'subtitles', 'mask', 'interpolate', 'upscale']
    params: ToolParams = Field(default_factory=ToolParams)


class StartPipeline(BaseModel):
    model_config = ConfigDict(extra='forbid')
    assetId: str = Field(min_length=1)
    steps: list[PipelineStepRequest] = Field(min_length=2, max_length=5)


def create_toolkit_router(data_dir, get_project, compute_queue, *, config=None, runner=None, environment=None, source_lock=None):
    root = Path(data_dir).absolute()
    config = config or ToolkitConfig.local()
    runner = runner or execute_tool
    environment = environment or config.environment
    lock = RLock()
    router = APIRouter(prefix='/api/projects/{project_id}/toolkit')

    def fail(message, code=409):
        raise HTTPException(code, detail={'code':'video_toolkit_error', 'message':message})

    def path(pid, *parts):
        try: return safe_child(root, 'project-files', pid, 'toolkit', *parts)
        except (ValueError, OSError): fail('工具素材路径不可用。',404)

    def atomic(file, value):
        file.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=file.parent, prefix='.state-')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as handle:
                json.dump(value,handle,ensure_ascii=False,allow_nan=False);handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,file)
        finally: Path(temporary).unlink(missing_ok=True)

    def save(pid, run): atomic(path(pid,run['id'],'state.json'),run)
    def load(pid,rid):
        try:
            run=json.loads(path(pid,rid,'state.json').read_text())
            if run['id']!=rid or run['status'] not in ('queued','running','completed','failed','cancelled'): raise ValueError()
            return run
        except FileNotFoundError: fail('处理任务不存在。',404)
        except (ValueError,KeyError,OSError): fail('处理任务记录损坏。',503)

    def runs(pid):
        directory=path(pid)
        values=[]
        for item in directory.glob('*/state.json') if directory.exists() else []:
            values.append(load(pid,item.parent.name))
        return sorted(values,key=lambda r:r['createdAt'],reverse=True)

    pipeline_statuses = ('queued', 'running', 'completed', 'failed', 'cancelled')
    step_statuses = pipeline_statuses + ('blocked',)

    def pipeline_path(pid, pipeline_id, *parts):
        return path(pid, 'pipelines', pipeline_id, *parts)

    def save_pipeline(pid, pipeline):
        atomic(pipeline_path(pid, pipeline['id'], 'state.json'), pipeline)

    def load_pipeline(pid, pipeline_id):
        try:
            pipeline = json.loads(pipeline_path(pid, pipeline_id, 'state.json').read_text())
            if (pipeline['id'] != pipeline_id or pipeline['status'] not in pipeline_statuses
                    or not isinstance(pipeline['steps'], list)
                    or any(step.get('status') not in step_statuses for step in pipeline['steps'])):
                raise ValueError()
            return pipeline
        except FileNotFoundError:
            fail('处理流水线不存在。', 404)
        except (ValueError, KeyError, OSError, TypeError):
            fail('处理流水线记录损坏。', 503)

    def pipelines(pid):
        directory = path(pid, 'pipelines')
        values = []
        for item in directory.glob('*/state.json') if directory.exists() else []:
            values.append(load_pipeline(pid, item.parent.name))
        return sorted(values, key=lambda value: value['createdAt'], reverse=True)

    def active_count(pid):
        return (len([run for run in runs(pid) if run['status'] in ('queued', 'running')])
                + len([pipeline for pipeline in pipelines(pid) if pipeline['status'] in ('queued', 'running')]))

    def block_downstream(pipeline, after):
        for step in pipeline['steps'][after + 1:]:
            if step['status'] == 'queued':
                step['status'] = 'blocked'

    def pipeline_input(pid, pipeline, index):
        kind = pipeline['steps'][index]['kind']
        if kind in ('scenes', 'subtitles', 'mask'):
            return pipeline_path(pid, pipeline['id'], 'input.mp4')
        for previous in reversed(pipeline['steps'][:index]):
            if previous['kind'] in ('interpolate', 'upscale') and previous['status'] == 'completed':
                return pipeline_path(pid, pipeline['id'], previous['directory'], 'output.mp4')
        return pipeline_path(pid, pipeline['id'], 'input.mp4')

    def next_pipeline_step(pipeline):
        for index, step in enumerate(pipeline['steps']):
            if step['status'] == 'queued':
                return index
        return None

    base=root/'project-files'
    if base.is_dir() and not base.is_symlink():
        for file in base.glob('*/toolkit/*/state.json'):
            try:
                pid,rid=file.parents[2].name,file.parent.name
                run=load(pid,rid)
                recovery = LOCAL_RUN_POLICY.recover_after_restart(run['status'])
                if recovery.reason == 'interrupted':
                    run.update(status=recovery.status,error='服务重启中断了处理，可重试。');save(pid,run)
            except (HTTPException,OSError): continue
        for file in base.glob('*/toolkit/pipelines/*/state.json'):
            try:
                pid, pipeline_id = file.parents[3].name, file.parent.name
                pipeline = load_pipeline(pid, pipeline_id)
                recovered_in_flight = any(step.pop('inFlight', False) for step in pipeline['steps'])
                recovery = LOCAL_RUN_POLICY.recover_after_restart(pipeline['status'])
                if recovery.reason == 'interrupted':
                    for index, step in enumerate(pipeline['steps']):
                        if step['status'] in ('queued', 'running'):
                            step.update(status='failed', error='服务重启中断了处理，可重试。', inFlight=False)
                            block_downstream(pipeline, index)
                            break
                    for step in pipeline['steps']:
                        step['inFlight'] = False
                    pipeline.update(status=recovery.status, error='服务重启中断了处理，可重试。')
                    save_pipeline(pid, pipeline)
                elif recovered_in_flight:
                    save_pipeline(pid, pipeline)
            except (HTTPException, OSError):
                continue

    def shutdown():
        # Signal children before the shared executor waits for them.
        with lock:
            if base.is_dir() and not base.is_symlink():
                for file in base.glob('*/toolkit/*/state.json'):
                    try:
                        pid,rid=file.parents[2].name,file.parent.name
                        run=load(pid,rid)
                        if LOCAL_RUN_POLICY.active(run['status']):
                            run.update(status=LOCAL_RUN_POLICY.cancel(run['status']),stage='cancelled',error='服务关闭，处理已取消，可重试。')
                            save(pid,run)
                    except (HTTPException,OSError):continue
                for file in base.glob('*/toolkit/pipelines/*/state.json'):
                    try:
                        pid, pipeline_id = file.parents[3].name, file.parent.name
                        pipeline = load_pipeline(pid, pipeline_id)
                        if LOCAL_RUN_POLICY.active(pipeline['status']):
                            for index, step in enumerate(pipeline['steps']):
                                if step['status'] in ('queued', 'running'):
                                    step.update(status='cancelled', error='服务关闭，处理已取消，可重试。')
                                    block_downstream(pipeline, index)
                                    break
                            pipeline.update(status=LOCAL_RUN_POLICY.cancel(pipeline['status']), error='服务关闭，处理已取消，可重试。')
                            save_pipeline(pid, pipeline)
                    except (HTTPException, OSError):
                        continue

    router.add_event_handler('shutdown', shutdown)

    def clean_work(pid,rid):
        directory=path(pid,rid)
        # Retain source snapshots for retry and synchronized comparison.
        for name in ('frames','masks','rife-frames'):
            target=path(pid,rid,name)
            if target.is_dir(): shutil.rmtree(target)
        for name in ('audio.wav','silent.mp4','output.part.mp4'):
            path(pid,rid,name).unlink(missing_ok=True)

    def process(pid,rid):
        def cancelled():
            with lock: return load(pid,rid)['status']=='cancelled'
        def progress(stage,percent):
            # No fabricated numerical progress for external model inference.
            with lock:
                run=load(pid,rid)
                if run['status']=='running' and run['stage']!=stage:
                    run.update(stage=stage);save(pid,run)
        try:
            with lock:
                run=load(pid,rid)
                if run['status']=='cancelled':return
                run.update(status='running',stage='preparing');save(pid,run)
            artifacts=runner(path(pid,rid,'input.mp4'),path(pid,rid),run['kind'],run['params'],config,cancelled,progress)
            with lock:
                run=load(pid,rid)
                if run['status']=='cancelled':return
                if not artifacts or any(name not in ARTIFACTS or not path(pid,rid,name).is_file() for name in artifacts):
                    raise ToolkitError('处理产物不完整。')
                run.update(status='completed',stage='completed',artifacts=artifacts);save(pid,run)
        except ToolkitCancelled:
            with lock:
                run=load(pid,rid);run.update(status='cancelled',stage='cancelled');save(pid,run)
        except Exception as error:
            with lock:
                run=load(pid,rid)
                if run['status']!='cancelled':
                    run.update(status='failed',error=str(error) if isinstance(error,ToolkitError) else '处理失败，请检查本地工具环境和素材。');save(pid,run)
        finally: clean_work(pid,rid)

    def clean_pipeline_work(pid, pipeline_id, step, preserve_artifacts):
        directory = pipeline_path(pid, pipeline_id, step['directory'])
        for name in ('frames', 'masks', 'rife-frames'):
            target = pipeline_path(pid, pipeline_id, step['directory'], name)
            if target.is_dir():
                shutil.rmtree(target)
        for name in ('audio.wav', 'silent.mp4', 'output.part.mp4', 'request.json', 'result.json', 'error.json', 'worker.log'):
            pipeline_path(pid, pipeline_id, step['directory'], name).unlink(missing_ok=True)
        if not preserve_artifacts:
            for name in ARTIFACTS:
                pipeline_path(pid, pipeline_id, step['directory'], name).unlink(missing_ok=True)

    def process_pipeline(pid, pipeline_id, index):
        def cancelled():
            with lock:
                return load_pipeline(pid, pipeline_id)['status'] == 'cancelled'

        def progress(stage, percent):
            with lock:
                pipeline = load_pipeline(pid, pipeline_id)
                step = pipeline['steps'][index]
                if pipeline['status'] == 'running' and step['status'] == 'running' and step['stage'] != stage:
                    step['stage'] = stage
                    save_pipeline(pid, pipeline)

        should_enqueue = None
        try:
            with lock:
                pipeline = load_pipeline(pid, pipeline_id)
                if pipeline['status'] == 'cancelled' or pipeline['steps'][index]['status'] == 'cancelled':
                    return
                step = pipeline['steps'][index]
                pipeline.update(status='running', error=None)
                step.update(status='running', stage='preparing', error=None)
                save_pipeline(pid, pipeline)
                source = pipeline_input(pid, pipeline, index)
                directory = pipeline_path(pid, pipeline_id, step['directory'])
            clean_pipeline_work(pid, pipeline_id, step, preserve_artifacts=False)
            artifacts = runner(source, directory, step['kind'], step['params'], config, cancelled, progress)
            with lock:
                pipeline = load_pipeline(pid, pipeline_id)
                step = pipeline['steps'][index]
                if pipeline['status'] == 'cancelled':
                    return
                if (not artifacts or any(name not in ARTIFACTS or not pipeline_path(pid, pipeline_id, step['directory'], name).is_file() for name in artifacts)):
                    raise ToolkitError('处理产物不完整。')
                step.update(status='completed', stage='completed', artifacts=artifacts)
                following = next_pipeline_step(pipeline)
                if following is None:
                    pipeline.update(status='completed', error=None)
                else:
                    pipeline.update(status='queued', error=None)
                    should_enqueue = following
                save_pipeline(pid, pipeline)
        except ToolkitCancelled:
            with lock:
                pipeline = load_pipeline(pid, pipeline_id)
                step = pipeline['steps'][index]
                step.update(status='cancelled', stage='cancelled')
                block_downstream(pipeline, index)
                pipeline.update(status='cancelled', error='处理已取消，可重试。')
                save_pipeline(pid, pipeline)
        except Exception as error:
            with lock:
                pipeline = load_pipeline(pid, pipeline_id)
                if pipeline['status'] != 'cancelled':
                    step = pipeline['steps'][index]
                    step.update(status='failed', stage='failed', error=str(error) if isinstance(error, ToolkitError) else '处理失败，请检查本地工具环境和素材。')
                    block_downstream(pipeline, index)
                    pipeline.update(status='failed', error=step['error'])
                    save_pipeline(pid, pipeline)
        finally:
            with lock:
                pipeline = load_pipeline(pid, pipeline_id)
                step = pipeline['steps'][index]
                preserve_artifacts = step['status'] == 'completed'
            clean_pipeline_work(pid, pipeline_id, step, preserve_artifacts)
            with lock:
                pipeline = load_pipeline(pid, pipeline_id)
                step = pipeline['steps'][index]
                if step.get('inFlight'):
                    step['inFlight'] = False
                    save_pipeline(pid, pipeline)
        if should_enqueue is not None:
            with lock:
                pipeline = load_pipeline(pid, pipeline_id)
                next_ready = pipeline['status'] == 'queued' and pipeline['steps'][should_enqueue]['status'] == 'queued'
            if next_ready:
                enqueue_pipeline(pid, pipeline_id, should_enqueue)

    def snapshot(source,target):
        descriptor=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(descriptor,'rb') as src:
            info=os.fstat(src.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>1024*1024*1024: fail('素材不可读或超过 1 GB。')
            with target.open('xb') as dst: shutil.copyfileobj(src,dst)

    def create(pid,source,asset_id,label,kind,params):
        rid=str(uuid4());directory=path(pid,rid);directory.mkdir(parents=True)
        try: snapshot(source,path(pid,rid,'input.mp4'))
        except BaseException:
            shutil.rmtree(directory);raise
        run={'id':rid,'assetId':asset_id,'label':label,'kind':kind,'params':params,'status':'queued','stage':'queued','error':None,
             'artifacts':[],'revision':0,'createdAt':datetime.now(timezone.utc).isoformat()}
        save(pid,run)
        return run

    def enqueue(pid,run):
        rid=run['id']
        if not compute_queue.submit('toolkit:'+rid,pid,lambda p:process(p,rid)):
            run.update(status='failed',error='本地处理队列不可用，请重试。');save(pid,run)

    def enqueue_pipeline(pid, pipeline_id, index):
        with lock:
            pipeline = load_pipeline(pid, pipeline_id)
            step = pipeline['steps'][index]
            if step.get('inFlight') or pipeline['status'] == 'cancelled':
                return
            step['inFlight'] = True
            save_pipeline(pid, pipeline)
        if not compute_queue.submit(f'toolkit-pipeline:{pipeline_id}:{index}', pid, lambda project_id: process_pipeline(project_id, pipeline_id, index)):
            with lock:
                pipeline = load_pipeline(pid, pipeline_id)
                step = pipeline['steps'][index]
                step['inFlight'] = False
                if pipeline['status'] != 'cancelled':
                    step.update(status='failed', error='本地处理队列不可用，请重试。')
                    block_downstream(pipeline, index)
                    pipeline.update(status='failed', error=step['error'])
                    save_pipeline(pid, pipeline)

    @router.get('')
    def state(project_id: str):
        project=get_project(project_id)
        with lock:
            return {'assets':list_assets(root,project),'runs':runs(project_id),'pipelines':pipelines(project_id),'environment':environment()}

    def validate_pipeline_steps(steps, values):
        order = {'scenes': 0, 'subtitles': 1, 'mask': 2, 'interpolate': 3, 'upscale': 4}
        kinds = [step.kind for step in steps]
        if kinds != sorted(kinds, key=order.__getitem__) or len(set(kinds)) != len(kinds):
            fail('流水线步骤必须按分析、补帧、超分的固定顺序排列，且不可重复。', 422)
        for step in steps:
            if not values[step.kind]['available']:
                fail(values[step.kind]['message'])
            if step.kind == 'mask' and not any(point[2] == 1 for point in step.params.points):
                fail('主体跟踪步骤至少需要一个前景选点。', 422)

    def create_pipeline(pid, source, asset_id, label, steps):
        pipeline_id = str(uuid4())
        directory = pipeline_path(pid, pipeline_id)
        directory.mkdir(parents=True)
        try:
            snapshot(source, pipeline_path(pid, pipeline_id, 'input.mp4'))
            saved_steps = []
            for index, requested in enumerate(steps):
                name = f'{index:02d}-{requested.kind}'
                pipeline_path(pid, pipeline_id, name).mkdir()
                saved_steps.append({'id': str(uuid4()), 'kind': requested.kind, 'params': requested.params.model_dump(),
                                    'directory': name, 'status': 'queued', 'stage': 'queued', 'error': None, 'artifacts': [], 'inFlight': False})
            pipeline = {'id': pipeline_id, 'assetId': asset_id, 'label': label, 'status': 'queued', 'error': None,
                        'steps': saved_steps, 'createdAt': datetime.now(timezone.utc).isoformat()}
            save_pipeline(pid, pipeline)
            return pipeline
        except BaseException:
            shutil.rmtree(directory)
            raise

    @router.post('/pipelines', status_code=202)
    def start_pipeline(project_id: str, body: StartPipeline):
        with source_lock or nullcontext(), lock:
            project = get_project(project_id)
            values = environment()
            validate_pipeline_steps(body.steps, values)
            assets = asset_paths(root, project)
            if body.assetId not in assets:
                fail('素材已变更或不可用，请刷新列表。')
            if active_count(project_id) >= 12:
                fail('每个项目最多排队 12 个处理任务。')
            if any(pipeline['assetId'] == body.assetId and pipeline['status'] in ('queued', 'running') for pipeline in pipelines(project_id)):
                fail('同一份素材已有进行中的流水线。')
            source, label = assets[body.assetId]
            pipeline = create_pipeline(project_id, source, body.assetId, label, body.steps)
        enqueue_pipeline(project_id, pipeline['id'], 0)
        return {'pipeline': pipeline}

    @router.post('/pipelines/{pipeline_id}/cancel')
    def cancel_pipeline(project_id: str, pipeline_id: str):
        get_project(project_id)
        with lock:
            pipeline = load_pipeline(project_id, pipeline_id)
            if pipeline['status'] not in ('queued', 'running'):
                fail('流水线已结束。')
            index = next((number for number, step in enumerate(pipeline['steps']) if step['status'] in ('queued', 'running')), None)
            if index is not None:
                pipeline['steps'][index].update(status='cancelled', stage='cancelled')
                block_downstream(pipeline, index)
            pipeline.update(status='cancelled', error='处理已取消，可重试。')
            save_pipeline(project_id, pipeline)
            return pipeline

    @router.post('/pipelines/{pipeline_id}/retry', status_code=202)
    def retry_pipeline(project_id: str, pipeline_id: str):
        get_project(project_id)
        with lock:
            pipeline = load_pipeline(project_id, pipeline_id)
            if pipeline['status'] not in ('failed', 'cancelled'):
                fail('只有失败或取消的流水线可以重试。')
            if active_count(project_id) >= 12:
                fail('队列已满。')
            if any(other['id'] != pipeline_id and other['assetId'] == pipeline['assetId'] and other['status'] in ('queued', 'running') for other in pipelines(project_id)):
                fail('同一份素材已有进行中的流水线。')
            index = next((number for number, step in enumerate(pipeline['steps']) if step['status'] in ('failed', 'cancelled')), None)
            if index is None:
                fail('没有可重试的步骤。')
            if pipeline['steps'][index].get('inFlight'):
                fail('取消中的步骤仍在停止，请稍后刷新后重试。')
            values = environment()
            for step in pipeline['steps'][index:]:
                if step['status'] not in ('blocked', 'queued', 'failed', 'cancelled'):
                    continue
                if not values[step['kind']]['available']:
                    fail(values[step['kind']]['message'])
            pipeline['steps'][index].update(status='queued', stage='queued', error=None)
            for step in pipeline['steps'][index + 1:]:
                if step['status'] == 'blocked':
                    step.update(status='queued', stage='queued', error=None)
            pipeline.update(status='queued', error=None)
            save_pipeline(project_id, pipeline)
        enqueue_pipeline(project_id, pipeline_id, index)
        return pipeline

    @router.post('/runs',status_code=202)
    def start(project_id: str, body: StartTools):
        with source_lock or nullcontext(),lock:
            project=get_project(project_id)
            if not environment()[body.kind]['available']: fail(environment()[body.kind]['message'])
            if body.kind=='mask' and not any(p[2]==1 for p in body.params.points): fail('请在首帧至少标记一个主体前景点。')
            if body.kind=='mask' and len(body.assetIds)!=1: fail('主体选点一次只能应用于一份素材。')
            if len(set(body.assetIds))!=len(body.assetIds):fail('批量列表包含重复素材。')
            assets=asset_paths(root,project)
            if any(key not in assets for key in body.assetIds):fail('素材已变更或不可用，请刷新列表。')
            if active_count(project_id)+len(body.assetIds)>12:fail('每个项目最多排队 12 个处理任务。')
            created=[]
            try:
                for key in body.assetIds:
                    source,label=assets[key]
                    created.append(create(project_id,source,key,label,body.kind,body.params.model_dump()))
            except BaseException:
                for run in created:shutil.rmtree(path(project_id,run['id']))
                raise
            for run in created:enqueue(project_id,run)
            return {'runs':created}

    @router.post('/runs/{rid}/cancel')
    def cancel(project_id: str,rid: str):
        get_project(project_id)
        with lock:
            run=load(project_id,rid)
            if run['status'] not in ('queued','running'):fail('任务已结束。')
            run.update(status='cancelled',stage='cancelled');save(project_id,run)
            return run

    @router.post('/runs/{rid}/retry',status_code=202)
    def retry(project_id: str,rid: str):
        get_project(project_id)
        with lock:
            old=load(project_id,rid)
            if old['status'] not in ('failed','cancelled'):fail('只有失败或取消的任务可以重试。')
            if not environment()[old['kind']]['available']:fail(environment()[old['kind']]['message'])
            if active_count(project_id)>=12:fail('队列已满。')
            run=create(project_id,path(project_id,rid,'input.mp4'),old['assetId'],old['label'],old['kind'],old['params'])
            enqueue(project_id,run)
            return run

    @router.put('/runs/{rid}/cuts')
    def edit_cuts(project_id: str,rid: str,body: EditCuts):
        get_project(project_id)
        with lock:
            run=load(project_id,rid)
            if run['status']!='completed' or run['kind']!='scenes':fail('仅可编辑已完成的分镜结果。')
            file=path(project_id,rid,'scenes.json')
            data=json.loads(file.read_text())
            # Store revision alongside cuts atomically so a restart cannot reuse an old token.
            revision=data.get('revision',0)
            if body.revision!=revision:fail('切点已更新，请刷新后重试。')
            try:data['cuts']=validate_cuts(body.cuts,data['duration'])
            except ValueError as error:fail(str(error),422)
            data['revision']=revision+1
            atomic(file,data)
            return data

    def stream(file,request,download=False):
        try:
            fd=os.open(file,os.O_RDONLY|os.O_NOFOLLOW)
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):os.close(fd);fail('产物不可用。',404)
        except OSError:fail('产物不可用。',404)
        try:start,length,partial=parse_single_byte_range(request.headers.get('range'),info.st_size)
        except ValueError:
            os.close(fd)
            raise HTTPException(416,headers={'Content-Range':f'bytes */{info.st_size}'})
        content_type={'.mp4':'video/mp4','.webm':'video/webm','.mov':'video/quicktime','.json':'application/json','.srt':'application/x-subrip'}.get(file.suffix,'application/octet-stream')
        headers={'Accept-Ranges':'bytes','Content-Length':str(length),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
        if partial:headers['Content-Range']=f'bytes {start}-{start+length-1}/{info.st_size}'
        if download:headers['Content-Disposition']=f'attachment; filename="{file.name}"'
        body=PersonArtifactStream(fd,start,length)
        return StreamingResponse(stream_person_artifact(body),status_code=206 if partial else 200,media_type=content_type,headers=headers,background=BackgroundTask(body.aclose))

    @router.get('/assets/{asset_id}/video')
    def asset_video(project_id:str,asset_id:str,request:Request):
        with source_lock or nullcontext():
            assets=asset_paths(root,get_project(project_id))
            if asset_id not in assets:fail('素材不存在。',404)
            return stream(assets[asset_id][0],request)

    @router.get('/assets/{asset_id}/frame')
    def asset_frame(project_id:str,asset_id:str):
        with source_lock or nullcontext():
            assets=asset_paths(root,get_project(project_id))
            if asset_id not in assets:fail('素材不存在。',404)
            try:
                image=subprocess.run([config.ffmpeg,'-v','error','-nostdin','-i',str(assets[asset_id][0]),'-frames:v','1','-vf','scale=640:640:force_original_aspect_ratio=decrease','-f','image2pipe','-vcodec','png','pipe:1'],capture_output=True,check=True,timeout=20).stdout
                if not image:raise ValueError()
            except (OSError,ValueError,subprocess.SubprocessError):fail('无法读取素材首帧。',422)
            return Response(image,media_type='image/png',headers={'Cache-Control':'no-store'})

    @router.get('/runs/{rid}/input')
    def input_video(project_id:str,rid:str,request:Request):
        get_project(project_id);load(project_id,rid)
        return stream(path(project_id,rid,'input.mp4'),request)

    @router.get('/runs/{rid}/artifacts/{name}')
    def artifact(project_id:str,rid:str,name:str,request:Request,download:bool=False):
        get_project(project_id)
        with lock:
            run=load(project_id,rid)
            if run['status']!='completed':fail('处理尚未完成。')
            if name not in ARTIFACTS or name not in run['artifacts']:fail('产物不存在。',404)
            return stream(path(project_id,rid,name),request,download)

    @router.get('/pipelines/{pipeline_id}/steps/{step_id}/artifacts/{name}')
    def pipeline_artifact(project_id: str, pipeline_id: str, step_id: str, name: str, request: Request, download: bool = False):
        get_project(project_id)
        with lock:
            pipeline = load_pipeline(project_id, pipeline_id)
            step = next((value for value in pipeline['steps'] if value['id'] == step_id), None)
            if step is None or step['status'] != 'completed' or name not in ARTIFACTS or name not in step['artifacts']:
                fail('产物不存在。', 404)
            return stream(pipeline_path(project_id, pipeline_id, step['directory'], name), request, download)
    return router
