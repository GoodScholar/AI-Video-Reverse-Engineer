import json

import httpx
import pytest

from app.analysis_input import ImageAnalysisInput
from app.analysis_prompt import build_analysis_prompt
from app.analysis_providers.base import ProviderRequest
from app.analysis_providers.local_openai_compatible import LocalOpenAICompatibleAnalysisProvider


def _request():
    return ProviderRequest(
        analysisInput=ImageAnalysisInput(
            analysisProxyBytes=b"\xff\xd8\xff\xe0proxy\xff\xd9",
            width=16,
            height=16,
            aspectRatio=1.0,
        ),
        prompt=build_analysis_prompt("image"),
        model="local-vision",
    )


def test_local_compatible_provider_allows_an_uncredentialed_loopback_service():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = LocalOpenAICompatibleAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "http://127.0.0.1:11434/v1/",
    )

    result = provider.analyze(_request())

    assert result.rawText == "{}"
    assert str(captured[0].url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert "authorization" not in captured[0].headers


@pytest.mark.parametrize("base_url", ["https://example.com/v1", "http://user:pw@localhost:1234/v1"])
def test_local_compatible_provider_rejects_non_loopback_base_url(base_url):
    with pytest.raises(ValueError, match="回环主机"):
        LocalOpenAICompatibleAnalysisProvider(httpx.Client(), base_url)


def test_local_connection_test_uses_the_fixed_probe():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = LocalOpenAICompatibleAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "http://localhost:11434/v1",
    )

    provider.test_connection("local-vision")

    assert "data:image/png;base64," in captured[0].content.decode("utf-8")
