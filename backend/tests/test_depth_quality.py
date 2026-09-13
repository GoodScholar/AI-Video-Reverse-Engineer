import math

import pytest
from pydantic import ValidationError

from app.depth_capture import (
    DEPTH_QUALITY_CHECK_ORDER,
    DepthQualityAssessment,
    DepthQualityCheck,
)
from app.depth_quality import (
    EDGE_BREAK_REVIEW_FRACTION,
    FLICKER_REVIEW_RATIO,
    MIN_DEPTH_PERCENTILE_SPAN,
    QUALITY_THRESHOLD_VERSION,
    DepthQualityInput,
    SourceMotionSample,
    TIMELINE_TOLERANCE_FRAMES,
    assess_depth_quality,
)


def _normal_input(**overrides):
    payload = {
        "values": [
            0.1, 0.2, 0.2, 0.3,
            0.2, 0.3, 0.3, 0.4,
            0.3, 0.4, 0.4, 0.5,
        ],
        "frame_count": 3,
        "height": 2,
        "width": 2,
        "frame_rate": 2.0,
        "expected_duration_seconds": 1.5,
        "source_motion_samples": (
            SourceMotionSample(timestamp_seconds=0.5, magnitude=0.25),
            SourceMotionSample(timestamp_seconds=1.0, magnitude=0.25),
        ),
    }
    payload.update(overrides)
    return DepthQualityInput(**payload)


def _check(assessment, criterion):
    return next(check for check in assessment.checks if check.criterion == criterion)


def test_normal_clip_passes_all_six_versioned_quality_gates():
    assessment = assess_depth_quality(_normal_input())

    assert assessment.status == "passed"
    assert assessment.thresholdVersion == QUALITY_THRESHOLD_VERSION == 1
    assert [check.criterion for check in assessment.checks] == [
        "completeness", "dynamicRange", "temporalFlicker", "directionStability",
        "edgeContinuity", "timelineAlignment",
    ]
    assert {check.status for check in assessment.checks} == {"passed"}
    for check in assessment.checks:
        assert math.isfinite(check.metric)
        assert math.isfinite(check.threshold)
        assert check.message and check.evidence
        assert len(check.sampleTimestamps) <= 8
        assert all(math.isfinite(value) and 0 <= value <= 1.5 for value in check.sampleTimestamps)


def test_completeness_fails_when_tensor_size_does_not_match_declared_shape():
    assessment = assess_depth_quality(_normal_input(values=[0.0] * 11))

    assert _check(assessment, "completeness").status == "failed"


def test_dynamic_range_fails_for_a_constant_normalized_clip():
    assessment = assess_depth_quality(_normal_input(values=[0.5] * 12))

    check = _check(assessment, "dynamicRange")
    assert check.status == "failed"
    assert check.threshold == MIN_DEPTH_PERCENTILE_SPAN


def test_temporal_flicker_requires_review_when_depth_changes_exceed_source_motion():
    assessment = assess_depth_quality(_normal_input(
        values=[0.0] * 4 + [1.0] * 4 + [0.0] * 4,
        source_motion_samples=(
            SourceMotionSample(timestamp_seconds=0.5, magnitude=0.01),
            SourceMotionSample(timestamp_seconds=1.0, magnitude=0.01),
        ),
    ))

    check = _check(assessment, "temporalFlicker")
    assert check.status == "review_required"
    assert check.metric > FLICKER_REVIEW_RATIO
    assert check.sampleTimestamps == [0.5, 1.0]


def test_direction_stability_fails_for_reversed_adjacent_depth_frames():
    assessment = assess_depth_quality(_normal_input(
        values=[0.0, 0.3, 0.6, 0.9] + [0.9, 0.6, 0.3, 0.0] + [0.0, 0.3, 0.6, 0.9],
    ))

    check = _check(assessment, "directionStability")
    assert check.status == "failed"
    assert check.metric <= -0.65
    assert check.sampleTimestamps == [0.5, 1.0]


def test_edge_continuity_requires_review_for_fragmented_depth_edges():
    assessment = assess_depth_quality(_normal_input(
        values=[0.0, 1.0, 1.0, 0.0] * 3,
    ))

    check = _check(assessment, "edgeContinuity")
    assert check.status == "review_required"
    assert check.metric > EDGE_BREAK_REVIEW_FRACTION
    assert "V1" in check.evidence


