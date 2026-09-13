import base64
import json

import httpx
import pytest

from app.analysis_input import ImageAnalysisInput
from app.analysis_prompt import build_analysis_prompt
from app.analysis_providers.base import ProviderRequest
from app.analysis_providers.bailian import BailianAnalysisProvider
from app.analysis_providers.base import ProviderAnalysisError, ProviderResult
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
