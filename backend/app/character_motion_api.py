"""角色动画/动作迁移 API，所有外部执行都需要显式环境检查与提交。"""
import io
import json
import os
import shutil
import zipfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from threading import RLock
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from .character_motion import (CharacterImage, CharacterMotionState, CharacterMotionStore, MotionRun,
                               MotionSettings, OFFICIAL_TEMPLATE, driver_for, source_hash, utc_now)
from .character_motion_templates import build_workflow, template_info
from .character_motion_media import normalize_driver, validate_video
from .reference_media_storage import managed_reference_media_is_safe, resolve_reference_media_path

MAX_CHARACTER_BYTES = 20 * 1024 * 1024


class SavePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    prompt: str = Field(min_length=1, max_length=12000)
    settings: MotionSettings
    comfyUrl: str = Field(max_length=512)


class ConfirmAction(BaseModel):
    revision: int = Field(ge=0)
    disclosureAccepted: bool
    preprocessorConfirmed: bool = False


class ResolveRun(BaseModel):
    promptId: Optional[str] = Field(default=None, min_length=1, max_length=200)
    confirmedNotQueued: bool = False


def _fail(code, message, status=409):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def create_character_motion_router(data_dir, get_project, *, client_factory=None, ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", source_lock=None):
    from .comfyui_client import ComfyUIClient, validate_comfy_url

    router = APIRouter(prefix="/api/projects/{project_id}/character-motion")
    store = CharacterMotionStore(data_dir)
    lock = RLock()
    submissions = set()
    make_client = client_factory or ComfyUIClient

    def load(project_id):
        project = get_project(project_id)
        try:
            state = store.load(project)
        except (OSError, ValueError):
            _fail("character_motion_storage_failed", "角色动作方案或素材无法读取，请检查本地文件后重试。", 503)
        changed = False
        for run in state.runs:
            if run.status == "submitting" and run.id not in submissions:
                run.status, run.error, changed = "unknown", "上次提交被中断，请在 ComfyUI 中核对是否已排队。", True
        if changed:
            store.save(project_id, state)
        return project, state

    def save(project_id, state):
        try:
            store.save(project_id, state)
        except (OSError, ValueError):
            _fail("character_motion_storage_failed", "角色动作方案无法保存，请检查本地存储后重试。", 503)

    def driver(project):
        item = driver_for(project)
        if item is None:
            _fail("motion_driver_required", "请先上传当前项目的 MP4 或 MOV 参考视频作为动作驱动。")
        if not managed_reference_media_is_safe(data_dir, project.id, item):
            _fail("motion_driver_unavailable", "当前动作驱动视频不存在或路径不安全，请重新上传参考视频。")
        return item

    def character_path(project_id, character):
        path = store.path(project_id, "characters", f"{character.id}.png")
        if not path.is_file() or path.is_symlink():
            _fail("character_image_unavailable", "角色图不存在或路径不安全，请重新上传。")
        return path

    def assets(project, state):
        with source_lock or nullcontext():
            current = get_project(project.id)
            ready(current, state)
            try:
                return snapshot_assets(current, state)
            except (OSError, ValueError):
                _fail("motion_input_failed", "动作素材预处理失败，请检查文件、路径和 FFmpeg。", 422)

    def snapshot_assets(project, state):
        if state.character is None:
            _fail("character_image_required", "请先上传独立角色图。")
        source = driver(project)
        role = character_path(project.id, state.character)
        driver_path = resolve_reference_media_path(data_dir, project.id, source)
        revision_dir = store.path(project.id, f"inputs-{state.revision}")
        revision_dir.mkdir(parents=True, exist_ok=True)
        role_target = store.path(project.id, f"inputs-{state.revision}", "character.png")
        driver_target = store.path(project.id, f"inputs-{state.revision}", "driver.mp4")
        for origin, target in ((role, role_target),):
            temporary = store.path(project.id, f"inputs-{state.revision}", f".{uuid4()}.part")
            shutil.copyfile(origin, temporary)
            os.replace(temporary, target)
        normalize_driver(driver_path, driver_target, state.settings, ffmpeg_path)
        return {"input/character.png": role_target, "input/driver.mp4": driver_target}

    def graph(state, character="character.png", driver_name="driver.mp4"):
        return build_workflow(state.prompt, state.settings.width, state.settings.height, state.settings.frames,
                              state.settings.fps, state.settings.seed, character, driver_name)

    def ready(project, state):
        if not state.prompt.strip():
            _fail("motion_prompt_required", "请填写角色动作提示词。")
        driver(project)
        if state.character is None:
            _fail("character_image_required", "请先上传独立角色图。")
        character_path(project.id, state.character)
        if state.sourceHash != source_hash(project, state.character):
            _fail("character_motion_stale", "角色图或当前驱动视频已改变，请保存并重新检查方案。")

    def view(project, state):
        data = state.model_dump(mode="json")
        data["driver"] = None
        current_driver = driver_for(project)
        if current_driver is not None:
            data["driver"] = {"id": current_driver.id, "originalName": current_driver.originalName,
                              "width": current_driver.width, "height": current_driver.height}
        data["stale"] = bool(state.character and current_driver and state.sourceHash != source_hash(project, state.character))
        data["template"] = template_info()
        data["runs"] = [{key: value for key, value in run.items() if key not in {"workflow", "sourceHash", "comfyUrl"}}
                        for run in data["runs"]]
        return data

    def checked_revision(state, revision):
        if state.revision != revision:
            _fail("character_motion_conflict", "方案已发生变化，请刷新后重试。")

    @contextmanager
    def client_for(url):
        client = make_client(url)
        try:
            yield client
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(status_code=502, detail={"code": "comfy_request_failed", "message": "无法完成 ComfyUI 请求，请检查连接和服务状态。", "outcomeUnknown": getattr(error, "outcome_unknown", True)}) from None
        finally:
            close = getattr(client, "close", None)
            if close:
                close()

    @router.get("")
    def get_plan(project_id: str):
        with lock:
            return view(*load(project_id))

    @router.put("")
    def save_plan(project_id: str, body: SavePlan):
        try:
            url = validate_comfy_url(body.comfyUrl)
        except ValueError:
            _fail("comfy_url_invalid", "ComfyUI 地址必须是回环或私网 HTTP(S) 地址。", 422)
        with lock:
            project, state = load(project_id)
            checked_revision(state, body.revision)
            state.prompt, state.settings, state.comfyUrl = body.prompt, body.settings, url
            state.sourceHash = source_hash(project, state.character) if state.character and driver_for(project) else None
            state.revision += 1
            save(project_id, state)
            return view(project, state)

    @router.post("/character")
    async def upload_character(project_id: str, file: UploadFile = File(...)):
        content = await file.read(MAX_CHARACTER_BYTES + 1)
        await file.close()
        if not content:
            _fail("character_image_empty", "请选择角色图片。", 422)
        if len(content) > MAX_CHARACTER_BYTES:
            _fail("character_image_too_large", "角色图片超过 20 MB 限制。", 413)
        try:
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                if width < 1 or height < 1 or width > 8192 or height > 8192:
                    _fail("character_image_invalid", "角色图片尺寸不受支持。", 422)
                image.load()
                normalized = ImageOps.exif_transpose(image).convert("RGBA")
                normalized.info.clear()
                width, height = normalized.size
        except (UnidentifiedImageError, OSError, ValueError):
            _fail("character_image_invalid", "角色图片无法解码，请上传 PNG、JPEG 或 WebP。", 422)
        encoded = io.BytesIO()
        normalized.save(encoded, format="PNG")
        if encoded.tell() > MAX_CHARACTER_BYTES:
            _fail("character_image_too_large", "标准化后的角色图片超过 20 MB，请缩小后上传。", 413)
        name = Path(file.filename or "character.png").name
        with lock:
            project, state = load(project_id)
            image_id = str(uuid4())
            target = store.path(project_id, "characters", f"{image_id}.png")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{image_id}.tmp")
            temporary.write_bytes(encoded.getvalue())
            os.replace(temporary, target)
            state.character = CharacterImage(id=image_id, originalName=name, width=width, height=height, sizeBytes=target.stat().st_size)
            state.sourceHash = None
            state.revision += 1
            save(project_id, state)
            return view(project, state)

    @router.get("/package")
    def download_package(project_id: str):
        with lock:
            project, state = load(project_id)
            ready(project, state)
            asset_paths = assets(project, state)
            driver_name = next(name for name in asset_paths if name.startswith("input/driver."))[6:]
            workflow = graph(state, "character.png", driver_name)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, path in asset_paths.items():
                    archive.write(path, name)
                archive.writestr("workflow-api.json", json.dumps(workflow, ensure_ascii=False, indent=2))
                snapshot = Path(__file__).with_name("workflow_templates") / "video_wan2_2_14B_animate.official.json"
                archive.write(snapshot, "official-template.json")
                archive.write(snapshot.with_name("COMFY_TEMPLATE_LICENSE.txt"), "COMFY_TEMPLATE_LICENSE.txt")
                archive.writestr("manifest.json", json.dumps({"schemaVersion": 1, "revision": state.revision, "sourceHash": state.sourceHash, "createdAt": utc_now(), "template": OFFICIAL_TEMPLATE, "inputs": list(asset_paths)}, ensure_ascii=False, indent=2))
                archive.writestr("README.txt", "这是 Wan Animate 候选包，未在本机 GPU/ComfyUI 验证。\n将 input/ 素材复制到 ComfyUI/input/，按 official-template.json 安装模板注明的节点和模型。\nworkflow-api.json 使用官方 Move 动作迁移链，不含 SAM2 背景替换；official-template.json 仅供来源参考。驱动按输出帧率采样并截取开头片段，不足时保持末帧。DW 预处理扩展及其权重应提前在 ComfyUI 安装。\n")
            return Response(payload.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="character-motion-{project_id}-v{state.revision}.zip"'})

    @router.post("/check")
    def check_environment(project_id: str):
        with lock:
            project, state = load(project_id)
            ready(project, state)
            workflow = graph(state)
        with client_for(state.comfyUrl) as client:
            result = client.check(workflow)
            if result.get("ready"):
                result["message"] = "节点与主模型检查通过；DWPose 预处理权重缓存需在目标环境人工确认。"
            result["preprocessorVerified"] = False
            return result

    @router.post("/runs")
    def start_run(project_id: str, body: ConfirmAction):
        if not body.disclosureAccepted:
            _fail("motion_confirmation_required", "请确认后再提交本地 ComfyUI 生成。", 422)
        if not body.preprocessorConfirmed:
            _fail("motion_preprocessor_unconfirmed", "请先确认目标 ComfyUI 已缓存 yolox_l.onnx 和 dw-ll_ucoco_384_bs5.torchscript.pt 预处理权重。", 422)
        with lock:
            project, state = load(project_id)
            checked_revision(state, body.revision)
            ready(project, state)
            if any(run.status in {"submitting", "queued", "running", "unknown"} for run in state.runs):
                _fail("motion_generation_in_progress", "已有未结束或状态未知的生成，请先刷新或人工恢复。")
            asset_paths = assets(project, state)
            driver_key = next(name for name in asset_paths if name.startswith("input/driver."))
            workflow = graph(state, "character.png", driver_key[6:])
            run = MotionRun(id=str(uuid4()), status="submitting", createdAt=utc_now(), revision=state.revision,
                            sourceHash=state.sourceHash or "", comfyUrl=state.comfyUrl, workflow=workflow)
            state.runs.append(run); submissions.add(run.id); save(project_id, state)
        try:
            with client_for(run.comfyUrl) as client:
                check = client.check(workflow)
                if not check.get("ready"):
                    _fail("comfy_not_ready", "ComfyUI 环境不完整，请先检查缺失模型和节点。", 422)
                names = {name: client.upload_image(path, f"{run.id}-{path.name}") for name, path in asset_paths.items()}
                run.workflow = graph(state, names["input/character.png"], names[driver_key])
                prompt_id = client.submit(run.workflow)
            with lock:
                project, current = load(project_id)
                saved = next(item for item in current.runs if item.id == run.id)
                saved.status, saved.promptId, saved.workflow, saved.error = "queued", prompt_id, run.workflow, None
                save(project_id, current)
                return view(project, current)
        except HTTPException as error:
            with lock:
                project, current = load(project_id)
                saved = next(item for item in current.runs if item.id == run.id)
                saved.status = "unknown" if error.detail.get("outcomeUnknown") is True else "failed"
                saved.error = error.detail.get("message")
                save(project_id, current)
            raise
        except Exception as error:
            with lock:
                project, current = load(project_id)
                saved = next(item for item in current.runs if item.id == run.id)
                saved.status, saved.error = "unknown", "无法确认请求是否进入 ComfyUI 队列。"
                save(project_id, current)
            raise HTTPException(status_code=502, detail={"code": "comfy_request_failed", "message": "无法确认 ComfyUI 是否已接收工作流。", "outcomeUnknown": True}) from None
        finally:
            submissions.discard(run.id)

    @router.post("/runs/{run_id}/refresh")
    def refresh_run(project_id: str, run_id: str):
        with lock:
            project, state = load(project_id)
            run = next((item for item in state.runs if item.id == run_id), None)
            if run is None:
                _fail("motion_run_not_found", "生成记录不存在。", 404)
            if run.status in {"completed", "failed"} or not run.promptId:
                return view(project, state)
            prompt_id, url = run.promptId, run.comfyUrl
        with client_for(url) as client:
            result = client.poll(prompt_id)
            outputs = []
            if result["status"] == "completed":
                for index, output in enumerate(result.get("outputs", [])):
                    data = client.fetch_output(output)
                    suffix = Path(output["filename"]).suffix.lower()
                    if suffix not in (".mp4", ".webm", ".mov"):
                        continue
                    filename = f"output-{index}{suffix}"
                    target = store.path(project_id, "runs", run_id, "outputs", filename)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = store.path(project_id, "runs", run_id, "outputs", f".{uuid4()}{suffix}")
                    try:
                        temporary.write_bytes(data)
                        validate_video(temporary, ffprobe_path)
                        os.replace(temporary, target)
                    except (OSError, ValueError):
                        result = {"status": "failed", "error": "生成服务返回的文件不是有效视频。"}
                        outputs = []
                        break
                    finally:
                        temporary.unlink(missing_ok=True)
                    outputs.append({"filename": filename, "url": f"/api/projects/{project_id}/character-motion/runs/{run_id}/output/{len(outputs)}"})
                if not outputs:
                    result = {"status": "failed", "error": result.get("error") or "ComfyUI 已结束，但未返回可预览视频文件。"}
        with lock:
            project, state = load(project_id)
            saved = next(item for item in state.runs if item.id == run_id)
            if saved.promptId != prompt_id or saved.status in {"completed", "failed"}:
                return view(project, state)
            saved.status, saved.error, saved.outputs = result["status"], result.get("error"), outputs
            save(project_id, state)
            return view(project, state)

    @router.post("/runs/{run_id}/resolve")
    def resolve_run(project_id: str, run_id: str, body: ResolveRun):
        with lock:
            project, state = load(project_id)
            run = next((item for item in state.runs if item.id == run_id), None)
            if run is None:
                _fail("motion_run_not_found", "生成记录不存在。", 404)
            if run.status != "unknown":
                return view(project, state)
            if body.promptId:
                run.status, run.promptId, run.error = "queued", body.promptId, None
            elif body.confirmedNotQueued:
                run.status, run.error = "failed", "已人工确认未进入 ComfyUI 队列。"
            else:
                _fail("motion_resolution_required", "请填写 ComfyUI prompt ID，或确认该任务未排队。", 422)
            save(project_id, state)
            return view(project, state)

    @router.get("/runs/{run_id}/output/{index}")
    def output_file(project_id: str, run_id: str, index: int, download: bool = False):
        with lock:
            _, state = load(project_id)
            run = next((item for item in state.runs if item.id == run_id), None)
            if run is None or run.status != "completed" or index < 0 or index >= len(run.outputs):
                _fail("motion_output_not_found", "生成视频不存在。", 404)
            path = store.path(project_id, "runs", run_id, "outputs", run.outputs[index]["filename"])
            if not path.is_file() or path.is_symlink():
                _fail("motion_output_not_found", "生成视频不存在。", 404)
            return FileResponse(path, media_type="video/webm" if path.suffix == ".webm" else "video/mp4", filename=path.name if download else None)

    return router
