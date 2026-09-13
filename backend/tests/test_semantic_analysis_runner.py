import importlib
import json

from app.analysis_input import ImageAnalysisInput
from app.analysis_providers.base import ProviderResult


class RecordingProvider:
    provider_id = "bailian"

    def __init__(self):
        self.requests = []

    def analyze(self, request):
        self.requests.append(request)
        return ProviderResult(rawText=json.dumps({
            "observedFacts": {
                "staticVisual": {
                    "subject": "人物", "scene": "室内", "composition": "中景",
                    "viewpoint": "平视", "lighting": "柔光", "color": "暖色",
                    "visualStyle": "写实",
                },
                "temporal": None,
            },
            "generationSuggestions": {
                "subjectMotion": "轻微动作", "environmentalMotion": "无",
                "cameraMotion": "固定", "rhythm": "平稳",
                "suggestedDuration": 3.0, "audio": "环境声",
            },
        }, ensure_ascii=False))


def test_runner_builds_task_one_input_and_validates_with_task_three(monkeypatch, tmp_path):
    runner = importlib.import_module("app.semantic_analysis_runner")
    source = ImageAnalysisInput(
        analysisProxyBytes=b"proxy", width=16, height=16, aspectRatio=1.0,
    )
    monkeypatch.setattr(runner, "build_analysis_input", lambda data_dir, project: source)
    provider = RecordingProvider()

    result = runner.run_semantic_analysis(
        data_dir=tmp_path,
        project=object(),
        provider=provider,
        model="qwen3.7-flash",
    )

    assert result.observedFacts.staticVisual.subject == "人物"
    assert provider.requests[0].analysisInput == source
    assert provider.requests[0].isRepair is False
