from typing import Optional

import httpx

from ..provider_models import model_is_allowed, models_for
from .base import ProviderAnalysisError, ProviderFailure, ProviderRequest, ProviderResult, failure_for_status
from .http_transport import ProviderHTTPTransportError, post_provider_json
from .responses_api import build_responses_payload, result_from_responses_body
from .test_image import connection_test_request


class GrokAnalysisProvider:
    provider_id = "grok"
    _URL = "https://api.x.ai/v1/responses"
    models = tuple(model.id for model in models_for("grok"))

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


__all__ = ["GrokAnalysisProvider"]
