from typing import Any

import httpx

from ..analysis_models import StructuredAnalysis
from ..analysis_provider import OpenAIProviderConfig, ProviderConfig, ProviderRequest
from . import image_data_url, instructions, post_json, request_context, response_schema, validate_configuration, validate_structured_analysis


class OpenAIAnalysisProvider:
    provider_id = "openai"
    _URL = "https://api.openai.com/v1/responses"

    def __init__(self, client: httpx.Client):
        self._client = client

    def validate_configuration(self, config: ProviderConfig, credential: str) -> None:
        validate_configuration(config, credential, OpenAIProviderConfig)

    def analyze(self, request: ProviderRequest, config: ProviderConfig, credential: str) -> StructuredAnalysis:
        self.validate_configuration(config, credential)
        assert isinstance(config, OpenAIProviderConfig)
        payload: dict[str, Any] = {
            "model": config.modelId,
            "instructions": instructions(),
            "input": [{"role": "user", "content": [
                {"type": "input_image", "image_url": image_data_url(request)},
                {"type": "input_text", "text": request_context(request)},
            ]}],
            "text": {"format": {
                "type": "json_schema", "name": "structured_analysis", "strict": True,
                "schema": response_schema(request),
            }},
        }
        response = post_json(self._client, self._URL, {
            "Authorization": "Bearer " + credential,
            "Content-Type": "application/json",
        }, payload)
        try:
            raw_text = response["output"][0]["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raw_text = None
        return validate_structured_analysis(raw_text)
