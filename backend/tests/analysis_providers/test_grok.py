import json

import httpx
import pytest

from app.analysis_input import ImageAnalysisInput
from app.analysis_prompt import build_analysis_prompt
from app.analysis_providers.base import ProviderAnalysisError, ProviderRequest
from app.analysis_providers.grok import GrokAnalysisProvider


def _request():
    image = ImageAnalysisInput(
        analysisProxyBytes=b"\xff\xd8\xff\xe0image-proxy\xff\xd9",
        width=16,
        height=16,
        aspectRatio=1.0,
    )
    return ProviderRequest(
        analysisInput=image,
        prompt=build_analysis_prompt(image),
        model="grok-4.6",
    )


def _response(text="{}"):
    return {"status": "completed", "output": [{
        "type": "message", "content": [{"type": "output_text", "text": text}],
    }]}


def test_grok_sends_the_responses_request_to_the_fixed_endpoint_with_bearer_auth():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_response())

    provider = GrokAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    provider.analyze(_request())

    assert str(captured[0].url) == "https://api.x.ai/v1/responses"
    assert captured[0].headers["authorization"] == "Bearer test-key"
    body = json.loads(captured[0].content)
    assert body["model"] == "grok-4.6"
    assert body["store"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    assert any(part["type"] == "input_image" for part in body["input"][0]["content"])


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"error": "denied"}), "authentication_failed"),
        (httpx.Response(429, json={"error": "limited"}), "rate_limited"),
        (httpx.Response(503, json={"error": "unavailable"}), "provider_error"),
        (httpx.ConnectError("network"), "network_error"),
    ],
)
def test_grok_maps_provider_and_network_failures_to_safe_stable_codes(response, code):
    def handler(request):
        if isinstance(response, Exception):
            raise response
        return response

    provider = GrokAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_request())

    assert error.value.failure.code == code
    assert "test-key" not in str(error.value)


def test_grok_rejects_missing_responses_output_text():
    provider = GrokAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"status": "completed", "output": [{"type": "message", "content": []}]},
        ))),
        credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_request())

    assert error.value.failure.code == "invalid_analysis_response"
