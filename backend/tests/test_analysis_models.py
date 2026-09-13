import pytest
from pydantic import ValidationError

from datetime import datetime, timezone

from app.analysis_models import (
    ReproducibilityCheck,
    SemanticAnalysisError,
    SemanticAnalysisTask,
    StructuredAnalysis,
    merge_reproducibility,
    new_semantic_analysis,
)
from app.local_preprocessing import ReproducibilityAssessment


def valid_analysis_payload():
    def entry(entry_id):
        return {
            "id": entry_id,
            "kind": "observation",
            "summary": "主体从画面左侧向右侧移动。",
            "timeRange": {"startSeconds": 0.0, "endSeconds": 1.0},
            "confidence": "high",
            "confidenceReason": "联系表中的主体位置连续变化。",
        }

    return {
        "version": 1,
        "status": "in_scope",
        "referenceVideoDurationSeconds": 2.5,
        "basicFacts": [entry("fact-001")],
        "suitability": [
            {
                "id": "check-single-shot",
                "kind": "observation",
                "summary": "未检测到镜头切换。",
                "timeRange": {"startSeconds": 0.0, "endSeconds": 2.5},
                "confidence": "high",
                "confidenceReason": "有效镜头切换点 0 个。",
                "criterion": "single_shot",
                "status": "passed",
                "evidence": "有效镜头切换点 0 个。",
            },
            {
                "id": "check-motion-range",
                "kind": "observation",
                "summary": "运动强度处于轻中度范围。",
                "timeRange": {"startSeconds": 0.0, "endSeconds": 2.5},
                "confidence": "high",
                "confidenceReason": "运动强度 P90 为 2.000。",
                "criterion": "motion_range",
                "status": "passed",
                "evidence": "运动强度 P90 为 2.000。",
            },
            {
                "id": "check-primary-subject-count",
                "kind": "observation",
                "summary": "只有一个主要主体。",
                "timeRange": {"startSeconds": 0.0, "endSeconds": 2.5},
                "confidence": "medium",
                "confidenceReason": "联系表中持续可见同一主体。",
                "criterion": "primary_subject_count",
                "status": "passed",
                "evidence": "联系表中持续可见同一主体。",
            },
            {
                "id": "check-complex-interaction",
                "kind": "observation",
                "summary": "未观察到复杂交互。",
                "timeRange": {"startSeconds": 0.0, "endSeconds": 2.5},
                "confidence": "medium",
                "confidenceReason": "主体未与其他主体发生复杂交互。",
                "criterion": "complex_interaction",
                "status": "passed",
                "evidence": "主体未与其他主体发生复杂交互。",
            },
        ],
        "subject": [entry("subject-001")],
        "scene": [entry("scene-001")],
        "action": [entry("action-001")],
        "camera": [entry("camera-001")],
        "lighting": [entry("lighting-001")],
    }


def test_structured_analysis_requires_all_sections_and_time_ranges():
    analysis = StructuredAnalysis.model_validate(valid_analysis_payload())

    assert analysis.version == 1
    assert [entry.kind for entry in analysis.subject] == ["observation"]
    assert analysis.action[0].timeRange.startSeconds < analysis.action[0].timeRange.endSeconds


@pytest.mark.parametrize("field", ["id", "kind", "timeRange", "confidence", "confidenceReason"])
def test_suitability_checks_require_common_analysis_entry_fields(field):
    payload = valid_analysis_payload()
    payload["suitability"][0].pop(field, None)

    with pytest.raises(ValidationError):
        StructuredAnalysis.model_validate(payload)


