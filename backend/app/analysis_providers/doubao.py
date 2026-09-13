from typing import Any, Mapping, Optional

import httpx

from ..analysis_models import StructuredAnalysis
from ..analysis_provider import DoubaoProviderConfig, ProviderConfig, ProviderFailure as LegacyProviderFailure, ProviderRequest as LegacyProviderRequest
from ..provider_models import model_is_allowed, models_for
from . import ProviderAnalysisError as LegacyProviderAnalysisError, image_data_url as legacy_image_data_url, instructions, post_json, request_context, response_schema, validate_configuration, validate_structured_analysis
from .base import ProviderAnalysisError, ProviderFailure, ProviderRequest, ProviderResult, failure_for_status
from .http_transport import ProviderHTTPTransportError, post_provider_json
from .responses_api import build_responses_payload, result_from_responses_body
from .test_image import connection_test_request


class DoubaoAnalysisProvider:
    provider_id = "doubao"
    _URL = "https://ark.cn-beijing.volces.com/api/v3/responses"

    models = tuple(model.id for model in models_for("doubao"))

    def __init__(
        self,
        client: httpx.Client,
        verified_endpoint_bindings: Mapping[str, str] = (),
        *,
        credential: Optional[str] = None,
    ):
        """Keep legacy endpoint bindings positional; core credentials are explicit."""

        self._client = client
        self._verified_endpoint_bindings = dict(verified_endpoint_bindings)
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
                headers={"Authorization": "Bearer " + self._credential, "Content-Type": "application/json"},
                payload=build_responses_payload(request),
            )
        except ProviderHTTPTransportError as error:
            raise ProviderAnalysisError(ProviderFailure.for_code(error.failure_code)) from None
        if response.status_code >= 300:
            raise ProviderAnalysisError(failure_for_status(response.status_code))
        try:
            return result_from_responses_body(response.json_body, response.request_id)
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
        """Compatibility validation for the 03-era endpoint-bound contract."""

        validate_configuration(config, credential, DoubaoProviderConfig)
        assert isinstance(config, DoubaoProviderConfig)
        if self._verified_endpoint_bindings.get(config.endpointId) != config.modelId:
            raise LegacyProviderAnalysisError(LegacyProviderFailure.for_code("unsupported_model_capability"))

    def analyze_legacy(
        self,
        request: LegacyProviderRequest,
        config: ProviderConfig,
        credential: str,
    ) -> StructuredAnalysis:
        """Explicit legacy entrypoint; new callers must use ``analyze`` above."""

        self.validate_configuration(config, credential)
        assert isinstance(config, DoubaoProviderConfig)
        payload: dict[str, Any] = {
            "model": config.endpointId,
            "instructions": instructions(),
            "input": [{"role": "user", "content": [
                {"type": "input_image", "image_url": legacy_image_data_url(request)},
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
