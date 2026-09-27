"""Project-scoped, explicit reproduction actions and durable generation records."""
import io
import json
import subprocess
import os
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from .durable_runs import EXTERNAL_RUN_POLICY
from .reproduction import (GenerationRun, OutputSettings, ReproductionStore, SavedPrompts,
                           analysis_ready, current_depth, default_settings, source_hash, utc_now)


class ConfirmAction(BaseModel):
    revision: int = Field(ge=0)
    disclosureAccepted: Literal[True]


class ResolveRun(BaseModel):
    promptId: Optional[str] = Field(default=None, min_length=1, max_length=200)
    confirmedNotQueued: bool = False


class SavePlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    prompts: SavedPrompts
    settings: OutputSettings
    comfyUrl: str = Field(max_length=512)


def fail(code, message, status=409):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message})


@contextmanager
def storage_errors():
    try:
        yield
    except (OSError, ValueError):
        fail('reproduction_storage_failed', '复刻方案或素材无法读取，请检查本地文件后重试。', 503)


def create_reproduction_router(data_dir, get_project, generate, *, ffmpeg_path='ffmpeg', client_factory=None):
    from .comfyui_client import ComfyUIClient, validate_comfy_url
    from .workflow_templates import build_workflow, template_info

    router = APIRouter(prefix='/api/projects/{project_id}/reproduction')
    store = ReproductionStore(data_dir)
    lock = RLock()
    busy = set()
    submissions = set()
    make_client = client_factory or ComfyUIClient

    def load(project_id):
        project = get_project(project_id)
        with storage_errors():
            state = store.load(project)
            interrupted = False
            for run in state.runs:
                if run.status == "submitting" and run.id not in submissions:
                    run.status = EXTERNAL_RUN_POLICY.recover_after_restart(run.status).status
                    run.error = "上次提交被中断，请在 ComfyUI 中核对是否已排队。"
                    interrupted = True
            if interrupted:
                store.save(project_id, state)
            return project, state

    def view(project, state):
        value = state.model_dump(mode='json')
        value.update(stale=bool(state.sourceHash and state.sourceHash != source_hash(project)),
                     canGeneratePrompts=analysis_ready(project), analysisReady=analysis_ready(project),
                     hasDepth=current_depth(project) is not None,
                     adjustments=default_settings(project)[1],
                     templates=[template_info(key) for key in ('wan22_i2v', 'wan22_fun_control')])
        value['runs'] = [{k: v for k, v in run.items() if k not in ('workflow', 'sourceHash', 'comfyUrl')}
                         for run in value['runs']]
        return value

    def save(project_id, state):
        with storage_errors():
            store.save(project_id, state)

    def revision_matches(state, revision):
        if state.revision != revision:
            fail('reproduction_conflict', '方案已发生变化，请刷新后重试。')

    def ready(project, state):
        if not state.prompts or not analysis_ready(project):
            fail('prompts_required', '请先完成语义分析并生成提示词。')
        if state.sourceHash != source_hash(project):
            fail('reproduction_stale', '素材或分析已改变，请重新生成提示词，再检查方案。')
        assessment = project.localPreprocessing.reproducibilityAssessment
        if assessment and assessment.status == 'out_of_scope':
            fail('reproduction_out_of_scope', '当前素材超出可复刻范围，可编辑提示词，但不能导出或执行此模板。')
        if state.settings.strategy == 'wan22_fun_control' and current_depth(project) is None:
            fail('depth_required', '深度控制策略需要当前参考视频已通过或已确认复核的深度素材。')

    def graph(state, image='reference.png', depth='depth-control.mp4'):
        s = state.settings
        return build_workflow(s.strategy, state.prompts.positiveEn, state.prompts.negativeEn,
                              s.width, s.height, s.frames, s.fps, s.seed, image,
                              depth_video=depth if s.strategy == 'wan22_fun_control' else None)

    def input_paths(project, state):
        """Copy only generation inputs; never include the full reference video."""
        ready(project, state)
        revision_dir = store.path(project.id, f'inputs-{state.revision}')
        revision_dir.mkdir(parents=True, exist_ok=True)
        media = project.referenceMedia
        pre = Path(data_dir).absolute() / 'project-files' / project.id / 'local-preprocessing' / project.localPreprocessing.id
        source = pre / ('normalized.png' if media.type == 'image' else 'keyframes/frame-0001.jpg')
        check_source(source)
        image = store.path(project.id, f'inputs-{state.revision}', 'reference.png')
        with Image.open(source) as original:
            settings = state.settings
            fitted = ImageOps.contain(original.convert('RGB'), (settings.width, settings.height),
                                      method=Image.Resampling.LANCZOS)
            canvas = Image.new('RGB', (settings.width, settings.height), 'black')
            canvas.paste(fitted, ((settings.width - fitted.width) // 2, (settings.height - fitted.height) // 2))
            canvas.save(image, format='PNG')
        assets = {'input/reference.png': image}
        if state.settings.strategy == 'wan22_fun_control':
            capture = current_depth(project)
            source = Path(data_dir).absolute() / 'project-files' / project.id / 'depth-captures' / capture.id / 'depth-control.mp4'
            check_source(source)
            depth = store.path(project.id, f'inputs-{state.revision}', 'depth-control.mp4')
            settings = state.settings
            result = subprocess.run([
                ffmpeg_path, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y', '-i', str(source),
                '-an', '-vf', (f'fps={settings.fps},scale={settings.width}:{settings.height}:force_original_aspect_ratio=decrease,'
                               f'pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2:color=black,'
                               f'tpad=stop_mode=clone:stop_duration={settings.frames / settings.fps}'),
                '-frames:v', str(settings.frames), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(depth),
            ], capture_output=True, timeout=180, check=False)
            if result.returncode != 0:
                fail('depth_input_conversion_failed', '深度控制素材转换失败，请检查 FFmpeg 后重试。', 422)
            assets['input/depth-control.mp4'] = depth
        return assets

    def check_source(path):
        root = Path(data_dir).absolute()
        current = root
        for part in path.relative_to(root).parts:
            current /= part
            if current.is_symlink():
                raise OSError('素材路径无效')
        if not path.is_file():
            raise OSError('素材缺失')

    @router.get('')
    def get_plan(project_id: str):
        with lock:
            return view(*load(project_id))

    @router.post('/prompts')
    def generate_plan(project_id: str, body: ConfirmAction):
        with lock:
            project, state = load(project_id)
            revision_matches(state, body.revision)
            if not analysis_ready(project):
                fail('analysis_required', '请先完成当前参考素材的语义分析。')
            if project_id in busy:
                fail('prompts_in_progress', '提示词正在生成，请稍候。')
            busy.add(project_id)
            snapshot = source_hash(project)
        try:
            prompts = generate(project)
            with lock:
                current, state = load(project_id)
                revision_matches(state, body.revision)
                if source_hash(current) != snapshot:
                    fail('reproduction_stale', '生成期间素材或分析发生变化，请重新生成。')
                state.prompts = SavedPrompts.model_validate(prompts.model_dump())
                state.sourceHash = snapshot
                state.revision += 1
                state.lastError = None
                save(project_id, state)
                return view(current, state)
        finally:
            with lock:
                busy.discard(project_id)

    @router.put('')
    def save_plan(project_id: str, body: SavePlan):
        try:
            url = validate_comfy_url(body.comfyUrl)
        except ValueError:
            fail('comfy_url_invalid', 'ComfyUI 地址必须是回环或私网 HTTP(S) 地址。', 422)
        with lock:
            project, state = load(project_id)
            revision_matches(state, body.revision)
            if state.prompts is None:
                fail("prompts_required", "请先生成提示词。")
            state.prompts = body.prompts
            state.settings = body.settings
            state.comfyUrl = url
            state.revision += 1
            save(project_id, state)
            return view(project, state)

    @router.get('/package')
    def download_package(project_id: str):
        with lock, storage_errors():
            project, state = load(project_id)
            assets = input_paths(project, state)
            workflow = graph(state)
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, path in assets.items():
                    archive.write(path, name)
                documents = {
                    'workflow-api.json': workflow,
                    'prompts.json': state.prompts.model_dump(),
                    'settings.json': state.settings.model_dump(),
                    'analysis.json': project.semanticAnalysis.result.model_dump(mode='json'),
                    'manifest.json': {'schemaVersion': 1, 'revision': state.revision, 'sourceHash': state.sourceHash,
                                      'createdAt': utc_now(), 'template': template_info(state.settings.strategy),
                                      'inputs': list(assets)},
                }
                for name, document in documents.items():
                    archive.writestr(name, json.dumps(document, ensure_ascii=False, indent=2))
                archive.writestr('README.txt', '此包是候选复刻方案，尚未在你的 ComfyUI 环境验证。\n'
                    'workflow-api.json 为 ComfyUI API 格式，请通过 API 或支持 API 格式的工具提交。\n'
                    '将 input/ 下的素材复制到 ComfyUI/input/，按照 manifest.json 配齐模型和节点。\n'
                    '此包不包含生成模型，不安装任何环境。生成时使用英文提示词；中文版本供阅读编辑。\n'
                    '深度控制按输出尺寸和帧率转换；长片截取开头，短片末尾保持最后一帧；不会上传完整参考视频。\n')
            return Response(output.getvalue(), media_type='application/zip', headers={
                'Content-Disposition': f'attachment; filename="reproduction-{project.id}-v{state.revision}.zip"'})

    @contextmanager
    def client_for(url):
        client = make_client(url)
        try:
            yield client
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(status_code=502, detail={
                "code": "comfy_request_failed", "message": "无法完成 ComfyUI 请求，请检查连接和服务状态。",
                "outcomeUnknown": getattr(error, "outcome_unknown", True),
            }) from None
        finally:
            close = getattr(client, 'close', None)
            if close:
                close()

    @router.post('/check')
    def check_environment(project_id: str):
        with lock:
            project, state = load(project_id)
            ready(project, state)
            workflow = graph(state)
        with client_for(state.comfyUrl) as client:
            return client.check(workflow)

    @router.post('/runs')
    def start_run(project_id: str, body: ConfirmAction):
        with lock, storage_errors():
            project, state = load(project_id)
            revision_matches(state, body.revision)
            ready(project, state)
            if any(EXTERNAL_RUN_POLICY.active(run.status) for run in state.runs):
                fail('generation_in_progress', '已有生成运行未结束；请先刷新其状态，避免重复提交。')
            assets = input_paths(project, state)
            workflow = graph(state)
            run = GenerationRun(id=str(uuid4()), status=EXTERNAL_RUN_POLICY.initial_status, createdAt=utc_now(), revision=state.revision,
                                width=state.settings.width, height=state.settings.height,
                                sourceHash=state.sourceHash, comfyUrl=state.comfyUrl, workflow=workflow)
            state.runs.append(run)
            submissions.add(run.id)
            save(project_id, state)
        submitted = False
        try:
            with client_for(run.comfyUrl) as client:
                check = client.check(workflow)
                if not check['ready']:
                    fail('comfy_not_ready', 'ComfyUI 环境不完整，请先检查缺失模型和节点。', 422)
                names = {name: client.upload_image(path, f'{run.id}-{path.name}') for name, path in assets.items()}
                workflow = graph(state, names['input/reference.png'], names.get('input/depth-control.mp4'))
                # Re-check the source just before dispatching a frozen workflow snapshot.
                with lock:
                    current, current_state = load(project_id)
                    revision_matches(current_state, body.revision)
                    if source_hash(current) != run.sourceHash:
                        fail('reproduction_stale', '提交前素材或分析发生变化，请重新检查方案。')
                submitted = True
                prompt_id = client.submit(workflow)
            with lock:
                project, state = load(project_id)
                saved = next(item for item in state.runs if item.id == run.id)
                saved.promptId = prompt_id
                saved.status = EXTERNAL_RUN_POLICY.validate('queued')
                saved.workflow = workflow
                save(project_id, state)
                return view(project, state)
        except HTTPException as error:
            with lock:
                project, state = load(project_id)
                saved = next(item for item in state.runs if item.id == run.id)
                uncertain = submitted and error.detail.get('outcomeUnknown', True)
                saved.status = (EXTERNAL_RUN_POLICY.mark_outcome_unknown(saved.status) if uncertain
                                else EXTERNAL_RUN_POLICY.fail(saved.status))
                saved.error = ('提交响应不确定，请在 ComfyUI 中核对，避免重复生成。' if uncertain
                               else error.detail.get('message', '生成提交失败。'))
                save(project_id, state)
            raise
        finally:
            with lock:
                submissions.discard(run.id)

    @router.post('/runs/{run_id}/resolve')
    def resolve_run(project_id: str, run_id: str, body: ResolveRun):
        with lock:
            project, state = load(project_id)
            run = next((item for item in state.runs if item.id == run_id), None)
            if run is None:
                fail('generation_not_found', '生成记录不存在。', 404)
            if run.status != 'unknown':
                fail('generation_not_unknown', '此运行不需要人工核对。')
            if body.promptId and not body.confirmedNotQueued:
                from .reference_video import validate_storage_id
                try:
                    validate_storage_id(body.promptId)
                except ValueError:
                    fail('prompt_id_invalid', 'ComfyUI 提示 ID 无效。', 422)
                run.promptId, run.status, run.error = body.promptId, 'queued', None
            elif body.confirmedNotQueued and not body.promptId:
                run.status, run.error = 'failed', '用户已确认此运行未在 ComfyUI 排队，可重新提交。'
            else:
                fail('generation_resolution_required', '请提供 ComfyUI 提示 ID，或明确确认未排队。', 422)
            save(project_id, state)
            return view(project, state)

    @router.post('/runs/{run_id}/refresh')
    def refresh_run(project_id: str, run_id: str):
        with lock:
            project, state = load(project_id)
            run = next((run for run in state.runs if run.id == run_id), None)
            if run is None:
                fail('generation_not_found', '生成记录不存在。', 404)
            if run.status in ('completed', 'failed') or not run.promptId:
                return view(project, state)
        with client_for(run.comfyUrl) as client:
            result = client.poll(run.promptId)
            outputs = []
            if result['status'] == 'completed':
                for index, item in enumerate(result.get('outputs', [])):
                    suffix = Path(item['filename']).suffix.lower()
                    if suffix not in ('.mp4', '.webm'):
                        continue
                    path = store.path(project_id, run.id, f'output-{index}{suffix}')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    payload = client.fetch_output(item)
                    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.video-')
                    try:
                        with os.fdopen(fd, 'wb') as target:
                            target.write(payload)
                        os.replace(temporary, path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                    outputs.append({'filename': path.name,
                                    'url': f'/api/projects/{project_id}/reproduction/runs/{run.id}/output/{len(outputs)}'})
                if not outputs:
                    result = {'status': 'failed', 'error': 'ComfyUI 已结束，但未返回可预览的视频文件。'}
        with lock:
            project, state = load(project_id)
            saved = next(item for item in state.runs if item.id == run_id)
            if saved.status in ('completed', 'failed') or saved.promptId != run.promptId:
                return view(project, state)
            saved.status = EXTERNAL_RUN_POLICY.validate(result['status'])
            saved.error = result.get('error') or ('ComfyUI 中已找不到此运行，请核对其状态。' if result['status'] == 'unknown' else None)
            saved.outputs = outputs
            save(project_id, state)
            return view(project, state)

    @router.get('/runs/{run_id}/output/{index}')
    def output_file(project_id: str, run_id: str, index: int):
        with lock, storage_errors():
            _, state = load(project_id)
            run = next((run for run in state.runs if run.id == run_id), None)
            if run is None or index < 0 or index >= len(run.outputs):
                fail('generation_output_not_found', '生成视频不存在。', 404)
            path = store.path(project_id, run.id, run.outputs[index]['filename'])
            if not path.is_file():
                fail('generation_output_not_found', '生成视频文件已丢失。', 404)
            return FileResponse(path, media_type='video/webm' if path.suffix == '.webm' else 'video/mp4')

    return router