def test_local_failure_cannot_be_overridden_by_provider():
    local = ReproducibilityAssessment.model_validate({
        "status": "out_of_scope",
        "checks": [
            {
                "criterion": "single_shot",
                "status": "failed",
                "message": "检测到多个镜头。",
                "evidence": "有效镜头切换点 1 个。",
            },
            {
                "criterion": "motion_range",
                "status": "passed",
                "message": "运动强度处于轻中度范围。",
                "evidence": "运动强度 P90 为 2.000。",
            },
            {
                "criterion": "primary_subject_count",
                "status": "pending",
                "message": "主要主体数量待语义分析确认。",
                "evidence": "本地预处理不执行主体识别。",
            },
            {
                "criterion": "complex_interaction",
                "status": "pending",
                "message": "复杂交互待语义分析确认。",
                "evidence": "本地预处理不执行交互识别。",
            },
        ],
    })
    provider = [
        ReproducibilityCheck.model_validate({
            "id": f"provider-{criterion}",
            "kind": "observation",
            "summary": "供应商判断。",
            "timeRange": {"startSeconds": 0.0, "endSeconds": 2.5},
            "confidence": "medium",
            "confidenceReason": "供应商分析结果。",
            "criterion": criterion,
            "status": "passed",
            "evidence": "供应商分析结果。",
        })
        for criterion in (
            "primary_subject_count", "complex_interaction",
        )
    ]

    merged = merge_reproducibility(local, provider, 2.5)

    assert merged.status == "out_of_scope"
    assert merged.checks[0].criterion == "single_shot"
    assert merged.checks[0].status == "failed"


@pytest.mark.parametrize(
    "section",
    ["basicFacts", "suitability", "subject", "scene", "action", "camera", "lighting"],
)
def test_structured_analysis_rejects_empty_required_sections(section):
    payload = valid_analysis_payload()
    payload[section] = []

    with pytest.raises(ValidationError):
        StructuredAnalysis.model_validate(payload)


@pytest.mark.parametrize(
    "time_range",
    [
        {"startSeconds": -0.1, "endSeconds": 1.0},
        {"startSeconds": 1.0, "endSeconds": 1.0},
        {"startSeconds": 0.0, "endSeconds": 2.6},
    ],
)
def test_structured_analysis_rejects_invalid_or_out_of_range_time_ranges(time_range):
    payload = valid_analysis_payload()
    payload["action"][0]["timeRange"] = time_range

    with pytest.raises(ValidationError):
        StructuredAnalysis.model_validate(payload)


def test_structured_analysis_rejects_invalid_confidence_values():
    payload = valid_analysis_payload()
    payload["action"][0]["confidence"] = "certain"

    with pytest.raises(ValidationError):
        StructuredAnalysis.model_validate(payload)


def test_structured_analysis_rejects_unsupported_aggregate_similarity_claims():
    payload = valid_analysis_payload()
    payload["aggregateSimilarity"] = 0.91

    with pytest.raises(ValidationError):
        StructuredAnalysis.model_validate(payload)


@pytest.mark.parametrize("criterion", ["primary_subject_count", "complex_interaction"])
def test_structured_analysis_requires_all_semantic_reproducibility_checks(criterion):
    payload = valid_analysis_payload()
    payload["suitability"] = [
        check for check in payload["suitability"] if check["criterion"] != criterion
    ]

    with pytest.raises(ValidationError):
        StructuredAnalysis.model_validate(payload)


def test_new_semantic_analysis_records_sources_selection_and_stable_error_codes():
    task = new_semantic_analysis(
        analysis_id="analysis-001",
        provider_id="openai",
        model_id="verified-model-001",
        source_reference_video_id="video-001",
        source_preprocessing_id="prep-001",
        now=datetime(2026, 9, 12, tzinfo=timezone.utc),
    )

    assert task.model_dump() == {
        "id": "analysis-001",
        "providerId": "openai",
        "modelId": "verified-model-001",
        "sourceReferenceVideoId": "video-001",
        "sourcePreprocessingId": "prep-001",
        "status": "queued",
        "queuedAt": "2026-09-12T00:00:00+00:00",
        "startedAt": None,
        "updatedAt": "2026-09-12T00:00:00+00:00",
        "completedAt": None,
        "result": None,
        "error": None,
    }
    assert SemanticAnalysisError.model_validate({
        "code": "invalid_response",
        "message": "分析服务返回的数据不符合结构化契约。",
        "retryable": True,
    }).code == "invalid_response"

    with pytest.raises(ValidationError):
        SemanticAnalysisError.model_validate({
            "code": "unknown_failure", "message": "未知错误。", "retryable": False,
        })


