import json

import httpx
import pytest

from app.analysis_input import ImageAnalysisInput
from app.analysis_prompt import build_analysis_prompt
from app.analysis_providers.base import ProviderAnalysisError, ProviderRequest
from app.analysis_providers.local_openai_compatible import LocalOpenAICompatibleAnalysisProvider


def _valid_analysis():
    return {
        "observedFacts": {
            "staticVisual": {
                "subject": "人物", "scene": "室内", "composition": "居中", "viewpoint": "平视",
                "lighting": "柔光", "color": "暖色", "visualStyle": "纪实",
            },
            "temporal": None,
        },
        "generationSuggestions": {
            "subjectMotion": "缓慢移动", "environmentalMotion": "轻微变化", "cameraMotion": "稳定",
            "rhythm": "平缓", "suggestedDuration": 5.0, "audio": "环境音建议",
        },
    }


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
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(_valid_analysis(), ensure_ascii=False),
        }}]})

    provider = LocalOpenAICompatibleAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "http://localhost:11434/v1",
    )

    result = provider.test_connection("local-vision")

    assert result.observedFacts.temporal is None
    assert "data:image/png;base64," in captured[0].content.decode("utf-8")


def test_local_connection_test_rejects_invalid_text_after_one_safe_repair_attempt():
    captured = []
    original_text = "这不是 JSON：Bearer local-secret data:image/png;base64,NOT_A_REAL_IMAGE"

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": original_text}}]})

    provider = LocalOpenAICompatibleAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "http://localhost:11434/v1",
        credential="local-secret",
    )
    observed_requests = []
    original_analyze = provider.analyze

    def observe_request(request):
        observed_requests.append(request)
        return original_analyze(request)

    provider.analyze = observe_request

    with pytest.raises(ProviderAnalysisError) as error:
        provider.test_connection("local-vision")

    assert error.value.failure.code == "invalid_analysis_response"
    assert [request.isRepair for request in observed_requests] == [False, True]
    assert len(captured) == 2
    repair = json.loads(captured[1].content)
    assert repair["response_format"] == {"type": "json_object"}
    assert [part["type"] for part in repair["messages"][0]["content"]] == ["text"]
    assert "data:image/png;base64," not in repair["messages"][0]["content"][0]["text"]
    assert original_text not in repair["messages"][0]["content"][0]["text"]
    assert "local-secret" not in repair["messages"][0]["content"][0]["text"]


def test_local_connection_test_returns_a_repaired_structured_result_after_one_text_only_retry():
    captured = []
    responses = iter(["{}", json.dumps(_valid_analysis(), ensure_ascii=False)])

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": next(responses)}}]})

    provider = LocalOpenAICompatibleAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "http://localhost:11434/v1",
    )
    observed_requests = []
    original_analyze = provider.analyze

    def observe_request(request):
        observed_requests.append(request)
        return original_analyze(request)

    provider.analyze = observe_request

    result = provider.test_connection("local-vision")

    assert result.observedFacts.temporal is None
    assert [request.isRepair for request in observed_requests] == [False, True]
    assert len(captured) == 2
    first = json.loads(captured[0].content)
    repair = json.loads(captured[1].content)
    assert [part["type"] for part in first["messages"][0]["content"]] == [
        "text", "text", "image_url",
    ]
    assert repair["response_format"] == {"type": "json_object"}
    assert [part["type"] for part in repair["messages"][0]["content"]] == ["text"]
