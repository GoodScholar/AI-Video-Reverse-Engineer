from datetime import datetime, timezone

import pytest

from app.local_preprocessing import (
    FrameMetric,
    MotionSummary,
    SceneChange,
    assess_reproducibility,
    detect_scene_changes,
    linear_percentile,
    new_local_preprocessing,
    parse_scdet_metadata,
    select_keyframe_times,
    summarize_motion,
)


def metric(time: float, mafd: float, score: float = 0.0) -> FrameMetric:
    return FrameMetric(timeSeconds=time, mafd=mafd, sceneScore=score)


def test_scene_changes_ignore_start_merge_neighbors_and_include_threshold():
    changes = detect_scene_changes([
        metric(0.25, 30.0, 30.0),
        metric(1.00, 12.0, 10.0),
        metric(1.25, 20.0, 16.0),
        metric(2.00, 9.9, 9.9),
    ])

    assert [(item.timeSeconds, item.score) for item in changes] == [(1.25, 16.0)]


def test_motion_boundaries_and_insufficient_samples_are_explicit():
    light = summarize_motion([metric(index / 8, 2.5) for index in range(8)], [])
    moderate = summarize_motion([metric(index / 8, 8.0) for index in range(8)], [])
    high = summarize_motion([metric(index / 8, 8.01) for index in range(8)], [])
    unavailable = summarize_motion([metric(index / 8, 1.0) for index in range(7)], [])

    assert (light.level, moderate.level, high.level) == ("light", "moderate", "high")
    assert unavailable.model_dump() == {
        "p50": None,
        "p90": None,
        "peak": None,
        "level": "unavailable",
        "validSampleCount": 7,
        "excludedSampleCount": 0,
        "validSamples": [
            {"timeSeconds": index / 8, "mafd": 1.0} for index in range(7)
        ],
        "excludedTimesSeconds": [],
    }


def test_motion_level_uses_unrounded_p90_at_both_boundaries():
    just_above_light = summarize_motion(
        [metric(index / 8, 2.5001) for index in range(8)], []
    )
    just_above_moderate = summarize_motion(
        [metric(index / 8, 8.0001) for index in range(8)], []
    )

    assert (just_above_light.p90, just_above_light.level) == (2.5, "moderate")
    assert (just_above_moderate.p90, just_above_moderate.level) == (8.0, "high")


def test_assessment_never_claims_final_in_scope():
    result = assess_reproducibility([], MotionSummary(
        p50=2.0,
        p90=3.0,
        peak=4.0,
        level="moderate",
        validSampleCount=16,
        excludedSampleCount=0,
    ))

    assert result.status == "pending_semantic_confirmation"
    assert [(item.criterion, item.status) for item in result.checks] == [
        ("single_shot", "passed"),
        ("motion_range", "passed"),
        ("primary_subject_count", "pending"),
        ("complex_interaction", "pending"),
    ]


def test_new_task_has_all_five_ordered_pending_stages():
    task = new_local_preprocessing(
        preprocessing_id="prep-001",
        reference_media_id="video-001", media_type="video",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )

    assert task.status == "queued"
    assert [stage.name for stage in task.stages] == [
        "decoding",
        "sceneDetection",
        "keyframeExtraction",
        "motionAnalysis",
        "reproducibilityAssessment",
    ]
    assert {stage.status for stage in task.stages} == {"pending"}


@pytest.mark.parametrize(
    "omitted_line",
    ["pts_time:1.250", "lavfi.scd.mafd=3.500", "lavfi.scd.score=12.000"],
)
def test_parse_rejects_missing_required_metric(omitted_line: str):
    lines = [
        "frame:0 pts:1500 pts_time:1.250",
        "lavfi.scd.mafd=3.500",
        "lavfi.scd.score=12.000",
    ]
    if omitted_line.startswith("pts_time"):
        lines[0] = "frame:0 pts:1500"
    else:
        lines.remove(omitted_line)

    with pytest.raises(ValueError, match="镜头检测元数据不完整"):
        parse_scdet_metadata("\n".join(lines))


def test_motion_excludes_inclusive_cut_window():
    metrics = [metric(index / 4, float(index)) for index in range(11)]
    summary = summarize_motion(metrics, [SceneChange(timeSeconds=0.25, score=10.0)])

    assert summary.excludedSampleCount == 3
    assert summary.validSampleCount == 8
    assert (summary.p50, summary.p90, summary.peak) == (6.5, 9.3, 10.0)


def test_motion_summary_keeps_time_ordered_valid_samples_and_cut_exclusions():
    summary = summarize_motion(
        [metric(index / 8, float(index)) for index in range(16)],
        [SceneChange(timeSeconds=0.5, score=12.0)],
    )

    assert [sample.model_dump() for sample in summary.validSamples] == [
        {"timeSeconds": 0.0, "mafd": 0.0},
        {"timeSeconds": 0.125, "mafd": 1.0},
        {"timeSeconds": 0.875, "mafd": 7.0},
        {"timeSeconds": 1.0, "mafd": 8.0},
        {"timeSeconds": 1.125, "mafd": 9.0},
        {"timeSeconds": 1.25, "mafd": 10.0},
        {"timeSeconds": 1.375, "mafd": 11.0},
        {"timeSeconds": 1.5, "mafd": 12.0},
        {"timeSeconds": 1.625, "mafd": 13.0},
        {"timeSeconds": 1.75, "mafd": 14.0},
        {"timeSeconds": 1.875, "mafd": 15.0},
    ]
    assert summary.excludedTimesSeconds == [0.25, 0.375, 0.5, 0.625, 0.75]


def test_linear_percentile_interpolates_fixed_examples():
    assert linear_percentile([0.0, 10.0], 0.9) == 9.0


def test_assessment_keeps_both_local_failure_reasons():
    result = assess_reproducibility(
        [SceneChange(timeSeconds=1.0, score=12.0)],
        MotionSummary(
            p50=9.0,
            p90=9.0,
            peak=10.0,
            level="high",
            validSampleCount=8,
            excludedSampleCount=0,
        ),
    )

    assert result.status == "out_of_scope"
    assert [(item.criterion, item.status) for item in result.checks[:2]] == [
        ("single_shot", "failed"),
        ("motion_range", "failed"),
    ]


def test_unavailable_motion_is_not_assessed():
    result = assess_reproducibility([], MotionSummary(
        p50=None,
        p90=None,
        peak=None,
        level="unavailable",
        validSampleCount=7,
        excludedSampleCount=1,
    ))

    assert result.status == "pending_semantic_confirmation"
    assert result.checks[1].status == "not_assessed"
    assert "低运动" not in result.checks[1].message


@pytest.mark.parametrize(
    ("duration_seconds", "frame_rate", "target_count", "expected_end"),
    [(2.0, 24.0, 4, 1.958), (10.0, 30.0, 10, 9.967)],
)
def test_keyframes_keep_ends_and_target_count(
    duration_seconds: float,
    frame_rate: float,
    target_count: int,
    expected_end: float,
):
    times = select_keyframe_times(duration_seconds, frame_rate, [])

    assert times == sorted(times)
    assert len(times) == target_count
    assert times[0] == 0.0
    assert times[-1] == expected_end
