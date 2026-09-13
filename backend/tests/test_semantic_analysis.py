import pytest
from pydantic import ValidationError

from app.semantic_analysis import StructuredVisualAnalysis, validate_analysis_for_media


def valid_image_result():
    return {
        "version": 1,
        "observedFacts": {
            "staticVisual": {
                "subject": "一位穿红色外套的人",
                "scene": "城市街道",
                "composition": "主体位于画面中央",
                "viewpoint": "平视中景",
                "lighting": "阴天柔光",
                "color": "低饱和冷色调",
                "visualStyle": "纪实摄影",
            },
            "temporal": None,
        },
        "generationSuggestions": {
            "subjectMotion": "缓慢向前行走",
            "environmentalMotion": "背景行人轻微移动",
            "cameraMotion": "稳定跟拍",
            "rhythm": "平缓",
            "suggestedDuration": 5.0,
            "audio": "轻微城市环境音",
        },
    }


def test_image_analysis_rejects_temporal_observations():
    payload = valid_image_result()
    payload["observedFacts"]["temporal"] = {
        "subjectMotion": "奔跑",
        "environmentalMotion": "树叶摇动",
        "cameraMotion": "向右平移",
        "rhythm": "快速",
    }

    with pytest.raises(ValidationError):
        validate_analysis_for_media(payload, "image")


def test_structured_analysis_rejects_unknown_nested_fields():
    payload = valid_image_result()
    payload["observedFacts"]["staticVisual"]["providerReasoning"] = "不应保存"

    with pytest.raises(ValidationError):
        StructuredVisualAnalysis.model_validate(payload)


def test_video_analysis_accepts_temporal_observations():
    payload = valid_image_result()
    payload["observedFacts"]["temporal"] = {
        "subjectMotion": "奔跑",
        "environmentalMotion": "树叶摇动",
        "cameraMotion": "向右平移",
        "rhythm": "快速",
    }

    analysis = validate_analysis_for_media(payload, "video")

    assert analysis.observedFacts.temporal.subjectMotion == "奔跑"


def test_analysis_rejects_unknown_media_type():
    with pytest.raises(ValueError, match="不支持的媒体类型"):
        validate_analysis_for_media(valid_image_result(), "audio")


def test_structured_visual_analysis_defaults_result_version_to_one():
    payload = valid_image_result()
    payload.pop("version")

    analysis = StructuredVisualAnalysis.model_validate(payload)

    assert analysis.version == 1
