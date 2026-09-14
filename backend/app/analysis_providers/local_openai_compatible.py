from typing import Optional

import httpx

from ..analysis_settings import validate_loopback_base_url
from ..semantic_analysis import StructuredVisualAnalysis
from .base import ProviderRequest, ProviderResult
from .openai_compatible_chat import post_chat_completion
from .test_image import connection_test_request


class LocalOpenAICompatibleAnalysisProvider:
    provider_id = "local_openai_compatible"
    models = ()

    def __init__(self, client: httpx.Client, base_url: str, credential: Optional[str] = None):
        self._client = client
        self._base_url = validate_loopback_base_url(base_url)
        self._credential = credential

    def analyze(self, request: ProviderRequest) -> ProviderResult:
        return post_chat_completion(
            self._client,
            self._base_url + "/chat/completions",
            self._credential,
            request,
            credential_required=False,
        )

    def test_connection(self, model: str) -> StructuredVisualAnalysis:
        request = connection_test_request(model)
        from ..analysis_response import validate_or_repair

        return validate_or_repair(self, self.analyze(request), request)


__all__ = ["LocalOpenAICompatibleAnalysisProvider"]
