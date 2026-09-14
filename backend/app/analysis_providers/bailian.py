from typing import Any, Optional

import httpx

from ..analysis_models import StructuredAnalysis
from ..analysis_provider import BailianProviderConfig, ProviderConfig, ProviderRequest
from ..provider_models import models_for
from ..semantic_analysis import StructuredVisualAnalysis
from . import image_data_url, instructions, post_json, request_context, response_schema, validate_configuration, validate_structured_analysis
from .base import ProviderAnalysisError, ProviderFailure, ProviderRequest as CoreProviderRequest
from .base import ProviderResult
from .openai_compatible_chat import post_chat_completion
from .test_image import connection_test_request


class BailianAnalysisProvider:
    provider_id = "bailian"
    _URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    models = tuple(model.id for model in models_for("bailian"))

    def __init__(self, client: httpx.Client, credential: Optional[str] = None):
        self._client = client
        self._credential = credential

    def validate_configuration(self, config: ProviderConfig, credential: str) -> None:
        validate_configuration(config, credential, BailianProviderConfig)

    def analyze(
        self,
        request: Any,
        config: Optional[ProviderConfig] = None,
        credential: Optional[str] = None,
    ) -> Any:
        """Support the 04c safe request contract and the historical contract.

        The old adapter was not wired into the application, but public tests
        still exercise it.  Keeping the legacy branch here avoids duplicating
        its HTTP/error mapping in a second, incompatible Bailian class.
        """
        if isinstance(request, CoreProviderRequest):
            if config is not None or credential is not None:
                raise TypeError("新供应商请求不接受旧配置对象。")
            if request.model not in self.models:
                raise ProviderAnalysisError(ProviderFailure.for_code("unsupported_model_capability"))
            return post_chat_completion(self._client, self._URL, self._credential, request)
        if config is None or credential is None:
            raise TypeError("旧供应商请求需要配置和凭据。")
        return self.analyze_legacy(request, config, credential)

    def analyze_legacy(
        self,
        request: ProviderRequest,
        config: ProviderConfig,
        credential: str,
    ) -> StructuredAnalysis:
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

    def test_connection(self, model: Optional[str] = None) -> StructuredVisualAnalysis:
        selected_model = model or self.models[0]
        if selected_model not in self.models:
            raise ProviderAnalysisError(ProviderFailure.for_code("unsupported_model_capability"))
        request = connection_test_request(selected_model)
        from ..analysis_response import validate_or_repair

        return validate_or_repair(self, self.analyze(request), request)
