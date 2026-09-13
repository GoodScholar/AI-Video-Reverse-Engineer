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
from app.analysis_prompt import build_analysis_prompt
from app.analysis_provider import OpenAIProviderConfig, ProviderRequest as LegacyProviderRequest
from app.analysis_providers import ProviderAnalysisError as LegacyProviderAnalysisError
from app.analysis_providers.base import ProviderAnalysisError, ProviderRequest
from app.analysis_providers.openai import OpenAIAnalysisProvider


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
        model="gpt-5.6-luna",
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
        model="gpt-5.6-luna",
    )


def _responses(*texts, status="completed"):
    return {
        "status": status,
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": text} for text in texts],
        }],
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


def test_openai_sends_a_nonpersistent_responses_image_request_with_real_png_mime():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_responses("{}"))

    provider = OpenAIAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    provider.analyze(_image_request())

    assert str(captured[0].url) == "https://api.openai.com/v1/responses"
    assert captured[0].headers["authorization"] == "Bearer test-key"
    body = json.loads(captured[0].content)
    assert body["model"] == "gpt-5.6-luna"
    assert body["store"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    content = body["input"][0]["content"]
    assert {part["type"] for part in content} == {"input_text", "input_image"}
    image = next(part for part in content if part["type"] == "input_image")
    assert image["image_url"] == "data:image/png;base64," + base64.b64encode(
        b"\x89PNG\r\n\x1a\nimage-proxy",
    ).decode("ascii")


def test_openai_sends_a_video_contact_sheet_with_its_real_jpeg_mime_and_proxy_context():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_responses("{}"))

    provider = OpenAIAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    provider.analyze(_video_request())

    content = json.loads(captured[0].content)["input"][0]["content"]
    image = next(part for part in content if part["type"] == "input_image")
    text_parts = [part["text"] for part in content if part["type"] == "input_text"]
    assert image["image_url"].startswith("data:image/jpeg;base64,")
    assert any('"mediaType":"video"' in text for text in text_parts)
    assert any('"contactSheetFile":"contact-sheet.jpg"' in text for text in text_parts)


def test_openai_repair_request_sends_only_text_and_keeps_responses_structured_output():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_responses("{}"))

    provider = OpenAIAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    provider.analyze(_image_request().repair("只修复 JSON。"))

    body = json.loads(captured[0].content)
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["input"][0]["content"] == [{"type": "input_text", "text": "只修复 JSON。"}]


def test_openai_collects_only_output_text_from_all_message_output_items():
    response = {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": '{"a":'}]},
            {"type": "function_call", "name": "ignored"},
            {"type": "message", "content": [{"type": "output_text", "text": "1}"}]},
        ],
    }
    provider = OpenAIAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))),
        credential="test-key",
    )

    result = provider.analyze(_image_request())

    assert result.rawText == '{"a":1}'


@pytest.mark.parametrize(
    "response",
    [
        {"status": "incomplete", "output": []},
        {"status": "completed", "incomplete_details": {"reason": "max_output_tokens"}, "output": []},
        {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]},
        {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "   "}]}]},
    ],
)
def test_openai_rejects_incomplete_refused_or_empty_responses(response):
    provider = OpenAIAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))),
        credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_image_request())

    assert error.value.failure.code == "invalid_analysis_response"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"error": "key test-key"}), "authentication_failed"),
        (httpx.Response(429, json={"error": "limited"}), "rate_limited"),
        (httpx.Response(500, json={"error": "unavailable"}), "provider_error"),
        (httpx.TimeoutException("timeout"), "timeout"),
    ],
)
def test_openai_maps_transport_errors_without_exposing_provider_body(response, code):
    def handler(request):
        if isinstance(response, Exception):
            raise response
        return response

    provider = OpenAIAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_image_request())

    assert error.value.failure.code == code
    assert "test-key" not in str(error.value)


def test_openai_connection_probe_uses_the_fixed_png_and_the_normal_strict_validation_path():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_responses(json.dumps(_valid_analysis(), ensure_ascii=False)))

    provider = OpenAIAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    result = provider.test_connection()

    assert result.observedFacts.temporal is None
    assert "data:image/png;base64," in captured[0].content.decode("utf-8")


def test_openai_rejects_unlisted_model_before_sending_a_request():
    provider = OpenAIAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("不得联网"))),
        credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_image_request().model_copy(update={"model": "not-listed"}))

    assert error.value.failure.code == "unsupported_model_capability"


def test_openai_legacy_contract_is_available_only_through_an_explicit_entrypoint():
    provider = OpenAIAnalysisProvider(httpx.Client())
    legacy_request = LegacyProviderRequest(
        contactSheetBytes=b"\xff\xd8\xff\xe0legacy\xff\xd9",
        proxy={"durationSeconds": 1.0},
        responseSchema={"type": "object"},
    )
    config = OpenAIProviderConfig(providerId="openai", modelId="gpt-5.6-luna")

    with pytest.raises(LegacyProviderAnalysisError) as error:
        provider.analyze_legacy(legacy_request, config, "")

    assert error.value.failure.code == "provider_unconfigured"
