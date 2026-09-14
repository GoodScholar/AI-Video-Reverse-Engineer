from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .reference_video import validate_storage_id


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StaticVisualFacts(_StrictModel):
    subject: str = Field(min_length=1)
    scene: str = Field(min_length=1)
    composition: str = Field(min_length=1)
    viewpoint: str = Field(min_length=1)
    lighting: str = Field(min_length=1)
    color: str = Field(min_length=1)
    visualStyle: str = Field(min_length=1)


class TemporalFacts(_StrictModel):
    subjectMotion: str = Field(min_length=1)
    environmentalMotion: str = Field(min_length=1)
    cameraMotion: str = Field(min_length=1)
    rhythm: str = Field(min_length=1)


class ObservedFacts(_StrictModel):
    staticVisual: StaticVisualFacts
    temporal: Optional[TemporalFacts]


class GenerationSuggestions(_StrictModel):
    subjectMotion: str = Field(min_length=1)
    environmentalMotion: str = Field(min_length=1)
    cameraMotion: str = Field(min_length=1)
    rhythm: str = Field(min_length=1)
    suggestedDuration: float = Field(gt=0)
    audio: str = Field(min_length=1)


class StructuredVisualAnalysis(_StrictModel):
    version: int = Field(default=1, ge=1)
    observedFacts: ObservedFacts
    generationSuggestions: GenerationSuggestions


class SemanticAnalysisError(_StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool


class SemanticAnalysis(_StrictModel):
    id: str = Field(min_length=1)
    sourceReferenceMediaId: str = Field(min_length=1)
    sourcePreprocessingId: str = Field(min_length=1)
    provider: Literal[
        "bailian", "openai", "doubao", "gemini", "grok", "claude", "chatanywhere", "local_openai_compatible",
    ]
    model: str = Field(min_length=1)
    promptVersion: int = Field(ge=1)
    schemaVersion: int = Field(default=1, ge=1)
    status: Literal["queued", "running", "completed", "failed"]
    createdAt: str
    startedAt: Optional[str] = None
    updatedAt: str
    completedAt: Optional[str] = None
    result: Optional[StructuredVisualAnalysis] = None
    error: Optional[SemanticAnalysisError] = None

    @field_validator("id", "sourceReferenceMediaId", "sourcePreprocessingId")
    @classmethod
    def ids_must_be_safe_storage_segments(cls, value: str) -> str:
        return validate_storage_id(value)

    @field_validator("createdAt", "startedAt", "updatedAt", "completedAt")
    @classmethod
    def timestamps_must_be_timezone_aware_iso_datetimes(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("语义分析时间戳无效") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("语义分析时间戳必须包含时区")
        return value


def validate_analysis_for_media(
    payload: object, media_type: Literal["image", "video"],
) -> StructuredVisualAnalysis:
    if media_type not in {"image", "video"}:
        raise ValueError("不支持的媒体类型")
    analysis = StructuredVisualAnalysis.model_validate(payload)
    if media_type == "image" and analysis.observedFacts.temporal is not None:
        raise ValidationError.from_exception_data(
            "StructuredVisualAnalysis",
            [{
                "type": "value_error",
                "loc": ("observedFacts", "temporal"),
                "input": analysis.observedFacts.temporal.model_dump(),
                "ctx": {"error": ValueError("图片语义分析不能包含时间观察事实")},
            }],
        )
    return analysis
