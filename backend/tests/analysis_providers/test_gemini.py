import base64
import json
from unittest.mock import ANY

import httpx
import pytest

from app.analysis_input import (
    ImageAnalysisInput,
    VideoAnalysisInput,
    VideoKeyframe,
    VideoMotion,
    VideoProxy,
    VideoScene,
    VideoSource,
)
from app.analysis_prompt import build_analysis_prompt
from app.analysis_provider import GeminiProviderConfig, ProviderRequest as LegacyProviderRequest
from app.analysis_providers import ProviderAnalysisError as LegacyProviderAnalysisError
from app.analysis_providers.base import ProviderAnalysisError, ProviderRequest
from app.analysis_providers.gemini import GeminiAnalysisProvider


def _image_request():
    image = ImageAnalysisInput(
        analysisProxyBytes=b"\x89PNG\r\n\x1a\nimage-proxy",
        width=32,
        height=24,
        aspectRatio=32 / 24,
    )
    return ProviderRequest(
        analysisInput=image,
        prompt=build_analysis_prompt(image),
        model="gemini-2.5-flash",
    )


def _video_request():
    video = VideoAnalysisInput(
        contactSheetBytes=b"\xff\xd8\xff\xe0contact-sheet\xff\xd9",
        analysisProxy=VideoProxy(
            schemaVersion=1,
            source=VideoSource(durationSeconds=1, width=32, height=24, frameRate=24),
            keyframes=[VideoKeyframe(index=1, timeSeconds=0)],
            scene=VideoScene(changeCount=0),
            motion=VideoMotion(samples=[], p50=None, p90=None, peak=None, level="unavailable"),
            contactSheetFile="contact-sheet.jpg",
        ),
    )
    return ProviderRequest(
        analysisInput=video,
        prompt=build_analysis_prompt(video),
        model="gemini-2.5-flash",
    )


def _candidate(*parts, finish_reason="STOP"):
    return {"candidates": [{
        "content": {"role": "model", "parts": list(parts)},
        "finishReason": finish_reason,
    }]}


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


def _assert_gemini_schema(value):
    if isinstance(value, dict):
        assert "$defs" not in value
        assert "$ref" not in value
        assert "anyOf" not in value
        assert "title" not in value
        assert "description" not in value
        for nested in value.values():
            _assert_gemini_schema(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_gemini_schema(nested)


def test_gemini_sends_fixed_generate_content_request_with_header_inline_png_and_normalized_schema():
    captured = []

    provider = GeminiAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: (
            captured.append(request) or httpx.Response(200, json=_candidate({"text": "{}"}))
        ))),
        credential="test-key",
    )

    provider.analyze(_image_request())

    request = captured[0]
    assert str(request.url) == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    )
    assert "test-key" not in str(request.url)
    assert request.headers["x-goog-api-key"] == "test-key"
    body = json.loads(request.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    _assert_gemini_schema(body["generationConfig"]["responseJsonSchema"])
    parts = body["contents"][0]["parts"]
    image = next(part["inlineData"] for part in parts if "inlineData" in part)
    assert image == {
        "mimeType": "image/png",
        "data": base64.b64encode(b"\x89PNG\r\n\x1a\nimage-proxy").decode("ascii"),
    }
    text_parts = [part["text"] for part in parts if "text" in part]
    assert build_analysis_prompt("image") in text_parts
    assert '{"mediaType":"image","width":32,"height":24,"aspectRatio":1.3333333333333333}' in text_parts


def test_gemini_wire_schema_keeps_nullable_video_and_repair_objects_strict():
    captured = []
    provider = GeminiAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: (
            captured.append(request) or httpx.Response(200, json=_candidate({"text": "{}"}))
        ))),
        credential="test-key",
    )

    provider.analyze(_image_request())
    provider.analyze(_video_request())
    provider.analyze(_video_request().repair("只修复 JSON。"))

    image_schema = json.loads(captured[0].content)["generationConfig"]["responseJsonSchema"]
    video_schema = json.loads(captured[1].content)["generationConfig"]["responseJsonSchema"]
    repair_schema = json.loads(captured[2].content)["generationConfig"]["responseJsonSchema"]
    assert image_schema["properties"]["observedFacts"]["properties"]["temporal"] == {"type": "null"}
    temporal = video_schema["properties"]["observedFacts"]["properties"]["temporal"]
    repair_temporal = repair_schema["properties"]["observedFacts"]["properties"]["temporal"]
    suggestions = video_schema["properties"]["generationSuggestions"]
    assert temporal["type"] == ["object", "null"]
    assert repair_temporal["type"] == ["object", "null"]
    assert temporal["additionalProperties"] is False
    assert set(temporal["required"]) == set(temporal["properties"])
    assert suggestions["additionalProperties"] is False
    assert set(suggestions["required"]) == set(suggestions["properties"])


