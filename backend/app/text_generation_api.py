"""Safe configuration and explicit billable connection checks for text generation."""
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .analysis_service_secrets import SecureStorageUnavailable
from .text_generation import TextGenerationConfigurations, TextGenerationError, TextGenerationService


class _ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modelId: str = Field(min_length=1, max_length=256)
    label: Optional[str] = Field(default=None, min_length=1, max_length=256)


class _ConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseUrl: str
    models: list[_ModelInput] = Field(min_length=1)
    apiKey: Optional[str] = None
    revision: Optional[str] = None


class _ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: str = Field(min_length=1)
    modelId: str = Field(min_length=1, max_length=256)
    mode: Literal["text", "image"]


def create_text_generation_router(configurations: TextGenerationConfigurations,
                                  service: Optional[TextGenerationService] = None) -> APIRouter:
    router = APIRouter(prefix="/api/text-generation")

    @router.get("/configuration")
    def get_configuration():
        return configurations.get()

    @router.put("/configuration")
    async def put_configuration(request: Request):
        try:
            configuration = _ConfigurationInput.model_validate(await request.json())
        except (TypeError, ValueError, ValidationError):
            raise HTTPException(422, "文本生成配置输入无效。") from None
        try:
            return configurations.save(
                base_url=configuration.baseUrl,
                models=[item.model_dump(exclude_none=True) for item in configuration.models],
                api_key=configuration.apiKey,
                expected_revision=configuration.revision,
            )
        except SecureStorageUnavailable:
            raise HTTPException(503, "系统安全存储不可用，无法保存文本生成密钥。") from None
        except TextGenerationError as error:
            if error.code == "configuration_changed":
                raise HTTPException(409, "文本生成配置已更新，请刷新后重试。") from None
            raise HTTPException(422, "文本生成配置无效。") from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @router.post("/test-connection")
    async def test_connection(request: Request):
        try:
            body = _ConnectionInput.model_validate(await request.json())
        except (TypeError, ValueError, ValidationError):
            raise HTTPException(422, "文本生成连接测试输入无效。") from None
        if service is None:
            raise HTTPException(422, "文本生成服务不可用。")
        try:
            return await run_in_threadpool(
                service.test_connection, body.revision, body.modelId, mode=body.mode,
            )
        except TextGenerationError as error:
            if error.code == "configuration_changed":
                raise HTTPException(409, "文本生成配置已更新，请刷新后重试。") from None
            status = 503 if error.code == "provider_unconfigured" else 422
            raise HTTPException(status, "文本生成连接测试失败。") from None

    return router
