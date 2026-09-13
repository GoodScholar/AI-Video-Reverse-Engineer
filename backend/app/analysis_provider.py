import math
import re
from types import MappingProxyType
from typing import Annotated, Any, Dict, Literal, Mapping, Optional, Protocol, Tuple, Union, runtime_checkable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_serializer, field_validator, model_serializer, model_validator

from .analysis_models import StructuredAnalysis


FIRST_WAVE_PROVIDER_IDS = frozenset({
    "bailian", "openai", "gemini", "doubao", "claude",
})
_SENSITIVE_KEY_PARTS = (
    "auth", "credential", "secret", "password", "token", "privatekey", "apikey",
)
_CREDENTIAL_VALUE = re.compile(
    r"(?:\bbearer\s+\S+|(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]+|\bAKIA[0-9A-Z]{16}\b)",
    re.IGNORECASE,
)
_HTTP_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_JSON_POINTER_FRAGMENT = re.compile(r"^#/(?:[^~/]|~[01])*(?:/(?:[^~/]|~[01])*)*$")
_URI_FRAGMENT = re.compile(r"^(?:[A-Za-z0-9\-._~!$&'()*+,;=:@/?]|%[0-9A-Fa-f]{2})*$")
_LOCAL_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9])/(?:[^\s/]+(?:/[^\s]*)*)?|(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\[^\s\\/]+(?:[\\/]|$))",
)
_FAILURE_DETAILS = {
    "provider_unconfigured": ("请先在设置中配置分析供应商。", False),
    "authentication_failed": ("分析服务认证失败，请检查凭据。", False),
    "rate_limited": ("分析服务暂时限流，请稍后重试。", True),
    "network_error": ("无法连接分析服务，请检查网络后重试。", True),
    "timeout": ("分析服务响应超时，请稍后重试。", True),
    "unsupported_model_capability": ("所选模型不支持所需的视觉或结构化输出能力。", False),
    "content_rejected": ("分析服务拒绝处理当前内容。", False),
    "invalid_response": ("分析服务返回的数据不符合结构化契约。", True),
    "provider_error": ("分析服务暂时不可用，请稍后重试。", True),
    "analysis_interrupted": ("分析任务已中断，可重新发起。", True),
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProviderConfigurationError(ValueError):
    """A provider selection does not satisfy the verified catalog."""


def _normalised_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _is_forbidden_key(key: str) -> bool:
    normalised = _normalised_key(key)
    return any(part in normalised for part in _SENSITIVE_KEY_PARTS)


def _without_valid_http_urls(value: str) -> str:
    def remove_if_valid(match: re.Match[str]) -> str:
        candidate = match.group()
        parsed = urlsplit(candidate)
        try:
            parsed.port
            is_valid = parsed.scheme in {"http", "https"} and parsed.hostname is not None
        except ValueError:
            is_valid = False
        return "" if is_valid else candidate

    return _HTTP_URL.sub(remove_if_valid, value)


def _is_valid_json_pointer_uri_fragment(value: str) -> bool:
    return bool(
        _JSON_POINTER_FRAGMENT.fullmatch(value)
        and _URI_FRAGMENT.fullmatch(value[1:])
    )


def _freeze_json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if _CREDENTIAL_VALUE.search(value) or (
            not _is_valid_json_pointer_uri_fragment(value)
            and _LOCAL_ABSOLUTE_PATH.search(_without_valid_http_urls(value))
        ):
            raise ValueError("分析代理内容不能包含敏感信息或本地路径。")
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("分析代理内容只能包含 JSON 数值。")
        return value
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, dict):
        frozen = {}
        for key, nested_value in value.items():
            if type(key) is not str:
                raise ValueError("分析代理 JSON 键必须是字符串。")
            if _is_forbidden_key(key):
                raise ValueError("分析代理内容不能包含敏感或本地标识字段。")
            frozen[key] = _freeze_json_value(nested_value)
        return MappingProxyType(frozen)
    raise ValueError("分析代理内容只能包含 JSON 值。")


