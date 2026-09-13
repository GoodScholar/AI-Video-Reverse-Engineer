from datetime import datetime
import math
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .reference_video import validate_storage_id


DEPTH_ALGORITHM_VERSION = 1

DepthStageName = Literal[
    "preparing", "estimatingDepth", "encoding", "qualityAssessment",
]
DEPTH_STAGE_ORDER: tuple[DepthStageName, DepthStageName, DepthStageName, DepthStageName] = (
    "preparing", "estimatingDepth", "encoding", "qualityAssessment",
)
DepthQualityCriterion = Literal[
    "completeness", "dynamicRange", "temporalFlicker", "directionStability",
    "edgeContinuity", "timelineAlignment",
]
DEPTH_QUALITY_CHECK_ORDER: tuple[
    DepthQualityCriterion, DepthQualityCriterion, DepthQualityCriterion,
    DepthQualityCriterion, DepthQualityCriterion, DepthQualityCriterion,
] = (
    "completeness", "dynamicRange", "temporalFlicker", "directionStability",
    "edgeContinuity", "timelineAlignment",
)


class DepthQualityCheck(BaseModel):
    criterion: DepthQualityCriterion
    status: Literal["passed", "review_required", "failed"]
    message: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    metric: float
    threshold: float
    sampleTimestamps: list[float] = Field(max_length=8)

    @field_validator("metric", "threshold")
    @classmethod
    def metrics_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("深度质量指标必须为有限数值")
        return value

    @field_validator("sampleTimestamps")
    @classmethod
    def sample_timestamps_must_be_finite(cls, values: list[float]) -> list[float]:
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("深度质量采样时间必须为非负有限数值")
        return values


class DepthQualityAssessment(BaseModel):
    status: Literal["passed", "review_required", "failed"]
    checks: list[DepthQualityCheck]
    thresholdVersion: Literal[1] = 1

    @model_validator(mode="after")
    def checks_must_have_fixed_order_and_status(self) -> "DepthQualityAssessment":
        if [check.criterion for check in self.checks] != list(DEPTH_QUALITY_CHECK_ORDER):
            raise ValueError("深度质量检查顺序无效")
        statuses = {check.status for check in self.checks}
        expected = "failed" if "failed" in statuses else "review_required" if "review_required" in statuses else "passed"
        if self.status != expected:
            raise ValueError("深度质量汇总状态无效")
        return self


class DepthModelIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    modelId: Literal["video-depth-anything-small-relative"] = "video-depth-anything-small-relative"
    upstreamCommit: Literal["4f5ae23172ba60fd7bc11ef671cca678842c7072"] = (
        "4f5ae23172ba60fd7bc11ef671cca678842c7072"
    )
    checkpointSha256: Literal[
        "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609"
    ] = "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609"


class DepthOutputSummary(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frameRate: float = Field(gt=0)
    frameCount: int = Field(ge=0)
    durationSeconds: float = Field(ge=0)


class DepthCaptureStage(BaseModel):
    name: DepthStageName
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None

    @field_validator("startedAt", "completedAt")
    @classmethod
    def timestamps_must_be_timezone_aware_iso_datetimes(cls, value: Optional[str]) -> Optional[str]:
        return _validate_timezone_aware_iso_datetime(value)


DepthStageState = DepthCaptureStage


class DepthCaptureError(BaseModel):
    code: str
    message: str
    stage: DepthStageName
    retryable: Literal[True] = True


class DepthCapture(BaseModel):
    id: str
    sourceReferenceVideoId: str
    algorithmVersion: Literal[1] = DEPTH_ALGORITHM_VERSION
    status: Literal["queued", "running", "completed", "failed"]
    devicePreference: Literal["auto", "cuda", "mps", "cpu"]
    executionDevice: Optional[Literal["cuda", "mps", "cpu"]] = None
    modelIdentity: DepthModelIdentity = Field(default_factory=DepthModelIdentity)
    normalizationDirection: Literal["near_white_far_black"] = "near_white_far_black"
    currentStage: Optional[DepthStageName] = None
    stages: list[DepthStageState]
    outputSummary: Optional[DepthOutputSummary] = None
    qualityAssessment: Optional[DepthQualityAssessment] = None
    reviewConfirmedAt: Optional[str] = None
    error: Optional[DepthCaptureError] = None
    queuedAt: str
    startedAt: Optional[str] = None
    updatedAt: str
    completedAt: Optional[str] = None

    @field_validator("id", "sourceReferenceVideoId")
    @classmethod
    def ids_must_be_safe_storage_segments(cls, value: str) -> str:
        return validate_storage_id(value)

    @field_validator("queuedAt", "startedAt", "updatedAt", "completedAt", "reviewConfirmedAt")
    @classmethod
    def timestamps_must_be_timezone_aware_iso_datetimes(cls, value: Optional[str]) -> Optional[str]:
        return _validate_timezone_aware_iso_datetime(value)

    @model_validator(mode="after")
    def stages_must_have_fixed_order(self) -> "DepthCapture":
        if [stage.name for stage in self.stages] != list(DEPTH_STAGE_ORDER):
            raise ValueError("深度捕捉阶段顺序无效")
        return self


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


def new_depth_capture(
    reference_video_id: str,
    device_preference: Literal["auto", "cuda", "mps", "cpu"],
    queued_at: str,
) -> DepthCapture:
    stages = [DepthCaptureStage(name=name) for name in DEPTH_STAGE_ORDER]
    return DepthCapture(
        id=str(uuid4()),
        sourceReferenceVideoId=reference_video_id,
        status="queued",
        devicePreference=device_preference,
        stages=stages,
        queuedAt=queued_at,
        updatedAt=queued_at,
    )


def select_execution_device(
    preference: Literal["auto", "cuda", "mps", "cpu"], *, cuda: bool, mps: bool
) -> Literal["cuda", "mps", "cpu"]:
    if preference == "auto":
        if cuda:
            return "cuda"
        if mps:
            return "mps"
        return "cpu"
    if (preference == "cuda" and not cuda) or (preference == "mps" and not mps):
        raise ValueError("所选深度计算设备不可用")
    return preference
