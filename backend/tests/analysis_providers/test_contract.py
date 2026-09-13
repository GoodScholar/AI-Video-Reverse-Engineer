import base64
import json

import httpx
import pytest
from pydantic import ValidationError

from app.analysis_input import ImageAnalysisInput, VideoAnalysisInput, VideoKeyframe, VideoMotion, VideoProxy, VideoScene, VideoSource
from app.analysis_prompt import build_analysis_prompt, repair_prompt, structured_analysis_schema
from app.analysis_providers.base import ProviderRequest
from app.analysis_providers.bailian import BailianAnalysisProvider
from app.analysis_providers.base import ProviderAnalysisError, ProviderResult
from app.analysis_providers.test_image import connection_test_request
from app.analysis_response import validate_or_repair


def test_bailian_sends_image_proxy_and_dimensions_without_project_metadata():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    proxy = b"\xff\xd8\xff\xe0analysis-proxy\xff\xd9"
    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        credential="test-credential",
    )
    request = ProviderRequest(
        analysisInput=ImageAnalysisInput(
            analysisProxyBytes=proxy,
            width=640,
            height=360,
            aspectRatio=640 / 360,
        ),
        prompt=build_analysis_prompt("image"),
        model="qwen3.7-flash",
    )

    provider.analyze(request)

    body = json.loads(captured[0].content)
    serialized = json.dumps(body)
    assert "project-name" not in serialized
    assert "original.png" not in serialized
    assert "/Users/" not in serialized
    assert "test-credential" not in serialized
    assert "data:image/jpeg;base64," + base64.b64encode(proxy).decode("ascii") in serialized
    assert "640" in serialized


def _valid_image_result():
    return {
        "observedFacts": {
            "staticVisual": {
                "subject": "人物",
                "scene": "街道",
                "composition": "居中",
                "viewpoint": "平视",
                "lighting": "柔光",
                "color": "冷色",
                "visualStyle": "纪实",
            },
            "temporal": None,
        },
        "generationSuggestions": {
            "subjectMotion": "缓慢移动",
            "environmentalMotion": "轻微变化",
            "cameraMotion": "稳定",
            "rhythm": "平缓",
            "suggestedDuration": 5.0,
            "audio": "环境音建议",
        },
    }


def test_invalid_response_is_repaired_once_without_resending_the_image_proxy():
    class RepairOnlyProvider:
        provider_id = "bailian"

        def __init__(self):
            self.requests = []

        def analyze(self, request):
            self.requests.append(request)
            return ProviderResult(rawText=json.dumps(_valid_image_result(), ensure_ascii=False))

    provider = RepairOnlyProvider()
    request = ProviderRequest(
        analysisInput=ImageAnalysisInput(
            analysisProxyBytes=b"\xff\xd8\xff\xe0analysis-proxy\xff\xd9",
            width=640,
            height=360,
            aspectRatio=640 / 360,
        ),
        prompt=build_analysis_prompt("image"),
        model="qwen3.7-flash",
    )

    analysis = validate_or_repair(provider, ProviderResult(rawText="{bad json"), request)

    assert analysis.observedFacts.temporal is None
    assert len(provider.requests) == 1
    assert provider.requests[0].isRepair is True
    assert provider.requests[0].analysisInput is None
    assert "data:image" not in provider.requests[0].prompt


def test_response_that_is_still_invalid_after_one_repair_has_a_stable_error():
    class StillInvalidProvider:
        provider_id = "bailian"

        def analyze(self, request):
            return ProviderResult(rawText="{}")

    request = ProviderRequest(
        analysisInput=ImageAnalysisInput(
            analysisProxyBytes=b"\xff\xd8\xff\xe0analysis-proxy\xff\xd9",
            width=16,
            height=16,
            aspectRatio=1.0,
        ),
        prompt=build_analysis_prompt("image"),
        model="qwen3.7-flash",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        validate_or_repair(StillInvalidProvider(), ProviderResult(rawText="{}"), request)

    assert error.value.failure.code == "invalid_analysis_response"


def test_image_initial_and_repair_prompts_include_the_same_media_limited_schema():
    schema = structured_analysis_schema("image")
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))

    initial = build_analysis_prompt("image")
    repair = repair_prompt("{}", "[]", "image")

    assert schema_text in initial
    assert schema_text in repair
    observed = schema["$defs"]["ObservedFacts"]["properties"]
    assert observed["temporal"] == {"type": "null"}
    assert '"title"' not in schema_text
    assert '"description"' not in schema_text


