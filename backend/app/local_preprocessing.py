import math
import re
from datetime import datetime
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

from .reference_video import validate_storage_id


ALGORITHM_VERSION = 1
ANALYSIS_SAMPLE_RATE = 8.0
ANALYSIS_LONG_EDGE = 320
KEYFRAME_LONG_EDGE = 768
CONTACT_SHEET_CELL_LONG_EDGE = 384
MAX_KEYFRAMES = 12
MIN_KEYFRAMES = 4
SCENE_SCORE_THRESHOLD = 10.0
SCENE_START_IGNORE_SECONDS = 0.5
SCENE_MERGE_WINDOW_SECONDS = 0.5
MOTION_EXCLUSION_SECONDS = 0.25
MIN_MOTION_SAMPLES = 8
LIGHT_MOTION_P90_MAX = 2.5
MODERATE_MOTION_P90_MAX = 8.0
FFMPEG_TIMEOUT_SECONDS = 120.0

MediaType = Literal["image", "video"]
StageName = Literal[
    "decoding", "sceneDetection", "keyframeExtraction",
    "motionAnalysis", "reproducibilityAssessment",
    "imageDecoding", "imageNormalization", "proxyGeneration",
]
VIDEO_STAGE_ORDER: tuple[StageName, ...] = (
    "decoding", "sceneDetection", "keyframeExtraction",
    "motionAnalysis", "reproducibilityAssessment",
)
IMAGE_STAGE_ORDER: tuple[StageName, ...] = (
    "imageDecoding", "imageNormalization",
    "proxyGeneration", "reproducibilityAssessment",
)
# 视频处理链路的临时兼容别名；新通用逻辑必须使用 stage_order_for()。
STAGE_ORDER = VIDEO_STAGE_ORDER


def stage_order_for(media_type: MediaType) -> tuple[StageName, ...]:
    return IMAGE_STAGE_ORDER if media_type == "image" else VIDEO_STAGE_ORDER


class FrameMetric(BaseModel):
    timeSeconds: float = Field(ge=0)
    mafd: float = Field(ge=0)
    sceneScore: float = Field(ge=0)


class SceneChange(BaseModel):
    timeSeconds: float = Field(ge=0)
    score: float = Field(ge=SCENE_SCORE_THRESHOLD)


class MotionSample(BaseModel):
    timeSeconds: float = Field(ge=0)
    mafd: float = Field(ge=0)


class MotionSummary(BaseModel):
    p50: Optional[float]
    p90: Optional[float]
    peak: Optional[float]
    level: Literal["light", "moderate", "high", "unavailable"]
    validSampleCount: int = Field(ge=0)
    excludedSampleCount: int = Field(ge=0)
    validSamples: list[MotionSample] = Field(default_factory=list)
    excludedTimesSeconds: list[float] = Field(default_factory=list)


class VideoProxySummary(BaseModel):
    mediaType: Literal["video"] = "video"
    keyframeCount: int = Field(ge=MIN_KEYFRAMES, le=MAX_KEYFRAMES)
    contactSheetCount: Literal[1] = 1
    sceneChangeCount: int = Field(ge=0)
    motionP50: Optional[float]
    motionP90: Optional[float]
    motionPeak: Optional[float]
    motionLevel: Literal["light", "moderate", "high", "unavailable"]


class ImageProxySummary(BaseModel):
    mediaType: Literal["image"] = "image"


ProxySummary = Annotated[
    Union[ImageProxySummary, VideoProxySummary], Field(discriminator="mediaType"),
]
# 视频处理链路的临时兼容别名；新代码应使用 VideoProxySummary。
AnalysisProxySummary = VideoProxySummary


class ReproducibilityCheck(BaseModel):
    criterion: Literal[
        "single_shot", "motion_range",
        "primary_subject_count", "complex_interaction",
    ]
    status: Literal["passed", "failed", "pending", "not_assessed"]
    message: str
    evidence: str


class ReproducibilityAssessment(BaseModel):
    status: Literal["out_of_scope", "pending_semantic_confirmation"]
    checks: list[ReproducibilityCheck]


class PreprocessingStageState(BaseModel):
    name: StageName
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None

    @field_validator("startedAt", "completedAt")
    @classmethod
    def timestamps_must_be_timezone_aware_iso_datetimes(cls, value: Optional[str]) -> Optional[str]:
        return _validate_timezone_aware_iso_datetime(value)


