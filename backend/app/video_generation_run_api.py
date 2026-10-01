"""Project routes for externally managed video GenerationRun records."""
from __future__ import annotations

from typing import Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .generation_service import (
    VIDEO_MAX_OPTION_LENGTH,
    VIDEO_MAX_PROFILE_ID_LENGTH,
    VIDEO_MAX_REFERENCES,
    VIDEO_MAX_RESULTS,
    GenerationError,
    GenerationService,
)


class _Disclosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=20_000)
    accepted: Literal[True]


class _Reference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assetId: str = Field(min_length=1, max_length=200)
    assetVersionId: str = Field(min_length=1, max_length=220)
    referenceType: Literal["first-frame", "last-frame", "reference"]


class _VideoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profileId: str = Field(min_length=1, max_length=VIDEO_MAX_PROFILE_ID_LENGTH)
    mode: Literal["text-to-video", "image-to-video"]
    references: list[_Reference] = Field(max_length=VIDEO_MAX_REFERENCES)
    durationSeconds: int = Field(ge=1, le=300)
    aspectRatio: str = Field(min_length=1, max_length=VIDEO_MAX_OPTION_LENGTH)
    resolution: str = Field(min_length=1, max_length=VIDEO_MAX_OPTION_LENGTH)
    audio: bool
    resultCount: int = Field(ge=1, le=VIDEO_MAX_RESULTS)


class _Submit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disclosure: _Disclosure
    videoParams: _VideoParams
    idempotencyKey: str = Field(min_length=1, max_length=200)
    caller: str = Field(min_length=1, max_length=120)


class _Retry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotencyKey: str = Field(min_length=1, max_length=200)


_ERRORS = {
    "generation_run_missing": (404, "视频生成运行不存在。"),
    "generation_idempotency_conflict": (409, "同一幂等键已用于不同视频生成输入。"),
    "generation_retry_invalid": (409, "当前视频生成运行不能进行本地结果重试。"),
    "generation_refresh_invalid": (409, "当前视频生成运行不能查询远端状态。"),
    "generation_stop_invalid": (409, "当前视频生成运行不能停止本地轮询。"),
    "generation_cancel_invalid": (409, "当前视频生成运行不能请求远端取消。"),
    "generation_adapter_missing": (422, "所选视频模型尚未通过异步适配器验证。"),
    "generation_adapter_version_changed": (409, "该视频任务的适配器版本已变化，已停止远端操作以避免切换执行语义。"),
    "generation_capability_unavailable": (422, "所选视频模型尚未声明并验证本次能力。"),
    "generation_profile_unsupported": (422, "视频生成档位不在模型已验证范围内。"),
    "generation_reference_pattern_unsupported": (422, "文生视频或参考图角色组合不在模型已验证范围内。"),
    "generation_reference_invalid": (422, "参考图片或不可变版本不可用。"),
    "generation_combination_unsupported": (422, "时长、比例、分辨率与音频组合不在模型已验证范围内。"),
    "generation_result_count_unsupported": (422, "结果数量不在模型已验证范围内。"),
    "generation_protocol_invalid": (502, "视频 Provider 返回了无效协议数据。"),
}


def _fail(error: GenerationError) -> None:
    status, message = _ERRORS.get(error.code, (422, "视频生成请求无效。"))
    raise HTTPException(status, detail={"code": error.code, "message": message}) from None


_PUBLIC_RUN_KEYS = (
    "id", "status", "outcome", "reason", "provider", "model", "lastConfirmedRemoteStatus",
    "localDisposition", "remoteCancelResult", "billingUnknown", "capabilitySnapshot", "retryOf",
)
_PUBLIC_OUTPUT_KEYS = (
    "index", "resultId", "status", "reason", "billingUnknown", "assetId", "assetVersionId",
    "url", "name", "duration", "width", "height",
)


def _public_run(run: dict) -> dict:
    outputs = []
    for item in run.get("outputItems", []):
        public_item = {key: item[key] for key in _PUBLIC_OUTPUT_KEYS if key in item}
        public_item["retryable"] = item.get("status") == "failed" and isinstance(item.get("remoteResultId"), str)
        outputs.append(public_item)
    return {key: run[key] for key in _PUBLIC_RUN_KEYS if key in run} | {"outputItems": outputs}


def create_video_generation_run_router(
    service: GenerationService,
    get_project: Callable[[str], object],
) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/video-generation")

    def require_project(project_id: str) -> None:
        if get_project(project_id) is None:
            raise HTTPException(404, detail={"code": "project_not_found", "message": "内容项目不存在。"})

    def require_video_run(project_id: str, run_id: str) -> dict:
        run = service.get_run(project_id, run_id)
        if run.get("modality") != "video":
            raise GenerationError("generation_run_missing")
        return run

    @router.get("/models")
    def models(project_id: str):
        require_project(project_id)
        return {"models": service.list_video_models()}

    @router.get("/capabilities")
    def capabilities(project_id: str, provider: str, model: str):
        require_project(project_id)
        try:
            return service.capabilities(provider, model, modality="video")
        except GenerationError as error:
            _fail(error)

    @router.get("/runs")
    def list_runs(project_id: str):
        require_project(project_id)
        return {"runs": [_public_run(run) for run in service.list_runs(project_id) if run.get("modality") == "video"]}

    @router.post("/runs", status_code=202)
    async def submit_run(project_id: str, request: Request):
        require_project(project_id)
        try:
            body = _Submit.model_validate(await request.json())
        except (TypeError, ValueError, ValidationError):
            raise HTTPException(422, detail={
                "code": "generation_request_invalid", "message": "视频生成请求无效。",
            }) from None
        try:
            run = await run_in_threadpool(
                service.submit_video,
                project_id,
                disclosure=body.disclosure.model_dump(),
                video_params=body.videoParams.model_dump(),
                idempotency_key=body.idempotencyKey,
                caller=body.caller,
            )
            return {"run": _public_run(run)}
        except GenerationError as error:
            _fail(error)

    @router.get("/runs/{run_id}")
    def get_run(project_id: str, run_id: str):
        require_project(project_id)
        try:
            return {"run": _public_run(require_video_run(project_id, run_id))}
        except GenerationError as error:
            _fail(error)

    @router.post("/runs/{run_id}/refresh")
    def refresh_run(project_id: str, run_id: str):
        require_project(project_id)
        try:
            require_video_run(project_id, run_id)
            return {"run": _public_run(service.refresh_video(project_id, run_id))}
        except GenerationError as error:
            _fail(error)

    @router.post("/runs/{run_id}/stop-local")
    def stop_local(project_id: str, run_id: str):
        require_project(project_id)
        try:
            require_video_run(project_id, run_id)
            return {"run": _public_run(service.stop_video_local(project_id, run_id))}
        except GenerationError as error:
            _fail(error)

    @router.post("/runs/{run_id}/cancel-remote")
    def cancel_remote(project_id: str, run_id: str):
        require_project(project_id)
        try:
            require_video_run(project_id, run_id)
            return {"run": _public_run(service.cancel_video_remote(project_id, run_id))}
        except GenerationError as error:
            _fail(error)

    @router.post("/runs/{run_id}/retry", status_code=202)
    def retry_run(project_id: str, run_id: str, body: _Retry):
        require_project(project_id)
        try:
            require_video_run(project_id, run_id)
            return {"run": _public_run(service.retry_video(project_id, run_id, idempotency_key=body.idempotencyKey))}
        except GenerationError as error:
            _fail(error)

    return router
