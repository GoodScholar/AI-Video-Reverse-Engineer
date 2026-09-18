"""Bounded local-media transforms used by the preproduction workbench."""
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


FFMPEG_TIMEOUT_SECONDS = 60.0
FFPROBE_TIMEOUT_SECONDS = 15.0
MAX_MEDIA_DURATION_SECONDS = 300.0
MAX_OUTPUT_DURATION_SECONDS = 120.0
MAX_OUTPUT_DIMENSION = 4096
MAX_OUTPUT_PIXELS = 16_777_216
MAX_PROMPT_BYTES = 32_000
_IMAGE_FORMATS = frozenset({
    "apng", "avif", "bmp_pipe", "gif", "ico", "image2", "image2pipe",
    "jpeg_pipe", "jxl_pipe", "png_pipe", "tiff_pipe", "webp_pipe",
})


class NodeExecutionError(Exception):
    """A media-node failure that is safe to expose through the API."""


@dataclass(frozen=True)
class _MediaInfo:
    kind: str
    duration: Optional[float]
    width: Optional[int]
    height: Optional[int]


def execute_node(
    kind: str,
    source: Optional[Path],
    destination: Path,
    params: dict,
    *,
    ffmpeg_path: str,
    ffprobe_path: str,
) -> list[str]:
    """Run one supported node and return only the artifact's basename.

    The caller owns source resolution and supplies a fresh per-run destination.
    This function never interpolates shell strings and removes partial artifacts
    when a command or validation step fails.
    """
    if kind not in {"reference", "trim", "first_frame", "last_frame", "crop", "resize", "prompt"}:
        raise NodeExecutionError("不支持的工作台节点类型。")
    if not isinstance(params, dict):
        raise NodeExecutionError("节点参数必须是对象。")
    _prepare_destination(destination)

    try:
        if kind == "prompt":
            return _write_prompt(destination, params)

        media = _load_source(source, ffmpeg_path, ffprobe_path)
        if kind == "reference":
            return _copy_reference(source, destination)
        if kind == "trim":
            return _trim(source, destination, params, media, ffmpeg_path, ffprobe_path)
        if kind in {"first_frame", "last_frame"}:
            return _extract_frame(kind, source, destination, params, media, ffmpeg_path, ffprobe_path)
        if kind == "crop":
            return _crop(source, destination, params, media, ffmpeg_path, ffprobe_path)
        return _resize(source, destination, params, media, ffmpeg_path, ffprobe_path)
    except NodeExecutionError:
        _clear_destination(destination)
        raise
    except (OSError, ValueError, TypeError) as error:
        _clear_destination(destination)
        raise NodeExecutionError("节点执行失败，请检查输入素材和参数后重试。") from error


def _prepare_destination(destination: Path) -> None:
    if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise NodeExecutionError("节点产物目录无效。")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise NodeExecutionError("节点产物目录必须为空。")


def _clear_destination(destination: Path) -> None:
    if not destination.is_dir() or destination.is_symlink():
        return
    for child in destination.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError:
            pass


def _load_source(source: Optional[Path], ffmpeg_path: str, ffprobe_path: str) -> _MediaInfo:
    if source is None or not source.is_file() or source.is_symlink():
        raise NodeExecutionError("节点输入素材不可用。")
    media = _probe_media(source, ffprobe_path)
    _verify_decodable(source, media.kind, ffmpeg_path)
    return media


