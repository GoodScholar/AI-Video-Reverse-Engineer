from typing import Any, List, Optional

from pydantic import model_validator

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .analysis_service_secrets import SecureStorageUnavailable
from .image_generation import ImageGenerationConfigurations, ImageGenerationError


class _ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modelId: str = Field(min_length=1, max_length=256)
    label: Optional[str] = Field(default=None, min_length=1, max_length=256)
    capabilities: Optional[dict[str, Any]] = None


class _ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providerId: str = Field(min_length=1, max_length=64)
    protocol: str = Field(min_length=1, max_length=64)
    baseUrl: str
    defaultModelId: str = Field(min_length=1, max_length=256)
    models: List[_ModelInput] = Field(min_length=1)
    apiKey: Optional[str] = None


class _ConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Legacy one-provider input stays accepted for existing settings clients.
    baseUrl: Optional[str] = None
    model: Optional[str] = Field(default=None, min_length=1)
    provider: Optional[str] = None
    models: Optional[List[str]] = None
    apiKey: Optional[str] = None
    providers: Optional[List[_ProviderInput]] = Field(default=None, min_length=1)
    activeProviderId: Optional[str] = Field(default=None, min_length=1, max_length=64)
    revision: Optional[str] = None

    @model_validator(mode="after")
    def require_provider_input(self) -> "_ConfigurationInput":
        if self.providers is None and (self.baseUrl is None or self.model is None):
            raise ValueError("请提供图片生成服务地址和模型，或 providers 配置。")
        if self.providers is not None and (self.baseUrl is not None or self.model is not None or self.provider is not None or self.models is not None or self.apiKey is not None):
            raise ValueError("providers 配置不能与旧单供应商字段混用。")
        return self


def create_image_generation_router(configurations: ImageGenerationConfigurations) -> APIRouter:
    """Expose only safe configuration metadata; secrets never enter responses."""

    router = APIRouter(prefix="/api/image-generation")

    @router.get("/configuration")
    def get_configuration():
        return configurations.get()

    @router.put("/configuration")
    async def put_configuration(request: Request):
        try:
            body = await request.json()
            configuration = _ConfigurationInput.model_validate(body)
        except (TypeError, ValueError, ValidationError):
            raise HTTPException(422, "图片生成配置输入无效。") from None
        try:
            return configurations.save(
                base_url=configuration.baseUrl,
                model=configuration.model,
                provider=configuration.provider,
                models=configuration.models,
                api_key=configuration.apiKey,
                expected_revision=configuration.revision,
                providers=[item.model_dump(exclude_none=True) for item in configuration.providers] if configuration.providers is not None else None,
                active_provider_id=configuration.activeProviderId,
            )
        except SecureStorageUnavailable:
            raise HTTPException(503, "系统安全存储不可用，无法保存图片生成密钥。") from None
        except ImageGenerationError as error:
            if error.code == "configuration_changed":
                raise HTTPException(409, "图片生成配置已更新，请刷新后重试。") from None
            raise HTTPException(422, "图片生成配置无效。") from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    return router
