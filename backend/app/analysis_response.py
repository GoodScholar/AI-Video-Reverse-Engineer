import json
from typing import Union

from pydantic import ValidationError

from .analysis_prompt import repair_prompt
from .analysis_providers.base import AnalysisProvider, ProviderAnalysisError, ProviderFailure, ProviderRequest, ProviderResult
from .semantic_analysis import StructuredVisualAnalysis, validate_analysis_for_media


def _raw_text(value: Union[ProviderResult, str]) -> str:
    if isinstance(value, ProviderResult):
        return value.rawText
    if isinstance(value, str):
        return value
    raise TypeError("供应商响应必须是文本")


def _validation_error_summary(error: Exception) -> str:
    if isinstance(error, ValidationError):
        items = [
            {
                "location": ".".join(str(part) for part in item["loc"]),
                "type": item["type"],
                "message": item["msg"],
            }
            for item in error.errors(include_input=False)
        ]
        return json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return json.dumps([{"type": "invalid_json", "message": "响应不是有效 JSON"}], ensure_ascii=False)


def _validate(raw_text: str, request: ProviderRequest) -> StructuredVisualAnalysis:
    if request.analysisInput is None:
        raise ValueError("修复请求不能作为初始响应校验输入")
    return validate_analysis_for_media(json.loads(raw_text), request.analysisInput.mediaType)


def validate_or_repair(
    provider: AnalysisProvider,
    raw: Union[ProviderResult, str],
    request: ProviderRequest,
) -> StructuredVisualAnalysis:
    """Validate a response, then permit exactly one image-free retry."""

    raw_text = _raw_text(raw)
    try:
        return _validate(raw_text, request)
    except (ValueError, TypeError, ValidationError, json.JSONDecodeError) as error:
        repair = request.repair(repair_prompt(raw_text, _validation_error_summary(error)))
    try:
        repaired = provider.analyze(repair)
        return _validate(_raw_text(repaired), request)
    except (ProviderAnalysisError, ValueError, TypeError, ValidationError, json.JSONDecodeError):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response")) from None


__all__ = ["validate_or_repair"]