class LocalPreprocessingError(BaseModel):
    code: str
    message: str
    stage: StageName
    retryable: Literal[True] = True


class LocalPreprocessing(BaseModel):
    id: str
    sourceReferenceMediaId: str
    mediaType: MediaType
    algorithmVersion: Literal[1] = ALGORITHM_VERSION
    status: Literal["queued", "running", "completed", "failed"]
    currentStage: Optional[StageName] = None
    stages: list[PreprocessingStageState]
    queuedAt: str
    startedAt: Optional[str] = None
    updatedAt: str
    completedAt: Optional[str] = None
    proxySummary: Optional[ProxySummary] = None
    reproducibilityAssessment: Optional[ReproducibilityAssessment] = None
    error: Optional[LocalPreprocessingError] = None

    @field_validator("id", "sourceReferenceMediaId")
    @classmethod
    def ids_must_be_safe_storage_segments(cls, value: str) -> str:
        return validate_storage_id(value)

    @field_validator("queuedAt", "startedAt", "updatedAt", "completedAt")
    @classmethod
    def timestamps_must_be_timezone_aware_iso_datetimes(cls, value: Optional[str]) -> Optional[str]:
        return _validate_timezone_aware_iso_datetime(value)

    @property
    def sourceReferenceVideoId(self) -> Optional[str]:
        """Temporary internal compatibility for video-only preprocessing consumers."""
        if self.mediaType == "video":
            return self.sourceReferenceMediaId
        return None


