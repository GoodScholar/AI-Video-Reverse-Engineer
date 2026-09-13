from typing import Any, Mapping

import httpx

from ..analysis_models import StructuredAnalysis
from ..analysis_provider import DoubaoProviderConfig, ProviderConfig, ProviderFailure, ProviderRequest
from . import ProviderAnalysisError, image_data_url, instructions, post_json, request_context, response_schema, validate_configuration, validate_structured_analysis


class DoubaoAnalysisProvider:
    provider_id = "doubao"
    _URL = "https://ark.cn-beijing.volces.com/api/v3/responses"

    def __init__(self, client: httpx.Client, verified_endpoint_bindings: Mapping[str, str] = ()): 
        self._client = client
        self._verified_endpoint_bindings = dict(verified_endpoint_bindings)

    def validate_configuration(self, config: ProviderConfig, credential: str) -> None:
        validate_configuration(config, credential, DoubaoProviderConfig)
        assert isinstance(config, DoubaoProviderConfig)
        if self._verified_endpoint_bindings.get(config.endpointId) != config.modelId:
            raise ProviderAnalysisError(ProviderFailure.for_code("unsupported_model_capability"))

    def analyze(self, request: ProviderRequest, config: ProviderConfig, credential: str) -> StructuredAnalysis:
        self.validate_configuration(config, credential)
        assert isinstance(config, DoubaoProviderConfig)
        payload: dict[str, Any] = {
            "model": config.endpointId,
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
