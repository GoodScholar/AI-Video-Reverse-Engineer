from typing import Any

import httpx

from ..analysis_models import StructuredAnalysis
from ..analysis_provider import BailianProviderConfig, ProviderConfig, ProviderRequest
from . import image_data_url, instructions, post_json, request_context, response_schema, validate_configuration, validate_structured_analysis


class BailianAnalysisProvider:
    provider_id = "bailian"
    _URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    def __init__(self, client: httpx.Client):
        self._client = client

    def validate_configuration(self, config: ProviderConfig, credential: str) -> None:
        validate_configuration(config, credential, BailianProviderConfig)

    def analyze(self, request: ProviderRequest, config: ProviderConfig, credential: str) -> StructuredAnalysis:
        self.validate_configuration(config, credential)
        assert isinstance(config, BailianProviderConfig)
        payload: dict[str, Any] = {
            "model": config.modelId,
            "messages": [
                {"role": "system", "content": instructions()},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url(request)}},
                    {"type": "text", "text": request_context(request)},
                ]},
            ],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "structured_analysis", "strict": True, "schema": response_schema(request),
            }},
        }
        response = post_json(self._client, self._URL, {
            "Authorization": "Bearer " + credential,
            "Content-Type": "application/json",
        }, payload)
        try:
            raw_text = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raw_text = None
        return validate_structured_analysis(raw_text)
