import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional, Union

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .local_preprocessing_storage import preprocessing_directory, validate_completed_stages

if TYPE_CHECKING:
    from .main import Project


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", ser_json_bytes="base64")


class ImageAnalysisInput(_StrictInput):
    mediaType: Literal["image"] = "image"
    analysisProxyBytes: bytes = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    aspectRatio: float = Field(gt=0)


class VideoKeyframe(_StrictInput):
    index: int = Field(ge=1)
    timeSeconds: float = Field(ge=0)


class VideoScene(_StrictInput):
    changeCount: int = Field(ge=0)
    changeTimesSeconds: list[float] = Field(default_factory=list)


class VideoMotionSample(_StrictInput):
    timeSeconds: float = Field(ge=0)
    mafd: float = Field(ge=0)


class VideoMotion(_StrictInput):
    samples: list[VideoMotionSample] = Field(default_factory=list)
    p50: Optional[float]
    p90: Optional[float]
    peak: Optional[float]
    level: Literal["light", "moderate", "high", "unavailable"]


class VideoProxy(_StrictInput):
    schemaVersion: Literal[1]
    keyframes: list[VideoKeyframe] = Field(min_length=1)
    scene: VideoScene
    motion: VideoMotion
    contactSheetFile: Literal["contact-sheet.jpg"]


class VideoAnalysisInput(_StrictInput):
    mediaType: Literal["video"] = "video"
    contactSheetBytes: bytes = Field(min_length=1)
    analysisProxy: VideoProxy


AnalysisInput = Union[ImageAnalysisInput, VideoAnalysisInput]


def build_analysis_input(data_dir: Path, project: "Project") -> AnalysisInput:
    preprocessing = project.localPreprocessing
    reference = project.referenceMedia
    if preprocessing is None or reference is None or preprocessing.status != "completed":
        raise ValueError("本地预处理尚未完成，无法构造语义分析输入")
    if preprocessing.sourceReferenceMediaId != reference.id or preprocessing.mediaType != reference.type:
        raise ValueError("本地预处理来源与参考素材不一致")

    directory = preprocessing_directory(data_dir, project.id, preprocessing.id)
    try:
        validation = validate_completed_stages(preprocessing, directory)
    except OSError as error:
        raise ValueError("本地预处理产物不可用") from error
    if validation.firstInvalidStage is not None:
        raise ValueError("本地预处理产物未通过校验")

    if preprocessing.mediaType == "image":
        return _build_image_input(directory)
    return _build_video_input(directory)


def _build_image_input(directory: Path) -> ImageAnalysisInput:
    try:
        proxy_path = directory / "analysis-proxy.jpg"
        proxy_bytes = proxy_path.read_bytes()
        with Image.open(proxy_path) as image:
            image.load()
            width, height = image.size
    except (OSError, ValueError) as error:
        raise ValueError("图片分析代理不可用") from error
    return ImageAnalysisInput(
        analysisProxyBytes=proxy_bytes,
        width=width,
        height=height,
        aspectRatio=width / height,
    )


def _build_video_input(directory: Path) -> VideoAnalysisInput:
    try:
        contact_sheet_bytes = (directory / "contact-sheet.jpg").read_bytes()
        with (directory / "analysis-proxy.json").open(encoding="utf-8") as file:
            proxy = VideoProxy.model_validate(json.load(file))
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as error:
        raise ValueError("视频分析代理不可用") from error
    return VideoAnalysisInput(contactSheetBytes=contact_sheet_bytes, analysisProxy=proxy)
