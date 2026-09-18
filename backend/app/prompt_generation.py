"""Generate reusable prompt suggestions from confirmed structured analysis."""

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .analysis_providers.base import AnalysisProvider, ProviderAnalysisError, ProviderFailure, ProviderRequest, ProviderResult
from .semantic_analysis import StructuredVisualAnalysis


_MAX_PROMPT_TEXT_LENGTH = 12_000
_MAX_PROVIDER_RESPONSE_CHARS = 64_000
_DATA_URL = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
_BEARER = re.compile(r"\bbearer\s+[^\s\"\\]+", re.IGNORECASE)
_SK_CREDENTIAL = re.compile(r"\bsk-[A-Za-z0-9_-]+", re.IGNORECASE)
_POSIX_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s\"\\/]+(?:/[^\s\"\\/]+)*)")
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\s\"\\]+[\\/]?)*")


class PromptTexts(BaseModel):
    """Four editable prompt suggestions returned by the selected provider."""

    model_config = ConfigDict(extra="forbid")

    positiveZh: str = Field(min_length=1, max_length=_MAX_PROMPT_TEXT_LENGTH)
    negativeZh: str = Field(min_length=1, max_length=_MAX_PROMPT_TEXT_LENGTH)
    positiveEn: str = Field(min_length=1, max_length=_MAX_PROMPT_TEXT_LENGTH)
    negativeEn: str = Field(min_length=1, max_length=_MAX_PROMPT_TEXT_LENGTH)

    @field_validator("positiveZh", "negativeZh", "positiveEn", "negativeEn")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("提示词不能为空白。")
        return value


def generate_prompts(
    analysis: StructuredVisualAnalysis,
    provider: AnalysisProvider,
    model: str,
) -> PromptTexts:
    """Request one text-only, schema-constrained prompt suggestion response."""

    request = ProviderRequest(
        task="prompt_generation",
        prompt=_prompt_generation_instruction(analysis),
        model=model,
        responseSchema=PromptTexts.model_json_schema(),
    )
    result = provider.analyze(request)
    try:
        raw_text = _provider_text(result)
        if len(raw_text) > _MAX_PROVIDER_RESPONSE_CHARS:
            raise ValueError("提示词响应超过安全边界")
        return PromptTexts.model_validate(json.loads(raw_text))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response")) from None


def _provider_text(result: ProviderResult) -> str:
    if not isinstance(result, ProviderResult) or not isinstance(result.rawText, str):
        raise TypeError("供应商响应必须是文本")
    return result.rawText


def _prompt_generation_instruction(analysis: StructuredVisualAnalysis) -> str:
    safe_analysis = _redact_value(analysis.model_dump(mode="json"))
    return (
        "你是视频复刻方案的提示词编写器。只根据下方结构化分析生成通用图生视频复刻建议，"
        "不要声称恢复了参考素材的原始提示词。请提供中文和英文的正向、负向提示词。"
        "不要使用 Markdown、解释或代码围栏，只返回符合响应结构的 JSON 对象。\n"
        "结构化分析：\n"
        + json.dumps(safe_analysis, ensure_ascii=False, separators=(",", ":"))
    )


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def _redact_text(value: str) -> str:
    value = _DATA_URL.sub("<redacted-image>", value)
    value = _BEARER.sub("Bearer <redacted>", value)
    value = _SK_CREDENTIAL.sub("<redacted>", value)
    value = _WINDOWS_PATH.sub("<redacted-path>", value)
    return _POSIX_PATH.sub("<redacted-path>", value)


__all__ = ["PromptTexts", "generate_prompts"]
