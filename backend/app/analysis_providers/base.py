from typing import Optional, Protocol, Union, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..analysis_input import AnalysisInput


_FAILURES = {
    "provider_unconfigured": ("分析供应商尚未配置。", False),
    "authentication_failed": ("分析服务认证失败，请检查凭据。", False),
    "rate_limited": ("分析服务暂时限流，请稍后重试。", True),
    "network_error": ("无法连接分析服务，请检查网络后重试。", True),
    "timeout": ("分析服务响应超时，请稍后重试。", True),
    "unsupported_model_capability": ("所选模型不支持所需的视觉或结构化输出能力。", False),
    "provider_error": ("分析服务暂时不可用，请稍后重试。", True),
    "invalid_analysis_response": ("分析服务返回的数据不符合结构化契约。", True),
}


class ProviderFailure(BaseModel):
    """A stable, safe error that can cross the provider boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool

    @classmethod
    def for_code(cls, code: str) -> "ProviderFailure":
        message, retryable = _FAILURES[code]
        return cls(code=code, message=message, retryable=retryable)


class ProviderAnalysisError(Exception):
    def __init__(self, failure: ProviderFailure):
        self.failure = failure
        super().__init__(failure.message)


class ProviderRequest(BaseModel):
    """The complete safe payload available to an analysis provider.

    Credentials intentionally belong to the configured provider instance, not
    to this value.  This keeps request serialization free of secrets and of
    project/storage details.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    analysisInput: Optional[AnalysisInput] = None
    prompt: str = Field(min_length=1)
    model: str = Field(min_length=1)
    isRepair: bool = False

    @field_validator("prompt", "model")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("供应商请求文本不能为空白。")
        return value

    @model_validator(mode="after")
    def request_shape_matches_its_role(self) -> "ProviderRequest":
        if self.isRepair and self.analysisInput is not None:
            raise ValueError("修复请求不得携带分析输入。")
        if not self.isRepair and self.analysisInput is None:
            raise ValueError("初始供应商请求必须携带分析输入。")
        return self

    def repair(self, prompt: str) -> "ProviderRequest":
        if self.isRepair:
            raise ValueError("修复请求不能再次修复。")
        return ProviderRequest(
            prompt=prompt,
            model=self.model,
            isRepair=True,
        )


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rawText: str
    providerRequestId: Optional[str] = None


@runtime_checkable
class AnalysisProvider(Protocol):
    provider_id: str

    def analyze(self, request: ProviderRequest) -> ProviderResult:
        ...


def failure_for_status(status_code: int) -> ProviderFailure:
    if status_code in (401, 403):
        return ProviderFailure.for_code("authentication_failed")
    if status_code == 429:
        return ProviderFailure.for_code("rate_limited")
    return ProviderFailure.for_code("provider_error")


__all__ = [
    "AnalysisProvider",
    "ProviderAnalysisError",
    "ProviderFailure",
    "ProviderRequest",
    "ProviderResult",
    "failure_for_status",
]
