import base64
from copy import deepcopy
from typing import Any, Optional, Union

import httpx

from ..analysis_input import ImageAnalysisInput, VideoAnalysisInput
from ..analysis_models import StructuredAnalysis
from ..analysis_prompt import analysis_input_context, structured_analysis_schema
from ..analysis_provider import ClaudeProviderConfig, ProviderConfig, ProviderRequest as LegacyProviderRequest
from ..provider_models import model_is_allowed, models_for
from ..semantic_analysis import StructuredVisualAnalysis
from . import instructions, post_json, request_context, response_schema, validate_configuration, validate_structured_analysis
from .base import ProviderAnalysisError, ProviderFailure, ProviderRequest, ProviderResult, failure_for_status
from .http_transport import ProviderHTTPTransportError, post_provider_json
from .test_image import connection_test_request


_UNSUPPORTED_SCHEMA_CONSTRAINTS = frozenset({
    "exclusiveMaximum", "exclusiveMinimum", "maxItems", "maxLength", "maxProperties",
    "maximum", "minItems", "minLength", "minProperties", "minimum", "multipleOf", "pattern",
    "uniqueItems",
})
_SUPPORTED_STRING_FORMATS = frozenset({"date-time", "date", "time", "duration", "email", "hostname", "ipv4", "ipv6", "uri", "uuid"})


class ClaudeAnalysisProvider:
    provider_id = "claude"
    _URL = "https://api.anthropic.com/v1/messages"
    models = tuple(model.id for model in models_for("claude"))

    def __init__(self, client: httpx.Client, credential: Optional[str] = None):
        self._client = client
        self._credential = credential

    def analyze(self, request: ProviderRequest) -> ProviderResult:
        if not model_is_allowed(self.provider_id, request.model):
            raise ProviderAnalysisError(ProviderFailure.for_code("unsupported_model_capability"))
        if self._credential is None or not self._credential.strip():
            raise ProviderAnalysisError(ProviderFailure.for_code("provider_unconfigured"))
        try:
            response = post_provider_json(
                self._client,
                self._URL,
                headers={
                    "x-api-key": self._credential,
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "structured-outputs-2025-11-13",
                    "Content-Type": "application/json",
                },
                payload=_messages_payload(request),
            )
        except ProviderHTTPTransportError as error:
            raise ProviderAnalysisError(ProviderFailure.for_code(error.failure_code)) from None
        if response.status_code >= 300:
            raise ProviderAnalysisError(failure_for_status(response.status_code))
        try:
            return _result_from_message_body(response.json_body, response.request_id)
        except (TypeError, ValueError):
            raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response")) from None

    def test_connection(self, model: Optional[str] = None):
        selected_model = model or self.models[0]
        if not model_is_allowed(self.provider_id, selected_model):
            raise ProviderAnalysisError(ProviderFailure.for_code("unsupported_model_capability"))
        request = connection_test_request(selected_model)
        from ..analysis_response import validate_or_repair

        return validate_or_repair(self, self.analyze(request), request)

    def validate_configuration(self, config: ProviderConfig, credential: str) -> None:
        """Compatibility validation for the 03-era public adapter contract."""

        validate_configuration(config, credential, ClaudeProviderConfig)

    def analyze_legacy(
        self,
        request: LegacyProviderRequest,
        config: ProviderConfig,
        credential: str,
    ) -> StructuredAnalysis:
        """Explicit legacy entrypoint; new callers use ``analyze`` above."""

        self.validate_configuration(config, credential)
        assert isinstance(config, ClaudeProviderConfig)
        payload: dict[str, Any] = {
            "model": config.modelId,
            "max_tokens": 4096,
            "system": instructions(),
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.b64encode(request.contactSheetBytes).decode("ascii"),
                }},
                {"type": "text", "text": request_context(request)},
            ]}],
            "output_config": {"format": {
                "type": "json_schema", "schema": response_schema(request),
            }},
        }
        response = post_json(self._client, self._URL, {
            "x-api-key": credential,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "structured-outputs-2025-11-13",
            "Content-Type": "application/json",
        }, payload)
        try:
            raw_text = response["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raw_text = None
        return validate_structured_analysis(raw_text)


def _messages_payload(request: ProviderRequest) -> dict[str, Any]:
    content = [{"type": "text", "text": request.prompt}]
    if request.analysisInput is not None:
        content.extend([
            {"type": "text", "text": analysis_input_context(request.analysisInput)},
            {"type": "image", "source": _base64_image_source(request.analysisInput)},
        ])
        schema = structured_analysis_schema(request.analysisInput.mediaType)
    else:
        schema = StructuredVisualAnalysis.model_json_schema()
    return {
        "model": request.model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {
            "type": "json_schema",
            "schema": _claude_schema(schema),
        }},
    }


def _base64_image_source(value: Union[ImageAnalysisInput, VideoAnalysisInput]) -> dict[str, str]:
    image_bytes = value.analysisProxyBytes if isinstance(value, ImageAnalysisInput) else value.contactSheetBytes
    return {
        "type": "base64",
        "media_type": "image/png" if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg",
        "data": base64.b64encode(image_bytes).decode("ascii"),
    }


def _claude_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy the domain schema into Claude's supported strict-output dialect."""

    normalized = deepcopy(schema)
    _normalize_claude_schema(normalized)
    return normalized


def _normalize_claude_schema(value: Any) -> None:
    if isinstance(value, dict):
        for constraint in _UNSUPPORTED_SCHEMA_CONSTRAINTS:
            value.pop(constraint, None)
        if value.get("type") == "string" and value.get("format") not in _SUPPORTED_STRING_FORMATS:
            value.pop("format", None)
        if isinstance(value.get("properties"), dict):
            value["additionalProperties"] = False
        for nested in value.values():
            _normalize_claude_schema(nested)
    elif isinstance(value, list):
        for nested in value:
            _normalize_claude_schema(nested)


def _result_from_message_body(body: Any, request_id: Optional[str]) -> ProviderResult:
    if not isinstance(body, dict) or body.get("type") != "message" or body.get("role") != "assistant":
        raise ValueError("Messages 响应不是完成的助手消息")
    if body.get("stop_reason") != "end_turn":
        raise ValueError("Messages 响应未正常结束")
    stop_details = body.get("stop_details")
    if isinstance(stop_details, dict) and stop_details.get("type") == "refusal":
        raise ValueError("Messages 响应被拒绝")
    content = body.get("content")
    if not isinstance(content, list):
        raise ValueError("Messages 响应缺少内容")
    chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            raise ValueError("Messages 内容块无效")
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError("Messages 文本无效")
        chunks.append(text)
    raw_text = "".join(chunks)
    if not raw_text.strip():
        raise ValueError("Messages 响应没有文本")
    return ProviderResult(rawText=raw_text, providerRequestId=request_id)


__all__ = ["ClaudeAnalysisProvider"]
