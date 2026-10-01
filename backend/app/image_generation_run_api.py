"""Project routes for durable image GenerationRun records."""

from typing import Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .generation_service import GenerationError, GenerationService


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
    referenceType: Literal["reference", "edit-source", "upscale-source"]


class _ImageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["generate", "edit", "super-resolution"]
    references: list[_Reference]
    aspectRatio: str = Field(min_length=1, max_length=32)
    resolution: str = Field(min_length=1, max_length=32)
    resultCount: int = Field(ge=1, le=64)
    timeoutSeconds: int | None = Field(default=None, ge=1, le=86_400)


class _Submit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disclosure: _Disclosure
    imageParams: _ImageParams
    idempotencyKey: str = Field(min_length=1, max_length=200)
    caller: str = Field(min_length=1, max_length=120)


class _Retry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotencyKey: str = Field(min_length=1, max_length=200)


_ERRORS = {
    "generation_run_missing": (404, "图片生成运行不存在。"),
    "generation_idempotency_conflict": (409, "同一幂等键已用于不同图片生成输入。"),
    "generation_retry_invalid": (409, "当前图片生成运行不能重试。"),
    "generation_configuration_changed": (409, "图片模型配置或能力已更新，请重新提交。"),
    "generation_cancel_invalid": (409, "当前图片生成运行不能取消。"),
    "generation_adapter_missing": (422, "所选图片模型尚未通过能力验证。"),
    "generation_capability_unavailable": (422, "所选图片模型尚未声明并验证本次能力。"),
    "generation_reference_limit": (422, "参考图片数量超出模型已验证范围。"),
    "generation_reference_type_unsupported": (422, "参考图片类型不在模型已验证范围内。"),
    "generation_reference_invalid": (422, "参考图片或不可变版本不可用。"),
    "generation_image_edit_unsupported": (422, "所选模型未验证图片编辑能力。"),
    "generation_super_resolution_unsupported": (422, "所选模型未验证模型超分辨率能力。"),
    "generation_aspect_ratio_unsupported": (422, "图片比例不在模型已验证范围内。"),
    "generation_resolution_unsupported": (422, "图片分辨率不在模型已验证范围内。"),
    "generation_result_count_unsupported": (422, "结果数量不在模型已验证范围内。"),
    "generation_cancel_unsupported": (422, "所选图片模型未验证远端取消能力。"),
}


def _fail(error: GenerationError) -> None:
    status, message = _ERRORS.get(error.code, (422, "图片生成请求无效。"))
    raise HTTPException(status, detail={"code": error.code, "message": message}) from None


def create_image_generation_run_router(
    service: GenerationService,
    get_project: Callable[[str], object],
) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/image-generation")

    def require_project(project_id: str) -> None:
        if get_project(project_id) is None:
            raise HTTPException(404, detail={"code": "project_not_found", "message": "内容项目不存在。"})

    @router.get("/capabilities")
    def capabilities(project_id: str, provider: str, model: str):
        require_project(project_id)
        try:
            return service.capabilities(provider, model, modality="image")
        except GenerationError as error:
            _fail(error)

    @router.get("/runs")
    def list_runs(project_id: str):
        require_project(project_id)
        return {"runs": [run for run in service.list_runs(project_id) if run.get("modality") == "image"]}

    @router.post("/runs", status_code=202)
    async def submit_run(project_id: str, request: Request):
        require_project(project_id)
        try:
            body = _Submit.model_validate(await request.json())
        except (TypeError, ValueError, ValidationError):
            raise HTTPException(422, detail={
                "code": "generation_request_invalid", "message": "图片生成请求无效。",
            }) from None
        try:
            run = service.submit_image(
                project_id,
                disclosure=body.disclosure.model_dump(),
                image_params=body.imageParams.model_dump(exclude_none=True),
                idempotency_key=body.idempotencyKey,
                caller=body.caller,
            )
            return {"run": run}
        except GenerationError as error:
            _fail(error)

    @router.get("/runs/{run_id}")
    def get_run(project_id: str, run_id: str):
        require_project(project_id)
        try:
            run = service.get_run(project_id, run_id)
            if run.get("modality") != "image":
                raise GenerationError("generation_run_missing")
            return {"run": run}
        except GenerationError as error:
            _fail(error)

    @router.post("/runs/{run_id}/cancel")
    def cancel_run(project_id: str, run_id: str):
        require_project(project_id)
        try:
            run = service.get_run(project_id, run_id)
            if run.get("modality") != "image":
                raise GenerationError("generation_run_missing")
            return {"run": service.cancel(project_id, run_id)}
        except GenerationError as error:
            _fail(error)

    @router.post("/runs/{run_id}/retry", status_code=202)
    def retry_run(project_id: str, run_id: str, body: _Retry):
        require_project(project_id)
        try:
            return {"run": service.retry_image(project_id, run_id, idempotency_key=body.idempotencyKey)}
        except GenerationError as error:
            _fail(error)

    return router