def test_timeline_alignment_fails_for_wrong_motion_transition_count_without_hiding_other_results():
    assessment = assess_depth_quality(_normal_input(source_motion_samples=(
        SourceMotionSample(timestamp_seconds=0.5, magnitude=0.25),
    )))

    assert _check(assessment, "timelineAlignment").status == "failed"
    assert len(assessment.checks) == 6
    assert _check(assessment, "dynamicRange").status == "passed"


def test_failed_check_wins_over_review_required_status_and_malformed_values_remain_finite():
    assessment = assess_depth_quality(_normal_input(
        values=[float("nan")] * 12,
        source_motion_samples=(
            SourceMotionSample(timestamp_seconds=0.5, magnitude=0.0),
            SourceMotionSample(timestamp_seconds=1.0, magnitude=0.0),
        ),
    ))

    assert assessment.status == "failed"
    assert _check(assessment, "dynamicRange").status == "failed"
    assert _check(assessment, "temporalFlicker").status == "failed"
    for check in assessment.checks:
        assert math.isfinite(check.metric)
        assert math.isfinite(check.threshold)
        assert all(math.isfinite(value) for value in check.sampleTimestamps)


@pytest.mark.parametrize(
    "samples,expected_timestamp",
    [
        ((SourceMotionSample(0.5, 0.25),), 1.0),
        ((SourceMotionSample(0.5, 0.25), SourceMotionSample(1.0, 0.25), SourceMotionSample(1.5, 0.25)), 1.0),
        ((SourceMotionSample(0.5, 0.25), SourceMotionSample(0.5, 0.25)), 1.0),
        ((SourceMotionSample(0.5, 0.25), SourceMotionSample(float("nan"), 0.25)), 1.0),
        ((SourceMotionSample(0.5, 0.25), SourceMotionSample(2.1, 0.25)), 1.0),
    ],
)
def test_timeline_failures_use_frame_metric_and_canonical_transition_evidence(samples, expected_timestamp):
    assessment = assess_depth_quality(_normal_input(source_motion_samples=samples))

    check = _check(assessment, "timelineAlignment")
    assert check.status == "failed"
    assert check.threshold == TIMELINE_TOLERANCE_FRAMES == 1
    assert check.metric > check.threshold
    assert check.sampleTimestamps == [expected_timestamp]


def test_quality_assessment_uses_bounded_tensor_reads_once_across_all_gates():
    class CountingSequence:
        def __init__(self, length):
            self.length = length
            self.reads = 0

        def __len__(self):
            return self.length

        def __getitem__(self, index):
            self.reads += 1
            if not 0 <= index < self.length:
                raise IndexError(index)
            return (index % 11) / 10

    values = CountingSequence(20_000)
    assessment = assess_depth_quality(_normal_input(
        values=values,
        frame_count=5_000,
        height=1,
        width=4,
        frame_rate=10.0,
        expected_duration_seconds=500.0,
        source_motion_samples=tuple(SourceMotionSample((index + 1) / 10, 0.5) for index in range(4_999)),
    ), input_fully_validated=True)

    assert len(assessment.checks) == 6
    assert values.reads < 10_000


def test_public_quality_boundary_converts_runtime_error_sequences_to_six_finite_failed_checks():
    class RuntimeErrorSequence:
        def __len__(self):
            return 12

        def __getitem__(self, index):
            raise RuntimeError(index)

    assessment = assess_depth_quality(_normal_input(values=RuntimeErrorSequence()))

    assert assessment.status == "failed"
    assert len(assessment.checks) == 6
    assert all(check.status == "failed" for check in assessment.checks)
    assert all(math.isfinite(check.metric) and math.isfinite(check.threshold) for check in assessment.checks)


def test_untrusted_public_quality_input_detects_nan_outside_the_bounded_sampling_indices():
    values = [(index % 11) / 10.0 for index in range(10_000)]
    values[1] = float("nan")
    assessment = assess_depth_quality(_normal_input(
        values=values,
        frame_count=2_500,
        height=1,
        width=4,
        frame_rate=10.0,
        expected_duration_seconds=250.0,
        source_motion_samples=tuple(SourceMotionSample((index + 1) / 10.0, 0.5) for index in range(2_499)),
    ))

    assert assessment.status == "failed"
    assert len(assessment.checks) == 6
    assert _check(assessment, "dynamicRange").status == "failed"


