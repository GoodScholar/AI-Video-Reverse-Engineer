import base64
import json

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
from app.analysis_prompt import build_analysis_prompt, structured_analysis_schema
from app.analysis_providers.base import ProviderAnalysisError, ProviderRequest
from app.analysis_providers.claude import ClaudeAnalysisProvider


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
        model="claude-sonnet-5",
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
        model="claude-sonnet-5",
    )


def _message(*blocks, stop_reason="end_turn", request_id="msg_test"):
    return {
        "id": request_id,
        "type": "message",
        "role": "assistant",
        "stop_reason": stop_reason,
        "content": list(blocks),
    }


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


def _assert_claude_schema(value):
    if isinstance(value, dict):
        assert "minimum" not in value
        assert "maximum" not in value
        assert "minLength" not in value
        assert "maxLength" not in value
        if isinstance(value.get("properties"), dict):
            assert value["additionalProperties"] is False
        for nested in value.values():
            _assert_claude_schema(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_claude_schema(nested)


def test_claude_sends_messages_image_request_with_official_headers_real_png_and_strict_wire_schema():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_message({"type": "text", "text": "{}"}))

    provider = ClaudeAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )
    provider.analyze(_image_request())

    request = captured[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "test-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["model"] == "claude-sonnet-5"
    assert body["max_tokens"] == 4096
    assert body["output_config"]["format"]["type"] == "json_schema"
    content = body["messages"][0]["content"]
    image = next(part for part in content if part["type"] == "image")
    assert image["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.b64encode(b"\x89PNG\r\n\x1a\nimage-proxy").decode("ascii"),
    }
    text_parts = [part["text"] for part in content if part["type"] == "text"]
    assert build_analysis_prompt("image") in text_parts
    assert '{"mediaType":"image","width":32,"height":24,"aspectRatio":1.3333333333333333}' in text_parts
    _assert_claude_schema(body["output_config"]["format"]["schema"])
    assert "minimum" in json.dumps(structured_analysis_schema("image"))


def test_claude_sends_video_contact_sheet_with_real_jpeg_mime_and_context():
    captured = []
    provider = ClaudeAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: (
            captured.append(request) or httpx.Response(200, json=_message({"type": "text", "text": "{}"}))
        ))),
        credential="test-key",
    )

    provider.analyze(_video_request())

    content = json.loads(captured[0].content)["messages"][0]["content"]
    image = next(part for part in content if part["type"] == "image")
    assert image["source"]["media_type"] == "image/jpeg"
    assert any('"mediaType":"video"' in part["text"] for part in content if part["type"] == "text")
    assert any('"contactSheetFile":"contact-sheet.jpg"' in part["text"] for part in content if part["type"] == "text")


def test_claude_repair_request_sends_only_its_complete_text_prompt():
    captured = []
    provider = ClaudeAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: (
            captured.append(request) or httpx.Response(200, json=_message({"type": "text", "text": "{}"}))
        ))),
        credential="test-key",
    )

    provider.analyze(_image_request().repair("只修复 JSON。"))

    body = json.loads(captured[0].content)
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "只修复 JSON。"}]}]
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_claude_collects_all_text_blocks_and_ignores_thinking_and_signatures():
    body = _message(
        {"type": "thinking", "thinking": "不要泄露", "signature": "sig"},
        {"type": "text", "text": '{"a":'},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "1}"},
    )
    provider = ClaudeAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body, headers={"x-request-id": "req_test"}),
        )),
        credential="test-key",
    )

    result = provider.analyze(_image_request())

    assert result.rawText == '{"a":1}'
    assert result.providerRequestId == "req_test"


@pytest.mark.parametrize(
    "response",
    [
        _message({"type": "text", "text": "{}"}, stop_reason="max_tokens"),
        _message({"type": "text", "text": "{}"}, stop_reason="stop_sequence"),
        _message({"type": "text", "text": "{}"}, stop_reason=None),
        {**_message({"type": "text", "text": "{}"}), "stop_details": {"type": "refusal"}},
        _message({"type": "text", "text": "   "}),
        _message({"type": "thinking", "thinking": "reason", "signature": "sig"}),
        _message({"type": "text"}),
        {"type": "message", "role": "assistant", "stop_reason": "end_turn", "content": ["bad"]},
        {"type": "error", "error": {"type": "invalid_request_error"}},
    ],
)
def test_claude_rejects_refused_truncated_empty_or_malformed_messages(response):
    provider = ClaudeAnalysisProvider(
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
        (httpx.Response(500, json={"error": {"message": "unavailable"}}), "provider_error"),
        (httpx.ConnectError("secret test-key"), "network_error"),
        (httpx.TimeoutException("secret test-key"), "timeout"),
    ],
)
def test_claude_maps_transport_and_status_errors_without_exposing_body(response, code):
    def handler(request):
        if isinstance(response, Exception):
            raise response
        return response

    provider = ClaudeAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_image_request())

    assert error.value.failure.code == code
    assert "test-key" not in str(error.value)


def test_claude_connection_probe_uses_fixed_png_and_normal_validation_with_one_text_only_repair():
    captured = []
    responses = iter([
        _message({"type": "text", "text": "{}"}),
        _message({"type": "text", "text": json.dumps(_valid_analysis(), ensure_ascii=False)}),
    ])

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=next(responses))

    provider = ClaudeAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    result = provider.test_connection()

    assert result.observedFacts.temporal is None
    first_content = json.loads(captured[0].content)["messages"][0]["content"]
    second_content = json.loads(captured[1].content)["messages"][0]["content"]
    assert next(part for part in first_content if part["type"] == "image")["source"]["media_type"] == "image/png"
    assert second_content[0]["type"] == "text"
    assert len(second_content) == 1


def test_claude_rejects_unlisted_model_before_sending_a_request():
    provider = ClaudeAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("不得联网"))),
        credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_image_request().model_copy(update={"model": "not-listed"}))

    assert error.value.failure.code == "unsupported_model_capability"