def _validate_timezone_aware_iso_datetime(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("项目时间戳无效") from error
    if timestamp.tzinfo is None:
        raise ValueError("项目时间戳必须包含时区")
    return value


def parse_scdet_metadata(output: str) -> list[FrameMetric]:
    blocks: list[dict[str, float]] = []
    current: Optional[dict[str, float]] = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("frame:"):
            if current is not None:
                blocks.append(current)
            current = {}
            match = re.search(r"(?:^|\s)pts_time:([^\s]+)", line)
            if match:
                _set_finite_metric(current, "timeSeconds", match.group(1))
        elif current is not None and line.startswith("lavfi.scd.mafd="):
            _set_finite_metric(current, "mafd", line.split("=", 1)[1])
        elif current is not None and line.startswith("lavfi.scd.score="):
            _set_finite_metric(current, "sceneScore", line.split("=", 1)[1])
    if current is not None:
        blocks.append(current)

    required = {"timeSeconds", "mafd", "sceneScore"}
    if not blocks or any(set(block) != required for block in blocks):
        raise ValueError("镜头检测元数据不完整")
    try:
        return [FrameMetric(**block) for block in blocks]
    except ValueError as error:
        raise ValueError("镜头检测元数据不完整") from error


def _set_finite_metric(block: dict[str, float], name: str, raw_value: str) -> None:
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ValueError("镜头检测元数据不完整") from error
    if not math.isfinite(value) or value < 0:
        raise ValueError("镜头检测元数据不完整")
    block[name] = value


def detect_scene_changes(metrics: list[FrameMetric]) -> list[SceneChange]:
    candidates = [
        SceneChange(timeSeconds=item.timeSeconds, score=item.sceneScore)
        for item in sorted(metrics, key=lambda value: value.timeSeconds)
        if item.timeSeconds >= SCENE_START_IGNORE_SECONDS
        and item.sceneScore >= SCENE_SCORE_THRESHOLD
    ]
    groups: list[list[SceneChange]] = []
    for candidate in candidates:
        if (
            not groups
            or candidate.timeSeconds - groups[-1][0].timeSeconds > SCENE_MERGE_WINDOW_SECONDS
        ):
            groups.append([candidate])
        else:
            groups[-1].append(candidate)
    return [max(group, key=lambda item: (item.score, -item.timeSeconds)) for group in groups]


def linear_percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("百分位至少需要一个样本")
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_motion(
    metrics: list[FrameMetric], changes: list[SceneChange]
) -> MotionSummary:
    valid_metrics = []
    valid_samples = []
    excluded_times = []
    for item in sorted(metrics, key=lambda value: value.timeSeconds):
        if any(
            abs(item.timeSeconds - change.timeSeconds) <= MOTION_EXCLUSION_SECONDS
            for change in changes
        ):
            excluded_times.append(round(item.timeSeconds, 3))
        else:
            valid_metrics.append(item)
            valid_samples.append(MotionSample(
                timeSeconds=round(item.timeSeconds, 3), mafd=round(item.mafd, 3),
            ))
    values = [item.mafd for item in valid_metrics]
    excluded = len(excluded_times)
    if len(values) < MIN_MOTION_SAMPLES:
        return MotionSummary(
            p50=None,
            p90=None,
            peak=None,
            level="unavailable",
            validSampleCount=len(values),
            excludedSampleCount=excluded,
            validSamples=valid_samples,
            excludedTimesSeconds=excluded_times,
    )
    p50 = round(linear_percentile(values, 0.5), 3)
    raw_p90 = linear_percentile(values, 0.9)
    p90 = round(raw_p90, 3)
    level = (
        "light" if raw_p90 <= LIGHT_MOTION_P90_MAX
        else "moderate" if raw_p90 <= MODERATE_MOTION_P90_MAX
        else "high"
    )
    return MotionSummary(
        p50=p50,
        p90=p90,
        peak=round(max(values), 3),
        level=level,
        validSampleCount=len(values),
        excludedSampleCount=excluded,
        validSamples=valid_samples,
        excludedTimesSeconds=excluded_times,
    )


def select_keyframe_times(
    duration_seconds: float,
    frame_rate: float,
    changes: list[SceneChange],
) -> list[float]:
    end = max(0.0, duration_seconds - 1 / frame_rate)
    target = min(MAX_KEYFRAMES, max(MIN_KEYFRAMES, math.ceil(duration_seconds)))
    boundaries = [0.0] + [min(end, item.timeSeconds) for item in changes] + [end]
    midpoints = [(left + right) / 2 for left, right in zip(boundaries, boundaries[1:])]
    uniform = [end * index / (target - 1) for index in range(target)]
    selected = [0.0, end]

    def is_new(value: float) -> bool:
        return all(abs(value - existing) > 1 / ANALYSIS_SAMPLE_RATE for existing in selected)

    for pool in (midpoints, uniform):
        remaining = [value for value in pool if is_new(value)]
        while remaining and len(selected) < target:
            chosen = max(
                remaining,
                key=lambda value: (
                    min(abs(value - existing) for existing in selected),
                    -value,
                ),
            )
            selected.append(chosen)
            remaining = [value for value in remaining if is_new(value)]
    return sorted({round(value, 3) for value in selected})


def assess_reproducibility(
    changes: list[SceneChange], motion: MotionSummary
) -> ReproducibilityAssessment:
    shot_failed = bool(changes)
    motion_status = (
        "not_assessed" if motion.level == "unavailable"
        else "failed" if motion.level == "high" else "passed"
    )
    checks = [
        ReproducibilityCheck(
            criterion="single_shot",
            status="failed" if shot_failed else "passed",
            message="检测到多个镜头。" if shot_failed else "未检测到镜头切换。",
            evidence=f"有效镜头切换点 {len(changes)} 个。",
        ),
        ReproducibilityCheck(
            criterion="motion_range",
            status=motion_status,
            message=(
                "运动样本不足，无法独立评估。" if motion.level == "unavailable"
                else "运动强度超出轻中度范围。" if motion.level == "high"
                else "运动强度处于轻中度范围。"
            ),
            evidence=(
                "有效连续运动样本少于 8 个。"
                if motion.p90 is None else f"运动强度 P90 为 {motion.p90:.3f}。"
            ),
        ),
        ReproducibilityCheck(
            criterion="primary_subject_count",
            status="pending",
            message="主要主体数量待语义分析确认。",
            evidence="本地预处理不执行主体识别。",
        ),
        ReproducibilityCheck(
            criterion="complex_interaction",
            status="pending",
            message="复杂交互待语义分析确认。",
            evidence="本地预处理不执行交互识别。",
        ),
    ]
    failed = any(item.status == "failed" for item in checks[:2])
    return ReproducibilityAssessment(
        status="out_of_scope" if failed else "pending_semantic_confirmation",
        checks=checks,
    )


def new_local_preprocessing(
    preprocessing_id: str,
    reference_media_id: str,
    media_type: MediaType,
    now: datetime,
) -> LocalPreprocessing:
    timestamp = now.isoformat()
    stages = [PreprocessingStageState(name=name) for name in stage_order_for(media_type)]
    return LocalPreprocessing(
        id=preprocessing_id,
        sourceReferenceMediaId=reference_media_id,
        mediaType=media_type,
        status="queued",
        stages=stages,
        queuedAt=timestamp,
        updatedAt=timestamp,
    )
