import base64
from typing import Any

import httpx

from ..analysis_models import StructuredAnalysis
from ..analysis_provider import GeminiProviderConfig, ProviderConfig, ProviderRequest
from . import instructions, post_json, request_context, response_schema, validate_configuration, validate_structured_analysis


class GeminiAnalysisProvider:
    provider_id = "gemini"
    _URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, client: httpx.Client):
        self._client = client

    def validate_configuration(self, config: ProviderConfig, credential: str) -> None:
        validate_configuration(config, credential, GeminiProviderConfig)

    def analyze(self, request: ProviderRequest, config: ProviderConfig, credential: str) -> StructuredAnalysis:
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
        response = post_json(self._client, self._URL_TEMPLATE.format(model=config.modelId), {
            "x-goog-api-key": credential,
            "Content-Type": "application/json",
        }, payload)
        try:
            raw_text = response["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raw_text = None
        return validate_structured_analysis(raw_text)
