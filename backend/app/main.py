import errno
import json
import logging
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import AsyncIterator, Callable, Literal, Optional, Union
from uuid import uuid4

from fastapi import File, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

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
from app.local_preprocessing_jobs import LocalPreprocessingJobQueue
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
from app.depth_capture_jobs import LocalComputeJobQueue, LocalPreprocessingQueueAdapter
from app.depth_capture_runner import DepthCaptureFailure, DepthCaptureRequest, run_depth_capture
from app.depth_capture_storage import inspect_depth_artifacts
from app.depth_capture_storage import DepthPreviewUnavailableError, open_validated_depth_preview
from app.semantic_analysis import SemanticAnalysis
from app.analysis_service_secrets import SecureStorageUnavailable
from app.analysis_settings import AnalysisProviderConfiguration, AnalysisSettings
from app.credential_store import CredentialStore


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
    depth_worker_python: str = "backend/depth_worker/.venv/bin/python",
    depth_worker_script: Path = Path("backend/depth_worker/run_depth.py"),
    depth_checkpoint: Path = Path("backend/depth_worker/checkpoints/video_depth_anything_vits.pth"),
    depth_upstream_root: Path = Path("backend/depth_worker/vendor/Video-Depth-Anything"),
    depth_device_probe: Optional[Callable[[], tuple[bool, bool]]] = None,
    credential_store=None,
    analysis_settings: Optional[AnalysisSettings] = None,
) -> FastAPI:
    app = FastAPI(title="AI 视频复刻分析器")
    project_write_lock = Lock()
    dispatching_project_ids: set[str] = set()
    reference_media_path_pattern = re.compile(r"^/api/projects/[^/]+/reference-media$")
    reference_video_path_pattern = re.compile(r"^/api/projects/[^/]+/reference-video$")
    configured_credentials = credential_store
    configured_analysis_settings = analysis_settings or AnalysisSettings(data_dir / "analysis-providers.json")

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

    def analysis_provider_configuration(provider: str) -> AnalysisProviderConfiguration:
        setting = configured_analysis_settings.get(provider)
        try:
            configured = credentials().get(provider) is not None
        except SecureStorageUnavailable as error:
            raise secure_storage_unavailable() from error
        return AnalysisProviderConfiguration(
            provider=provider,
            model=setting.model if setting is not None else None,
            baseUrl=setting.baseUrl if setting is not None else None,
            credentialState="configured" if configured else "unconfigured",
            selectedProvider=configured_analysis_settings.selected_provider(),
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
        task.status = "failed"
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
                    if preprocessing is None or preprocessing.status not in {"queued", "running"}:
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
            import torch
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
                        if capture.status in {"queued", "running"}:
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

    reconcile_interrupted_preprocessing()
    reconcile_depth_captures()
    compute_jobs = local_compute_queue or LocalComputeJobQueue()
    if preprocessing_queue_factory is None:
        preprocessing_jobs = LocalPreprocessingQueueAdapter(compute_jobs, run_preprocessing_job)
    else:
        preprocessing_jobs = preprocessing_queue_factory(run_preprocessing_job)
    app.add_event_handler("shutdown", compute_jobs.shutdown)
    if preprocessing_queue_factory is not None:
        app.add_event_handler("shutdown", preprocessing_jobs.shutdown)

    def is_reference_video_request(request: Request) -> bool:
        return request.method == "PUT" and reference_video_path_pattern.fullmatch(request.url.path) is not None

    def is_reference_media_request(request: Request) -> bool:
        return request.method == "PUT" and (
            is_reference_video_request(request)
            or reference_media_path_pattern.fullmatch(request.url.path) is not None
        )

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
            return [
                analysis_provider_configuration(provider)
                for provider in ("bailian", "local_openai_compatible")
            ]
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "analysis_settings_unavailable",
                    "message": "分析供应商设置不可用。",
                },
            ) from error

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

        credential_write_succeeded = False
        credential_state = previous_credential is not None
        try:
            if api_key is not None:
                credential_store_for_update.set(provider, api_key)
                credential_write_succeeded = True
                credential_state = True
            configured_analysis_settings.commit(prepared_settings)
        except (OSError, SecureStorageUnavailable) as error:
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
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "analysis_settings_unavailable", "message": "分析供应商设置不可用。"},
            ) from error

        return AnalysisProviderConfiguration(
            provider=provider,
            model=candidate.model,
            baseUrl=candidate.baseUrl,
            credentialState="configured" if credential_state else "unconfigured",
            selectedProvider=provider,
        )

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

    @app.post("/api/projects/{project_id}/local-preprocessing", response_model=Project)
    def start_local_preprocessing(project_id: str, response: Response) -> Project:
        completed = False
        dispatching = False

        def prepare_for_start(project: Project) -> Project:
            nonlocal completed, dispatching
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
            return project.model_copy(update={
                "localPreprocessing": task,
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
        preprocessing = project.localPreprocessing
        if project.referenceVideo is None or preprocessing is None:
            return False
        if (
            preprocessing.status != "completed"
            or preprocessing.sourceReferenceVideoId != project.referenceVideo.id
            or preprocessing.reproducibilityAssessment is None
            or preprocessing.reproducibilityAssessment.status == "out_of_scope"
        ):
            return False
        if not managed_reference_media_is_safe(data_dir, project.id, project.referenceVideo):
            return False
        return validate_completed_stages(
            preprocessing,
            preprocessing_directory(data_dir, project.id, preprocessing.id),
        ).firstInvalidStage is None

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
                if (
                    existing.status == "completed"
                    and existing.sourceReferenceVideoId == project.referenceVideo.id
                    and existing.algorithmVersion == DEPTH_ALGORITHM_VERSION
                    and existing.devicePreference == payload.devicePreference
                    and inspect_capture(project, existing)
                    and existing.qualityAssessment is not None
                    and existing.qualityAssessment.status in {"passed", "review_required"}
                ):
                    response.status_code = 200
                    return project
            now = datetime.now(timezone.utc).isoformat()
            capture = new_depth_capture(project.referenceVideo.id, payload.devicePreference, now)
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

    return app


app = create_app(Path(os.environ.get("AI_VIDEO_REVERSE_ENGINEER_DATA_DIR", "./data")))