def test_suitability_time_ranges_must_fit_the_reference_video():
    payload = valid_analysis_payload()
    payload["suitability"][0]["timeRange"] = {"startSeconds": 0.0, "endSeconds": 2.6}

    with pytest.raises(ValidationError):
        StructuredAnalysis.model_validate(payload)


@pytest.mark.parametrize(
    ("analysis_status", "criterion", "check_status"),
    [
        ("in_scope", "single_shot", "failed"),
        ("out_of_scope", "complex_interaction", "passed"),
        ("in_scope", "complex_interaction", "pending"),
    ],
)
def test_structured_analysis_rejects_status_that_disagrees_with_checks(
    analysis_status, criterion, check_status,
):
    payload = valid_analysis_payload()
    payload["status"] = analysis_status
    for check in payload["suitability"]:
        if check["criterion"] == criterion:
            check["status"] = check_status

    with pytest.raises(ValidationError):
        StructuredAnalysis.model_validate(payload)


@pytest.mark.parametrize(
    ("section", "entry_id"),
    [("action", "fact-001"), ("basicFacts", "   ")],
)
def test_structured_analysis_rejects_duplicate_or_blank_entry_ids(section, entry_id):
    payload = valid_analysis_payload()
    payload[section][0]["id"] = entry_id

    with pytest.raises(ValidationError):
        StructuredAnalysis.model_validate(payload)


def valid_task_payload():
    return {
        "id": "analysis-001",
        "providerId": "openai",
        "modelId": "verified-model-001",
        "sourceReferenceVideoId": "video-001",
        "sourcePreprocessingId": "prep-001",
        "status": "queued",
        "queuedAt": "2026-09-12T00:00:00+00:00",
        "startedAt": None,
        "updatedAt": "2026-09-12T00:00:00+00:00",
        "completedAt": None,
        "result": None,
        "error": None,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [("id", "   "), ("sourceReferenceVideoId", "../video-001"), ("sourcePreprocessingId", "..")],
)
def test_semantic_analysis_task_rejects_unsafe_task_and_source_ids(field, value):
    payload = valid_task_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        SemanticAnalysisTask.model_validate(payload)


@pytest.mark.parametrize("field", ["queuedAt", "startedAt", "updatedAt", "completedAt"])
def test_semantic_analysis_task_rejects_timestamps_without_timezones(field):
    payload = valid_task_payload()
    payload[field] = "2026-09-12T00:00:00"

    with pytest.raises(ValidationError):
        SemanticAnalysisTask.model_validate(payload)


def test_new_semantic_analysis_rejects_naive_datetime():
    with pytest.raises(ValueError):
        new_semantic_analysis(
            analysis_id="analysis-001",
            provider_id="openai",
            model_id="verified-model-001",
            source_reference_video_id="video-001",
            source_preprocessing_id="prep-001",
            now=datetime(2026, 9, 12),
        )


def test_valid_analysis_payload_uses_independent_entries_for_each_section():
    payload = valid_analysis_payload()
    payload["action"][0]["confidence"] = "low"

    assert payload["subject"][0]["confidence"] == "high"


def test_merge_reproducibility_rejects_provider_local_conclusions():
    local = ReproducibilityAssessment.model_validate({
        "status": "pending_semantic_confirmation",
        "checks": [
            {
                "criterion": criterion,
                "status": "passed" if criterion != "complex_interaction" else "pending",
                "message": "本地结论。",
                "evidence": "本地证据。",
            }
            for criterion in (
                "single_shot", "motion_range",
                "primary_subject_count", "complex_interaction",
            )
        ],
    })
    provider_check = ReproducibilityCheck.model_validate({
        "id": "provider-single-shot",
        "kind": "observation",
        "summary": "供应商判断。",
        "timeRange": {"startSeconds": 0.0, "endSeconds": 2.5},
        "confidence": "medium",
        "confidenceReason": "供应商分析结果。",
        "criterion": "single_shot",
        "status": "passed",
        "evidence": "供应商分析结果。",
    })

    with pytest.raises(ValueError):
        merge_reproducibility(local, [provider_check], 2.5)
