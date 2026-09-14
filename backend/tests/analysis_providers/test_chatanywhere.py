import json

import httpx
import pytest

from app.analysis_input import ImageAnalysisInput
from app.analysis_prompt import build_analysis_prompt
from app.analysis_providers.base import ProviderAnalysisError, ProviderRequest
from app.analysis_providers.chatanywhere import ChatAnywhereAnalysisProvider


def _request():
    image = ImageAnalysisInput(
        analysisProxyBytes=b"\x89PNG\r\n\x1a\nimage-proxy", width=16, height=16, aspectRatio=1.0,
    )
    return ProviderRequest(
        analysisInput=image, prompt=build_analysis_prompt(image), model="gpt-4o-mini",
    )


def _response(text):
    return {"status": "completed", "output": [{
        "type": "message", "content": [{"type": "output_text", "text": text}],
    }]}


def _valid_analysis():
    return {
        "observedFacts": {"staticVisual": {
            "subject": "人物", "scene": "室内", "composition": "居中", "viewpoint": "平视",
            "lighting": "柔光", "color": "暖色", "visualStyle": "纪实",
        }, "temporal": None},
        "generationSuggestions": {
            "subjectMotion": "缓慢移动", "environmentalMotion": "轻微变化", "cameraMotion": "稳定",
            "rhythm": "平缓", "suggestedDuration": 5.0, "audio": "环境音建议",
        },
    }


def test_chatanywhere_uses_fixed_responses_endpoint_bearer_auth_and_strict_image_schema():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_response("{}"))

    provider = ChatAnywhereAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    provider.analyze(_request())

    assert str(captured[0].url) == "https://api.chatanywhere.tech/v1/responses"
    assert captured[0].headers["authorization"] == "Bearer test-key"
    body = json.loads(captured[0].content)
    assert body["model"] == "gpt-4o-mini"
    assert body["store"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert any(part["type"] == "input_image" for part in body["input"][0]["content"])


def test_chatanywhere_maps_unauthorized_response_to_stable_authentication_failure():
    provider = ChatAnywhereAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": "denied"}))),
        credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_request())

    assert error.value.failure.code == "authentication_failed"
    assert "test-key" not in str(error.value)


def test_chatanywhere_connection_probe_uses_strict_validation_then_one_text_only_repair():
    captured = []
    responses = iter([_response("{}"), _response(json.dumps(_valid_analysis(), ensure_ascii=False))])

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    provider = ChatAnywhereAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    result = provider.test_connection()

    assert result.observedFacts.staticVisual.subject == "人物"
    assert len(captured) == 2
    assert any(part["type"] == "input_image" for part in captured[0]["input"][0]["content"])
    assert captured[1]["input"][0]["content"][0]["type"] == "input_text"
    assert all(part["type"] != "input_image" for part in captured[1]["input"][0]["content"])
