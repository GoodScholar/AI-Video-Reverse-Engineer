import json
import math
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


MAX_REFERENCE_VIDEO_BYTES = 200_000_000
MAX_MULTIPART_BODY_BYTES = 201_000_000
DEFAULT_FFPROBE_TIMEOUT_SECONDS = 30.0
_MP4_MAJOR_BRANDS = {
    "isom", "iso2", "iso3", "iso4", "iso5", "iso6", "mp41", "mp42", "avc1",
}


@dataclass(frozen=True)
class ProbedReferenceVideo:
    format: Literal["mp4", "mov"]
    duration_seconds: float
    width: int
    height: int
    frame_rate: float


class ReferenceVideo(BaseModel):
    type: Literal["video"] = "video"
    id: str = Field(min_length=1)
    originalName: str = Field(min_length=1)
    format: Literal["mp4", "mov"]
    sizeBytes: int = Field(gt=0)
    durationSeconds: float = Field(ge=2.0, le=10.0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frameRate: float = Field(gt=0)

    @field_validator("id")
    @classmethod
    def id_must_be_a_safe_storage_segment(cls, value: str) -> str:
        return validate_storage_id(value)


class ReferenceVideoError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def validate_storage_id(value: str) -> str:
    if not value or value in {".", ".."} or any(character in value for character in ("/", "\\", "\x00")):
        raise ValueError("存储标识符必须是安全的单段名称")
    return value


def reference_video_basename(original_name: str) -> str:
    basename = original_name.replace("\\", "/").rsplit("/", 1)[-1]
    if not basename:
        raise ReferenceVideoError(415, "unsupported_video_format", "参考视频文件名不能为空。")
    return basename


def reference_video_format_from_name(original_name: str) -> Literal["mp4", "mov"]:
    extension = Path(reference_video_basename(original_name)).suffix.lower()
    if extension not in {".mp4", ".mov"}:
        raise ReferenceVideoError(
            415,
            "unsupported_video_format",
            f"仅支持 MP4 或 MOV，当前文件扩展名为 {extension or '无扩展名'}。",
        )
    return "mov" if extension == ".mov" else "mp4"


def reference_video_format_from_major_brand(
    major_brand: Union[str, bytes, None],
) -> Optional[Literal["mp4", "mov"]]:
    """Map an authoritative ISO-BMFF major brand to an accepted persisted format."""
    if major_brand is None:
        return None
    if isinstance(major_brand, bytes):
        try:
            major_brand = major_brand.decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("invalid ISO-BMFF major brand") from error
    if not isinstance(major_brand, str):
        raise ValueError("invalid ISO-BMFF major brand")
    if major_brand == "qt  ":
        return "mov"
    if major_brand in _MP4_MAJOR_BRANDS:
        return "mp4"
    raise ValueError("unsupported ISO-BMFF major brand")


def probe_reference_video(
    path: Path,
    original_name: str,
    size_bytes: int,
    *,
    ffprobe_path: str,
    timeout_seconds: float,
) -> ProbedReferenceVideo:
    expected_format = reference_video_format_from_name(original_name)
    if size_bytes > MAX_REFERENCE_VIDEO_BYTES:
        raise ReferenceVideoError(
            413,
            "video_too_large",
            f"参考视频实测为 {size_bytes:,} 字节，最大允许 200,000,000 字节。",
        )

    command = [
        ffprobe_path,
        "-v",
        "error",
        "-count_frames",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as error:
        raise ReferenceVideoError(
            503,
            "ffprobe_unavailable",
            "本地未找到 ffprobe，请安装 FFmpeg 并确认 ffprobe -version 可运行。",
        ) from error
    except OSError as error:
        raise ReferenceVideoError(
            503,
            "ffprobe_unavailable",
            "本地无法启动 ffprobe，请检查 FFmpeg 安装和执行权限。",
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ReferenceVideoError(
            422,
            "video_unreadable",
            f"参考视频在 {timeout_seconds:g} 秒内未能完成读取，请检查文件是否损坏。",
        ) from error

    return _validate_probe_result(result, expected_format)


def _validate_probe_result(
    result: subprocess.CompletedProcess[str], expected_format: Literal["mp4", "mov"]
) -> ProbedReferenceVideo:
    if result.returncode != 0:
        _raise_unreadable()

    try:
        payload = json.loads(result.stdout)
        probe_format = payload["format"]
        streams = payload["streams"]
        format_name = probe_format["format_name"]
    except (KeyError, TypeError, json.JSONDecodeError):
        _raise_unreadable()

    if not isinstance(probe_format, dict) or not isinstance(streams, list) or not isinstance(format_name, str):
        _raise_unreadable()

    try:
        authoritative_format = reference_video_format_from_major_brand(
            _major_brand_from_probe_format(probe_format),
        )
    except ValueError as error:
        raise ReferenceVideoError(
            415,
            "unsupported_video_format",
            "ffprobe 报告的容器品牌不属于受支持的 MP4 或 MOV。",
        ) from error

    container_names = {name.strip() for name in format_name.split(",")}
    if not container_names.intersection({"mov", "mp4"}):
        raise ReferenceVideoError(
            415,
            "unsupported_video_format",
            f"探测到的容器格式为 {format_name}，仅支持 MP4 或 MOV。",
        )

    video_stream = _first_video_stream(streams)
    try:
        width = _positive_int(video_stream["width"])
        height = _positive_int(video_stream["height"])
        frame_count = _positive_int(video_stream["nb_read_frames"])
        duration_seconds = _duration_seconds(probe_format, video_stream)
    except (KeyError, TypeError, ValueError):
        _raise_unreadable()

    if frame_count <= 0 or not math.isfinite(duration_seconds):
        _raise_unreadable()

    rotation = _rotation_degrees(video_stream)
    if abs(round(rotation)) % 180 == 90:
        width, height = height, width

    frame_rate = _frame_rate(video_stream)

    if duration_seconds < 2:
        raise ReferenceVideoError(
            422,
            "video_too_short",
            f"参考视频实测时长为 {duration_seconds:.2f} 秒，最短允许 2.00 秒。",
        )
    if duration_seconds > 10:
        raise ReferenceVideoError(
            422,
            "video_too_long",
            f"参考视频实测时长为 {duration_seconds:.2f} 秒，最长允许 10.00 秒。",
        )
    if min(width, height) < 480:
        raise ReferenceVideoError(
            422,
            "video_resolution_too_low",
            f"参考视频旋转后的显示分辨率为 {width}×{height}，短边至少需要 480 像素。",
        )
    if _exceeds_maximum_resolution(width, height):
        raise ReferenceVideoError(
            422,
            "video_resolution_too_high",
            f"参考视频旋转后的显示分辨率为 {width}×{height}，最高支持 UHD 4K。",
        )
    if frame_rate is None:
        raise ReferenceVideoError(
            422,
            "video_frame_rate_invalid",
            "参考视频帧率无效，无法读取有效帧率。",
        )

    return ProbedReferenceVideo(
        format=authoritative_format or expected_format,
        duration_seconds=duration_seconds,
        width=width,
        height=height,
        frame_rate=frame_rate,
    )


def _major_brand_from_probe_format(probe_format: dict) -> object:
    tags = probe_format.get("tags")
    return tags.get("major_brand") if isinstance(tags, dict) else None


def _raise_unreadable() -> None:
    raise ReferenceVideoError(
        422,
        "video_unreadable",
        "参考视频无法读取有效视频帧或所需媒体信息。",
    )


def _first_video_stream(streams: list[object]) -> dict:
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        disposition = stream.get("disposition", {})
        if isinstance(disposition, dict) and disposition.get("attached_pic"):
            continue
        return stream
    _raise_unreadable()


def _positive_int(value: object) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError
    return parsed


def _duration_seconds(probe_format: dict, video_stream: dict) -> float:
    value = probe_format.get("duration", video_stream.get("duration"))
    duration_seconds = float(value)
    if not math.isfinite(duration_seconds):
        raise ValueError
    return duration_seconds


def _rotation_degrees(video_stream: dict) -> float:
    side_data_list = video_stream.get("side_data_list", [])
    if isinstance(side_data_list, list):
        for side_data in side_data_list:
            if isinstance(side_data, dict) and "rotation" in side_data:
                rotation = _finite_float(side_data["rotation"])
                if rotation is not None:
                    return rotation
    tags = video_stream.get("tags", {})
    if isinstance(tags, dict):
        rotation = _finite_float(tags.get("rotate"))
        if rotation is not None:
            return rotation
    return 0.0


def _finite_float(value: object, *, default: Optional[float] = None) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _frame_rate(video_stream: dict) -> Optional[float]:
    for field_name in ("avg_frame_rate", "r_frame_rate"):
        try:
            frame_rate = float(Fraction(video_stream[field_name]))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if math.isfinite(frame_rate) and frame_rate > 0:
            return frame_rate
    return None


def _exceeds_maximum_resolution(width: int, height: int) -> bool:
    if width == height:
        return width > 2160
    if width > height:
        return width > 3840 or height > 2160
    return width > 2160 or height > 3840
