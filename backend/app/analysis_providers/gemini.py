import base64
from copy import deepcopy
from typing import Any, Optional, Union
from urllib.parse import quote

import httpx

from ..analysis_input import ImageAnalysisInput, VideoAnalysisInput
from ..analysis_models import StructuredAnalysis
from ..analysis_prompt import analysis_input_context, structured_analysis_schema
from ..analysis_provider import GeminiProviderConfig, ProviderConfig, ProviderRequest as LegacyProviderRequest
from ..provider_models import model_is_allowed, models_for
from ..semantic_analysis import StructuredVisualAnalysis
from . import instructions, post_json, request_context, response_schema, validate_configuration, validate_structured_analysis
from .base import ProviderAnalysisError, ProviderFailure, ProviderRequest, ProviderResult, failure_for_status
from .http_transport import ProviderHTTPTransportError, post_provider_json
from .test_image import connection_test_request


_GEMINI_UNSUPPORTED_SCHEMA_FIELDS = frozenset({
    "default", "description", "examples", "exclusiveMaximum", "exclusiveMinimum",
    "maxLength", "minLength", "title",
})


class GeminiAnalysisProvider:
    provider_id = "gemini"
    _URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    models = tuple(model.id for model in models_for("gemini"))

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
                self._url_for_model(request.model),
                headers={"x-goog-api-key": self._credential, "Content-Type": "application/json"},
                payload=_generate_content_payload(request),
            )
        except ProviderHTTPTransportError as error:
            raise ProviderAnalysisError(ProviderFailure.for_code(error.failure_code)) from None
        if response.status_code >= 300:
            raise ProviderAnalysisError(failure_for_status(response.status_code))
        try:
            return _result_from_generate_content_body(response.json_body, response.request_id)
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

        validate_configuration(config, credential, GeminiProviderConfig)

    def analyze_legacy(
        self,
        request: LegacyProviderRequest,
        config: ProviderConfig,
        credential: str,
    ) -> StructuredAnalysis:
        """Explicit legacy entrypoint; new callers use ``analyze`` above."""

        self.validate_configuration(config, credential)
        assert isinstance(config, GeminiProviderConfig)
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": instructions()}]},
            "contents": [{"role": "user", "parts": [
                {"inlineData": {
                    "mimeType": "image/jpeg",
                    "data": base64.b64encode(request.contactSheetBytes).decode("ascii"),
                }},
                {"text": request_context(request)},
            ]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": response_schema(request),
            },
        }
        response = post_json(self._client, self._url_for_model(config.modelId), {
            "x-goog-api-key": credential,
            "Content-Type": "application/json",
        }, payload)
        try:
            raw_text = response["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raw_text = None
        return validate_structured_analysis(raw_text)

    @classmethod
    def _url_for_model(cls, model: str) -> str:
        """Compose a URL only after the closed model catalog has accepted it."""

        return cls._URL_TEMPLATE.format(model=quote(model, safe="-._~"))


def _generate_content_payload(request: ProviderRequest) -> dict[str, Any]:
    parts = [{"text": request.prompt}]
    if request.analysisInput is not None:
        parts.extend([
            {"text": analysis_input_context(request.analysisInput)},
            {"inlineData": _inline_data(request.analysisInput)},
        ])
        schema = structured_analysis_schema(request.analysisInput.mediaType)
    else:
        schema = StructuredVisualAnalysis.model_json_schema()
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": _gemini_schema(schema),
        },
    }


def _inline_data(value: Union[ImageAnalysisInput, VideoAnalysisInput]) -> dict[str, str]:
    image_bytes = value.analysisProxyBytes if isinstance(value, ImageAnalysisInput) else value.contactSheetBytes
    return {
        "mimeType": "image/png" if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg",
        "data": base64.b64encode(image_bytes).decode("ascii"),
    }


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline Pydantic references into Gemini's supported JSON Schema subset.

    This affects only the wire schema. The full domain model remains the
    authoritative final validator after a response has been received.
    """

    root = deepcopy(schema)
    definitions = root.pop("$defs", {})
    if not isinstance(definitions, dict):
        raise ValueError("结构化响应定义无效")
    return _normalize_gemini_schema(root, definitions)


def _normalize_gemini_schema(value: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [_normalize_gemini_schema(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"$ref"}:
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise ValueError("结构化响应引用无效")
        name = reference.removeprefix("#/$defs/")
        if name not in definitions:
            raise ValueError("结构化响应引用缺失")
        return _normalize_gemini_schema(deepcopy(definitions[name]), definitions)
    if "anyOf" in value:
        alternatives = value.get("anyOf")
        if not isinstance(alternatives, list) or len(alternatives) != 2:
            raise ValueError("Gemini 不支持该结构化响应联合")
        normalized = [_normalize_gemini_schema(item, definitions) for item in alternatives]
        null_schema = next((item for item in normalized if item == {"type": "null"}), None)
        non_null_schema = next((item for item in normalized if item != {"type": "null"}), None)
        if null_schema is None or not isinstance(non_null_schema, dict) or not isinstance(non_null_schema.get("type"), str):
            raise ValueError("Gemini 不支持该结构化响应联合")
        non_null_schema["type"] = [non_null_schema["type"], "null"]
        return non_null_schema
    normalized = {}
    for key, nested in value.items():
        if key in _GEMINI_UNSUPPORTED_SCHEMA_FIELDS:
            continue
        if key in {"$defs", "$ref"}:
            raise ValueError("结构化响应引用无效")
        normalized[key] = _normalize_gemini_schema(nested, definitions)
    return normalized


def _result_from_generate_content_body(body: Any, request_id: Optional[str]) -> ProviderResult:
    if not isinstance(body, dict):
        raise ValueError("GenerateContent 响应不是对象")
    feedback = body.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise ValueError("GenerateContent 请求被安全策略阻止")
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("GenerateContent 响应没有候选")
    candidate = candidates[0]
    if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
        raise ValueError("GenerateContent 候选未正常结束")
    safety_ratings = candidate.get("safetyRatings")
    if isinstance(safety_ratings, list) and any(
        isinstance(rating, dict) and rating.get("blocked") is True for rating in safety_ratings
    ):
        raise ValueError("GenerateContent 候选被安全策略阻止")
    content = candidate.get("content")
    if not isinstance(content, dict):
        raise ValueError("GenerateContent 候选缺少内容")
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("GenerateContent 候选缺少文本块")
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("GenerateContent 文本块无效")
        if part.get("thought") is True:
            continue
        text = part.get("text")
        if not isinstance(text, str):
            raise ValueError("GenerateContent 文本无效")
        chunks.append(text)
    raw_text = "".join(chunks)
    if not raw_text.strip():
        raise ValueError("GenerateContent 响应没有非思考文本")
    return ProviderResult(rawText=raw_text, providerRequestId=request_id)


__all__ = ["GeminiAnalysisProvider"]