class ProviderRequest(_StrictModel):
    contactSheetBytes: bytes = Field(min_length=4)
    proxy: Mapping[str, Any]
    responseSchema: Mapping[str, Any]

    @field_validator("contactSheetBytes")
    @classmethod
    def contact_sheet_must_be_a_complete_jpeg(cls, value: bytes) -> bytes:
        if not value.startswith(b"\xff\xd8") or not value.endswith(b"\xff\xd9"):
            raise ValueError("分析联系表必须是完整的 JPEG 字节。")
        return value

    @field_validator("proxy", "responseSchema")
    @classmethod
    def payloads_must_be_safe_json_mappings(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze_json_value(value)

    @field_serializer("proxy", "responseSchema")
    def serialize_mappings(self, value: Mapping[str, Any]) -> Dict[str, Any]:
        return dict(value)


class _ProviderConfigBase(_StrictModel):
    modelId: str = Field(min_length=1)

    @field_validator("modelId")
    @classmethod
    def model_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("供应商配置标识不能为空白。")
        return value


class BailianProviderConfig(_ProviderConfigBase):
    providerId: Literal["bailian"]
    region: str = Field(min_length=1)
    workspaceId: str = Field(min_length=1)


class OpenAIProviderConfig(_ProviderConfigBase):
    providerId: Literal["openai"]


class GeminiProviderConfig(_ProviderConfigBase):
    providerId: Literal["gemini"]
    projectId: str = Field(min_length=1)
    location: str = Field(min_length=1)


class DoubaoProviderConfig(_ProviderConfigBase):
    providerId: Literal["doubao"]
    endpointId: str = Field(min_length=1)


class ClaudeProviderConfig(_ProviderConfigBase):
    providerId: Literal["claude"]


ProviderConfig = Annotated[
    Union[
        BailianProviderConfig,
        OpenAIProviderConfig,
        GeminiProviderConfig,
        DoubaoProviderConfig,
        ClaudeProviderConfig,
    ],
    Field(discriminator="providerId"),
]
_PROVIDER_CONFIG_ADAPTER = TypeAdapter(ProviderConfig)


def parse_provider_config(value: Any) -> ProviderConfig:
    if isinstance(value, BaseModel):
        value = value.model_dump()
    return _PROVIDER_CONFIG_ADAPTER.validate_python(value)


class ProviderFailure(_StrictModel):
    code: Literal[
        "provider_unconfigured", "authentication_failed", "rate_limited",
        "network_error", "timeout", "unsupported_model_capability",
        "content_rejected", "invalid_response", "provider_error",
        "analysis_interrupted",
    ]
    message: str = ""
    retryable: bool = False

    @model_validator(mode="before")
    @classmethod
    def details_must_come_from_the_stable_code_mapping(cls, value):
        candidate = dict(value)
        details = _FAILURE_DETAILS.get(candidate.get("code"))
        if details is None:
            raise ValueError("不支持的分析错误码。")
        message, retryable = details
        if candidate.get("message", message) != message or candidate.get("retryable", retryable) != retryable:
            raise ValueError("分析错误详情必须使用稳定错误码映射。")
        candidate["message"] = message
        candidate["retryable"] = retryable
        return candidate

    @classmethod
    def for_code(cls, code: str) -> "ProviderFailure":
        return cls.model_validate({"code": code})

    @model_serializer
    def serialize_safe_failure(self):
        message, retryable = _FAILURE_DETAILS[self.code]
        return {
            "code": self.code,
            "message": message,
            "retryable": retryable,
        }


class _ImmutableValue:
    __slots__ = ("_initialized",)

    def _finish_initialization(self, **attributes) -> None:
        if getattr(self, "_initialized", False):
            raise TypeError("目录值对象不能重新初始化。")
        for name, value in attributes.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_initialized", True)

    def __setattr__(self, name, value):
        raise AttributeError("目录值对象不可变。")


class ProviderModel(_ImmutableValue):
    __slots__ = ("modelId", "label", "recommended", "vision", "structuredOutput", "regions")

    def __init__(self, modelId, label, recommended, vision, structuredOutput, regions):
        if not isinstance(modelId, str) or not modelId.strip() or not isinstance(label, str) or not label.strip():
            raise ValueError("模型 ID 和标签不能为空白。")
        regions = tuple(regions)
        if not regions or any(not isinstance(region, str) or not region.strip() for region in regions):
            raise ValueError("模型必须至少声明一个区域。")
        self._finish_initialization(
            modelId=modelId,
            label=label,
            recommended=recommended,
            vision=vision,
            structuredOutput=structuredOutput,
            regions=regions,
        )


class ProviderDefinition(_ImmutableValue):
    __slots__ = ("providerId", "label", "availability", "models", "verifiedEndpointBindings")

    def __init__(self, providerId, label, availability, models=(), verifiedEndpointBindings=None):
        if not isinstance(providerId, str) or not providerId.strip() or not isinstance(label, str) or not label.strip():
            raise ValueError("供应商 ID 和标签不能为空白。")
        if availability not in {"verified", "unverified"}:
            raise ValueError("供应商可用性无效。")
        models = tuple(models)
        if any(not isinstance(model, ProviderModel) for model in models):
            raise ValueError("供应商模型必须使用不可变值对象。")
        bindings = MappingProxyType(dict(verifiedEndpointBindings or {}))
        model_ids = [model.modelId for model in models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("已验证模型 ID 不得重复。")
        if availability == "unverified":
            if models or bindings:
                raise ValueError("未验证供应商不得公开模型或 endpoint 绑定。")
        else:
            recommended = [model for model in models if model.recommended]
            if len(recommended) != 1:
                raise ValueError("已验证供应商必须有且仅有一个推荐模型。")
            if any(not model.vision or not model.structuredOutput for model in models):
                raise ValueError("公开模型必须支持视觉和结构化输出。")
            if providerId == "doubao":
                if not bindings or any(model_id not in model_ids for model_id in bindings.values()):
                    raise ValueError("豆包 endpoint 必须绑定到已验证模型。")
            elif bindings:
                raise ValueError("只有豆包供应商允许登记 endpoint 绑定。")
        self._finish_initialization(
            providerId=providerId,
            label=label,
            availability=availability,
            models=models,
            verifiedEndpointBindings=bindings,
        )


class ProviderCatalog(_ImmutableValue):
    __slots__ = ("version", "providers")

    def __init__(self, version, providers):
        if not isinstance(version, int) or version < 1:
            raise ValueError("供应商目录版本无效。")
        providers = MappingProxyType(dict(providers))
        if set(providers) != FIRST_WAVE_PROVIDER_IDS:
            raise ValueError("供应商注册表必须只包含首批供应商。")
        if any(not isinstance(provider, ProviderDefinition) or provider_id != provider.providerId for provider_id, provider in providers.items()):
            raise ValueError("供应商注册表项无效。")
        self._finish_initialization(version=version, providers=providers)

    def select_model(self, config: ProviderConfig) -> ProviderModel:
        if isinstance(config, DoubaoProviderConfig) and not getattr(config, "endpointId", None):
            raise ProviderConfigurationError("豆包推理接入点为必填且必须已验证绑定。")
        try:
            catalog = ProviderCatalog(self.version, self.providers)
            config = parse_provider_config(config)
        except (ValidationError, ValueError) as error:
            raise ProviderConfigurationError("供应商注册表或配置无效。") from error
        provider = catalog.providers[config.providerId]
        if isinstance(config, DoubaoProviderConfig):
            if provider.verifiedEndpointBindings.get(config.endpointId) != config.modelId:
                raise ProviderConfigurationError("豆包推理接入点尚未验证绑定到所选模型。")
        if provider.availability != "verified":
            raise ProviderConfigurationError("该供应商尚未完成真实冒烟验证，不能用于分析。")
        for model in provider.models:
            if model.modelId == config.modelId:
                if isinstance(config, BailianProviderConfig) and config.region not in model.regions:
                    raise ProviderConfigurationError("百炼区域不支持所选已验证模型。")
                return model
        raise ProviderConfigurationError("所选模型不在已验证模型注册表中。")


@runtime_checkable
class AnalysisProvider(Protocol):
    provider_id: str

    def validate_configuration(self, config: ProviderConfig, credential: str) -> None:
        ...

    def analyze(
        self,
        request: ProviderRequest,
        config: ProviderConfig,
        credential: str,
    ) -> StructuredAnalysis:
        ...