def test_gemini_sends_video_contact_sheet_with_real_jpeg_mime_and_context():
    captured = []
    provider = GeminiAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: (
            captured.append(request) or httpx.Response(200, json=_candidate({"text": "{}"}))
        ))),
        credential="test-key",
    )

    provider.analyze(_video_request())

    parts = json.loads(captured[0].content)["contents"][0]["parts"]
    assert next(part["inlineData"]["mimeType"] for part in parts if "inlineData" in part) == "image/jpeg"
    assert any('"mediaType":"video"' in part["text"] for part in parts if "text" in part)
    assert any('"contactSheetFile":"contact-sheet.jpg"' in part["text"] for part in parts if "text" in part)


def test_gemini_repair_request_sends_only_text_and_keeps_structured_output():
    captured = []
    provider = GeminiAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: (
            captured.append(request) or httpx.Response(200, json=_candidate({"text": "{}"}))
        ))),
        credential="test-key",
    )

    provider.analyze(_image_request().repair("只修复 JSON。"))

    body = json.loads(captured[0].content)
    assert body["contents"] == [{"role": "user", "parts": [{"text": "只修复 JSON。"}]}]
    assert body["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_collects_non_thought_text_blocks_only():
    provider = GeminiAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=_candidate(
            {"thought": True, "text": "不要泄露"},
            {"text": '{"a":'},
            {"thought": True},
            {"text": "1}"},
        ), headers={"x-request-id": "req_test"}))),
        credential="test-key",
    )

    result = provider.analyze(_image_request())

    assert result.rawText == '{"a":1}'
    assert result.providerRequestId == "req_test"


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"candidates": []},
        {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []},
        _candidate({"text": "{}"}, finish_reason="MAX_TOKENS"),
        _candidate({"text": "{}"}, finish_reason="SAFETY"),
        _candidate({"thought": True, "text": "reasoning"}),
        _candidate({"text": "   "}),
        _candidate({"text": 1}),
        _candidate({"functionCall": {"name": "unexpected"}}),
        {"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP", "safetyRatings": [{"blocked": True}]}]},
    ],
)
def test_gemini_rejects_empty_blocked_truncated_thought_only_or_malformed_candidates(response):
    provider = GeminiAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))),
        credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_image_request())

    assert error.value.failure.code == "invalid_analysis_response"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"error": {"message": "key test-key"}}), "authentication_failed"),
        (httpx.Response(403, json={"error": {"message": "denied"}}), "authentication_failed"),
        (httpx.Response(429, json={"error": {"message": "limited"}}), "rate_limited"),
        (httpx.Response(503, json={"error": {"message": "unavailable"}}), "provider_error"),
        (httpx.ConnectError("secret test-key"), "network_error"),
        (httpx.TimeoutException("secret test-key"), "timeout"),
    ],
)
def test_gemini_maps_status_and_transport_errors_without_exposing_provider_body(response, code):
    def handler(request):
        if isinstance(response, Exception):
            raise response
        return response

    provider = GeminiAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_image_request())

    assert error.value.failure.code == code
    assert "test-key" not in str(error.value)


def test_gemini_connection_probe_uses_fixed_png_full_prompt_and_one_text_only_repair():
    captured = []
    responses = iter([
        _candidate({"text": "{}"}),
        _candidate({"text": json.dumps(_valid_analysis(), ensure_ascii=False)}),
    ])

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=next(responses))

    provider = GeminiAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    result = provider.test_connection()

    assert result.observedFacts.temporal is None
    first = json.loads(captured[0].content)["contents"][0]["parts"]
    second = json.loads(captured[1].content)["contents"][0]["parts"]
    assert next(part["inlineData"]["mimeType"] for part in first if "inlineData" in part) == "image/png"
    assert any(build_analysis_prompt("image") == part["text"] for part in first if "text" in part)
    assert second == [{"text": ANY}]


def test_gemini_rejects_unlisted_model_before_sending_a_request():
    provider = GeminiAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("不得联网"))),
        credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_image_request().model_copy(update={"model": "not-listed"}))

    assert error.value.failure.code == "unsupported_model_capability"


def test_gemini_legacy_contract_is_available_only_through_an_explicit_entrypoint():
    provider = GeminiAnalysisProvider(httpx.Client())
    legacy_request = LegacyProviderRequest(
        contactSheetBytes=b"\xff\xd8\xff\xe0legacy\xff\xd9",
        proxy={"durationSeconds": 1.0},
        responseSchema={"type": "object"},
    )
    config = GeminiProviderConfig(
        providerId="gemini", modelId="gemini-2.5-flash", projectId="project", location="global",
    )

    with pytest.raises(LegacyProviderAnalysisError) as error:
        provider.analyze_legacy(legacy_request, config, "")

    assert error.value.failure.code == "provider_unconfigured"