def test_provider_request_enforces_distinct_initial_and_repair_shapes():
    image = ImageAnalysisInput(
        analysisProxyBytes=b"\xff\xd8\xff\xe0analysis-proxy\xff\xd9",
        width=16,
        height=16,
        aspectRatio=1.0,
    )
    with pytest.raises(ValidationError):
        ProviderRequest(prompt="initial", model="model")
    with pytest.raises(ValidationError):
        ProviderRequest(analysisInput=image, prompt="repair", model="model", isRepair=True)

    initial = ProviderRequest(analysisInput=image, prompt="initial", model="model")
    repaired = initial.repair("repair")

    with pytest.raises(ValueError, match="不能再次修复"):
        repaired.repair("another")


def test_connection_test_has_no_project_data():
    request = connection_test_request("gpt-5.6-luna")

    assert request.analysisInput.mediaType == "image"
    assert (request.analysisInput.width, request.analysisInput.height) == (16, 16)
    assert request.analysisInput.aspectRatio == 1.0
    assert request.model == "gpt-5.6-luna"
    assert request.isRepair is False


def test_validate_or_repair_rejects_a_repair_request_as_an_initial_request():
    initial = ProviderRequest(
        analysisInput=ImageAnalysisInput(
            analysisProxyBytes=b"\xff\xd8\xff\xe0analysis-proxy\xff\xd9",
            width=16,
            height=16,
            aspectRatio=1.0,
        ),
        prompt="initial",
        model="model",
    )

    with pytest.raises(ValueError, match="修复请求不能作为初始请求"):
        validate_or_repair(object(), ProviderResult(rawText="{}"), initial.repair("repair"))


def test_repair_redacts_untrusted_candidate_and_keeps_error_locations_schema_safe():
    class RecordingProvider:
        provider_id = "bailian"

        def __init__(self):
            self.requests = []

        def analyze(self, request):
            self.requests.append(request)
            return ProviderResult(rawText=json.dumps(_valid_image_result(), ensure_ascii=False))

    provider = RecordingProvider()
    request = ProviderRequest(
        analysisInput=ImageAnalysisInput(
            analysisProxyBytes=b"\xff\xd8\xff\xe0analysis-proxy\xff\xd9",
            width=16,
            height=16,
            aspectRatio=1.0,
        ),
        prompt="initial",
        model="model",
    )
    raw = json.dumps({
        "privateField": "Bearer sentinel-bearer data:image/jpeg;base64,SENTINELDATA /Users/sentinel/private.png sk-sentinel-key",
        "anotherPrivateField": "C:\\Users\\sentinel\\private.png",
        **{"unknown%d" % index: "value" for index in range(10)},
    })

    validate_or_repair(provider, ProviderResult(rawText=raw), request)

    repair = provider.requests[0].prompt
    for forbidden in ("sentinel-bearer", "SENTINELDATA", "/Users/sentinel", "sk-sentinel-key", "C:\\Users\\sentinel"):
        assert forbidden not in repair
    assert "<unknown>" in repair
    assert repair.count('"location"') <= 8
    error_summary = repair.split("校验错误摘要：\n", 1)[1].split("\n必须符合", 1)[0]
    assert "privateField" not in error_summary


def test_oversized_or_overdeep_response_is_rejected_without_a_repair_request():
    class MustNotBeCalled:
        provider_id = "bailian"

        def analyze(self, request):
            pytest.fail("超过边界的响应不得再次外发")

    request = ProviderRequest(
        analysisInput=ImageAnalysisInput(
            analysisProxyBytes=b"\xff\xd8\xff\xe0analysis-proxy\xff\xd9",
            width=16,
            height=16,
            aspectRatio=1.0,
        ),
        prompt="initial",
        model="model",
    )
    values = ["{" + "x" * 1_000_000 + "}", "[" * 1_000 + "0" + "]" * 1_000]

    for raw in values:
        with pytest.raises(ProviderAnalysisError) as error:
            validate_or_repair(MustNotBeCalled(), ProviderResult(rawText=raw), request)
        assert error.value.failure.code == "invalid_analysis_response"


