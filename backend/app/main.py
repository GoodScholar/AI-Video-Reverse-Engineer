import errno
import httpx
import json
import logging
import os
import re
import stat
import tempfile
import subprocess
import zipfile
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import AsyncIterator, Callable, Literal, Optional, Union
from uuid import uuid4

from fastapi import File, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.shot_preparation_api import create_shot_preparation_router
from app.shot_analysis import analyze_preparation_shot
from app.reproduction_api import create_reproduction_router
from app.character_motion_api import create_character_motion_router
from app.prompt_generation import generate_prompts

from app.reference_video import (
    DEFAULT_FFPROBE_TIMEOUT_SECONDS,
    MAX_MULTIPART_BODY_BYTES,
    MAX_REFERENCE_VIDEO_BYTES,
    ReferenceVideo,
    ReferenceVideoError,
    probe_reference_video,
    reference_video_format_from_name,
    validate_storage_id,
)
from app.reference_image import probe_reference_image
from app.reference_media import ReferenceImage, ReferenceMedia, ReferenceMediaError
from app.project_migrations import migrate_project_payload
from app.reference_media_storage import (
    discard_managed_file,
    detect_reference_media_type,
    detect_reference_video_format,
    managed_reference_media_is_safe,
    managed_reference_media_path,
    promote_staged_reference_media,
    reference_media_basename,
    resolve_reference_media_path,
    stage_reference_media,
    UnsafeManagedMediaPathError,
)
from app.local_preprocessing import (
    ALGORITHM_VERSION,
    LocalPreprocessing,
    LocalPreprocessingError,
    StageName,
    new_local_preprocessing,
    stage_order_for,
)
from app.image_preprocessing_runner import run_image_preprocessing
from app.local_preprocessing_runner import LocalPreprocessingFailure, run_local_preprocessing
from app.local_preprocessing_storage import (
    discard_preprocessing,
    inspect_completed_stages,
    preprocessing_directory,
    reset_stage_artifacts,
    validate_completed_stages,
)
from app.depth_capture import (
    DEPTH_ALGORITHM_VERSION,
    DEPTH_STAGE_ORDER,
    DepthCapture,
    DepthCaptureError,
    DepthOutputSummary,
    new_depth_capture,
    select_execution_device,
)
from app.depth_capture_jobs import LocalPreprocessingQueueAdapter
from app.durable_runs import LOCAL_RUN_POLICY, LocalComputeJobQueue
from app.depth_capture_runner import DepthCaptureFailure, DepthCaptureRequest, run_depth_capture
from app.depth_capture_storage import DEPTH_ARTIFACTS, open_validated_depth_artifact, inspect_depth_artifacts
from app.depth_capture_storage import DepthPreviewUnavailableError, open_validated_depth_preview
from app.semantic_analysis import SemanticAnalysis, SemanticAnalysisError
from app.semantic_analysis_jobs import SemanticAnalysisQueueAdapter
from app.semantic_analysis_runner import run_semantic_analysis
from app.semantic_analysis_storage import (
    SCHEMA_VERSION,
    completed_checkpoint_matches,
    discard_semantic_analysis_checkpoint,
    interrupted_semantic_analysis,
    load_completed_checkpoint_for_recovery,
    new_semantic_analysis,
    write_semantic_analysis_checkpoint,
)
from app.analysis_prompt import PROMPT_VERSION
from app.analysis_service_secrets import SecureStorageUnavailable
from app.analysis_settings import AnalysisProviderConfiguration, AnalysisSettings, AnalysisSettingsConflictError
from app.credential_store import CredentialStore
from app.provider_models import CATALOG_VERSION, PROVIDER_IDS, model_is_allowed, provider_for
from app.analysis_providers.bailian import BailianAnalysisProvider
from app.analysis_providers.base import ProviderAnalysisError, ProviderFailure, ProviderRequest
from app.analysis_providers.claude import ClaudeAnalysisProvider
from app.analysis_providers.chatanywhere import ChatAnywhereAnalysisProvider
from app.analysis_providers.doubao import DoubaoAnalysisProvider
from app.analysis_providers.gemini import GeminiAnalysisProvider
from app.analysis_providers.grok import GrokAnalysisProvider
from app.analysis_providers.local_openai_compatible import LocalOpenAICompatibleAnalysisProvider
from app.analysis_providers.openai import OpenAIAnalysisProvider
from app.analysis_providers.http_transport import new_provider_http_client


class CreateProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("项目名称不能为空")
        return value


class StartDepthCaptureInput(BaseModel):
    devicePreference: Literal["auto", "cuda", "mps", "cpu"] = "auto"
    outputResolution: Literal["480p", "720p"] = "480p"


class StartSemanticAnalysisInput(BaseModel):
    provider: str
    model: str
    disclosureAccepted: bool


STORAGE_UNAVAILABLE_MESSAGE = "本地项目存储不可用，请检查数据目录的访问权限后重试。"
CORRUPT_PROJECT_DATA_MESSAGE = "本地项目数据已损坏，请从备份恢复 projects.json，或将损坏文件移到其他位置后重新读取。"

_STAGE_LABELS = {
    "decoding": "解码",
    "sceneDetection": "镜头检测",
    "keyframeExtraction": "关键帧提取",
    "motionAnalysis": "运动分析",
    "reproducibilityAssessment": "可复现性判断",
    "imageDecoding": "图片解码",
    "imageNormalization": "图片标准化",
    "proxyGeneration": "分析代理生成",
}

_MISSING_COMPLETED_ARTIFACT_CODES = {
    "decoding": "video_decode_failed",
    "sceneDetection": "scene_detection_failed",
    "keyframeExtraction": "keyframe_extraction_failed",
    "motionAnalysis": "motion_analysis_failed",
    "reproducibilityAssessment": "assessment_failed",
    "imageDecoding": "image_decode_failed",
    "imageNormalization": "image_normalization_failed",
    "proxyGeneration": "proxy_generation_failed",
}
_MISSING_COMPLETED_ARTIFACT_MESSAGE = "已完成结果缺少必需产物，请重新启动本地预处理。"
_MEDIA_CHUNK_BYTES = 64 * 1024
_MEDIA_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_MEDIA_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW
_MEDIA_NOT_FOUND_ERRNOS = {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}
_ALLOWED_ANALYSIS_MUTATION_ORIGINS = frozenset({
    "http://127.0.0.1:5173", "http://localhost:5173",
    "http://127.0.0.1:4173", "http://localhost:4173",
    "http://127.0.0.1:5188", "http://localhost:5188",
})
_ANALYSIS_REQUEST_INTENT = "semantic-analysis"


def _stage_label(stage: StageName) -> str:
    return _STAGE_LABELS[stage]


class CorruptProjectDataError(OSError):
    pass


class DepthDeviceProbeError(RuntimeError):
    pass


class ManagedMediaStream:
    def __init__(self, descriptor: int, start: int, length: int):
        self._descriptor = descriptor
        self._remaining = length
        self._closed = False
        try:
            os.lseek(descriptor, start, os.SEEK_SET)
        except BaseException:
            self._closed = True
            os.close(descriptor)
            raise

    def __aiter__(self) -> "ManagedMediaStream":
        return self

    async def __anext__(self) -> bytes:
        if self._closed or self._remaining == 0:
            await self.aclose()
            raise StopAsyncIteration
        try:
            chunk = os.read(self._descriptor, min(_MEDIA_CHUNK_BYTES, self._remaining))
        except BaseException:
            await self.aclose()
            raise
        if not chunk:
            await self.aclose()
            raise StopAsyncIteration
        self._remaining -= len(chunk)
        return chunk

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self._descriptor)


async def stream_managed_media(stream: ManagedMediaStream) -> AsyncIterator[bytes]:
    try:
        async for chunk in stream:
            yield chunk
    finally:
        await stream.aclose()


