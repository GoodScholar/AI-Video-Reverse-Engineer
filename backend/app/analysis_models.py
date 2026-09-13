from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .reference_video import validate_storage_id


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRange(_StrictModel):
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)

    @model_validator(mode="after")
    def start_must_precede_end(self):
        if self.startSeconds >= self.endSeconds:
            raise ValueError("时间范围的开始必须早于结束")
        return self


class AnalysisEntry(_StrictModel):
    id: str
    kind: Literal["observation", "recommendation", "unsupported"]
    summary: str = Field(min_length=1, max_length=2000)
    timeRange: TimeRange
    confidence: Literal["high", "medium", "low"]
    confidenceReason: str = Field(min_length=1, max_length=1000)

    @field_validator("id")
    @classmethod
    def id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("分析条目 ID 不能为空白")
        return value


class ReproducibilityCheck(AnalysisEntry):
    criterion: Literal[
        "single_shot", "motion_range",
        "primary_subject_count", "complex_interaction",
    ]
    status: Literal["passed", "failed", "pending", "not_assessed"]
    evidence: str


class MergedReproducibilityAssessment(_StrictModel):
    status: Literal["in_scope", "out_of_scope"]
    checks: list[ReproducibilityCheck]


class StructuredAnalysis(_StrictModel):
    version: int = Field(ge=1)
    status: Literal["in_scope", "out_of_scope"]
    referenceVideoDurationSeconds: float = Field(gt=0)
    basicFacts: list[AnalysisEntry] = Field(min_length=1)
    suitability: list[ReproducibilityCheck] = Field(min_length=1)
    subject: list[AnalysisEntry] = Field(min_length=1)
    scene: list[AnalysisEntry] = Field(min_length=1)
    action: list[AnalysisEntry] = Field(min_length=1)
    camera: list[AnalysisEntry] = Field(min_length=1)
    lighting: list[AnalysisEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def time_ranges_must_fit_the_reference_video(self):
        criteria = {check.criterion for check in self.suitability}
        if criteria != {
            "single_shot", "motion_range",
            "primary_subject_count", "complex_interaction",
        } or len(self.suitability) != 4:
            raise ValueError("适用性检查必须包含且仅包含四项标准判断")
        for entries in (
            self.basicFacts, self.suitability, self.subject, self.scene, self.action,
            self.camera, self.lighting,
        ):
            if any(entry.timeRange.endSeconds > self.referenceVideoDurationSeconds for entry in entries):
                raise ValueError("分析条目的时间范围超出参考视频时长")
        entry_ids = [
            entry.id
            for entries in (
                self.basicFacts, self.suitability, self.subject, self.scene,
                self.action, self.camera, self.lighting,
            )
            for entry in entries
        ]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("分析条目 ID 必须在结果中全局唯一")
        if any(check.status == "failed" for check in self.suitability):
            expected_status = "out_of_scope"
        elif all(check.status == "passed" for check in self.suitability):
            expected_status = "in_scope"
        else:
            raise ValueError("适用性检查必须全部通过或至少一项失败")
        if self.status != expected_status:
            raise ValueError("结构化分析状态必须与适用性检查一致")
        return self


class SemanticAnalysisError(_StrictModel):
    code: Literal[
        "provider_unconfigured", "authentication_failed", "rate_limited",
        "network_error", "timeout", "unsupported_model_capability",
        "content_rejected", "invalid_response", "provider_error",
        "analysis_interrupted",
    ]
    message: str = Field(min_length=1)
    retryable: bool


class SemanticAnalysisTask(_StrictModel):
    id: str = Field(min_length=1)
    providerId: str = Field(min_length=1)
    modelId: str = Field(min_length=1)
    sourceReferenceVideoId: str = Field(min_length=1)
    sourcePreprocessingId: str = Field(min_length=1)
    status: Literal["queued", "running", "completed", "failed"]
    queuedAt: str
    startedAt: Optional[str] = None
    updatedAt: str
    completedAt: Optional[str] = None
    result: Optional[StructuredAnalysis] = None
    error: Optional[SemanticAnalysisError] = None

    @field_validator("id", "sourceReferenceVideoId", "sourcePreprocessingId")
    @classmethod
    def ids_must_be_safe_storage_segments(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("任务和来源 ID 不能为空白")
        return validate_storage_id(value)

    @field_validator("queuedAt", "startedAt", "updatedAt", "completedAt")
    @classmethod
    def timestamps_must_be_timezone_aware_iso_datetimes(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("语义分析时间戳无效") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("语义分析时间戳必须包含时区")
        return timestamp.isoformat()


def merge_reproducibility(
    local_assessment,
    provider_checks: list[ReproducibilityCheck],
    reference_video_duration_seconds: float,
) -> MergedReproducibilityAssessment:
    local_checks = {check.criterion: check for check in local_assessment.checks}
    semantic_checks = {check.criterion: check for check in provider_checks}
    if set(semantic_checks) != {"primary_subject_count", "complex_interaction"} or len(provider_checks) != 2:
        raise ValueError("语义分析只能提供主体数量和复杂交互检查")
    checks = [
        ReproducibilityCheck(
            id=f"local-{criterion}",
            kind="observation",
            summary=local_checks[criterion].message,
            timeRange={
                "startSeconds": 0.0,
                "endSeconds": reference_video_duration_seconds,
            },
            confidence="high",
            confidenceReason=local_checks[criterion].evidence,
            criterion=criterion,
            status=local_checks[criterion].status,
            evidence=local_checks[criterion].evidence,
        )
        if criterion in {"single_shot", "motion_range"}
        else semantic_checks[criterion]
        for criterion in (
            "single_shot", "motion_range",
            "primary_subject_count", "complex_interaction",
        )
    ]
    return MergedReproducibilityAssessment(
        status="out_of_scope" if any(check.status == "failed" for check in checks) else "in_scope",
        checks=checks,
    )


def new_semantic_analysis(
    analysis_id: str,
    provider_id: str,
    model_id: str,
    source_reference_video_id: str,
    source_preprocessing_id: str,
    now: datetime,
) -> SemanticAnalysisTask:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("语义分析时间必须包含时区")
    timestamp = now.isoformat()
    return SemanticAnalysisTask(
        id=analysis_id,
        providerId=provider_id,
        modelId=model_id,
        sourceReferenceVideoId=source_reference_video_id,
        sourcePreprocessingId=source_preprocessing_id,
        status="queued",
        queuedAt=timestamp,
        updatedAt=timestamp,
    )