def test_bailian_sends_only_contact_sheet_and_proxy_for_video_input():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    contact_sheet = b"\xff\xd8\xff\xe0contact-sheet\xff\xd9"
    request = ProviderRequest(
        analysisInput=VideoAnalysisInput(
            contactSheetBytes=contact_sheet,
            analysisProxy=VideoProxy(
                schemaVersion=1,
                source=VideoSource(durationSeconds=2.0, width=640, height=360, frameRate=24.0),
                keyframes=[VideoKeyframe(index=1, timeSeconds=0.0)],
                scene=VideoScene(changeCount=0),
                motion=VideoMotion(samples=[], p50=None, p90=None, peak=None, level="unavailable"),
                contactSheetFile="contact-sheet.jpg",
            ),
        ),
        prompt=build_analysis_prompt("video"),
        model="qwen3.7-flash",
    )
    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-credential"
    )

    provider.analyze(request)

    body = captured[0].content.decode("utf-8")
    payload = json.loads(body)
    content = payload["messages"][0]["content"]
    context = next(item["text"] for item in content if item["type"] == "text" and '"mediaType":"video"' in item["text"])
    assert "data:image/jpeg;base64," + base64.b64encode(contact_sheet).decode("ascii") in body
    assert '"mediaType":"video"' in context
    assert '"contactSheetFile":"contact-sheet.jpg"' in context
    for forbidden in ("analysis-proxy.jpg", "original.mp4", "/Users/", "test-credential"):
        assert forbidden not in body


def _repair_request():
    return ProviderRequest(
        analysisInput=ImageAnalysisInput(
            analysisProxyBytes=b"\xff\xd8\xff\xe0analysis-proxy\xff\xd9",
            width=16,
            height=16,
            aspectRatio=1.0,
        ),
        prompt="initial",
        model="model",
    )


def _candidate_from_repair_prompt(prompt):
    return prompt.split("候选 JSON：\n", 1)[1].split("\n校验错误摘要：", 1)[0]


def test_repair_decodes_and_recursively_sanitizes_escaped_json_keys_and_values():
    class RecordingProvider:
        provider_id = "bailian"

        def __init__(self):
            self.requests = []

        def analyze(self, request):
            self.requests.append(request)
            return ProviderResult(rawText=json.dumps(_valid_image_result(), ensure_ascii=False))

    provider = RecordingProvider()
    raw = (
        '{"\\u0042earer escaped-key":"\\u0042earer escaped-value",'
        '"image":"data\\u003aimage/jpeg;base64,ESCAPED_IMAGE",'
        '"path":' + json.dumps(r"C:\\Users\\sentinel\\private.png") + "}"
    )

    validate_or_repair(provider, ProviderResult(rawText=raw), _repair_request())

    candidate = _candidate_from_repair_prompt(provider.requests[0].prompt)
    decoded = json.loads(candidate)
    serialized = json.dumps(decoded, ensure_ascii=False)
    for forbidden in ("escaped-key", "escaped-value", "ESCAPED_IMAGE", r"C:\Users\sentinel"):
        assert forbidden not in serialized
    assert "<redacted>" in serialized
    assert "<redacted-image>" in serialized


def test_unparseable_candidate_uses_a_fixed_safe_json_placeholder():
    class RecordingProvider:
        provider_id = "bailian"

        def __init__(self):
            self.requests = []

        def analyze(self, request):
            self.requests.append(request)
            return ProviderResult(rawText=json.dumps(_valid_image_result(), ensure_ascii=False))

    provider = RecordingProvider()
    raw = '{"secret":"\\u0042earer escaped-value data\\u003aimage/jpeg;base64,ESCAPED_IMAGE C:\\\\Users\\\\sentinel"'

    validate_or_repair(provider, ProviderResult(rawText=raw), _repair_request())

    candidate = _candidate_from_repair_prompt(provider.requests[0].prompt)
    assert json.loads(candidate) == {"candidate": "<unavailable>"}
    for forbidden in ("escaped-value", "ESCAPED_IMAGE", r"C:\Users\sentinel"):
        assert forbidden not in candidate


def _deep_candidate_json():
    value = "x"
    for index in range(40):
        value = {"field%d" % index: value}
    return json.dumps(value)


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"field": "x" * 5_000}),
        _deep_candidate_json(),
        json.dumps({"field%d" % index: "x" for index in range(100)}),
        json.dumps({"field": "x" * 20_000}),
    ],
)
def test_repair_candidate_resource_limits_use_the_same_safe_placeholder(raw):
    class RecordingProvider:
        provider_id = "bailian"

        def __init__(self):
            self.requests = []

        def analyze(self, request):
            self.requests.append(request)
            return ProviderResult(rawText=json.dumps(_valid_image_result(), ensure_ascii=False))

    provider = RecordingProvider()

    validate_or_repair(provider, ProviderResult(rawText=raw), _repair_request())

    candidate = _candidate_from_repair_prompt(provider.requests[0].prompt)
    assert json.loads(candidate) == {"candidate": "<unavailable>"}