class Project(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    createdAt: str
    updatedAt: str
    referenceMedia: Optional[ReferenceMedia] = None
    localPreprocessing: Optional[LocalPreprocessing] = None
    depthCaptures: list[DepthCapture] = Field(default_factory=list)
    activeDepthCaptureId: Optional[str] = None
    semanticAnalysis: Optional[SemanticAnalysis] = None

    @field_validator("id")
    @classmethod
    def id_must_be_a_safe_storage_segment(cls, value: str) -> str:
        return validate_storage_id(value)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("项目名称不能为空")
        return value

    @field_validator("createdAt", "updatedAt")
    @classmethod
    def timestamp_must_be_timezone_aware_iso_datetime(cls, value: str) -> str:
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("项目时间戳无效") from error
        if timestamp.tzinfo is None:
            raise ValueError("项目时间戳必须包含时区")
        return value

    @property
    def referenceVideo(self) -> Optional[ReferenceVideo]:
        """Temporary internal compatibility for video-only consumers pending media branching."""
        if isinstance(self.referenceMedia, ReferenceVideo):
            return self.referenceMedia
        return None


def _projects_path(data_dir: Path) -> Path:
    return data_dir / "projects.json"


def _read_projects(data_dir: Path) -> list[Project]:
    if data_dir.exists() and not data_dir.is_dir():
        raise OSError("项目数据目录不是目录")
    path = _projects_path(data_dir)
    if not path.exists():
        return []
    try:
        projects = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CorruptProjectDataError("项目数据无法读取") from error
    if not isinstance(projects, list):
        raise CorruptProjectDataError("项目数据格式无效")
    try:
        return [Project.model_validate(migrate_project_payload(project)) for project in projects]
    except ValidationError as error:
        raise CorruptProjectDataError("项目数据格式无效") from error


def _write_projects(data_dir: Path, projects: list[Project]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        dir=data_dir, prefix=".projects-", suffix=".tmp", text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump([project.model_dump() for project in projects], file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, _projects_path(data_dir))
    except OSError:
        Path(temporary_path).unlink(missing_ok=True)
        raise


DEPTH_WORKER_ROOT = Path(__file__).resolve().parents[1] / "depth_worker"
PERSON_WORKER_ROOT = Path(__file__).resolve().parents[1] / "person_worker"


def create_app(
    data_dir: Path,
    *,
    ffprobe_path: str = "ffprobe",
    ffprobe_timeout_seconds: float = DEFAULT_FFPROBE_TIMEOUT_SECONDS,
    max_reference_video_bytes: int = MAX_REFERENCE_VIDEO_BYTES,
    max_multipart_body_bytes: int = MAX_MULTIPART_BODY_BYTES,
    ffmpeg_path: str = "ffmpeg",
    preprocessing_runner: Callable = run_local_preprocessing,
    preprocessing_queue_factory: Optional[Callable] = None,
    local_compute_queue: Optional[LocalComputeJobQueue] = None,
    depth_capture_runner: Callable = run_depth_capture,
    depth_worker_python: str = str(DEPTH_WORKER_ROOT / ".venv/bin/python"),
    depth_worker_script: Path = DEPTH_WORKER_ROOT / "run_depth.py",
    depth_checkpoint: Path = DEPTH_WORKER_ROOT / "checkpoints/video_depth_anything_vits.pth",
    depth_upstream_root: Path = DEPTH_WORKER_ROOT / "vendor/Video-Depth-Anything",
    depth_device_probe: Optional[Callable[[], tuple[bool, bool]]] = None,
    person_worker_python: str = str(PERSON_WORKER_ROOT / ".venv/bin/python"),
    person_worker_script: Path = PERSON_WORKER_ROOT / "run_person.py",
    person_model: Path = PERSON_WORKER_ROOT / "models/pose_landmarker_full.task",
    credential_store=None,
    analysis_settings: Optional[AnalysisSettings] = None,
    semantic_analysis_queue_factory: Optional[Callable] = None,
    semantic_analysis_runner: Optional[Callable] = None,
    analysis_provider_registry=None,
    provider_registry=None,
    clock: Optional[Callable[[], datetime]] = None,
) -> FastAPI:
    app = FastAPI(title="AI 视频复刻分析器")
    analysis_provider_configuration_lock = Lock()
    project_write_lock = RLock()
    dispatching_project_ids: set[str] = set()
    reference_media_path_pattern = re.compile(r"^/api/projects/[^/]+/reference-media$")
    reference_video_path_pattern = re.compile(r"^/api/projects/[^/]+/reference-video$")
    configured_credentials = credential_store
    configured_analysis_settings = analysis_settings or AnalysisSettings(data_dir / "analysis-providers.json")
    now_utc = clock or (lambda: datetime.now(timezone.utc))
    configured_provider_registry = provider_registry or analysis_provider_registry

    def credentials():
        nonlocal configured_credentials
        if configured_credentials is None:
            configured_credentials = CredentialStore()
        return configured_credentials

    def secure_storage_unavailable() -> HTTPException:
        return HTTPException(
            status_code=503,
            detail={
                "code": "secure_storage_unavailable",
                "message": "系统安全存储不可用。",
            },
        )

    def _analysis_provider_snapshot_locked(provider: str):
        return (
            configured_analysis_settings.get(provider),
            credentials().get(provider),
            configured_analysis_settings.selected_provider(),
        )

    def analysis_provider_snapshot(provider: str):
        with analysis_provider_configuration_lock:
            return _analysis_provider_snapshot_locked(provider)

    def _analysis_provider_configuration_locked(provider: str) -> AnalysisProviderConfiguration:
        setting, credential, selected_provider = _analysis_provider_snapshot_locked(provider)
        return analysis_provider_configuration_snapshot(
            provider=provider,
            setting=setting,
            credential_configured=credential is not None,
            selected_provider=selected_provider,
        )

    def analysis_provider_configuration_snapshot(
        *,
        provider: str,
        setting,
        credential_configured: bool,
        selected_provider: Optional[str],
    ) -> AnalysisProviderConfiguration:
        catalog = provider_for(provider)
        return AnalysisProviderConfiguration(
            provider=provider,
            label=catalog.label,
            models=tuple(model.model_dump() for model in catalog.models),
            model=setting.model if setting is not None else None,
            baseUrl=setting.baseUrl if setting is not None else None,
            credentialState="configured" if credential_configured else "unconfigured",
            selectedProvider=selected_provider,
            configurationRevision=setting.configurationRevision if setting is not None else None,
            catalogVersion=setting.catalogVersion if setting is not None else CATALOG_VERSION,
            verificationState=setting.verificationState if setting is not None else "unverified",
            verifiedAt=setting.verifiedAt if setting is not None else None,
            failedAt=setting.failedAt if setting is not None else None,
            errorCode=setting.errorCode if setting is not None else None,
        )

    def storage_error(error: OSError) -> HTTPException:
        detail = CORRUPT_PROJECT_DATA_MESSAGE if isinstance(error, CorruptProjectDataError) else STORAGE_UNAVAILABLE_MESSAGE
        return HTTPException(status_code=503, detail=detail)

    def video_storage_error(error: OSError) -> HTTPException:
        message = CORRUPT_PROJECT_DATA_MESSAGE if isinstance(error, CorruptProjectDataError) else STORAGE_UNAVAILABLE_MESSAGE
        return HTTPException(
            status_code=503,
            detail={"code": "storage_unavailable", "message": message},
        )

    def media_not_found() -> HTTPException:
        return HTTPException(
            status_code=404,
            detail={"code": "media_not_found", "message": "本地视频不存在或不可用。"},
        )

    def open_managed_media(parts: tuple[str, ...]) -> tuple[int, int]:
        """Open one fixed, managed file without following a managed ancestor symlink."""
        root = Path(os.path.abspath(data_dir))
        descriptors: list[int] = []
        try:
            descriptor = os.open(root, _MEDIA_DIRECTORY_FLAGS)
            descriptors.append(descriptor)
            for name in parts[:-1]:
                descriptor = os.open(name, _MEDIA_DIRECTORY_FLAGS, dir_fd=descriptors[-1])
                descriptors.append(descriptor)
            file_descriptor = os.open(parts[-1], _MEDIA_FILE_FLAGS, dir_fd=descriptors[-1])
            metadata = os.fstat(file_descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                os.close(file_descriptor)
                raise FileNotFoundError(parts[-1])
            return file_descriptor, metadata.st_size
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def parse_single_range(range_header: Optional[str], size: int) -> tuple[int, int, bool]:
        if range_header is None:
            return 0, size, False
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if match is None or (not match.group(1) and not match.group(2)) or size == 0:
            raise ValueError("invalid range")
        start_text, end_text = match.groups()
        if start_text:
            start = int(start_text)
            if start >= size:
                raise ValueError("unsatisfiable range")
            end = min(int(end_text), size - 1) if end_text else size - 1
            if end < start:
                raise ValueError("reversed range")
        else:
            suffix_size = int(end_text)
            if suffix_size == 0:
                raise ValueError("empty range")
            start = max(size - suffix_size, 0)
            end = size - 1
        return start, end - start + 1, True

    def media_response_from_descriptor(
        descriptor: int,
        size: int,
        range_header: Optional[str],
        media_type: str = "video/mp4",
    ) -> Union[StreamingResponse, Response]:
        try:
            start, length, partial = parse_single_range(range_header, size)
        except ValueError:
            os.close(descriptor)
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "Content-Length": str(length),
        }
        if partial:
            headers["Content-Range"] = f"bytes {start}-{start + length - 1}/{size}"
        try:
            stream = ManagedMediaStream(descriptor, start, length)
        except OSError as error:
            raise video_storage_error(error) from error
        return StreamingResponse(
            stream_managed_media(stream),
            status_code=206 if partial else 200,
            media_type=media_type,
            headers=headers,
            background=BackgroundTask(stream.aclose),
        )

    def media_response(
        parts: tuple[str, ...],
        range_header: Optional[str],
        media_type: str = "video/mp4",
    ) -> Union[StreamingResponse, Response]:
        try:
            descriptor, size = open_managed_media(parts)
        except OSError as error:
            if error.errno in _MEDIA_NOT_FOUND_ERRNOS:
                raise media_not_found() from error
            raise video_storage_error(error) from error
        return media_response_from_descriptor(descriptor, size, range_header, media_type)

    def reference_media_mime_type(reference: ReferenceMedia) -> str:
        return {
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
            "mp4": "video/mp4",
            "mov": "video/quicktime",
        }[reference.format]

    def reference_media_content_response(
        project: Project,
        request: Request,
    ) -> Union[StreamingResponse, Response]:
        reference = project.referenceMedia
        if reference is None:
            raise media_not_found()
        try:
            path = resolve_reference_media_path(data_dir, project.id, reference)
        except UnsafeManagedMediaPathError as error:
            raise media_not_found() from error
        except OSError as error:
            raise video_storage_error(error) from error
        return media_response(
            tuple(path.relative_to(Path(os.path.abspath(data_dir))).parts),
            request.headers.get("range"),
            reference_media_mime_type(reference),
        )

    def ensure_project_exists(project_id: str) -> Project:
        try:
            projects = _read_projects(data_dir)
        except OSError as error:
            raise video_storage_error(error) from error
        project = next((project for project in projects if project.id == project_id), None)
        if project is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found", "message": "复刻项目不存在。"},
            )
        return project

    def update_project(project_id: str, transform: Callable[[Project], Project]) -> Project:
        with project_write_lock:
            projects = _read_projects(data_dir)
            index = next((i for i, project in enumerate(projects) if project.id == project_id), None)
            if index is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "project_not_found", "message": "复刻项目不存在。"},
                )
            updated = transform(projects[index])
            projects[index] = updated
            _write_projects(data_dir, projects)
            return updated

    def preprocessing_error(code: str, message: str, stage: StageName) -> LocalPreprocessingError:
        return LocalPreprocessingError(code=code, message=message, stage=stage)

    def project_read_projection(project: Project) -> Project:
        task = project.localPreprocessing
        updates = {}
        if task is not None and task.status == "completed":
            directory = preprocessing_directory(data_dir, project.id, task.id)
            validated = inspect_completed_stages(task, directory)
            stage = validated.firstInvalidStage
            if stage is not None:
                projected = validated.preprocessing
                projected.status = "failed"
                projected.currentStage = stage
                projected.completedAt = None
                projected.proxySummary = None
                projected.reproducibilityAssessment = None
                projected.error = preprocessing_error(
                    _MISSING_COMPLETED_ARTIFACT_CODES[stage],
                    _MISSING_COMPLETED_ARTIFACT_MESSAGE,
                    stage,
                )
                updates["localPreprocessing"] = projected
        captures = [capture.model_copy(deep=True) for capture in project.depthCaptures]
        projected_capture_ids: set[str] = set()
        for capture in captures:
            if capture.status == "completed" and not inspect_capture(project, capture):
                failed_depth_capture(
                    capture,
                    "depth_capture_artifacts_invalid",
                    "已完成深度捕捉缺少有效产物，请重新开始。",
                )
                projected_capture_ids.add(capture.id)
        if projected_capture_ids:
            updates["depthCaptures"] = captures
            if project.activeDepthCaptureId in projected_capture_ids:
                updates["activeDepthCaptureId"] = None
        return project.model_copy(update=updates) if updates else project

    def first_unfinished_stage(preprocessing: LocalPreprocessing) -> StageName:
        states = {state.name: state for state in preprocessing.stages}
        order = stage_order_for(preprocessing.mediaType)
        return next(
            (stage for stage in order if states.get(stage) is None or states[stage].status != "completed"),
            preprocessing.currentStage or order[-1],
        )

    def reset_stages_from(preprocessing: LocalPreprocessing, stage: StageName) -> None:
        order = stage_order_for(preprocessing.mediaType)
        start = order.index(stage)
        for state in preprocessing.stages:
            if order.index(state.name) >= start:
                state.status = "pending"
                state.startedAt = None
                state.completedAt = None

    def interrupted_preprocessing(project: Project, preprocessing: LocalPreprocessing) -> LocalPreprocessing:
        stage = first_unfinished_stage(preprocessing)
        reset_stage_artifacts(
            preprocessing_directory(data_dir, project.id, preprocessing.id),
            stage,
            preprocessing.mediaType,
        )
        task = preprocessing.model_copy(deep=True)
        reset_stages_from(task, stage)
        task.status = LOCAL_RUN_POLICY.recover_after_restart(task.status).status
        task.currentStage = stage
        task.updatedAt = datetime.now(timezone.utc).isoformat()
        task.error = preprocessing_error(
            "preprocessing_interrupted",
            f"本地服务曾退出，已保留完成阶段，可从{_stage_label(stage)}继续重试。",
            stage,
        )
        return task

    def reconcile_interrupted_preprocessing() -> None:
        try:
            with project_write_lock:
                projects = _read_projects(data_dir)
                changed = False
                for index, project in enumerate(projects):
                    preprocessing = project.localPreprocessing
                    if preprocessing is None or not LOCAL_RUN_POLICY.active(preprocessing.status):
                        continue
                    task = interrupted_preprocessing(project, preprocessing)
                    projects[index] = project.model_copy(
                        update={"localPreprocessing": task, "updatedAt": task.updatedAt}
                    )
                    changed = True
                if changed:
                    _write_projects(data_dir, projects)
        except OSError:
            logging.getLogger(__name__).exception("无法协调中断的本地预处理任务")

    def persist_preprocessing_change(
        project_id: str,
        preprocessing_id: str,
        transform: Callable[[LocalPreprocessing], LocalPreprocessing],
    ) -> Project:
        def update(project: Project) -> Project:
            task = project.localPreprocessing
            if task is None or task.id != preprocessing_id:
                return project
            updated_task = transform(task.model_copy(deep=True))
            return project.model_copy(update={
                "localPreprocessing": updated_task,
                "updatedAt": updated_task.updatedAt,
            })
        return update_project(project_id, update)

    def run_preprocessing_job(project_id: str) -> None:
        preprocessing_id: Optional[str] = None
        try:
            should_run = False

            def prepare(project: Project) -> Project:
                nonlocal preprocessing_id, should_run
                task = project.localPreprocessing
                if task is None or task.status != "queued" or project.referenceMedia is None:
                    return project
                preprocessing_id = task.id
                directory = preprocessing_directory(data_dir, project.id, task.id)
                validated = validate_completed_stages(task, directory).preprocessing
                now = datetime.now(timezone.utc).isoformat()
                validated.status = "running"
                validated.startedAt = validated.startedAt or now
                validated.updatedAt = now
                validated.error = None
                should_run = True
                return project.model_copy(update={
                    "localPreprocessing": validated, "updatedAt": now,
                })

            project = update_project(project_id, prepare)
            if not should_run or preprocessing_id is None or project.referenceMedia is None:
                return
            task = project.localPreprocessing
            assert task is not None
            reference = project.referenceMedia
            source_path = resolve_reference_media_path(data_dir, project_id, reference)
            output_directory = preprocessing_directory(data_dir, project_id, preprocessing_id)

            def stage_started(stage: StageName) -> None:
                now = datetime.now(timezone.utc).isoformat()

                def update(task: LocalPreprocessing) -> LocalPreprocessing:
                    state = next(item for item in task.stages if item.name == stage)
                    state.status = "running"
                    state.startedAt = now
                    state.completedAt = None
                    task.currentStage = stage
                    task.updatedAt = now
                    return task
                persist_preprocessing_change(project_id, preprocessing_id, update)

            def stage_completed(stage: StageName) -> None:
                now = datetime.now(timezone.utc).isoformat()

                def update(task: LocalPreprocessing) -> LocalPreprocessing:
                    state = next(item for item in task.stages if item.name == stage)
                    state.status = "completed"
                    state.completedAt = now
                    order = stage_order_for(task.mediaType)
                    task.currentStage = next(
                        (
                            candidate for candidate in order[order.index(stage) + 1:]
                            if next(item for item in task.stages if item.name == candidate).status != "completed"
                        ),
                        None,
                    )
                    task.updatedAt = now
                    return task
                persist_preprocessing_change(project_id, preprocessing_id, update)

            if reference.type == "image":
                result = run_image_preprocessing(
                    source_path=source_path,
                    reference=reference,
                    preprocessing=task,
                    output_directory=output_directory,
                    on_stage_started=stage_started,
                    on_stage_completed=stage_completed,
                )
            else:
                result = preprocessing_runner(
                    source_path=source_path,
                    reference=reference,
                    preprocessing=task,
                    output_directory=output_directory,
                    ffmpeg_path=ffmpeg_path,
                    on_stage_started=stage_started,
                    on_stage_completed=stage_completed,
                )
            now = datetime.now(timezone.utc).isoformat()

            def complete(task: LocalPreprocessing) -> LocalPreprocessing:
                task.status = "completed"
                task.currentStage = None
                task.updatedAt = now
                task.completedAt = now
                task.proxySummary = result.proxy_summary
                task.reproducibilityAssessment = result.assessment
                task.error = None
                return task
            persist_preprocessing_change(project_id, preprocessing_id, complete)
        except LocalPreprocessingFailure as error:
            if preprocessing_id is None:
                logging.getLogger(__name__).exception("本地预处理在开始前失败：%s", error)
                return
            now = datetime.now(timezone.utc).isoformat()

            def fail(task: LocalPreprocessing) -> LocalPreprocessing:
                state = next(item for item in task.stages if item.name == error.stage)
                state.status = "failed"
                task.status = "failed"
                task.currentStage = error.stage
                task.updatedAt = now
                task.error = preprocessing_error(error.code, error.message, error.stage)
                return task
            _persist_worker_failure(project_id, preprocessing_id, fail)
        except OSError:
            logging.getLogger(__name__).exception("本地预处理产物无法安全读写")
            if preprocessing_id is None:
                return
            now = datetime.now(timezone.utc).isoformat()

            def fail_storage(task: LocalPreprocessing) -> LocalPreprocessing:
                stage = task.currentStage or first_unfinished_stage(task)
                state = next(item for item in task.stages if item.name == stage)
                state.status = "failed"
                task.status = "failed"
                task.currentStage = stage
                task.updatedAt = now
                task.error = preprocessing_error(
                    "preprocessing_storage_unavailable",
                    "本地预处理产物无法安全读写，请检查数据目录后重试。",
                    stage,
                )
                return task
            _persist_worker_failure(project_id, preprocessing_id, fail_storage)
        except Exception:
            logging.getLogger(__name__).exception("本地预处理出现未预期错误")
            if preprocessing_id is None:
                return
            now = datetime.now(timezone.utc).isoformat()

            def fail_unexpected(task: LocalPreprocessing) -> LocalPreprocessing:
                stage = task.currentStage or first_unfinished_stage(task)
                state = next(item for item in task.stages if item.name == stage)
                state.status = "failed"
                task.status = "failed"
                task.currentStage = stage
                task.updatedAt = now
                task.error = preprocessing_error(
                    "preprocessing_unexpected_error", "本地预处理出现未预期错误，请重试。", stage,
                )
                return task
            _persist_worker_failure(project_id, preprocessing_id, fail_unexpected)

    def _persist_worker_failure(
        project_id: str, preprocessing_id: str, transform: Callable[[LocalPreprocessing], LocalPreprocessing],
    ) -> None:
        try:
            persist_preprocessing_change(project_id, preprocessing_id, transform)
        except (OSError, HTTPException):
            logging.getLogger(__name__).exception("preprocessing_storage_unavailable")

    def available_depth_devices() -> tuple[bool, bool]:
        try:
            if depth_device_probe is not None:
                return depth_device_probe()
            import importlib
            torch = importlib.import_module("torch")
            return bool(torch.cuda.is_available()), bool(
                getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
            )
        except ImportError:
            return False, False
        except Exception as error:
            raise DepthDeviceProbeError from error

    def depth_error(code: str, message: str, stage: str) -> DepthCaptureError:
        return DepthCaptureError(code=code, message=message, stage=stage)

    def first_unfinished_depth_stage(capture: DepthCapture) -> str:
        states = {state.name: state for state in capture.stages}
        return next(
            (stage for stage in DEPTH_STAGE_ORDER if states[stage].status != "completed"),
            capture.currentStage or DEPTH_STAGE_ORDER[-1],
        )

    def persist_depth_capture_change(
        project_id: str,
        capture_id: str,
        transform: Callable[[DepthCapture], DepthCapture],
    ) -> Project:
        def update(project: Project) -> Project:
            captures = [capture.model_copy(deep=True) for capture in project.depthCaptures]
            index = next((i for i, capture in enumerate(captures) if capture.id == capture_id), None)
            if index is None:
                return project
            captures[index] = transform(captures[index])
            return project.model_copy(update={
                "depthCaptures": captures,
                "updatedAt": captures[index].updatedAt,
            })
        return update_project(project_id, update)

    def failed_depth_capture(capture: DepthCapture, code: str, message: str) -> DepthCapture:
        stage = capture.currentStage or first_unfinished_depth_stage(capture)
        state = next(item for item in capture.stages if item.name == stage)
        state.status = "failed"
        state.completedAt = None
        now = datetime.now(timezone.utc).isoformat()
        capture.status = "failed"
        capture.currentStage = stage
        capture.updatedAt = now
        capture.completedAt = None
        capture.error = depth_error(code, message, stage)
        return capture

    def inspect_capture(project: Project, capture: DepthCapture) -> bool:
        return inspect_depth_artifacts(
            data_dir=data_dir,
            project_id=project.id,
            capture_id=capture.id,
            source_reference_video_id=capture.sourceReferenceVideoId,
            algorithm=capture.algorithmVersion,
        ).valid

    def reconcile_depth_captures() -> None:
        try:
            with project_write_lock:
                projects = _read_projects(data_dir)
                changed = False
                for project_index, project in enumerate(projects):
                    project_changed = False
                    captures = [capture.model_copy(deep=True) for capture in project.depthCaptures]
                    for capture in captures:
                        if LOCAL_RUN_POLICY.active(capture.status):
                            failed_depth_capture(
                                capture,
                                "depth_capture_interrupted",
                                "本地服务曾退出，深度捕捉已中断，请重新开始。",
                            )
                            changed = True
                            project_changed = True
                        elif capture.status == "completed" and not inspect_capture(project, capture):
                            failed_depth_capture(
                                capture,
                                "depth_capture_artifacts_invalid",
                                "已完成深度捕捉缺少有效产物，请重新开始。",
                            )
                            changed = True
                            project_changed = True
                    if project_changed:
                        projects[project_index] = project.model_copy(update={
                            "depthCaptures": captures,
                            "activeDepthCaptureId": (
                                project.activeDepthCaptureId
                                if any(item.id == project.activeDepthCaptureId and item.status == "completed" for item in captures)
                                else None
                            ),
                            "updatedAt": max(item.updatedAt for item in captures),
                        })
                if changed:
                    _write_projects(data_dir, projects)
        except OSError:
            logging.getLogger(__name__).exception("无法协调中断的深度捕捉任务")

    def semantic_analysis_error(code: str, message: str, retryable: bool) -> SemanticAnalysisError:
        return SemanticAnalysisError(code=code, message=message, retryable=retryable)

    def semantic_task_matches(
        project: Project,
        task: SemanticAnalysis,
        expected: SemanticAnalysis,
    ) -> bool:
        preprocessing = project.localPreprocessing
        return (
            task.id == expected.id
            and task.sourceReferenceMediaId == expected.sourceReferenceMediaId
            and task.sourcePreprocessingId == expected.sourcePreprocessingId
            and task.provider == expected.provider
            and task.model == expected.model
            and task.promptVersion == expected.promptVersion
            and task.schemaVersion == expected.schemaVersion
            and project.referenceMedia is not None
            and project.referenceMedia.id == expected.sourceReferenceMediaId
            and preprocessing is not None
            and preprocessing.id == expected.sourcePreprocessingId
            and preprocessing.sourceReferenceMediaId == expected.sourceReferenceMediaId
            and preprocessing.status == "completed"
        )

    def persist_semantic_analysis_change(
        project_id: str,
        expected: SemanticAnalysis,
        transform: Callable[[SemanticAnalysis], SemanticAnalysis],
    ) -> Project:
        def update(project: Project) -> Project:
            task = project.semanticAnalysis
            if task is None or not semantic_task_matches(project, task, expected):
                return project
            updated = transform(task.model_copy(deep=True))
            if updated.status == "completed":
                write_semantic_analysis_checkpoint(data_dir, project.id, updated)
            return project.model_copy(update={
                "semanticAnalysis": updated,
                "updatedAt": updated.updatedAt,
            })
        return update_project(project_id, update)

    def default_analysis_provider(
        *, provider: str, credential: Optional[str], base_url: Optional[str], model: str,
    ):
        client = new_provider_http_client(timeout=30.0)
        if provider == "bailian":
            return BailianAnalysisProvider(client, credential)
        if provider == "local_openai_compatible" and base_url is not None:
            return LocalOpenAICompatibleAnalysisProvider(client, base_url, credential)
        if provider == "openai":
            return OpenAIAnalysisProvider(client, credential)
        if provider == "doubao":
            return DoubaoAnalysisProvider(client, credential=credential)
        if provider == "gemini":
            return GeminiAnalysisProvider(client, credential)
        if provider == "grok":
            return GrokAnalysisProvider(client, credential)
        if provider == "claude":
            return ClaudeAnalysisProvider(client, credential)
        if provider == "chatanywhere":
            return ChatAnywhereAnalysisProvider(client, credential)
        raise ProviderAnalysisError(ProviderFailure.for_code("provider_unconfigured"))

    def analysis_provider_for(
        *, provider: str, credential: Optional[str], base_url: Optional[str], model: str,
    ):
        registry = configured_provider_registry
        if registry is None:
            return default_analysis_provider(
                provider=provider, credential=credential, base_url=base_url, model=model,
            )
        factory = getattr(registry, "create", registry)
        return factory(provider=provider, credential=credential, base_url=base_url, model=model)

    def analysis_model_is_supported(provider: str, model: str) -> bool:
        try:
            return model_is_allowed(provider, model)
        except ValueError:
            return False

    def close_default_analysis_provider(provider) -> None:
        if configured_provider_registry is not None:
            return
        client = getattr(provider, "_client", None)
        if isinstance(client, httpx.Client):
            client.close()

    def fail_semantic_analysis(
        project_id: str,
        expected: SemanticAnalysis,
        error: SemanticAnalysisError,
    ) -> None:
        now = now_utc().isoformat()

        def fail(task: SemanticAnalysis) -> SemanticAnalysis:
            task.status = "failed"
            task.updatedAt = now
            task.completedAt = None
            task.error = error
            task.result = None
            return task
        try:
            persist_semantic_analysis_change(project_id, expected, fail)
        except (OSError, HTTPException):
            logging.getLogger(__name__).warning("语义分析状态无法保存：projectId=%s", project_id)

    def run_semantic_analysis_job(project_id: str) -> None:
        expected: Optional[SemanticAnalysis] = None
        project_for_run: Optional[Project] = None
        try:
            def mark_running(project: Project) -> Project:
                nonlocal expected, project_for_run
                task = project.semanticAnalysis
                if task is None or task.status != "queued":
                    return project
                if not semantic_task_matches(project, task, task):
                    return project
                now = now_utc().isoformat()
                running = task.model_copy(deep=True)
                running.status = "running"
                running.startedAt = running.startedAt or now
                running.updatedAt = now
                running.error = None
                expected = running.model_copy(deep=True)
                project_for_run = project.model_copy(update={"semanticAnalysis": running})
                return project.model_copy(update={
                    "semanticAnalysis": running,
                    "updatedAt": now,
                })

            update_project(project_id, mark_running)
            if expected is None or project_for_run is None:
                return
            task = expected
            setting, credential, _ = analysis_provider_snapshot(task.provider)
            if (
                setting is None
                or setting.model != task.model
                or task.provider != "local_openai_compatible" and credential is None
            ):
                raise ProviderAnalysisError(ProviderFailure.for_code("provider_unconfigured"))
            provider = analysis_provider_for(
                provider=task.provider,
                credential=credential,
                base_url=setting.baseUrl,
                model=task.model,
            )
            try:
                runner = semantic_analysis_runner or run_semantic_analysis
                result = runner(
                    data_dir=data_dir,
                    project=project_for_run,
                    provider=provider,
                    model=task.model,
                )
            finally:
                close_default_analysis_provider(provider)
            now = now_utc().isoformat()

            def complete(current: SemanticAnalysis) -> SemanticAnalysis:
                current.status = "completed"
                current.updatedAt = now
                current.completedAt = now
                current.result = result.model_copy(update={"version": 1})
                current.error = None
                return current
            persist_semantic_analysis_change(project_id, task, complete)
        except ProviderAnalysisError as error:
            if expected is not None:
                fail_semantic_analysis(
                    project_id, expected,
                    semantic_analysis_error(
                        error.failure.code, error.failure.message, error.failure.retryable,
                    ),
                )
        except (OSError, ValueError):
            if expected is not None:
                checkpoint = load_completed_checkpoint_for_recovery(data_dir, project_id, expected)
                if checkpoint is not None:
                    fail_semantic_analysis(
                        project_id, expected,
                        semantic_analysis_error(
                            "semantic_analysis_finalize_failed",
                            "语义分析结果已验证，但本地最终保存失败，请直接重试语义分析。",
                            True,
                        ),
                    )
                    return
                fail_semantic_analysis(
                    project_id, expected,
                    semantic_analysis_error(
                        "semantic_analysis_input_unavailable",
                        "语义分析输入不可用，请重新完成本地预处理后再试。",
                        True,
                    ),
                )
        except Exception:
            if expected is not None:
                fail_semantic_analysis(
                    project_id, expected,
                    semantic_analysis_error(
                        "semantic_analysis_unexpected_error",
                        "语义分析出现未预期错误，请重试。",
                        True,
                    ),
                )

    def reconcile_interrupted_semantic_analyses() -> None:
        try:
            with project_write_lock:
                projects = _read_projects(data_dir)
                changed = False
                for index, project in enumerate(projects):
                    task = project.semanticAnalysis
                    if task is None or not LOCAL_RUN_POLICY.active(task.status):
                        continue
                    interrupted = interrupted_semantic_analysis(task, now_utc())
                    projects[index] = project.model_copy(update={
                        "semanticAnalysis": interrupted,
                        "updatedAt": interrupted.updatedAt,
                    })
                    changed = True
                if changed:
                    _write_projects(data_dir, projects)
        except OSError:
            logging.getLogger(__name__).warning("无法协调中断的语义分析任务")

    reconcile_interrupted_preprocessing()
    reconcile_depth_captures()
    reconcile_interrupted_semantic_analyses()
    compute_jobs = local_compute_queue or LocalComputeJobQueue()
    if preprocessing_queue_factory is None:
        preprocessing_jobs = LocalPreprocessingQueueAdapter(compute_jobs, run_preprocessing_job)
    else:
        preprocessing_jobs = preprocessing_queue_factory(run_preprocessing_job)
    if preprocessing_queue_factory is not None:
        app.add_event_handler("shutdown", preprocessing_jobs.shutdown)

    if semantic_analysis_queue_factory is None:
        semantic_analysis_jobs = SemanticAnalysisQueueAdapter(compute_jobs, run_semantic_analysis_job)
    else:
        semantic_analysis_jobs = semantic_analysis_queue_factory(run_semantic_analysis_job)
    app.add_event_handler("shutdown", semantic_analysis_jobs.shutdown)

    def is_reference_video_request(request: Request) -> bool:
        return request.method == "PUT" and reference_video_path_pattern.fullmatch(request.url.path) is not None

    def is_reference_media_request(request: Request) -> bool:
        return request.method == "PUT" and (
            is_reference_video_request(request)
            or reference_media_path_pattern.fullmatch(request.url.path) is not None
        )

    def is_character_motion_upload_request(request: Request) -> bool:
        return request.method == "POST" and bool(re.fullmatch(r"/api/projects/[^/]+/character-motion/character", request.url.path))

    def is_sensitive_analysis_mutation(request: Request) -> bool:
        path = request.url.path
        if request.method == "POST" and path == "/api/project-backups/restore":
            return True
        return (
            request.method in ("POST", "PUT")
            and bool(re.fullmatch(r"/api/projects/[^/]+/(?:reproduction|preparation|preproduction|timeline|batch-edits|upscale|character-motion|toolkit|backup)(?:/.*)?", path))
        ) or (
            request.method == "PUT" and bool(re.fullmatch(r"/api/analysis-providers/[^/]+/configuration", path))
        ) or (
            request.method == "POST" and (
                bool(re.fullmatch(r"/api/analysis-providers/[^/]+/(?:test-connection|connection-test)", path))
                or bool(re.fullmatch(r"/api/projects/[^/]+/semantic-analysis", path))
            )
        )

    def sensitive_mutation_rejection(request: Request) -> Optional[JSONResponse]:
        origin = request.headers.get("origin")
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if origin is not None and origin not in _ALLOWED_ANALYSIS_MUTATION_ORIGINS:
            return JSONResponse(status_code=403, content={"detail": {
                "code": "request_origin_rejected", "message": "请求来源不被本地分析服务允许。",
            }})
        expected_type = "application/zip" if request.url.path == "/api/project-backups/restore" else "application/json"
        if content_type != expected_type:
            return JSONResponse(status_code=403, content={"detail": {
                "code": "request_intent_rejected", "message": "请求内容类型不符合该接口要求。",
            }})
        if origin is not None and request.headers.get("x-aivre-intent") != _ANALYSIS_REQUEST_INTENT:
            return JSONResponse(status_code=403, content={"detail": {
                "code": "request_intent_rejected", "message": "敏感分析请求缺少明确意图。",
            }})
        return None

    def invalid_multipart_response() -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "code": "invalid_multipart",
                    "message": "请选择一个视频文件。",
                }
            },
        )

    async def close_request_uploads(request: Request) -> None:
        try:
            form = await request.form()
        except Exception:
            return
        for _, value in form.multi_items():
            if isinstance(value, StarletteUploadFile):
                await value.close()

    @app.exception_handler(StarletteHTTPException)
    async def reference_video_http_exception_handler(
        request: Request,
        error: StarletteHTTPException,
    ):
        if is_reference_media_request(request) and error.status_code == 400 and not isinstance(error.detail, dict):
            return invalid_multipart_response()
        return await http_exception_handler(request, error)

    @app.exception_handler(RequestValidationError)
    async def reference_video_validation_exception_handler(
        request: Request,
        error: RequestValidationError,
    ):
        has_invalid_file = any(
            tuple(item.get("loc", ()))[:2] == ("body", "file")
            for item in error.errors()
        )
        if is_reference_media_request(request) and has_invalid_file:
            await close_request_uploads(request)
            return invalid_multipart_response()
        return await request_validation_exception_handler(request, error)

    @app.middleware("http")
    async def reject_invalid_reference_video_request(request, call_next):
        if is_character_motion_upload_request(request) or (request.method == "POST" and re.fullmatch(r"/api/projects/[^/]+/preproduction/assets", request.url.path)):
            origin = request.headers.get("origin")
            if origin is not None and origin not in _ALLOWED_ANALYSIS_MUTATION_ORIGINS:
                return JSONResponse(status_code=403, content={"detail": {
                    "code": "request_origin_rejected", "message": "请求来源不被本地分析服务允许。",
                }})
        elif is_sensitive_analysis_mutation(request):
            rejection = sensitive_mutation_rejection(request)
            if rejection is not None:
                return rejection
        if request.method == "POST" and re.fullmatch(r"/api/projects/[^/]+/(?:reproduction|character-motion)/runs", request.url.path):
            return JSONResponse(status_code=410, content={"detail": {
                "code": "final_generation_disabled",
                "message": "当前工作台只准备素材与方案，请导出后在外部工具执行视频生成。",
            }})
        if not is_reference_media_request(request):
            return await call_next(request)

        project_id = request.url.path.split("/")[3]
        try:
            project = ensure_project_exists(project_id)
        except HTTPException as error:
            return JSONResponse(status_code=error.status_code, content={"detail": error.detail})
        task = project.localPreprocessing
        if task is not None and task.status in {"queued", "running"}:
            return JSONResponse(
                status_code=409,
                content={"detail": {
                    "code": "preprocessing_in_progress",
                    "message": "本地预处理正在排队或运行，请完成后再更换参考视频。",
                }},
            )
        if any(capture.status in {"queued", "running"} for capture in project.depthCaptures):
            return JSONResponse(
                status_code=409,
                content={"detail": {
                    "code": "depth_capture_in_progress",
                    "message": "深度捕捉正在排队或运行，请完成后再更换参考视频。",
                }},
            )
        if compute_jobs.is_active("person_control", project_id):
            return JSONResponse(status_code=409, content={"detail": {
                "code": "person_control_in_progress",
                "message": "人物控制素材正在排队或提取，请完成后再更换参考素材。",
            }})
        semantic = project.semanticAnalysis
        if semantic is not None and semantic.status in {"queued", "running"}:
            return JSONResponse(
                status_code=409,
                content={"detail": {
                    "code": "semantic_analysis_in_progress",
                    "message": "语义分析正在排队或运行，请完成后再更换参考素材。",
                }},
            )

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                parsed_content_length = int(content_length)
            except ValueError:
                parsed_content_length = -1
            if parsed_content_length < 0:
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail": {
                            "code": "invalid_multipart",
                            "message": "上传请求的 Content-Length 无效。",
                        }
                    },
                )
            if parsed_content_length > max_multipart_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": {
                            "code": "video_too_large",
                            "message": "上传请求体明显超过 200,000,000 字节文件上限，请选择更小的参考视频。",
                        }
                    },
                )
        return await call_next(request)

    def commit_project_reference_media(
        project_id: str,
        staged_path: Path,
        reference: ReferenceMedia,
    ) -> Project:
        with project_write_lock:
            projects = _read_projects(data_dir)
            project_index = next(
                (index for index, project in enumerate(projects) if project.id == project_id),
                None,
            )
            if project_index is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "project_not_found", "message": "复刻项目不存在。"},
                )
            previous_project = projects[project_index]
            task = previous_project.localPreprocessing
            if task is not None and task.status in {"queued", "running"}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "preprocessing_in_progress",
                        "message": "本地预处理正在排队或运行，请完成后再更换参考视频。",
                    },
                )
            if any(capture.status in {"queued", "running"} for capture in previous_project.depthCaptures):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "depth_capture_in_progress",
                        "message": "深度捕捉正在排队或运行，请完成后再更换参考视频。",
                    },
                )
            if compute_jobs.is_active("person_control", project_id):
                raise HTTPException(status_code=409, detail={
                    "code": "person_control_in_progress",
                    "message": "人物控制素材正在排队或提取，请完成后再更换参考素材。",
                })
            semantic = previous_project.semanticAnalysis
            if semantic is not None and semantic.status in {"queued", "running"}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "semantic_analysis_in_progress",
                        "message": "语义分析正在排队或运行，请完成后再更换参考素材。",
                    },
                )
            old_path = (
                resolve_reference_media_path(data_dir, project_id, previous_project.referenceMedia)
                if previous_project.referenceMedia is not None
                else None
            )
            final_path = managed_reference_media_path(data_dir, project_id, reference)
            try:
                promote_staged_reference_media(staged_path, final_path)
            except OSError:
                discard_managed_file(final_path)
                raise
            updated_project = previous_project.model_copy(
                update={
                    "referenceMedia": reference,
                    "localPreprocessing": None,
                    "activeDepthCaptureId": None,
                    "semanticAnalysis": None,
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }
            )
            projects[project_index] = updated_project
            try:
                _write_projects(data_dir, projects)
            except OSError:
                discard_managed_file(final_path)
                raise
            if old_path is not None:
                try:
                    discard_managed_file(old_path)
                except OSError:
                    logging.getLogger(__name__).warning("无法删除已替换的参考视频文件：%s", old_path)
            if previous_project.localPreprocessing is not None:
                try:
                    discard_preprocessing(
                        data_dir, project_id, previous_project.localPreprocessing.id,
                    )
                except OSError:
                    logging.getLogger(__name__).warning("无法删除已失效的本地预处理产物：%s", project_id)
            if previous_project.semanticAnalysis is not None:
                try:
                    discard_semantic_analysis_checkpoint(
                        data_dir, project_id, previous_project.semanticAnalysis,
                    )
                except OSError:
                    logging.getLogger(__name__).warning("无法删除已失效的语义分析检查点：%s", project_id)
            return updated_project

    def project_response_data(project: Project) -> dict:
        payload = project.model_dump()
        if "depthCaptures" not in project.model_fields_set:
            payload.pop("depthCaptures", None)
        if "activeDepthCaptureId" not in project.model_fields_set:
            payload.pop("activeDepthCaptureId", None)
        if "semanticAnalysis" not in project.model_fields_set:
            payload.pop("semanticAnalysis", None)
        return payload

    @app.get("/api/projects", response_model=None)
    def list_projects() -> list[dict]:
        try:
            projects = _read_projects(data_dir)
            projects = [project_read_projection(project) for project in projects]
        except OSError as error:
            raise storage_error(error) from error
        return [project_response_data(project) for project in sorted(
            projects, key=lambda project: project.updatedAt, reverse=True,
        )]

    @app.get("/api/capabilities")
    def get_capabilities() -> dict[str, dict[str, str]]:
        return {
            "analysisService": {"state": "unconfigured", "label": "未配置"},
            "localComfyui": {"state": "disconnected", "label": "未连接"},
        }

    @app.get("/api/analysis-providers", response_model=list[AnalysisProviderConfiguration])
    def get_analysis_provider_configurations() -> list[AnalysisProviderConfiguration]:
        try:
            with analysis_provider_configuration_lock:
                return [_analysis_provider_configuration_locked(provider) for provider in PROVIDER_IDS]
        except SecureStorageUnavailable as error:
            raise secure_storage_unavailable() from error
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "analysis_settings_unavailable",
                    "message": "分析供应商设置不可用。",
                },
            ) from error

    def save_analysis_provider_configuration(
        provider: str,
        model: str,
        base_url: Optional[str],
        api_key: Optional[str],
    ):
        with analysis_provider_configuration_lock:
            try:
                candidate, prepared_settings = configured_analysis_settings.prepare_save(
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    selected_provider=provider,
                )
            except (OSError, ValueError) as error:
                if isinstance(error, OSError):
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "code": "analysis_settings_unavailable", "message": "分析供应商设置不可用。"},
                    ) from error
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "invalid_analysis_provider_configuration", "message": "分析供应商配置无效。"},
                ) from error

            try:
                credential_store_for_update = credentials()
                previous_credential = credential_store_for_update.get(provider)
            except SecureStorageUnavailable as error:
                raise secure_storage_unavailable() from error

            if provider != "local_openai_compatible" and not (api_key or previous_credential):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "invalid_analysis_provider_configuration", "message": "分析供应商配置无效。"},
                )
            if api_key is not None and api_key != previous_credential:
                try:
                    candidate, prepared_settings = configured_analysis_settings.prepare_save(
                        provider=provider,
                        model=model,
                        base_url=base_url,
                        selected_provider=provider,
                        credential_changed=True,
                    )
                except (OSError, ValueError) as error:
                    if isinstance(error, OSError):
                        raise HTTPException(
                            status_code=503,
                            detail={"code": "analysis_settings_unavailable", "message": "分析供应商设置不可用。"},
                        ) from error
                    raise HTTPException(
                        status_code=400,
                        detail={"code": "invalid_analysis_provider_configuration", "message": "分析供应商配置无效。"},
                    ) from error

            credential_write_succeeded = False
            credential_state = previous_credential is not None
            try:
                if api_key is not None:
                    credential_store_for_update.set(provider, api_key)
                    credential_write_succeeded = True
                    credential_state = True
                committed_settings = configured_analysis_settings.commit(prepared_settings)
                candidate = next(item for item in committed_settings.providers if item.provider == provider)
            except (OSError, SecureStorageUnavailable, AnalysisSettingsConflictError) as error:
                if credential_write_succeeded:
                    try:
                        if previous_credential is None:
                            credential_store_for_update.delete(provider)
                        else:
                            credential_store_for_update.set(provider, previous_credential)
                    except SecureStorageUnavailable as rollback_error:
                        logging.getLogger(__name__).warning("无法回退分析供应商凭据：%s", provider)
                        raise secure_storage_unavailable() from rollback_error
                if isinstance(error, SecureStorageUnavailable):
                    raise secure_storage_unavailable() from error
                if isinstance(error, AnalysisSettingsConflictError):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "analysis_provider_configuration_changed",
                            "message": "分析供应商配置已变化，请重新保存。",
                        },
                    ) from error
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "analysis_settings_unavailable", "message": "分析供应商设置不可用。"},
                ) from error
            return candidate, committed_settings, credential_state

    @app.put(
        "/api/analysis-providers/{provider}/configuration",
        response_model=AnalysisProviderConfiguration,
    )
    async def put_analysis_provider_configuration(
        provider: str,
        request: Request,
    ) -> AnalysisProviderConfiguration:
        try:
            payload = await request.json()
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_analysis_provider_configuration", "message": "分析供应商配置无效。"},
            ) from None
        if not isinstance(payload, dict) or set(payload) - {"apiKey", "baseUrl", "model"}:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_analysis_provider_configuration", "message": "分析供应商配置无效。"},
            )
        model = payload.get("model")
        api_key = payload.get("apiKey")
        base_url = payload.get("baseUrl")
        if (
            not isinstance(model, str)
            or not model.strip()
            or api_key is not None and (not isinstance(api_key, str) or not api_key.strip())
            or base_url is not None and not isinstance(base_url, str)
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_analysis_provider_configuration", "message": "分析供应商配置无效。"},
            )
        if provider not in PROVIDER_IDS:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_analysis_provider_configuration", "message": "分析供应商配置无效。"},
            )
        if not analysis_model_is_supported(provider, model):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_analysis_model", "message": "所选模型不在该分析供应商的可用清单中。"},
            )
        candidate, committed_settings, credential_state = await run_in_threadpool(
            save_analysis_provider_configuration,
            provider,
            model,
            base_url,
            api_key,
        )

        catalog = provider_for(provider)
        return AnalysisProviderConfiguration(
            provider=provider,
            label=catalog.label,
            models=tuple(model.model_dump() for model in catalog.models),
            model=candidate.model,
            baseUrl=candidate.baseUrl,
            credentialState="configured" if credential_state else "unconfigured",
            selectedProvider=committed_settings.selectedProvider,
            configurationRevision=candidate.configurationRevision,
            catalogVersion=candidate.catalogVersion,
            verificationState=candidate.verificationState,
            verifiedAt=candidate.verifiedAt,
            failedAt=candidate.failedAt,
            errorCode=candidate.errorCode,
        )

    @app.post("/api/analysis-providers/{provider}/test-connection", response_model=None)
    @app.post("/api/analysis-providers/{provider}/connection-test", response_model=None)
    def test_analysis_provider_connection(provider: str) -> dict[str, str]:
        try:
            setting, credential, _ = analysis_provider_snapshot(provider)
        except SecureStorageUnavailable as error:
            raise secure_storage_unavailable() from error
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_analysis_provider", "message": "分析供应商无效。"},
            ) from error
        if (
            setting is None
            or provider != "local_openai_compatible" and credential is None
            or not analysis_model_is_supported(provider, setting.model)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_provider_unconfigured",
                    "message": "所选分析供应商或模型尚未配置。",
                },
            )
        configured_provider = None
        configuration_revision = setting.configurationRevision
        catalog_version = setting.catalogVersion

        def persist_verification(
            *, state: Literal["available", "failed"], error_code: Optional[str] = None,
        ) -> None:
            try:
                persisted = configured_analysis_settings.record_verification(
                    provider=provider,
                    configuration_revision=configuration_revision,
                    catalog_version=catalog_version,
                    state=state,
                    error_code=error_code,
                    now=now_utc().isoformat(),
                )
            except (OSError, ValueError) as error:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "analysis_settings_unavailable",
                        "message": "分析供应商设置不可用。",
                    },
                ) from error
            if not persisted:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "analysis_provider_configuration_changed",
                        "message": "分析供应商配置已变化，请重新测试连接。",
                    },
                )

        try:
            configured_provider = analysis_provider_for(
                provider=provider,
                credential=credential,
                base_url=setting.baseUrl,
                model=setting.model,
            )
            configured_provider.test_connection(setting.model)
        except ProviderAnalysisError as error:
            persist_verification(state="failed", error_code=error.failure.code)
            raise HTTPException(
                status_code=502,
                detail=error.failure.model_dump(),
            ) from None
        except Exception:
            persist_verification(state="failed", error_code="provider_error")
            raise HTTPException(
                status_code=502,
                detail=semantic_analysis_error(
                    "provider_error", "分析服务暂时不可用，请稍后重试。", True,
                ).model_dump(),
            ) from None
        finally:
            if configured_provider is not None:
                close_default_analysis_provider(configured_provider)
        persist_verification(state="available")
        try:
            verified, current_credential, selected_provider = analysis_provider_snapshot(provider)
        except SecureStorageUnavailable as error:
            raise secure_storage_unavailable() from error
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "analysis_settings_unavailable", "message": "分析供应商设置不可用。"},
            ) from error
        if (
            verified is None
            or verified.configurationRevision != configuration_revision
            or verified.catalogVersion != catalog_version
            or verified.verificationState != "available"
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_provider_configuration_changed", "message": "分析供应商配置已变化，请重新测试连接。"},
            )
        return analysis_provider_configuration_snapshot(
            provider=provider,
            setting=verified,
            credential_configured=current_credential is not None,
            selected_provider=selected_provider,
        ).model_dump() | {"status": "connected"}

    @app.post("/api/projects/{project_id}/semantic-analysis", response_model=None)
    def start_semantic_analysis(
        project_id: str,
        payload: StartSemanticAnalysisInput,
        response: Response,
    ):
        if not payload.disclosureAccepted:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "analysis_disclosure_required",
                    "message": "请确认已同意将分析代理发送给所选服务。",
                },
            )
        idempotent = False

        def queue_analysis(project: Project) -> Project:
            nonlocal idempotent
            reference = project.referenceMedia
            preprocessing = project.localPreprocessing
            if (
                reference is None
                or preprocessing is None
                or preprocessing.status != "completed"
                or preprocessing.sourceReferenceMediaId != reference.id
                or preprocessing.mediaType != reference.type
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "semantic_analysis_preprocessing_required",
                        "message": "请先完成与当前参考素材一致的本地预处理。",
                    },
                )
            try:
                artifacts = inspect_completed_stages(
                    preprocessing,
                    preprocessing_directory(data_dir, project.id, preprocessing.id),
                )
            except OSError as error:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "semantic_analysis_preprocessing_invalid",
                        "message": "本地预处理产物无效，请重新完成预处理。",
                    },
                ) from error
            if artifacts.firstInvalidStage is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "semantic_analysis_preprocessing_invalid",
                        "message": "本地预处理产物无效，请重新完成预处理。",
                    },
                )
            existing = project.semanticAnalysis
            if existing is not None and existing.status in {"queued", "running"}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "semantic_analysis_in_progress",
                        "message": "语义分析正在排队或运行，请稍后再试。",
                    },
                )
            if (
                existing is not None
                and existing.status == "failed"
                and existing.sourceReferenceMediaId == reference.id
                and existing.sourcePreprocessingId == preprocessing.id
                and existing.provider == payload.provider
                and existing.model == payload.model
                and existing.promptVersion == PROMPT_VERSION
                and existing.schemaVersion == SCHEMA_VERSION
            ):
                checkpoint = load_completed_checkpoint_for_recovery(data_dir, project.id, existing)
                if checkpoint is not None:
                    idempotent = True
                    return project.model_copy(update={
                        "semanticAnalysis": checkpoint,
                        "updatedAt": checkpoint.updatedAt,
                    })
            try:
                setting, credential, _ = analysis_provider_snapshot(payload.provider)
                has_credential = credential is not None
            except SecureStorageUnavailable as error:
                raise secure_storage_unavailable() from error
            except (OSError, ValueError) as error:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "invalid_analysis_provider",
                        "message": "分析供应商或模型无效。",
                    },
                ) from error
            if not analysis_model_is_supported(payload.provider, payload.model):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "invalid_analysis_model",
                        "message": "所选模型不在该分析供应商的可用清单中。",
                    },
                )
            if (
                setting is None
                or payload.provider != "local_openai_compatible" and not has_credential
                or setting.model != payload.model
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "analysis_provider_unconfigured",
                        "message": "所选分析供应商或模型尚未配置。",
                    },
                )
            if (
                existing is not None
                and existing.status == "completed"
                and existing.result is not None
                and existing.sourceReferenceMediaId == reference.id
                and existing.sourcePreprocessingId == preprocessing.id
                and existing.provider == payload.provider
                and existing.model == payload.model
                and existing.promptVersion == PROMPT_VERSION
                and existing.schemaVersion == SCHEMA_VERSION
                and completed_checkpoint_matches(data_dir, project.id, existing)
            ):
                idempotent = True
                return project
            task = new_semantic_analysis(
                reference_media_id=reference.id,
                preprocessing_id=preprocessing.id,
                provider=payload.provider,
                model=payload.model,
                now=now_utc(),
            )
            return project.model_copy(update={
                "semanticAnalysis": task,
                "updatedAt": task.updatedAt,
            })

        try:
            project = update_project(project_id, queue_analysis)
        except OSError as error:
            raise storage_error(error) from error
        if idempotent:
            response.status_code = 200
            return project_response_data(project)
        try:
            submitted = semantic_analysis_jobs.submit(project_id)
        except Exception:
            submitted = False
        if not submitted:
            task = project.semanticAnalysis
            if task is not None:
                failure = semantic_analysis_error(
                    "semantic_analysis_queue_unavailable",
                    "语义分析队列暂时不可用，请重试。",
                    True,
                )
                now = now_utc().isoformat()

                def mark_queue_failure(current: SemanticAnalysis) -> SemanticAnalysis:
                    current.status = "failed"
                    current.updatedAt = now
                    current.completedAt = None
                    current.result = None
                    current.error = failure
                    return current
                try:
                    persist_semantic_analysis_change(project_id, task, mark_queue_failure)
                except (OSError, HTTPException):
                    logging.getLogger(__name__).warning(
                        "语义分析队列拒绝后的状态无法保存：projectId=%s", project_id,
                    )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "semantic_analysis_queue_unavailable",
                    "message": "语义分析队列暂时不可用，请重试。",
                },
            )
        response.status_code = 202
        return project_response_data(project)

    @app.post("/api/projects", status_code=201)
    def create_project(payload: CreateProjectInput) -> Project:
        now = datetime.now(timezone.utc).isoformat()
        project = Project(id=str(uuid4()), name=payload.name.strip(), createdAt=now, updatedAt=now)
        try:
            with project_write_lock:
                projects = _read_projects(data_dir)
                projects.append(project)
                _write_projects(data_dir, projects)
        except OSError as error:
            raise storage_error(error) from error
        return project

    @app.get("/api/projects/{project_id}", response_model=None)
    def get_project(project_id: str) -> dict:
        try:
            projects = _read_projects(data_dir)
            project = next((project for project in projects if project.id == project_id), None)
            if project is not None:
                return project_response_data(project_read_projection(project))
        except OSError as error:
            raise storage_error(error) from error
        raise HTTPException(status_code=404, detail="复刻项目不存在")

    @app.get("/api/projects/{project_id}/reference-video/content", response_model=None)
    def get_reference_video_content(project_id: str, request: Request) -> Union[StreamingResponse, Response]:
        project = ensure_project_exists(project_id)
        if project.referenceVideo is None:
            raise media_not_found()
        return reference_media_content_response(project, request)

    @app.get("/api/projects/{project_id}/reference-media/content", response_model=None)
    def get_reference_media_content(project_id: str, request: Request) -> Union[StreamingResponse, Response]:
        return reference_media_content_response(ensure_project_exists(project_id), request)

    @app.get("/api/projects/{project_id}/depth-captures/{capture_id}/package", response_model=None)
    def get_depth_capture_package(project_id: str, capture_id: str):
        project = ensure_project_exists(project_id)
        capture = next((item for item in project.depthCaptures if item.id == capture_id), None)
        if (capture is None or capture.status != "completed" or project.referenceVideo is None
                or capture.sourceReferenceVideoId != project.referenceVideo.id):
            raise media_not_found()
        output = tempfile.TemporaryFile()
        try:
            with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
                for name in DEPTH_ARTIFACTS:
                    descriptor, _ = open_validated_depth_artifact(
                        artifact=name, data_dir=data_dir, project_id=project.id, capture_id=capture.id,
                        source_reference_video_id=capture.sourceReferenceVideoId,
                        algorithm=capture.algorithmVersion,
                    )
                    with os.fdopen(descriptor, "rb") as source, archive.open(name, "w", force_zip64=True) as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                archive.writestr("capture.json", capture.model_dump_json(indent=2))
                archive.writestr("README.txt", "完整深度素材包，无需 ComfyUI 或提示词。\n"
                    "depth-control.mp4 是灰度控制视频，近白远黑；depth-preview.mp4 是彩色预览。\n"
                    "保留提取产物的完整时长、帧率和尺寸，不按生成模板截短或补帧。\n"
                    "capture.json 包含执行设备、版本、时间线与质量结论；其他 JSON 为提取报告。\n"
                    "下载不代表质量通过；failed 不可用于正式生成，review_required 仍需人工复核。\n")
            output.seek(0)
        except OSError as error:
            output.close()
            if isinstance(error, DepthPreviewUnavailableError) or error.errno in _MEDIA_NOT_FOUND_ERRNOS:
                raise media_not_found() from error
            raise video_storage_error(error) from error
        except BaseException:
            output.close()
            raise

        def chunks():
            try:
                while chunk := output.read(1024 * 1024):
                    yield chunk
            finally:
                output.close()

        return StreamingResponse(chunks(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="depth-materials-{capture.id}.zip"',
        }, background=BackgroundTask(output.close))

    @app.get("/api/projects/{project_id}/depth-captures/{capture_id}/preview", response_model=None)
    def get_depth_capture_preview(
        project_id: str,
        capture_id: str,
        request: Request,
    ) -> Union[StreamingResponse, Response]:
        project = ensure_project_exists(project_id)
        capture = next((item for item in project.depthCaptures if item.id == capture_id), None)
        if (
            capture is None
            or capture.status != "completed"
            or project.referenceVideo is None
            or capture.sourceReferenceVideoId != project.referenceVideo.id
        ):
            raise media_not_found()
        try:
            descriptor, size = open_validated_depth_preview(
                data_dir=data_dir,
                project_id=project.id,
                capture_id=capture.id,
                source_reference_video_id=capture.sourceReferenceVideoId,
                algorithm=capture.algorithmVersion,
            )
        except DepthPreviewUnavailableError as error:
            raise media_not_found() from error
        except OSError as error:
            if error.errno in _MEDIA_NOT_FOUND_ERRNOS:
                raise media_not_found() from error
            raise video_storage_error(error) from error
        return media_response_from_descriptor(descriptor, size, request.headers.get("range"))

    @app.get("/api/projects/{project_id}/depth-captures/{capture_id}/video", response_model=None)
    def get_depth_capture_video(
        project_id: str,
        capture_id: str,
        request: Request,
        download: bool = False,
    ) -> Union[StreamingResponse, Response]:
        project = ensure_project_exists(project_id)
        capture = next((item for item in project.depthCaptures if item.id == capture_id), None)
        if (
            capture is None
            or capture.status != "completed"
            or project.referenceVideo is None
            or capture.sourceReferenceVideoId != project.referenceVideo.id
        ):
            raise media_not_found()
        try:
            descriptor, size = open_validated_depth_artifact(
                artifact="depth-control.mp4", data_dir=data_dir, project_id=project.id,
                capture_id=capture.id, source_reference_video_id=capture.sourceReferenceVideoId,
                algorithm=capture.algorithmVersion,
            )
        except DepthPreviewUnavailableError as error:
            raise media_not_found() from error
        except OSError as error:
            if error.errno in _MEDIA_NOT_FOUND_ERRNOS:
                raise media_not_found() from error
            raise video_storage_error(error) from error
        response = media_response_from_descriptor(descriptor, size, request.headers.get("range"))
        if download:
            response.headers["Content-Disposition"] = f'attachment; filename="depth-{capture.id}.mp4"'
        return response

    @app.post("/api/projects/{project_id}/local-preprocessing", response_model=Project)
    def start_local_preprocessing(project_id: str, response: Response) -> Project:
        completed = False
        dispatching = False
        stale_semantic_analysis: Optional[SemanticAnalysis] = None

        def prepare_for_start(project: Project) -> Project:
            nonlocal completed, dispatching, stale_semantic_analysis
            if project.referenceMedia is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "reference_video_required",
                        "message": "请先上传参考视频后再开始本地预处理。",
                    },
                )
            existing = project.localPreprocessing
            if existing is not None and existing.status in {"queued", "running"}:
                is_active = getattr(preprocessing_jobs, "is_active", None)
                if (
                    project.id in dispatching_project_ids
                    or is_active is None
                    or is_active(project.id)
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "preprocessing_in_progress",
                            "message": "本地预处理正在排队或运行，请稍后再试。",
                        },
                    )
                existing = interrupted_preprocessing(project, existing)
            if (
                existing is not None
                and existing.status == "completed"
                and existing.sourceReferenceMediaId == project.referenceMedia.id
                and existing.mediaType == project.referenceMedia.type
                and existing.algorithmVersion == ALGORITHM_VERSION
            ):
                directory = preprocessing_directory(data_dir, project.id, existing.id)
                if validate_completed_stages(existing, directory).firstInvalidStage is None:
                    completed = True
                    return project
            now = datetime.now(timezone.utc)
            if (
                existing is not None
                and existing.status == "failed"
                and existing.sourceReferenceMediaId == project.referenceMedia.id
                and existing.mediaType == project.referenceMedia.type
                and existing.algorithmVersion == ALGORITHM_VERSION
            ):
                directory = preprocessing_directory(data_dir, project.id, existing.id)
                retry = validate_completed_stages(existing, directory).preprocessing
                restart_stage = retry.currentStage or first_unfinished_stage(retry)
                if all(state.status == "completed" for state in retry.stages):
                    restart_stage = stage_order_for(retry.mediaType)[0]
                reset_stage_artifacts(directory, restart_stage, retry.mediaType)
                reset_stages_from(retry, restart_stage)
                retry.status = "queued"
                retry.currentStage = None
                retry.queuedAt = now.isoformat()
                retry.startedAt = None
                retry.completedAt = None
                retry.updatedAt = now.isoformat()
                retry.proxySummary = None
                retry.reproducibilityAssessment = None
                retry.error = None
                task = retry
            else:
                task = new_local_preprocessing(
                    str(uuid4()), project.referenceMedia.id, project.referenceMedia.type, now,
                )
            dispatching_project_ids.add(project.id)
            dispatching = True
            stale_semantic_analysis = project.semanticAnalysis
            return project.model_copy(update={
                "localPreprocessing": task,
                "semanticAnalysis": None,
                "updatedAt": task.updatedAt,
            })

        try:
            try:
                project = update_project(project_id, prepare_for_start)
            except OSError as error:
                raise video_storage_error(error) from error
            if completed:
                response.status_code = 200
                return project
            if stale_semantic_analysis is not None:
                try:
                    discard_semantic_analysis_checkpoint(
                        data_dir, project_id, stale_semantic_analysis,
                    )
                except OSError:
                    logging.getLogger(__name__).warning(
                        "无法删除因本地预处理重建而失效的语义分析检查点：%s", project_id,
                    )
            if preprocessing_jobs.submit(project_id):
                response.status_code = 202
                return project

            now = datetime.now(timezone.utc).isoformat()

            def dispatch_failed(task: LocalPreprocessing) -> LocalPreprocessing:
                stage = first_unfinished_stage(task)
                task.status = "failed"
                task.currentStage = stage
                task.updatedAt = now
                task.error = preprocessing_error(
                    "preprocessing_unexpected_error", "本地预处理队列暂时不可用，请重试。", stage,
                )
                return task
            try:
                persist_preprocessing_change(project_id, project.localPreprocessing.id, dispatch_failed)
            except (OSError, HTTPException):
                logging.getLogger(__name__).exception("preprocessing_storage_unavailable")
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "preprocessing_queue_unavailable",
                    "message": "本地预处理队列暂时不可用，请重试。",
                },
            )
        finally:
            if dispatching:
                with project_write_lock:
                    dispatching_project_ids.discard(project_id)

    def depth_capture_preconditions_met(project: Project) -> bool:
        return project.referenceVideo is not None and managed_reference_media_is_safe(
            data_dir, project.id, project.referenceVideo,
        )

    def run_depth_capture_job(project_id: str) -> None:
        capture_id: Optional[str] = None
        try:
            should_run = False

            def prepare(project: Project) -> Project:
                nonlocal capture_id, should_run
                capture = next((item for item in project.depthCaptures if item.status == "queued"), None)
                if capture is None or project.referenceVideo is None:
                    return project
                capture_id = capture.id
                now = datetime.now(timezone.utc).isoformat()
                running = capture.model_copy(deep=True)
                running.status = "running"
                running.startedAt = running.startedAt or now
                running.updatedAt = now
                running.error = None
                captures = [item.model_copy(deep=True) for item in project.depthCaptures]
                index = next(i for i, item in enumerate(captures) if item.id == running.id)
                captures[index] = running
                should_run = True
                return project.model_copy(update={"depthCaptures": captures, "updatedAt": now})

            project = update_project(project_id, prepare)
            if not should_run or capture_id is None or project.referenceVideo is None:
                return
            capture = next(item for item in project.depthCaptures if item.id == capture_id)
            request = DepthCaptureRequest(
                data_dir=data_dir,
                project_id=project.id,
                capture_id=capture.id,
                source_reference_video_id=project.referenceVideo.id,
                input_video=resolve_reference_media_path(data_dir, project.id, project.referenceVideo),
                checkpoint=depth_checkpoint,
                upstream_root=depth_upstream_root,
                execution_device=capture.executionDevice,
                expected_duration_seconds=project.referenceVideo.durationSeconds,
                output_resolution=capture.outputResolution or "480p",
            )

            def stage_started(stage_name: str) -> None:
                now = datetime.now(timezone.utc).isoformat()

                def update(capture: DepthCapture) -> DepthCapture:
                    state = next(item for item in capture.stages if item.name == stage_name)
                    state.status = "running"
                    state.startedAt = now
                    state.completedAt = None
                    capture.currentStage = stage_name
                    capture.updatedAt = now
                    return capture
                persist_depth_capture_change(project_id, capture_id, update)

            def stage_completed(stage_name: str) -> None:
                now = datetime.now(timezone.utc).isoformat()

                def update(capture: DepthCapture) -> DepthCapture:
                    state = next(item for item in capture.stages if item.name == stage_name)
                    state.status = "completed"
                    state.completedAt = now
                    capture.currentStage = stage_name
                    capture.updatedAt = now
                    return capture
                persist_depth_capture_change(project_id, capture_id, update)

            result = depth_capture_runner(
                request=request,
                worker_python=depth_worker_python,
                worker_script=depth_worker_script,
                ffmpeg_path=ffmpeg_path,
                on_stage_started=stage_started,
                on_stage_completed=stage_completed,
            )

            def complete(project: Project) -> Project:
                capture = next((item for item in project.depthCaptures if item.id == capture_id), None)
                if capture is None or capture.status != "running":
                    return project
                if not all(stage.status == "completed" for stage in capture.stages):
                    captures = [
                        failed_depth_capture(item.model_copy(deep=True), "depth_capture_unexpected_error", "深度捕捉阶段状态不一致，请重试。")
                        if item.id == capture_id else item
                        for item in project.depthCaptures
                    ]
                    failed = next(item for item in captures if item.id == capture_id)
                    return project.model_copy(update={
                        "depthCaptures": captures,
                        "updatedAt": failed.updatedAt,
                    })
                now = datetime.now(timezone.utc).isoformat()
                completed = capture.model_copy(deep=True)
                completed.status = "completed"
                completed.executionDevice = result.executionDevice
                completed.currentStage = None
                completed.updatedAt = now
                completed.completedAt = now
                completed.outputSummary = DepthOutputSummary(
                    width=result.width,
                    height=result.height,
                    frameRate=result.frameRate,
                    frameCount=result.frameCount,
                    durationSeconds=result.frameCount / result.frameRate,
                )
                completed.qualityAssessment = result.qualityAssessment
                completed.error = None
                if not inspect_capture(project, completed):
                    completed = failed_depth_capture(
                        completed,
                        "depth_capture_artifacts_invalid",
                        "深度捕捉产物无效，无法完成。",
                    )
                captures = [item.model_copy(deep=True) for item in project.depthCaptures]
                index = next(i for i, item in enumerate(captures) if item.id == capture_id)
                captures[index] = completed
                activate = (
                    completed.status == "completed"
                    and completed.qualityAssessment is not None
                    and completed.qualityAssessment.status == "passed"
                    and project.referenceVideo is not None
                    and completed.sourceReferenceVideoId == project.referenceVideo.id
                )
                return project.model_copy(update={
                    "depthCaptures": captures,
                    "activeDepthCaptureId": capture_id if activate else project.activeDepthCaptureId,
                    "updatedAt": completed.updatedAt,
                })

            update_project(project_id, complete)
        except DepthCaptureFailure as error:
            if capture_id is not None:
                try:
                    persist_depth_capture_change(
                        project_id,
                        capture_id,
                        lambda capture: failed_depth_capture(capture, error.code, error.message),
                    )
                except (OSError, HTTPException):
                    logging.getLogger(__name__).exception("depth_capture_failure_persist_failed")
        except OSError:
            if capture_id is not None:
                try:
                    persist_depth_capture_change(
                        project_id,
                        capture_id,
                        lambda capture: failed_depth_capture(
                            capture, "depth_capture_storage_failed",
                            "深度捕捉结果无法安全保存，请检查数据目录后重试。",
                        ),
                    )
                except (OSError, HTTPException):
                    logging.getLogger(__name__).exception("depth_capture_storage_failure_persist_failed")
        except Exception:
            logging.getLogger(__name__).exception("depth_capture_unexpected_failure")
            if capture_id is not None:
                try:
                    persist_depth_capture_change(
                        project_id,
                        capture_id,
                        lambda capture: failed_depth_capture(
                            capture, "depth_capture_unexpected_error", "本地深度捕捉出现未预期错误，请重试。",
                        ),
                    )
                except (OSError, HTTPException):
                    logging.getLogger(__name__).exception("depth_capture_failure_persist_failed")

    @app.post("/api/projects/{project_id}/depth-captures", response_model=Project)
    def start_depth_capture(
        project_id: str,
        payload: StartDepthCaptureInput,
        response: Response,
    ) -> Project:
        queued = False

        def prepare(project: Project) -> Project:
            nonlocal queued
            if not depth_capture_preconditions_met(project):
                raise HTTPException(status_code=409, detail={
                    "code": "depth_capture_preconditions_not_met",
                    "message": "请先完成当前参考视频的本地预处理和可复现性检查。",
                })
            try:
                cuda, mps = available_depth_devices()
            except DepthDeviceProbeError as error:
                raise HTTPException(status_code=503, detail={
                    "code": "depth_device_probe_unavailable",
                    "message": "无法检查本地深度计算设备，请检查本地运行环境后重试。",
                }) from error
            try:
                device = select_execution_device(payload.devicePreference, cuda=cuda, mps=mps)
            except ValueError as error:
                raise HTTPException(status_code=409, detail={
                    "code": "depth_device_unavailable", "message": str(error),
                }) from error
            for existing in project.depthCaptures:
                if (
                    existing.status in {"queued", "running"}
                    and existing.sourceReferenceVideoId == project.referenceVideo.id
                ):
                    raise HTTPException(status_code=409, detail={
                        "code": "depth_capture_in_progress", "message": "深度捕捉正在排队或运行，请稍后再试。",
                    })
            now = datetime.now(timezone.utc).isoformat()
            capture = new_depth_capture(
                project.referenceVideo.id, payload.devicePreference, now, payload.outputResolution,
            )
            capture.executionDevice = device
            queued = True
            return project.model_copy(update={
                "depthCaptures": [*project.depthCaptures, capture], "updatedAt": now,
            })

        try:
            project = update_project(project_id, prepare)
        except OSError as error:
            raise video_storage_error(error) from error
        if not queued:
            return project
        if compute_jobs.submit("depth_capture", project_id, run_depth_capture_job):
            response.status_code = 202
            return project
        capture = project.depthCaptures[-1]
        try:
            persist_depth_capture_change(
                project_id, capture.id,
                lambda item: failed_depth_capture(
                    item, "depth_capture_queue_unavailable", "本地深度捕捉队列暂时不可用，请重试。",
                ),
            )
        except (OSError, HTTPException):
            logging.getLogger(__name__).exception("depth_capture_queue_failure_persist_failed")
        raise HTTPException(status_code=503, detail={
            "code": "depth_capture_queue_unavailable", "message": "本地深度捕捉队列暂时不可用，请重试。",
        })

    @app.post("/api/projects/{project_id}/depth-captures/{capture_id}/confirm-review", response_model=Project)
    def confirm_depth_review(project_id: str, capture_id: str) -> Project:
        def confirm(project: Project) -> Project:
            capture = next((item for item in project.depthCaptures if item.id == capture_id), None)
            if capture is None:
                raise HTTPException(status_code=404, detail={
                    "code": "depth_capture_not_found", "message": "深度捕捉不存在。",
                })
            if project.referenceVideo is None or capture.sourceReferenceVideoId != project.referenceVideo.id:
                raise HTTPException(status_code=409, detail={
                    "code": "depth_capture_stale_reference", "message": "深度捕捉不属于当前参考视频。",
                })
            if capture.status != "completed":
                raise HTTPException(status_code=409, detail={
                    "code": "depth_capture_not_completed", "message": "深度捕捉尚未完成。",
                })
            if not inspect_capture(project, capture):
                raise HTTPException(status_code=409, detail={
                    "code": "depth_capture_artifacts_invalid", "message": "深度捕捉产物无效，无法确认。",
                })
            if capture.qualityAssessment is None or capture.qualityAssessment.status != "review_required":
                raise HTTPException(status_code=409, detail={
                    "code": "depth_review_not_required", "message": "该深度捕捉不需要人工复核确认。",
                })
            if capture.reviewConfirmedAt is not None:
                return project
            now = datetime.now(timezone.utc).isoformat()
            captures = [item.model_copy(deep=True) for item in project.depthCaptures]
            selected = next(item for item in captures if item.id == capture_id)
            selected.reviewConfirmedAt = now
            selected.updatedAt = now
            return project.model_copy(update={
                "depthCaptures": captures,
                "activeDepthCaptureId": capture_id,
                "updatedAt": now,
            })
        try:
            return update_project(project_id, confirm)
        except OSError as error:
            raise video_storage_error(error) from error

    async def upload_reference_media(
        project_id: str,
        file: list[UploadFile],
        *,
        require_video: bool,
    ) -> Project:
        staged = None
        try:
            if len(file) != 1 or not file[0].filename:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "invalid_multipart",
                        "message": "请选择一个视频文件。" if require_video else "请选择一个参考素材文件。",
                    },
                )
            upload = file[0]
            ensure_project_exists(project_id)
            original_name = reference_media_basename(upload.filename)
            if require_video:
                reference_video_format_from_name(original_name)
            staged = await stage_reference_media(
                upload,
                data_dir,
                max_bytes=max(MAX_REFERENCE_VIDEO_BYTES, 30_000_000),
            )
            if detect_reference_media_type(staged.path) == "image":
                facts = probe_reference_image(staged.path, original_name, staged.size_bytes)
                reference = ReferenceImage(
                    id=str(uuid4()),
                    originalName=original_name,
                    format=facts.format,
                    sizeBytes=staged.size_bytes,
                    width=facts.width,
                    height=facts.height,
                    hasTransparency=facts.has_transparency,
                )
            else:
                if staged.size_bytes > max_reference_video_bytes:
                    raise ReferenceMediaError(
                        413,
                        "video_too_large",
                        f"参考视频实测为 {staged.size_bytes:,} 字节，最大允许 {max_reference_video_bytes:,} 字节。",
                    )
                detected_format = detect_reference_video_format(staged.path)
                facts = probe_reference_video(
                    staged.path,
                    f"detected.{detected_format}" if detected_format is not None else original_name,
                    staged.size_bytes,
                    ffprobe_path=ffprobe_path,
                    timeout_seconds=ffprobe_timeout_seconds,
                )
                reference = ReferenceVideo(
                    id=str(uuid4()),
                    originalName=original_name,
                    format=facts.format,
                    sizeBytes=staged.size_bytes,
                    durationSeconds=facts.duration_seconds,
                    width=facts.width,
                    height=facts.height,
                    frameRate=facts.frame_rate,
                )
            if require_video and not isinstance(reference, ReferenceVideo):
                raise ReferenceMediaError(
                    415,
                    "reference_video_required",
                    "旧上传路由仅支持参考视频，请改用 reference-media 上传图片。",
                )
            return commit_project_reference_media(project_id, staged.path, reference)
        except (ReferenceMediaError, ReferenceVideoError) as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail()) from error
        except OSError as error:
            raise video_storage_error(error) from error
        finally:
            if staged is not None:
                staged.path.unlink(missing_ok=True)
            for upload in file:
                await upload.close()

    @app.put("/api/projects/{project_id}/reference-media", response_model=Project)
    async def put_reference_media(
        project_id: str,
        file: list[UploadFile] = File(default=[]),
    ) -> Project:
        return await upload_reference_media(project_id, file, require_video=False)

    @app.put("/api/projects/{project_id}/reference-video", response_model=Project)
    async def put_reference_video(
        project_id: str,
        file: list[UploadFile] = File(default=[]),
    ) -> Project:
        return await upload_reference_media(project_id, file, require_video=True)

    def generate_reproduction_prompts(project):
        task = project.semanticAnalysis
        setting, credential, _ = analysis_provider_snapshot(task.provider)
        if setting is None or setting.model != task.model or (task.provider != "local_openai_compatible" and credential is None):
            raise HTTPException(status_code=422, detail={
                "code": "provider_unconfigured", "message": "原分析服务配置已变更或缺少凭据，请先重新配置并完成语义分析。",
            })
        provider = analysis_provider_for(
            provider=task.provider, credential=credential, base_url=setting.baseUrl, model=task.model,
        )
        try:
            return generate_prompts(task.result, provider, task.model)
        except ProviderAnalysisError as error:
            raise HTTPException(status_code=422, detail={
                "code": error.failure.code, "message": error.failure.message,
            }) from None
        finally:
            close_default_analysis_provider(provider)

    def analyze_shot(project, shot):
        task = project.semanticAnalysis
        if task is None:
            raise HTTPException(status_code=422, detail={
                "code": "provider_unconfigured", "message": "请先在语义分析中选择并配置分析服务。",
            })
        setting, credential, _ = analysis_provider_snapshot(task.provider)
        if setting is None or setting.model != task.model or (task.provider != "local_openai_compatible" and credential is None):
            raise HTTPException(status_code=422, detail={
                "code": "provider_unconfigured", "message": "分析服务配置已变更或缺少凭据，请先更新分析服务。",
            })
        provider = analysis_provider_for(
            provider=task.provider, credential=credential, base_url=setting.baseUrl, model=task.model,
        )
        try:
            return analyze_preparation_shot(data_dir, project, shot, provider, task.model, ffmpeg_path, ffprobe_path)
        except ProviderAnalysisError as error:
            raise HTTPException(status_code=422, detail={
                "code": error.failure.code, "message": error.failure.message,
            }) from None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise HTTPException(status_code=422, detail={
                "code": "shot_analysis_input_failed", "message": "镜头采样帧无法读取，请检查本地视频和 FFmpeg 后重试。",
            }) from None
        finally:
            close_default_analysis_provider(provider)

    app.include_router(create_shot_preparation_router(
        data_dir, ensure_project_exists, ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path,
        analyze_shot=analyze_shot, compute_queue=compute_jobs,
        person_worker_python=person_worker_python, person_worker_script=person_worker_script, person_model=person_model,
        source_lock=project_write_lock,
    ))

    from .video_upscale import UpscaleConfig
    from .video_upscale_api import create_upscale_router
    app.include_router(create_upscale_router(
        data_dir, ensure_project_exists, compute_jobs,
        config=UpscaleConfig.local(ffmpeg_path, ffprobe_path), source_lock=project_write_lock,
    ))

    app.include_router(create_reproduction_router(
        data_dir, ensure_project_exists, generate_reproduction_prompts, ffmpeg_path=ffmpeg_path,
    ))
    app.include_router(create_character_motion_router(data_dir, ensure_project_exists, ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, source_lock=project_write_lock))

    from .video_toolkit import ToolkitConfig
    from .video_toolkit_api import create_toolkit_router
    app.include_router(create_toolkit_router(data_dir, ensure_project_exists, compute_jobs,
        config=ToolkitConfig.local(ffmpeg_path, ffprobe_path), source_lock=project_write_lock))
    from .preproduction_api import create_preproduction_router
    app.include_router(create_preproduction_router(
        data_dir, ensure_project_exists, compute_jobs,
        ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, source_lock=project_write_lock,
    ))
    from .product_images_api import create_product_images_router
    app.include_router(create_product_images_router(data_dir, ensure_project_exists, source_lock=project_write_lock))
    from .timeline_api import create_timeline_router
    app.include_router(create_timeline_router(
        data_dir, ensure_project_exists, compute_jobs,
        ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, source_lock=project_write_lock,
    ))
    from .batch_editing_api import create_batch_editing_router
    from .local_voice import LocalVoiceService, create_voice_router
    voice_service = LocalVoiceService()
    app.include_router(create_voice_router(voice_service))
    app.include_router(create_batch_editing_router(
        data_dir, ensure_project_exists, compute_jobs,
        ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, source_lock=project_write_lock, voice_service=voice_service,
    ))
    from .aigc_content_api import create_aigc_content_router

    def generate_marketing_candidates(provider_id, model, prompt, schema):
        setting, credential, _ = analysis_provider_snapshot(provider_id)
        if (setting is None or setting.model != model or setting.verificationState != "available"
                or not analysis_model_is_supported(provider_id, model)
                or (provider_id != "local_openai_compatible" and credential is None)):
            raise HTTPException(422, detail={"code": "provider_unconfigured", "message": "请先配置并验证所选 AI 服务。"})
        provider = analysis_provider_for(provider=provider_id, credential=credential, base_url=setting.baseUrl, model=model)
        try:
            return provider.analyze(ProviderRequest(task="prompt_generation", prompt=prompt, model=model,
                                                    responseSchema=schema)).rawText
        except ProviderAnalysisError as error:
            raise HTTPException(422, detail={"code": error.failure.code, "message": error.failure.message}) from None
        finally:
            close_default_analysis_provider(provider)

    app.include_router(create_aigc_content_router(data_dir, ensure_project_exists,
                                                   script_generator=generate_marketing_candidates,
                                                   source_lock=project_write_lock))
    from .content_workflow_api import create_content_workflow_router
    app.include_router(create_content_workflow_router(
        data_dir, ensure_project_exists, source_lock=project_write_lock,
    ))
    from .project_backup import create_backup_router

    def register_restored_project(project):
        projects = _read_projects(data_dir)
        projects.append(project)
        _write_projects(data_dir, projects)

    app.include_router(create_backup_router(data_dir, ensure_project_exists, Project.model_validate, register_restored_project, project_write_lock,
        is_project_active=getattr(compute_jobs, "is_project_active", None)))
    app.add_event_handler("shutdown", compute_jobs.shutdown)
    app.add_event_handler("shutdown", voice_service.close)

    return app


def resolve_default_binary(name: str) -> str:
    env_key = f"{name.upper()}_PATH"
    if env_val := os.environ.get(env_key):
        return env_val
    for candidate_dir in (
        Path("/opt/homebrew/opt/ffmpeg-full/bin"),
        Path("/usr/local/opt/ffmpeg-full/bin"),
    ):
        candidate = candidate_dir / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return name


app = create_app(
    Path(os.environ.get("AI_VIDEO_REVERSE_ENGINEER_DATA_DIR", "./data")),
    ffmpeg_path=resolve_default_binary("ffmpeg"),
    ffprobe_path=resolve_default_binary("ffprobe"),
)