def _probe_media(source: Path, ffprobe_path: str) -> _MediaInfo:
    command = [
        ffprobe_path, "-v", "error", "-show_entries",
        "stream=codec_type,width,height:stream_disposition=attached_pic:format=format_name,duration", "-of", "json", str(source),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT_SECONDS,
            check=False, shell=False,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as error:
        raise NodeExecutionError("本地 FFmpeg 探测工具不可用。") from error
    if result.returncode != 0:
        raise NodeExecutionError("输入素材无法读取。")
    try:
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        format_data = payload["format"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise NodeExecutionError("输入素材探测结果无效。") from error
    if not isinstance(streams, list) or not isinstance(format_data, dict):
        raise NodeExecutionError("输入素材探测结果无效。")

    video = next((
        item for item in streams
        if isinstance(item, dict) and item.get("codec_type") == "video" and not _is_attached_picture(item)
    ), None)
    audio = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"), None)
    if video is not None:
        width, height = _dimensions(video)
        format_names = {
            item.strip().lower() for item in str(format_data.get("format_name", "")).split(",") if item.strip()
        }
        if format_names.intersection(_IMAGE_FORMATS):
            return _MediaInfo("image", None, width, height)
        duration = _duration(format_data)
        _validate_duration(duration)
        return _MediaInfo("video", duration, width, height)
    if audio is not None:
        duration = _duration(format_data)
        _validate_duration(duration)
        return _MediaInfo("audio", duration, None, None)
    raise NodeExecutionError("仅支持可解码的图片、视频或音频素材。")


def _is_attached_picture(stream: dict) -> bool:
    disposition = stream.get("disposition")
    return isinstance(disposition, dict) and disposition.get("attached_pic") == 1


def _dimensions(stream: dict) -> tuple[int, int]:
    width, height = stream.get("width"), stream.get("height")
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
        raise NodeExecutionError("输入素材尺寸无效。")
    if not 0 < width <= MAX_OUTPUT_DIMENSION or not 0 < height <= MAX_OUTPUT_DIMENSION:
        raise NodeExecutionError("输入素材尺寸超出工作台允许范围。")
    if width * height > MAX_OUTPUT_PIXELS:
        raise NodeExecutionError("输入素材像素数超出工作台允许范围。")
    return width, height


def _duration(format_data: dict) -> float:
    try:
        duration = float(format_data["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise NodeExecutionError("输入媒体时长无效。") from error
    return duration


def _validate_duration(duration: float) -> None:
    if not math.isfinite(duration) or not 0 < duration <= MAX_MEDIA_DURATION_SECONDS:
        raise NodeExecutionError("输入媒体时长超出工作台允许范围。")


def _verify_decodable(source: Path, media_kind: str, ffmpeg_path: str) -> None:
    if media_kind == "audio":
        mapping = ["-map", "0:a:0", "-t", "0.1"]
    else:
        mapping = ["-map", "0:v:0", "-frames:v", "1", "-an"]
    _run_ffmpeg(
        [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i", str(source), *mapping, "-f", "null", "-"],
        "输入素材解码失败。",
    )


def _write_prompt(destination: Path, params: dict) -> list[str]:
    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        raise NodeExecutionError("提示词节点需要非空文本。")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_PROMPT_BYTES:
        raise NodeExecutionError("提示词文本过长。")
    (destination / "prompt.txt").write_bytes(encoded)
    return ["prompt.txt"]


def _copy_reference(source: Path, destination: Path) -> list[str]:
    suffix = source.suffix.lower()
    if not suffix or len(suffix) > 16 or any(character not in ".abcdefghijklmnopqrstuvwxyz0123456789" for character in suffix):
        raise NodeExecutionError("输入素材扩展名无效。")
    name = "reference" + suffix
    shutil.copyfile(source, destination / name)
    return [name]


def _trim(source: Path, destination: Path, params: dict, media: _MediaInfo, ffmpeg_path: str, ffprobe_path: str) -> list[str]:
    if media.kind not in {"video", "audio"}:
        raise NodeExecutionError("裁剪节点仅支持视频或音频素材。")
    start, end = _trim_bounds(params, media.duration)
    duration = end - start
    if media.kind == "video":
        output = destination / "output.mp4"
        command = [
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", _number(start), "-i", str(source),
            "-t", _number(duration), "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", str(output),
        ]
    else:
        output = destination / "output.m4a"
        command = [
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", _number(start), "-i", str(source),
            "-t", _number(duration), "-map", "0:a:0", "-c:a", "aac", str(output),
        ]
    _run_ffmpeg(command, "媒体裁剪失败。")
    _verify_output(output, ffprobe_path)
    return [output.name]


def _trim_bounds(params: dict, source_duration: Optional[float]) -> tuple[float, float]:
    start, end = _finite_number(params.get("start"), "裁剪起点"), _finite_number(params.get("end"), "裁剪终点")
    if source_duration is None or start < 0 or not start < end <= source_duration or end - start > MAX_OUTPUT_DURATION_SECONDS:
        raise NodeExecutionError("裁剪时间范围无效或超出允许长度。")
    return start, end


def _extract_frame(kind: str, source: Path, destination: Path, params: dict, media: _MediaInfo, ffmpeg_path: str, ffprobe_path: str) -> list[str]:
    if params:
        raise NodeExecutionError("首尾帧节点不接受参数。")
    if media.kind != "video":
        raise NodeExecutionError("首尾帧节点仅支持视频素材。")
    output = destination / "output.png"
    before_input = ["-sseof", "-1"] if kind == "last_frame" else []
    filters = ["-vf", "reverse"] if kind == "last_frame" else []
    _run_ffmpeg(
        [
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", *before_input, "-i", str(source),
            "-map", "0:v:0", "-an", *filters, "-frames:v", "1", "-c:v", "png", str(output),
        ],
        "视频帧提取失败。",
    )
    if kind == "last_frame" and not output.is_file():
        # Sparse/VFR video can have no frame in its final second. Decode in
        # order and overwrite one image, without buffering the entire video.
        _run_ffmpeg(
            [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(source),
             "-map", "0:v:0", "-an", "-fps_mode", "passthrough", "-update", "1", "-c:v", "png", str(output)],
            "视频尾帧提取失败。",
        )
    _verify_output(output, ffprobe_path)
    return [output.name]


def _crop(source: Path, destination: Path, params: dict, media: _MediaInfo, ffmpeg_path: str, ffprobe_path: str) -> list[str]:
    if media.kind not in {"image", "video"}:
        raise NodeExecutionError("裁切节点仅支持图片或视频素材。")
    x, y, width, height = (_finite_number(params.get(key), "裁切参数") for key in ("x", "y", "width", "height"))
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise NodeExecutionError("裁切参数必须是 0 到 1 范围内的归一化矩形。")
    output_width = max(1, round(media.width * width))
    output_height = max(1, round(media.height * height))
    left, top = round(media.width * x), round(media.height * y)
    if left + output_width > media.width or top + output_height > media.height:
        raise NodeExecutionError("裁切区域超出输入素材范围。")
    if media.kind == "video":
        output_width -= output_width % 2
        output_height -= output_height % 2
        if output_width < 2 or output_height < 2:
            raise NodeExecutionError("视频裁切尺寸过小。")
    filter_value = "crop={}:{}:{}:{}".format(output_width, output_height, left, top)
    return _transform_visual(source, destination, filter_value, media.kind, ffmpeg_path, ffprobe_path)


def _resize(source: Path, destination: Path, params: dict, media: _MediaInfo, ffmpeg_path: str, ffprobe_path: str) -> list[str]:
    if media.kind not in {"image", "video"}:
        raise NodeExecutionError("缩放节点仅支持图片或视频素材。")
    width = _positive_int(params.get("width"), "缩放宽度")
    height = _positive_int(params.get("height"), "缩放高度")
    if width > MAX_OUTPUT_DIMENSION or height > MAX_OUTPUT_DIMENSION or width * height > MAX_OUTPUT_PIXELS:
        raise NodeExecutionError("缩放输出尺寸超出工作台允许范围。")
    if media.kind == "video" and (width % 2 or height % 2):
        raise NodeExecutionError("视频缩放宽高必须为偶数像素。")
    filter_value = (
        "scale={}:{}:force_original_aspect_ratio=decrease,"
        "pad={}:{}:(ow-iw)/2:(oh-ih)/2:color=black"
    ).format(width, height, width, height)
    return _transform_visual(source, destination, filter_value, media.kind, ffmpeg_path, ffprobe_path)


def _transform_visual(source: Path, destination: Path, filter_value: str, media_kind: str, ffmpeg_path: str, ffprobe_path: str) -> list[str]:
    if media_kind == "image":
        output = destination / "output.png"
        command = [
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0",
            "-an", "-vf", filter_value, "-frames:v", "1", "-c:v", "png", str(output),
        ]
    else:
        output = destination / "output.mp4"
        command = [
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0",
            "-map", "0:a?", "-vf", filter_value, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-movflags", "+faststart", str(output),
        ]
    _run_ffmpeg(command, "图片或视频处理失败。")
    _verify_output(output, ffprobe_path)
    return [output.name]


def _verify_output(output: Path, ffprobe_path: str) -> None:
    if not output.is_file() or output.stat().st_size == 0:
        raise NodeExecutionError("节点未生成有效产物。")
    _probe_media(output, ffprobe_path)


def _run_ffmpeg(command: list[str], message: str) -> None:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False, shell=False,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as error:
        raise NodeExecutionError("本地 FFmpeg 不可用或处理超时。") from error
    if result.returncode != 0:
        raise NodeExecutionError(message)


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise NodeExecutionError(label + "必须是有限数字。")
    return float(value)


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise NodeExecutionError(label + "必须是正整数。")
    return value


def _number(value: float) -> str:
    return "{:.6f}".format(value)
