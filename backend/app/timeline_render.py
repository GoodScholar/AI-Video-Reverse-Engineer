"""本地 FFmpeg 时间线渲染。

调用方负责时间线编辑校验和安全来源解析；本模块仍验证媒体可读性、
源区间和最终文件，避免把半成品发布为渲染结果。
"""

import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


_RENDER_TIMEOUT_SECONDS = 600.0
_PROBE_TIMEOUT_SECONDS = 30.0
_THREADS = "2"


class TimelineRenderError(RuntimeError):
    """时间线无法安全渲染或渲染被取消。"""


class _Media:
    def __init__(
        self,
        duration: Optional[float],
        video_duration: Optional[float],
        audio_duration: Optional[float],
        has_video: bool,
        has_audio: bool,
        image: bool,
    ):
        self.duration = duration
        self.video_duration = video_duration
        self.audio_duration = audio_duration
        self.has_video = has_video
        self.has_audio = has_audio
        self.image = image


def render_timeline(
    timeline: dict,
    sources: Dict[str, Path],
    output: Path,
    *,
    format: str,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    cancelled: Callable[[], bool] = lambda: False,
) -> None:
    """将已校验的时间线渲染为 MP4、预览 MP4 或 PCM WAV。

    输出在同目录临时文件中生成，只有编码完成、未取消且经过 ffprobe
    检验后才以原子替换方式发布到 ``output``。
    """
    if format not in {"preview", "mp4", "wav"}:
        raise TimelineRenderError("不支持的时间线输出格式。")
    if cancelled():
        raise TimelineRenderError("时间线渲染已取消。")

    settings = timeline.get("settings") or {}
    width = _positive_even(settings.get("width"), "输出宽度")
    height = _positive_even(settings.get("height"), "输出高度")
    fps = _positive_number(settings.get("fps"), "帧率")
    tracks = timeline.get("tracks")
    if not isinstance(tracks, list):
        raise TimelineRenderError("时间线轨道无效。")

    clips = _collect_clips(tracks, sources, ffprobe_path, format)
    duration = max((clip["end"] for clip in clips), default=0.0)
    if duration <= 0:
        raise TimelineRenderError("时间线没有可渲染的片段。")

    render_width, render_height = _output_size(width, height, format)
    command, expected = _build_command(
        clips, duration, render_width, render_height, fps, format, ffmpeg_path
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(".%s.%s%s" % (output.stem, uuid.uuid4().hex, output.suffix))
    command.append(str(temporary))
    try:
        _run_ffmpeg(command, cancelled)
        if cancelled():
            raise TimelineRenderError("时间线渲染已取消。")
        _verify_output(temporary, format, expected, ffprobe_path)
        if cancelled():
            raise TimelineRenderError("时间线渲染已取消。")
        temporary.replace(output)
    except TimelineRenderError:
        _remove_file(temporary)
        raise
    except OSError as error:
        _remove_file(temporary)
        raise TimelineRenderError("无法写入时间线渲染输出：%s" % error) from error


def _collect_clips(tracks: list, sources: Dict[str, Path], ffprobe_path: str, format: str) -> List[dict]:
    media_cache = {}
    result = []
    for track_index, track in enumerate(tracks):
        if not isinstance(track, dict):
            raise TimelineRenderError("时间线轨道无效。")
        kind = track.get("kind")
        if kind not in {"video", "audio"}:
            raise TimelineRenderError("时间线轨道类型无效。")
        track_clips = track.get("clips")
        if not isinstance(track_clips, list):
            raise TimelineRenderError("时间线片段无效。")
        muted = bool(track.get("muted", False))
        hidden = bool(track.get("hidden", False))
        for clip_index, clip in enumerate(track_clips):
            if not isinstance(clip, dict):
                raise TimelineRenderError("时间线片段无效。")
            asset_id = clip.get("assetId")
            source = sources.get(asset_id)
            if not isinstance(asset_id, str) or source is None:
                raise TimelineRenderError("时间线片段引用的素材不存在。")
            source = Path(source)
            if not source.is_file():
                raise TimelineRenderError("时间线片段引用的素材不存在。")
            cache_key = str(source)
            if cache_key not in media_cache:
                media_cache[cache_key] = _probe_media(source, ffprobe_path)
            media = media_cache[cache_key]
            start = _nonnegative_number(clip.get("start"), "片段起点")
            in_point = _nonnegative_number(clip.get("inPoint"), "片段入点")
            clip_duration = _positive_number(clip.get("duration"), "片段时长")
            speed = _positive_number(clip.get("speed"), "片段速度")
            volume = _nonnegative_number(clip.get("volume"), "片段音量")
            fade_in = _nonnegative_number(clip.get("fadeIn"), "音频淡入")
            fade_out = _nonnegative_number(clip.get("fadeOut"), "音频淡出")
            if fade_in > clip_duration or fade_out > clip_duration:
                raise TimelineRenderError("音频淡入淡出不能超过片段时长。")
            needed_source_duration = clip_duration * speed
            if kind == "video" and not media.has_video:
                raise TimelineRenderError("视频轨道片段不含视频流。")
            if kind == "audio" and not media.has_audio:
                raise TimelineRenderError("音频轨道片段不含音频流。")
            if format != "wav" and kind == "video" and not hidden and not media.image:
                _validate_source_range(media.video_duration, in_point, needed_source_duration, "视频流")
            if not muted and media.has_audio:
                _validate_source_range(media.audio_duration, in_point, needed_source_duration, "音频流")
            result.append({
                "source": source,
                "media": media,
                "kind": kind,
                "muted": muted,
                "hidden": hidden,
                "start": start,
                "in_point": in_point,
                "duration": clip_duration,
                "source_duration": needed_source_duration,
                "speed": speed,
                "volume": volume,
                "fade_in": fade_in,
                "fade_out": fade_out,
                "track_index": track_index,
                "clip_index": clip_index,
                "end": start + clip_duration,
            })
    return result


def _probe_media(source: Path, ffprobe_path: str) -> _Media:
    command = [
        ffprobe_path, "-v", "error", "-show_entries",
        "format=duration,format_name:stream=codec_type,duration", "-of", "json", str(source),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS)
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as error:
        raise TimelineRenderError("无法启动 ffprobe 检查媒体。") from error
    if result.returncode != 0:
        raise TimelineRenderError("无法读取时间线素材。")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise TimelineRenderError("ffprobe 返回了无效的媒体信息。") from error
    streams = payload.get("streams") or []
    kinds = {stream.get("codec_type") for stream in streams if isinstance(stream, dict)}
    has_video = "video" in kinds
    has_audio = "audio" in kinds
    if not has_video and not has_audio:
        raise TimelineRenderError("时间线素材不含可解码的音频或视频流。")
    metadata = payload.get("format") or {}
    raw_duration = metadata.get("duration")
    try:
        duration = float(raw_duration) if raw_duration not in (None, "N/A") else None
    except (TypeError, ValueError):
        duration = None
    format_name = str(metadata.get("format_name") or "")
    image = has_video and not has_audio and (duration is None or duration <= 0.001 or "image2" in format_name)
    return _Media(
        duration,
        _first_stream_duration(streams, "video"),
        _first_stream_duration(streams, "audio"),
        has_video,
        has_audio,
        image,
    )


def _first_stream_duration(streams: list, kind: str) -> Optional[float]:
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != kind:
            continue
        raw_duration = stream.get("duration")
        try:
            duration = float(raw_duration) if raw_duration not in (None, "N/A") else None
        except (TypeError, ValueError):
            duration = None
        if duration is not None and duration >= 0:
            return duration
        return None
    return None


def _validate_source_range(
    stream_duration: Optional[float], in_point: float, needed_source_duration: float, stream_name: str
) -> None:
    if stream_duration is None or in_point + needed_source_duration > stream_duration + 0.002:
        raise TimelineRenderError("片段需要的%s区间超出源媒体尾部。" % stream_name)


def _build_command(
    clips: List[dict],
    duration: float,
    width: int,
    height: int,
    fps: float,
    format: str,
    ffmpeg_path: str,
) -> Tuple[List[str], dict]:
    command = [
        ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-threads", _THREADS, "-filter_threads", _THREADS, "-filter_complex_threads", _THREADS,
    ]
    filters = []
    visual_labels = []
    audio_labels = []
    input_index = 0

    if format != "wav":
        filters.append("color=c=black:s=%dx%d:r=%s:d=%s[base0]" % (width, height, _number(fps), _number(duration)))
        for clip in clips:
            if clip["kind"] != "video" or clip["hidden"]:
                continue
            command.extend(_input_arguments(clip, fps))
            label = "v%d" % len(visual_labels)
            filters.append(_video_filter(input_index, clip, width, height, fps, label))
            visual_labels.append(label)
            input_index += 1

    for clip in clips:
        if clip["muted"] or not clip["media"].has_audio:
            continue
        command.extend(_input_arguments(clip, fps))
        label = "a%d" % len(audio_labels)
        filters.append(_audio_filter(input_index, clip, label))
        audio_labels.append(label)
        input_index += 1

    if format != "wav":
        current = "base0"
        for index, label in enumerate(visual_labels, start=1):
            next_label = "base%d" % index
            filters.append("[%s][%s]overlay=eof_action=pass:repeatlast=0:shortest=0[%s]" % (current, label, next_label))
            current = next_label
        filters.append("[%s]trim=duration=%s,setpts=PTS-STARTPTS[vout]" % (current, _number(duration)))

    if audio_labels:
        filters.append("anullsrc=r=48000:cl=stereo:d=%s[asilent]" % _number(duration))
        filters.append("[asilent]%samix=inputs=%d:duration=first:normalize=0,alimiter=limit=0.98,atrim=duration=%s[aout]" % (
            "".join("[%s]" % label for label in audio_labels), len(audio_labels) + 1, _number(duration)))
    else:
        filters.append("anullsrc=r=48000:cl=stereo:d=%s[aout]" % _number(duration))

    command.extend(["-filter_complex", ";".join(filters)])
    expected = {"duration": duration}
    if format == "wav":
        command.extend(["-map", "[aout]", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"])
        expected["video"] = False
    else:
        command.extend([
            "-map", "[vout]", "-map", "[aout]", "-t", _number(duration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
        ])
        expected.update({"video": True, "width": width, "height": height})
    return command, expected


def _input_arguments(clip: dict, fps: float) -> List[str]:
    if clip["media"].image:
        return ["-loop", "1", "-framerate", _number(fps), "-t", _number(clip["source_duration"]), "-i", str(clip["source"])]
    return ["-ss", _number(clip["in_point"]), "-t", _number(clip["source_duration"]), "-i", str(clip["source"])]


def _video_filter(index: int, clip: dict, width: int, height: int, fps: float, label: str) -> str:
    return (
        "[%d:v]setpts=PTS-STARTPTS,setpts=(PTS-STARTPTS)/%s,trim=duration=%s,"
        "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1,fps=%s,setpts=PTS-STARTPTS+%s/TB[%s]"
    ) % (
        index, _number(clip["speed"]), _number(clip["duration"]),
        width, height, width, height, _number(fps), _number(clip["start"]), label,
    )


def _audio_filter(index: int, clip: dict, label: str) -> str:
    speed_filters = ",".join("atempo=%s" % _number(part) for part in _tempo_parts(clip["speed"]))
    fade_parts = []
    if clip["fade_in"]:
        fade_parts.append("afade=t=in:st=0:d=%s" % _number(clip["fade_in"]))
    if clip["fade_out"]:
        fade_parts.append("afade=t=out:st=%s:d=%s" % (
            _number(clip["duration"] - clip["fade_out"]), _number(clip["fade_out"])))
    transform = ",".join([
        "atrim=duration=%s" % _number(clip["source_duration"]),
        "asetpts=PTS-STARTPTS",
        speed_filters,
        "atrim=duration=%s" % _number(clip["duration"]),
        "asetpts=PTS-STARTPTS",
        "volume=%s" % _number(clip["volume"]),
        *fade_parts,
        "aformat=sample_rates=48000:channel_layouts=stereo",
        "adelay=%s:all=1" % _milliseconds(clip["start"]),
    ])
    return "[%d:a]%s[%s]" % (index, transform, label)


def _tempo_parts(speed: float) -> List[float]:
    parts = []
    remaining = speed
    while remaining > 2.0 + 1e-9:
        parts.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        parts.append(0.5)
        remaining /= 0.5
    parts.append(remaining)
    return parts


def _run_ffmpeg(command: List[str], cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise TimelineRenderError("时间线渲染已取消。")
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True
        )
    except (FileNotFoundError, PermissionError) as error:
        raise TimelineRenderError("无法启动本地 FFmpeg。") from error
    deadline = time.monotonic() + _RENDER_TIMEOUT_SECONDS
    try:
        while True:
            if cancelled():
                _terminate(process)
                raise TimelineRenderError("时间线渲染已取消。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(process)
                raise TimelineRenderError("时间线渲染超时。")
            try:
                _, stderr = process.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            message = (stderr or "").strip().splitlines()
            detail = message[-1] if message else "FFmpeg 未能完成编码。"
            raise TimelineRenderError("时间线渲染失败：%s" % detail)
    finally:
        if process.poll() is None:
            _terminate(process)


def _terminate(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (AttributeError, ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, ProcessLookupError, PermissionError):
            process.kill()
        process.wait()


def _verify_output(output: Path, format: str, expected: dict, ffprobe_path: str) -> None:
    media = _probe_media(output, ffprobe_path)
    if format == "wav":
        if not media.has_audio:
            raise TimelineRenderError("WAV 输出缺少音频流。")
    elif not media.has_video or not media.has_audio:
        raise TimelineRenderError("MP4 输出缺少视频或音频流。")
    if media.duration is None or media.duration <= 0:
        raise TimelineRenderError("渲染输出时长无效。")
    if abs(media.duration - expected["duration"]) > 0.12:
        raise TimelineRenderError("渲染输出时长与时间线不一致。")


def _output_size(width: int, height: int, format: str) -> Tuple[int, int]:
    if format != "preview" or width <= 640:
        return width, height
    scaled_height = int(round(height * 640.0 / width))
    return 640, max(2, scaled_height - (scaled_height % 2))


def _positive_even(value, field: str) -> int:
    number = _positive_number(value, field)
    integer = int(number)
    if integer != number or integer % 2:
        raise TimelineRenderError("%s必须为正偶数。" % field)
    return integer


def _positive_number(value, field: str) -> float:
    number = _number_value(value, field)
    if number <= 0:
        raise TimelineRenderError("%s必须大于零。" % field)
    return number


def _nonnegative_number(value, field: str) -> float:
    number = _number_value(value, field)
    if number < 0:
        raise TimelineRenderError("%s不能小于零。" % field)
    return number


def _number_value(value, field: str) -> float:
    if isinstance(value, bool):
        raise TimelineRenderError("%s无效。" % field)
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise TimelineRenderError("%s无效。" % field) from error
    if not number == number or number in (float("inf"), float("-inf")):
        raise TimelineRenderError("%s无效。" % field)
    return number


def _number(value: float) -> str:
    return "%.9g" % value


def _milliseconds(seconds: float) -> int:
    return int(round(seconds * 1000))


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
