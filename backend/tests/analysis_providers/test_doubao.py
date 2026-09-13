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
from app.analysis_provider import DoubaoProviderConfig, ProviderRequest as LegacyProviderRequest
from app.analysis_providers import ProviderAnalysisError as LegacyProviderAnalysisError
from app.analysis_providers.base import ProviderAnalysisError, ProviderRequest
from app.analysis_providers.doubao import DoubaoAnalysisProvider


MODEL = "doubao-seed-2-0-lite-260428"
URL = "https://ark.cn-beijing.volces.com/api/v3/responses"


def _image_request():
    image = ImageAnalysisInput(
        analysisProxyBytes=b"\x89PNG\r\n\x1a\nimage-proxy",
        width=32,
        height=24,
        aspectRatio=32 / 24,
    )
    return ProviderRequest(analysisInput=image, prompt=build_analysis_prompt(image), model=MODEL)


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
    return ProviderRequest(analysisInput=video, prompt=build_analysis_prompt(video), model=MODEL)


def _responses(*texts):
    return {
        "status": "completed",
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


def _assert_all_object_properties_are_required(schema):
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            assert set(schema.get("required", [])) == set(properties)
            for value in properties.values():
                _assert_all_object_properties_are_required(value)
        for key, value in schema.items():
            if key != "properties":
                _assert_all_object_properties_are_required(value)
    elif isinstance(schema, list):
        for value in schema:
            _assert_all_object_properties_are_required(value)


def test_doubao_sends_a_fixed_nonpersistent_responses_image_request_with_real_png_mime():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_responses("{}"))

    provider = DoubaoAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    provider.analyze(_image_request())

    assert str(captured[0].url) == URL
    assert captured[0].headers["authorization"] == "Bearer test-key"
    body = json.loads(captured[0].content)
    assert body["model"] == MODEL
    assert body["store"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    _assert_all_object_properties_are_required(body["text"]["format"]["schema"])
    content = body["input"][0]["content"]
    image = next(part for part in content if part["type"] == "input_image")
    assert image["image_url"] == "data:image/png;base64," + base64.b64encode(
        b"\x89PNG\r\n\x1a\nimage-proxy",
    ).decode("ascii")


def test_doubao_sends_video_contact_sheet_with_real_jpeg_mime_and_safe_proxy_context():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_responses("{}"))

    provider = DoubaoAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    provider.analyze(_video_request())

    content = json.loads(captured[0].content)["input"][0]["content"]
    image = next(part for part in content if part["type"] == "input_image")
    text = [part["text"] for part in content if part["type"] == "input_text"]
    assert image["image_url"].startswith("data:image/jpeg;base64,")
    assert any('"mediaType":"video"' in value for value in text)
    assert any('"contactSheetFile":"contact-sheet.jpg"' in value for value in text)


def test_doubao_repair_request_sends_only_text_and_keeps_strict_json_schema():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_responses("{}"))

    provider = DoubaoAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    provider.analyze(_image_request().repair("只修复 JSON。"))

    body = json.loads(captured[0].content)
    assert body["text"]["format"]["strict"] is True
    assert body["input"][0]["content"] == [{"type": "input_text", "text": "只修复 JSON。"}]


def test_doubao_collects_output_text_from_every_message_output_item():
    response = {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": '{"a":'}]},
            {"type": "function_call", "name": "ignored"},
            {"type": "message", "content": [{"type": "output_text", "text": "1}"}]},
        ],
    }
    provider = DoubaoAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))),
        credential="test-key",
    )

    assert provider.analyze(_image_request()).rawText == '{"a":1}'


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"error": "key test-key"}), "authentication_failed"),
        (httpx.Response(429, json={"error": "limited"}), "rate_limited"),
        (httpx.Response(500, json={"error": "unavailable"}), "provider_error"),
        (httpx.TimeoutException("timeout"), "timeout"),
    ],
)
def test_doubao_maps_errors_without_exposing_provider_bodies(response, code):
    def handler(request):
        if isinstance(response, Exception):
            raise response
        return response

    provider = DoubaoAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_image_request())

    assert error.value.failure.code == code
    assert "test-key" not in str(error.value)


def test_doubao_connection_probe_uses_fixed_png_and_the_normal_strict_validation_path():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_responses(json.dumps(_valid_analysis(), ensure_ascii=False)))

    provider = DoubaoAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-key",
    )

    result = provider.test_connection()

    assert result.observedFacts.temporal is None
    assert "data:image/png;base64," in captured[0].content.decode("utf-8")


def test_doubao_core_credential_is_keyword_only_while_legacy_endpoint_bindings_stay_positional():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_responses("{}"))

    provider = DoubaoAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        {"legacy-endpoint": "legacy-model"},
        credential="core-key",
    )

    provider.analyze(_image_request())

    assert captured[0].headers["authorization"] == "Bearer core-key"
    with pytest.raises(TypeError):
        DoubaoAnalysisProvider(httpx.Client(), {"legacy-endpoint": "legacy-model"}, "core-key")


def test_doubao_legacy_contract_remains_available_only_through_its_explicit_entrypoint():
    provider = DoubaoAnalysisProvider(httpx.Client(), {"legacy-endpoint": "legacy-model"})
    request = LegacyProviderRequest(
        contactSheetBytes=b"\xff\xd8\xff\xe0legacy\xff\xd9",
        proxy={"durationSeconds": 1.0},
        responseSchema={"type": "object"},
    )
    config = DoubaoProviderConfig(
        providerId="doubao", modelId="legacy-model", endpointId="legacy-endpoint",
    )

    with pytest.raises(LegacyProviderAnalysisError) as error:
        provider.analyze_legacy(request, config, "")

    assert error.value.failure.code == "provider_unconfigured"
