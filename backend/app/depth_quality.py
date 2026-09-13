"""确定性的 V1 深度质量门禁；采样和数学仅使用 Python 标准库。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import islice
from typing import Sequence

from .depth_capture import DEPTH_QUALITY_CHECK_ORDER, DepthQualityAssessment, DepthQualityCheck

QUALITY_THRESHOLD_VERSION = 1
MIN_DEPTH_PERCENTILE_SPAN = 0.08
FLICKER_REVIEW_RATIO = 3.0
DIRECTION_REVERSAL_CORRELATION = -0.65
EDGE_BREAK_REVIEW_FRACTION = 0.20
TIMELINE_TOLERANCE_FRAMES = 1
EDGE_DISCONTINUITY_DELTA_V1 = 0.25
MOTION_MAGNITUDE_FLOOR = 0.01
MAX_PERCENTILE_SAMPLES = 4_096
MAX_SPATIAL_SAMPLES = 512
MAX_TEMPORAL_SAMPLES = 128
MAX_EDGE_FRAME_SAMPLES = 128
MAX_EVIDENCE_TIMESTAMPS = 8
_MAX_METRIC = 1_000_000_000_000.0
_GATE_EXCEPTIONS = (TypeError, ValueError, IndexError, KeyError, ArithmeticError, OverflowError, AttributeError)


@dataclass(frozen=True)
class SourceMotionSample:
    timestamp_seconds: float
    magnitude: float


@dataclass(frozen=True)
class DepthQualityInput:
    values: Sequence[float]
    frame_count: int
    height: int
    width: int
    frame_rate: float
    expected_duration_seconds: float
    source_motion_samples: Sequence[SourceMotionSample]


@dataclass(frozen=True)
class _QualityContext:
    data: DepthQualityInput
    expected_size: int | None
    observed_size: int | None
    tensor_usable: bool
    percentile_values: tuple[float, ...]
    spatial_positions: tuple[int, ...]
    transition_indices: tuple[int, ...]
    edge_frame_indices: tuple[int, ...]
    transition_count: int
    motion_samples: object | None
    motion_count: int | None


def assess_depth_quality(
    input_data: DepthQualityInput,
    *,
    input_fully_validated: bool = False,
) -> DepthQualityAssessment:
    """总是返回六项完成检查；可预期坏输入只会得到有限 failed 结果。"""
    try:
        context = _build_context(input_data, input_fully_validated is True)
    except Exception:
        context = _invalid_context(input_data)
    builders = {
        "completeness": _completeness, "dynamicRange": _dynamic_range,
        "temporalFlicker": _temporal_flicker, "directionStability": _direction_stability,
        "edgeContinuity": _edge_continuity, "timelineAlignment": _timeline_alignment,
    }
    checks = [_run_gate(criterion, builders[criterion], context) for criterion in DEPTH_QUALITY_CHECK_ORDER]
    statuses = {check.status for check in checks}
    status = "failed" if "failed" in statuses else "review_required" if "review_required" in statuses else "passed"
    return DepthQualityAssessment(status=status, checks=checks, thresholdVersion=QUALITY_THRESHOLD_VERSION)


def _build_context(data: DepthQualityInput, input_fully_validated: bool) -> _QualityContext:
    expected_size = _expected_size(data)
    observed_size = _safe_len(data.values)
    value_indices = _uniform_indices(observed_size or 0, MAX_PERCENTILE_SAMPLES)
    sampled = _sample_normalized_values(data.values, value_indices)
    area = data.height * data.width if expected_size is not None else 0
    transitions = data.frame_count - 1 if _valid_frame_count(data.frame_count) else -1
    motion_count = _safe_len(data.source_motion_samples)
    return _QualityContext(
        data, expected_size, observed_size,
        expected_size is not None and observed_size == expected_size and sampled is not None
        and (input_fully_validated or _all_normalized_values(data.values, observed_size)),
        sampled or (), tuple(_uniform_indices(area, MAX_SPATIAL_SAMPLES)),
        tuple(_uniform_indices(max(transitions, 0), MAX_TEMPORAL_SAMPLES)),
        tuple(_uniform_indices(data.frame_count if _valid_frame_count(data.frame_count) else 0, MAX_EDGE_FRAME_SAMPLES)),
        transitions, data.source_motion_samples if motion_count is not None else None, motion_count,
    )


def _invalid_context(data: DepthQualityInput) -> _QualityContext:
    return _QualityContext(data, None, None, False, (), (), (), (), -1, None, None)


def _run_gate(criterion: str, builder, context: _QualityContext) -> DepthQualityCheck:
    try:
        return builder(context)
    except Exception:
        return _failed_fallback(criterion)


def _completeness(context: _QualityContext) -> DepthQualityCheck:
    data = context.data
    duration_valid = _finite_positive(data.frame_rate) and _finite_positive(data.expected_duration_seconds)
    duration_error = _duration_error_frames(data.frame_count, data.expected_duration_seconds, data.frame_rate) if duration_valid else 2.0
    shape_difference = _safe_difference(context.observed_size, context.expected_size)
    valid = context.expected_size is not None and context.observed_size == context.expected_size and duration_valid and duration_error <= 1.0
    if valid:
        return _check("completeness", "passed", duration_error, 1.0, "深度张量尺寸与编码时长完整。", "张量长度精确匹配，时长误差不超过一帧。", [])
    metric = max(duration_error, 2.0 if shape_difference else 0.0)
    return _check("completeness", "failed", metric, 1.0, "深度张量尺寸或编码时长不完整。", f"values={context.observed_size}，expected={context.expected_size}，shapeDifference={shape_difference}，durationErrorFrames={duration_error:.6f}。", [])


def _dynamic_range(context: _QualityContext) -> DepthQualityCheck:
    if not context.tensor_usable or not context.percentile_values:
        return _check("dynamicRange", "failed", 0.0, MIN_DEPTH_PERCENTILE_SPAN, "深度张量无法计算动态范围。", "张量结构或有界样本不是有限的归一化深度。", [])
    low, high = _percentile(context.percentile_values, 2.0), _percentile(context.percentile_values, 98.0)
    span = _finite_metric(high - low)
    status = "passed" if span >= MIN_DEPTH_PERCENTILE_SPAN else "failed"
    return _check("dynamicRange", status, span, MIN_DEPTH_PERCENTILE_SPAN, "深度动态范围足够。" if status == "passed" else "深度动态范围不足。", f"整段有界均匀样本 P2={low:.6f}，P98={high:.6f}。", [])


def _temporal_flicker(context: _QualityContext) -> DepthQualityCheck:
    if not context.tensor_usable or context.transition_count <= 0 or context.motion_count is None or context.motion_count < context.transition_count:
        return _check("temporalFlicker", "failed", 0.0, FLICKER_REVIEW_RATIO, "无法计算深度时间闪烁。", "缺少可用的深度帧转换或来源运动样本。", [])
    ratios = []
    for index in context.transition_indices:
        motion = _motion_at(context, index)
        ratios.append(_mean_absolute_change(context, index) / max(motion.magnitude, MOTION_MAGNITUDE_FLOOR))
    worst = _finite_metric(max(ratios))
    worst_indices = [context.transition_indices[position] for position, value in enumerate(ratios) if math.isclose(value, worst, rel_tol=0.0, abs_tol=1e-12)]
    status = "review_required" if worst > FLICKER_REVIEW_RATIO else "passed"
    return _check("temporalFlicker", status, worst, FLICKER_REVIEW_RATIO, "深度帧间变化与来源运动一致。" if status == "passed" else "深度帧间变化相对来源运动过大，需复核。", f"最大帧间深度变化/运动幅度比，分母下限={MOTION_MAGNITUDE_FLOOR:.2f}。", _transition_timestamps(context, worst_indices))


def _direction_stability(context: _QualityContext) -> DepthQualityCheck:
    if not context.tensor_usable or context.transition_count <= 0:
        return _check("directionStability", "failed", 0.0, DIRECTION_REVERSAL_CORRELATION, "无法计算深度方向稳定性。", "缺少可用的相邻深度帧。", [])
    correlations = [_pearson_for_transition(context, index) for index in context.transition_indices]
    worst = _finite_metric(min(correlations))
    worst_indices = [context.transition_indices[position] for position, value in enumerate(correlations) if math.isclose(value, worst, rel_tol=0.0, abs_tol=1e-12)]
    status = "failed" if worst <= DIRECTION_REVERSAL_CORRELATION else "passed"
    return _check("directionStability", status, worst, DIRECTION_REVERSAL_CORRELATION, "相邻深度帧方向稳定。" if status == "passed" else "相邻深度帧出现反向相关。", "采样相邻帧 Pearson 相关系数；退化帧按 0.0 处理。", _transition_timestamps(context, worst_indices))


def _edge_continuity(context: _QualityContext) -> DepthQualityCheck:
    if not context.tensor_usable:
        return _check("edgeContinuity", "failed", 0.0, EDGE_BREAK_REVIEW_FRACTION, "无法计算深度边缘连续性。", "深度张量结构或有界样本无效。", [])
    fractions = [_edge_break_fraction(context, frame) for frame in context.edge_frame_indices]
    worst = _finite_metric(max(fractions) if fractions else 0.0)
    worst_frames = [context.edge_frame_indices[position] for position, value in enumerate(fractions) if math.isclose(value, worst, rel_tol=0.0, abs_tol=1e-12)]
    status = "review_required" if worst > EDGE_BREAK_REVIEW_FRACTION else "passed"
    evidence = f"V1 相邻像素深度差阈值={EDGE_DISCONTINUITY_DELTA_V1:.2f}；统计有界水平和垂直邻居。"
    return _check("edgeContinuity", status, worst, EDGE_BREAK_REVIEW_FRACTION, "深度边缘连续。" if status == "passed" else "深度边缘碎裂比例过高，需复核。", evidence, _frame_timestamps(context, worst_frames))


def _timeline_alignment(context: _QualityContext) -> DepthQualityCheck:
    if context.transition_count < 0 or context.motion_count is None:
        return _check("timelineAlignment", "failed", 2.0, TIMELINE_TOLERANCE_FRAMES, "来源运动时间线无效。", "来源运动样本必须是可计数的转换序列。", [])
    if context.motion_count != context.transition_count:
        index = context.motion_count if context.motion_count < context.transition_count else max(context.transition_count - 1, 0)
        return _check("timelineAlignment", "failed", _finite_metric(1 + abs(context.motion_count - context.transition_count)), TIMELINE_TOLERANCE_FRAMES, "来源运动时间线的转换数量不匹配。", "来源运动样本必须与深度转换一一对应。", _transition_timestamps(context, [index]))
    if not _finite_positive(context.data.frame_rate) or not _finite_frame_interval(context.data.frame_rate):
        return _check("timelineAlignment", "failed", 2.0, TIMELINE_TOLERANCE_FRAMES, "来源运动时间线帧率无效。", "输出帧率必须可安全换算为帧误差。", [])
    previous, worst_frames, failure_index = -math.inf, 0.0, None
    for index in range(context.transition_count):
        try:
            sample = _motion_at(context, index)
        except _GATE_EXCEPTIONS:
            return _check("timelineAlignment", "failed", 2.0, TIMELINE_TOLERANCE_FRAMES, "来源运动时间线样本无效。", "转换时间和运动幅度必须为有限且有效的数值。", _transition_timestamps(context, [index]))
        timestamp = sample.timestamp_seconds
        if timestamp <= previous:
            failure_index, worst_frames = index, 2.0
            break
        previous = timestamp
        error_frames = _finite_metric(abs(timestamp - (index + 1) / context.data.frame_rate) * context.data.frame_rate)
        worst_frames = max(worst_frames, error_frames)
        if error_frames > TIMELINE_TOLERANCE_FRAMES and error_frames >= worst_frames:
            failure_index = index
    if failure_index is not None:
        return _check("timelineAlignment", "failed", worst_frames, TIMELINE_TOLERANCE_FRAMES, "来源运动时间线未与深度帧对齐。", "转换时间必须单调、有限，且偏差不超过一个输出帧。", _transition_timestamps(context, [failure_index]))
    return _check("timelineAlignment", "passed", worst_frames, TIMELINE_TOLERANCE_FRAMES, "来源运动时间线与深度帧对齐。", "每个转换样本均在一个输出帧间隔内。", [])


def _expected_size(data: DepthQualityInput) -> int | None:
    if not _valid_frame_count(data.frame_count) or any(type(value) is not int or value <= 0 for value in (data.height, data.width)):
        return None
    return data.frame_count * data.height * data.width


def _sample_normalized_values(values, indices: list[int]) -> tuple[float, ...] | None:
    sampled = []
    for index in indices:
        value = values[index]
        if not _finite_number(value) or not 0.0 <= value <= 1.0:
            return None
        sampled.append(float(value))
    return tuple(sampled)


def _all_normalized_values(values, length: int | None) -> bool:
    if length is None:
        return False
    for index in range(length):
        value = values[index]
        if not _finite_number(value) or not 0.0 <= value <= 1.0:
            return False
    return True


def _motion_at(context: _QualityContext, index: int) -> SourceMotionSample:
    sample = context.motion_samples[index]
    if not isinstance(sample, SourceMotionSample) or not _finite_number(sample.timestamp_seconds) or not _finite_number(sample.magnitude) or not 0.0 <= sample.magnitude <= 1.0:
        raise ValueError("invalid motion sample")
    return sample


def _mean_absolute_change(context: _QualityContext, transition: int) -> float:
    area = context.data.height * context.data.width
    total = sum(abs(float(context.data.values[(transition + 1) * area + pixel]) - float(context.data.values[transition * area + pixel])) for pixel in context.spatial_positions)
    return _finite_metric(total / len(context.spatial_positions))


def _pearson_for_transition(context: _QualityContext, transition: int) -> float:
    area = context.data.height * context.data.width
    left = [float(context.data.values[transition * area + pixel]) for pixel in context.spatial_positions]
    right = [float(context.data.values[(transition + 1) * area + pixel]) for pixel in context.spatial_positions]
    left_mean, right_mean = sum(left) / len(left), sum(right) / len(right)
    covariance = sum((first - left_mean) * (second - right_mean) for first, second in zip(left, right))
    left_variance = sum((value - left_mean) ** 2 for value in left)
    right_variance = sum((value - right_mean) ** 2 for value in right)
    return 0.0 if left_variance <= 0.0 or right_variance <= 0.0 else _finite_metric(covariance / math.sqrt(left_variance * right_variance))


def _edge_break_fraction(context: _QualityContext, frame: int) -> float:
    area, broken, total = context.data.height * context.data.width, 0, 0
    for position in context.spatial_positions:
        row, column = divmod(position, context.data.width)
        current = float(context.data.values[frame * area + position])
        for neighbor in (position + 1 if column + 1 < context.data.width else None, position + context.data.width if row + 1 < context.data.height else None):
            if neighbor is not None:
                total += 1
                broken += abs(current - float(context.data.values[frame * area + neighbor])) > EDGE_DISCONTINUITY_DELTA_V1
    return _finite_metric(broken / total) if total else 0.0


def _transition_timestamps(context: _QualityContext, indices) -> list[float]:
    return _bounded_timestamps((_canonical_transition_timestamp(context, index) for index in indices))


def _frame_timestamps(context: _QualityContext, indices) -> list[float]:
    return [] if not _finite_positive(context.data.frame_rate) else _bounded_timestamps((index / context.data.frame_rate for index in indices))


def _canonical_transition_timestamp(context: _QualityContext, index: int) -> float | None:
    if not _finite_positive(context.data.frame_rate) or index < 0:
        return None
    timestamp = (index + 1) / context.data.frame_rate
    duration = context.data.frame_count / context.data.frame_rate if _valid_frame_count(context.data.frame_count) else 0.0
    return timestamp if math.isfinite(timestamp) and math.isfinite(duration) and 0.0 <= timestamp <= duration else None


def _bounded_timestamps(values) -> list[float]:
    return [float(value) for value in islice(values, MAX_EVIDENCE_TIMESTAMPS) if _finite_number(value) and value >= 0.0]


def _duration_error_frames(frame_count, expected_duration, frame_rate) -> float:
    return _finite_metric(abs(frame_count - expected_duration * frame_rate))


def _safe_difference(left, right) -> float:
    return _MAX_METRIC if left is None or right is None else _finite_metric(abs(left - right))


def _finite_frame_interval(frame_rate) -> bool:
    try:
        return math.isfinite(1.0 / frame_rate)
    except _GATE_EXCEPTIONS:
        return False


def _finite_metric(value) -> float:
    try:
        numeric = float(value)
    except _GATE_EXCEPTIONS:
        return _MAX_METRIC
    if math.isnan(numeric):
        return 0.0
    if math.isinf(numeric):
        return _MAX_METRIC
    return max(-_MAX_METRIC, min(_MAX_METRIC, numeric))


def _finite_number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except _GATE_EXCEPTIONS:
        return False


def _finite_positive(value) -> bool:
    return _finite_number(value) and value > 0


def _valid_frame_count(value) -> bool:
    return type(value) is int and value >= 1


def _safe_len(values) -> int | None:
    try:
        return len(values)
    except _GATE_EXCEPTIONS:
        return None


def _uniform_indices(length: int, limit: int) -> list[int]:
    if length <= 0:
        return []
    count = min(length, limit)
    return [0] if count == 1 else [(index * (length - 1)) // (count - 1) for index in range(count)]


def _percentile(values: tuple[float, ...], percent: float) -> float:
    ordered = sorted(values)
    position = percent / 100.0 * (len(ordered) - 1)
    lower, upper = int(math.floor(position)), int(math.ceil(position))
    return _finite_metric(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower))


def _failed_fallback(criterion: str) -> DepthQualityCheck:
    thresholds = {"completeness": 1.0, "dynamicRange": MIN_DEPTH_PERCENTILE_SPAN, "temporalFlicker": FLICKER_REVIEW_RATIO, "directionStability": DIRECTION_REVERSAL_CORRELATION, "edgeContinuity": EDGE_BREAK_REVIEW_FRACTION, "timelineAlignment": TIMELINE_TOLERANCE_FRAMES}
    return _check(criterion, "failed", 0.0, thresholds[criterion], "深度质量检查输入无效。", "检查期间遇到可预期的输入或算术错误。", [])


def _check(criterion, status, metric, threshold, message, evidence, timestamps) -> DepthQualityCheck:
    return DepthQualityCheck(criterion=criterion, status=status, metric=_finite_metric(metric), threshold=_finite_metric(threshold), message=message, evidence=evidence, sampleTimestamps=_bounded_timestamps(timestamps))
