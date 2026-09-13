import base64
from typing import Any

import httpx

from ..analysis_models import StructuredAnalysis
from ..analysis_provider import ClaudeProviderConfig, ProviderConfig, ProviderRequest
from . import instructions, post_json, request_context, response_schema, validate_configuration, validate_structured_analysis


class ClaudeAnalysisProvider:
    provider_id = "claude"
    _URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, client: httpx.Client):
        self._client = client

    def validate_configuration(self, config: ProviderConfig, credential: str) -> None:
        validate_configuration(config, credential, ClaudeProviderConfig)

    def analyze(self, request: ProviderRequest, config: ProviderConfig, credential: str) -> StructuredAnalysis:
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
