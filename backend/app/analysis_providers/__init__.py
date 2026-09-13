import base64
import json
from collections.abc import Mapping
from typing import Any, Type

import httpx
from pydantic import ValidationError

from ..analysis_models import StructuredAnalysis
from ..analysis_provider import ProviderConfig, ProviderFailure, ProviderRequest


_INSTRUCTIONS = (
    "仅依据所提供的联系表和分析代理生成结构化视频语义分析。"
    "不要推断本地文件、项目名称、原始文件名或凭据。"
)


class ProviderAnalysisError(Exception):
    def __init__(self, failure: ProviderFailure):
        self.failure = failure
        super().__init__(failure.message)


def validate_configuration(config: ProviderConfig, credential: str, expected_type: Type[Any]) -> None:
    if not isinstance(config, expected_type) or not isinstance(credential, str) or not credential.strip():
        raise ProviderAnalysisError(ProviderFailure.for_code("provider_unconfigured"))


def image_data_url(request: ProviderRequest) -> str:
    encoded = base64.b64encode(request.contactSheetBytes).decode("ascii")
    return "data:image/jpeg;base64," + encoded


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    if isinstance(value, list):
        return [_plain_json(item) for item in value]
    return value


def request_context(request: ProviderRequest) -> str:
    return "分析代理 JSON：" + json.dumps(_plain_json(request.proxy), ensure_ascii=False, separators=(",", ":"))


def response_schema(request: ProviderRequest) -> dict:
    return _plain_json(request.responseSchema)


def instructions() -> str:
    return _INSTRUCTIONS


def post_json(client: httpx.Client, url: str, headers: dict, payload: dict) -> Any:
    try:
        response = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as error:
        raise ProviderAnalysisError(ProviderFailure.for_code("timeout")) from None
    except httpx.RequestError as error:
        raise ProviderAnalysisError(ProviderFailure.for_code("network_error")) from None

    if response.status_code in (401, 403):
        raise ProviderAnalysisError(ProviderFailure.for_code("authentication_failed"))
    if response.status_code == 429:
        raise ProviderAnalysisError(ProviderFailure.for_code("rate_limited"))
    if response.status_code >= 500:
        raise ProviderAnalysisError(ProviderFailure.for_code("provider_error"))
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as error:
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_response")) from None
    if response.status_code >= 400:
        error_text = json.dumps(payload, ensure_ascii=False).lower()
        if any(marker in error_text for marker in ("unsupported", "capability", "model_not")):
            code = "unsupported_model_capability"
        elif any(marker in error_text for marker in ("content", "safety", "blocked", "rejected")):
            code = "content_rejected"
        else:
            code = "provider_error"
        raise ProviderAnalysisError(ProviderFailure.for_code(code))
    return payload


def validate_structured_analysis(raw_text: Any) -> StructuredAnalysis:
    if not isinstance(raw_text, str):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_response"))
    try:
        return StructuredAnalysis.model_validate(json.loads(raw_text))
    except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_response")) from None


__all__ = ["ProviderAnalysisError"]