def test_timeline_offset_metric_reports_the_actual_worst_frame_error():
    assessment = assess_depth_quality(_normal_input(source_motion_samples=(
        SourceMotionSample(0.5, 0.25), SourceMotionSample(1.6, 0.25),
    )))

    check = _check(assessment, "timelineAlignment")
    assert check.status == "failed"
    assert check.metric == pytest.approx(1.2)


def test_timeline_scans_every_structurally_valid_transition_after_an_early_offset_failure():
    assessment = assess_depth_quality(_normal_input(
        values=[0.1, 0.2, 0.2, 0.3] * 4,
        frame_count=4,
        height=2,
        width=2,
        frame_rate=2.0,
        expected_duration_seconds=2.0,
        source_motion_samples=(
            SourceMotionSample(0.5, 0.25),
            SourceMotionSample(1.6, 0.25),
            SourceMotionSample(5.0, 0.25),
        ),
    ))

    check = _check(assessment, "timelineAlignment")
    assert check.status == "failed"
    assert check.metric == pytest.approx(7.0)
    assert check.sampleTimestamps == [1.5]


@pytest.mark.parametrize(
    "overrides",
    [
        {"frame_rate": 5e-324},
        {"frame_count": 10**400, "height": 10**400, "width": 10**400},
        {"expected_duration_seconds": float("inf")},
        {"values": object()},
    ],
)
def test_malformed_quality_inputs_return_six_finite_failed_checks(overrides):
    assessment = assess_depth_quality(_normal_input(**overrides))

    assert assessment.status == "failed"
    assert [check.criterion for check in assessment.checks] == list(DEPTH_QUALITY_CHECK_ORDER)
    for check in assessment.checks:
        assert math.isfinite(check.metric)
        assert math.isfinite(check.threshold)
        assert all(math.isfinite(timestamp) and timestamp >= 0 for timestamp in check.sampleTimestamps)


def test_index_error_value_sequence_becomes_a_finite_six_check_failure():
    class ExplodingSequence:
        def __len__(self):
            return 12

        def __getitem__(self, index):
            raise IndexError(index)

    assessment = assess_depth_quality(_normal_input(values=ExplodingSequence()))

    assert assessment.status == "failed"
    assert len(assessment.checks) == 6
    assert all(math.isfinite(check.metric) and math.isfinite(check.threshold) for check in assessment.checks)


def _quality_check(criterion, status="passed"):
    return DepthQualityCheck(
        criterion=criterion, status=status, message="消息", evidence="证据",
        metric=0.0, threshold=1.0, sampleTimestamps=[],
    )


def test_depth_quality_pydantic_contract_rejects_invalid_criteria_timestamps_and_assessment_shape():
    with pytest.raises(ValidationError):
        _quality_check("unknown")
    with pytest.raises(ValidationError):
        DepthQualityCheck(criterion="completeness", status="passed", message="", evidence="证据", metric=0.0, threshold=1.0, sampleTimestamps=[])
    with pytest.raises(ValidationError):
        DepthQualityCheck(criterion="completeness", status="passed", message="消息", evidence="证据", metric=0.0, threshold=1.0, sampleTimestamps=[-0.1])
    with pytest.raises(ValidationError):
        DepthQualityCheck(criterion="completeness", status="passed", message="消息", evidence="证据", metric=0.0, threshold=1.0, sampleTimestamps=[0.0] * 9)
    checks = [_quality_check(criterion) for criterion in DEPTH_QUALITY_CHECK_ORDER]
    with pytest.raises(ValidationError):
        DepthQualityAssessment(status="passed", checks=checks[:-1])
    with pytest.raises(ValidationError):
        DepthQualityAssessment(status="passed", checks=[checks[1], checks[0], *checks[2:]])
    with pytest.raises(ValidationError):
        DepthQualityAssessment(status="passed", checks=[checks[0], checks[0], *checks[2:]])
    failed = [_quality_check(criterion, "failed" if criterion == "dynamicRange" else "passed") for criterion in DEPTH_QUALITY_CHECK_ORDER]
    with pytest.raises(ValidationError):
        DepthQualityAssessment(status="passed", checks=failed)
    review = [_quality_check(criterion, "review_required" if criterion == "edgeContinuity" else "passed") for criterion in DEPTH_QUALITY_CHECK_ORDER]
    with pytest.raises(ValidationError):
        DepthQualityAssessment(status="passed", checks=review)


def test_depth_quality_pydantic_contract_serializes_a_normal_fixed_order_assessment():
    assessment = assess_depth_quality(_normal_input())

    assert assessment.model_dump(mode="json")["checks"][0]["criterion"] == "completeness"
