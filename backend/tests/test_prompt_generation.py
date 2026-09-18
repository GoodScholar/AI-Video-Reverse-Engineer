import json

import pytest
from pydantic import ValidationError

from app.analysis_providers.base import ProviderAnalysisError, ProviderRequest, ProviderResult
from app.prompt_generation import PromptTexts, generate_prompts
from app.semantic_analysis import StructuredVisualAnalysis


def _analysis(subject: str = "雨中的人物") -> StructuredVisualAnalysis:
    return StructuredVisualAnalysis.model_validate({
        "observedFacts": {
            "staticVisual": {
                "subject": subject,
                "scene": "夜晚街道",
                "composition": "居中",
                "viewpoint": "平视",
                "lighting": "霓虹侧光",
                "color": "蓝紫色",
                "visualStyle": "电影感",
            },
            "temporal": None,
        },
        "generationSuggestions": {
            "subjectMotion": "缓慢前行",
            "environmentalMotion": "雨滴飘落",
            "cameraMotion": "轻微推进",
            "rhythm": "平缓",
            "suggestedDuration": 5.0,
            "audio": "雨声建议",
        },
    })


def _texts() -> dict[str, str]:
    return {
        "positiveZh": "雨夜街道，一位人物缓慢前行，电影感，霓虹侧光。",
        "negativeZh": "低清晰度，文字，水印，畸形肢体。",
        "positiveEn": "A person walking slowly through a rainy neon street at night, cinematic lighting.",
        "negativeEn": "low resolution, text, watermark, malformed limbs.",
    }


def test_generate_prompts_uses_only_redacted_structured_analysis_and_the_prompt_text_schema():
    class RecordingProvider:
        provider_id = "recording"

        def __init__(self):
            self.requests = []

        def analyze(self, request):
            self.requests.append(request)
            return ProviderResult(rawText=json.dumps(_texts(), ensure_ascii=False))

    provider = RecordingProvider()
    result = generate_prompts(
        _analysis("人物 /Users/shen/private.mp4，Bearer secret-token"), provider, "test-model",
    )

    assert result == PromptTexts.model_validate(_texts())
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.task == "prompt_generation"
    assert request.analysisInput is None
    assert request.isRepair is False
    assert request.responseSchema == PromptTexts.model_json_schema()
    assert "/Users/shen/private.mp4" not in request.prompt
    assert "secret-token" not in request.prompt
    assert "data:image" not in request.prompt


def test_generate_prompts_rejects_invalid_json_without_a_repair_request():
    class InvalidResponseProvider:
        provider_id = "recording"

        def __init__(self):
            self.calls = 0

        def analyze(self, request):
            self.calls += 1
            return ProviderResult(rawText="not json")

    provider = InvalidResponseProvider()

    with pytest.raises(ProviderAnalysisError) as error:
        generate_prompts(_analysis(), provider, "test-model")

    assert error.value.failure.code == "invalid_analysis_response"
    assert provider.calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        {**_texts(), "positiveZh": "   "},
        {**_texts(), "negativeEn": "x" * 12001},
    ],
)
def test_prompt_texts_rejects_blank_or_unbounded_fields(payload):
    with pytest.raises(ValidationError):
        PromptTexts.model_validate(payload)


def test_prompt_generation_request_cannot_be_media_or_repair_request():
    with pytest.raises(ValidationError):
        ProviderRequest(
            task="prompt_generation", prompt="提示词", model="test", isRepair=True,
            responseSchema=PromptTexts.model_json_schema(),
        )
