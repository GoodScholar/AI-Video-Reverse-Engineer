"""本地 FFmpeg 时间线渲染。

调用方负责时间线编辑校验和安全来源解析；本模块仍验证媒体可读性、
源区间和最终文件，避免把半成品发布为渲染结果。
"""

import json
import hashlib
import math
import os
import signal
import shutil
import subprocess
import time
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


_RENDER_TIMEOUT_SECONDS = 600.0
_PROBE_TIMEOUT_SECONDS = 30.0
_THREADS = "2"
_XFADE_TEMPLATES = {
    "dissolve": "fade",
    "push": "slideleft",
    "wipe": "wipeleft",
    "flash-white": "fadewhite",
    "pixelize": "pixelize",
}


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
        width: Optional[int],
        height: Optional[int],
        fps: Optional[float],
        sample_aspect_ratio: Optional[str],
        rotation: float,
    ):
        self.duration = duration
        self.video_duration = video_duration
        self.audio_duration = audio_duration
        self.has_video = has_video
        self.has_audio = has_audio
        self.image = image
        self.width = width
        self.height = height
        self.fps = fps
        self.sample_aspect_ratio = sample_aspect_ratio
        self.rotation = rotation


def _validate_reframe_media(media: _Media) -> None:
    if (media.width is None or media.height is None or media.sample_aspect_ratio not in (None, "1:1", "0:1")
            or not math.isfinite(media.rotation) or abs(media.rotation) > .001):
        raise TimelineRenderError("主体裁剪不支持旋转或非方形像素的源视频。")


def render_timeline(
    timeline: dict,
    sources: Dict[str, Path],
    output: Path,
    *,
    format: str,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    cancelled: Callable[[], bool] = lambda: False,
    subtitles_path: Optional[Path] = None,
    packaging: Optional[dict] = None,
    packaging_cues: Optional[list] = None,
) -> dict:
    """将已校验的时间线渲染为 MP4、预览 MP4 或 PCM WAV。

    输出在同目录临时文件中生成，只有编码完成、未取消且经过 ffprobe
    检验后才以原子替换方式发布到 ``output``。
    """
    if format not in {"preview", "mp4", "wav"}:
        raise TimelineRenderError("不支持的时间线输出格式。")
    if cancelled():
        raise TimelineRenderError("时间线渲染已取消。")
    if subtitles_path is not None and (format == "wav" or not Path(subtitles_path).is_file()):
        raise TimelineRenderError("字幕文件不可用。")
    if packaging is None:
        packaging = timeline.get("packaging")

    settings = timeline.get("settings") or {}
    width = _positive_even(settings.get("width"), "输出宽度")
    height = _positive_even(settings.get("height"), "输出高度")
    fps = _positive_number(settings.get("fps"), "帧率")
    tracks = timeline.get("tracks")
    if not isinstance(tracks, list):
        raise TimelineRenderError("时间线轨道无效。")

    clips = _collect_clips(tracks, sources, ffprobe_path, format)
    from .timeline import validate_transitions
    try:
        validate_transitions(timeline.get('transitions', []), tracks)
    except (ValueError, TypeError, KeyError) as error:
        raise TimelineRenderError(str(error)) from error
    transitions = _collect_transitions(timeline.get("transitions", []), clips)
    duration = max((clip["end"] for clip in clips), default=0.0)
    if duration <= 0:
        raise TimelineRenderError("时间线没有可渲染的片段。")

    render_width, render_height = _output_size(width, height, format)
    # An explicit packaging snapshot owns all graphics, including ordinary
    # subtitle cues.  Do not create legacy PNG captions in that mode: doing so
    # would burn the same transcript twice and introduce an uncontrolled font.
    captions = []
    if subtitles_path is not None and packaging is None:
        from .batch_subtitles import caption_images
        captions = caption_images(Path(subtitles_path), render_width, render_height)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    commands_dir = output.parent / (".reframe-" + uuid.uuid4().hex)
    temporary = output.with_name(".%s.%s%s" % (output.stem, uuid.uuid4().hex, output.suffix))
    try:
        ass_path = None
        if packaging is not None:
            if format == "wav":
                raise TimelineRenderError("WAV 不支持图文包装。")
            from .montage_packaging import PackagingError, compile_ass, private_font_path
            try:
                ass_path = output.parent / ".packaging.ass"
                ass_path.write_text(compile_ass(packaging, packaging_cues or [], tracks, width, height, private_font_path()), encoding="utf-8")
            except (OSError, PackagingError) as error:
                raise TimelineRenderError(str(error)) from error
        command, expected = _build_command(
            clips, transitions, duration, render_width, render_height, fps, format, ffmpeg_path, captions,
            reframe_commands_dir=commands_dir, ass_path=ass_path,
        )
        command.append(str(temporary))
        _run_ffmpeg(command, cancelled, cwd=commands_dir if commands_dir.exists() else (Path(subtitles_path).parent if subtitles_path is not None else None))
        if cancelled():
            raise TimelineRenderError("时间线渲染已取消。")
        media = _verify_output(temporary, format, expected, ffprobe_path)
        if cancelled():
            raise TimelineRenderError("时间线渲染已取消。")
        temporary.replace(output)
        return media
    except TimelineRenderError:
        _remove_file(temporary)
        raise
    except OSError as error:
        _remove_file(temporary)
        raise TimelineRenderError("无法写入时间线渲染输出：%s" % error) from error
    finally:
        if 'ass_path' in locals() and ass_path is not None:
            _remove_file(ass_path)
        shutil.rmtree(commands_dir, ignore_errors=True)


def inspect_timeline_media(state: dict, assets: dict, resolve_source, format: str, ffprobe_path: str, *,
                           media_cache=None, timing_cache=None) -> list:
    """Read-only preflight using the same stream/range rules as rendering."""
    from .timeline import validate_workspace
    issues = []
    media_cache = {} if media_cache is None else media_cache
    timing_cache = {} if timing_cache is None else timing_cache
    audible = False
    visible = False
    for track in state["tracks"]:
        for index, clip in enumerate(track["clips"]):
            asset = assets.get(clip["assetId"], {})
            location = {"trackId": track["id"], "clipId": clip["id"], "assetId": clip["assetId"],
                        "label": f'{track["name"]} · 片段 {index + 1} · {asset.get("name", "缺失素材")}' }
            try:
                single_track = {**track, "clips": [clip]}
                validate_workspace({"revision": state["revision"], "settings": state["settings"], "tracks": [single_track]}, assets)
                source = resolve_source(asset)
                collected = _collect_clips([single_track], {clip["assetId"]: source}, ffprobe_path, format, media_cache, timing_cache)[0]
                media = collected["media"]
                if clip.get("reframe") is not None:
                    from .montage_reframe import ReframeError, reframe_filter, source_fingerprint, validate_stored_reframe
                    reframe = validate_stored_reframe(clip["reframe"])
                    _validate_reframe_media(media)
                    if reframe["status"] == "review_required" and reframe["override"]["mode"] == "auto":
                        raise ReframeError("主体构图待人工确认，不能渲染。")
                    # Call the same crop builder as rendering.  It checks
                    # target aspect, source identity, dimensions, and the
                    # explicit pending-manual binding path before FFmpeg.
                    reframe_filter(
                        reframe, source_width=media.width or 0, source_height=media.height or 0,
                        output_width=state["settings"]["width"], output_height=state["settings"]["height"],
                        in_point=clip["inPoint"], speed=clip["speed"], duration=clip["duration"],
                        source_hash=source_fingerprint(source), source_timing=collected.get("source_timing"), crop_name="preflight",
                    )
                visible |= track["kind"] == "video" and not track["hidden"] and media.has_video
                audible |= not track["muted"] and clip["volume"] > 0 and media.has_audio
                if track["kind"] == "video" and asset.get("kind") != "image" and not track["muted"] and clip["volume"] > 0 and not media.has_audio:
                    issues.append({**location, "level": "warning", "message": "视频不含音频流，此片段不会提供声音。"})
            except (ValueError, OSError, TimelineRenderError) as error:
                issues.append({**location, "level": "error", "message": str(error)})
    if not any(item["level"] == "error" for item in issues):
        try:
            validate_workspace({"revision": state["revision"], "settings": state["settings"],
                                "tracks": state["tracks"], "transitions": state.get("transitions", [])}, assets)
        except ValueError as error:
            issues.append({"level": "error", "message": str(error), "label": "时间线"})
    if format == "wav" and not audible:
        issues.append({"level": "error", "label": "输出", "message": "WAV 导出需要存在音频流、未静音且音量大于零的片段。"})
    elif format != "wav" and not visible:
        issues.append({"level": "error", "label": "输出", "message": "MP4 导出需要可用且可见的视频或图片片段。"})
    elif format != "wav" and not audible:
        issues.append({"level": "warning", "label": "输出", "message": "没有可用的音频，导出的视频将为静音。"})
    return issues


def _collect_clips(tracks: list, sources: Dict[str, Path], ffprobe_path: str, format: str, media_cache=None,
                   timing_cache=None) -> List[dict]:
    if media_cache is None:
        media_cache = {}
    if timing_cache is None:
        timing_cache = {}
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
                try:
                    media_cache[cache_key] = _probe_media(source, ffprobe_path)
                except (TimelineRenderError, OSError) as error:
                    media_cache[cache_key] = error
            media = media_cache[cache_key]
            if isinstance(media, Exception):
                raise media
            source_timing = None
            if clip.get("reframe") is not None and media.has_video and not media.image:
                from .montage_reframe import probe_source_timing, source_fingerprint
                fingerprint = source_fingerprint(source)
                if fingerprint not in timing_cache:
                    timing_cache[fingerprint] = probe_source_timing(source, ffprobe_path)
                source_timing = timing_cache[fingerprint]
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
                "id": clip.get("id"),
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
                "track_id": track.get("id"),
                "clip_index": clip_index,
                "end": start + clip_duration,
                "reframe": clip.get("reframe"),
                "source_timing": source_timing,
            })
    return result


def _collect_transitions(value: object, clips: List[dict]) -> List[dict]:
    # Schema, adjacency and overlap are checked by validate_transitions above.
    by_id = {clip.get("id"): clip for clip in clips if isinstance(clip.get("id"), str)}
    return [{**item, "left": by_id[item["leftClipId"]], "right": by_id[item["rightClipId"]]} for item in value]


def _probe_media(source: Path, ffprobe_path: str) -> _Media:
    command = [
        ffprobe_path, "-v", "error", "-show_entries",
        "format=duration,format_name:stream=codec_type,duration,width,height,avg_frame_rate,r_frame_rate,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation",
        "-of", "json", str(source),
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
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    width = video_stream.get("width") if type(video_stream.get("width")) is int else None
    height = video_stream.get("height") if type(video_stream.get("height")) is int else None
    try:
        frame_rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
        fps = float(Fraction(frame_rate)) if frame_rate not in (None, "0/0") else None
    except (ValueError, ZeroDivisionError):
        fps = None
    sample_aspect_ratio = video_stream.get("sample_aspect_ratio") if isinstance(video_stream.get("sample_aspect_ratio"), str) else None
    try:
        rotation = float((video_stream.get("tags") or {}).get("rotate", 0))
        for side_data in video_stream.get("side_data_list") or []:
            if isinstance(side_data, dict) and "rotation" in side_data:
                rotation = float(side_data["rotation"])
    except (TypeError, ValueError):
        rotation = float("nan")
    return _Media(
        duration,
        _first_stream_duration(streams, "video"),
        _first_stream_duration(streams, "audio"),
        has_video,
        has_audio,
        image,
        width,
        height,
        fps,
        sample_aspect_ratio,
        rotation,
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
    transitions: List[dict],
    duration: float,
    width: int,
    height: int,
    fps: float,
    format: str,
    ffmpeg_path: str,
    captions: Optional[list[tuple[Path, float, float]]] = None,
    reframe_commands_dir: Optional[Path] = None,
    ass_path: Optional[Path] = None,
) -> Tuple[List[str], dict]:
    command = [
        ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-threads", _THREADS, "-filter_threads", _THREADS, "-filter_complex_threads", _THREADS,
    ]
    filters = []
    visual_labels = []
    audio_labels = []
    input_index = 0
    captions = captions or []
    active = [item for item in transitions if item["enabled"] and item["template"] in _XFADE_TEMPLATES]
    active_by_left = {item["left"]["id"]: item for item in active}
    active_right_ids = {item["right"]["id"] for item in active}
    transitioned_video_ids = {item[side]["id"] for item in active for side in ("left", "right")}
    audio_active = [item for item in active if not item["left"]["muted"]]
    audio_by_left = {item["left"]["id"]: item for item in audio_active}
    audio_right_ids = {item["right"]["id"] for item in audio_active}
    transitioned_audio_ids = {item[side]["id"] for item in audio_active for side in ("left", "right")}

    if format != "wav":
        filters.append("color=c=black:s=%dx%d:r=%s:d=%s[base0]" % (width, height, _number(fps), _number(duration)))
        video_labels = {}
        visual_units = []
        for clip in clips:
            if clip["kind"] != "video" or clip["hidden"]:
                continue
            command.extend(_input_arguments(clip, fps))
            label = "v%d" % len(video_labels)
            command_file = None
            if clip.get("reframe") is not None and reframe_commands_dir is not None:
                reframe_commands_dir.mkdir(parents=True, exist_ok=True)
                command_file = reframe_commands_dir / ("crop-" + hashlib.sha256(str(clip["id"]).encode()).hexdigest()[:16] + ".cmd")
            filters.append(_video_filter(
                input_index, clip, width, height, fps, label,
                local=clip["id"] in transitioned_video_ids, reframe_commands_file=command_file,
            ))
            video_labels[clip["id"]] = label
            input_index += 1

        consumed = set()
        for clip in clips:
            if clip["id"] in consumed or clip["kind"] != "video" or clip["hidden"]:
                continue
            if clip["id"] in transitioned_video_ids and clip["id"] not in active_right_ids:
                current = video_labels[clip["id"]]
                cumulative = clip["duration"]
                cursor = clip
                step = 0
                consumed.add(cursor["id"])
                while cursor["id"] in active_by_left:
                    transition = active_by_left[cursor["id"]]
                    following = transition["right"]
                    output_label = "vx%d_%d" % (len(visual_units), step)
                    filters.append("[%s][%s]xfade=transition=%s:duration=%s:offset=%s[%s]" % (
                        current, video_labels[following["id"]], _XFADE_TEMPLATES[transition["template"]],
                        _number(transition["duration"]), _number(cumulative - transition["overlap"]), output_label,
                    ))
                    current = output_label
                    cumulative += following["duration"] - transition["overlap"]
                    cursor = following
                    consumed.add(cursor["id"])
                    step += 1
                positioned = "vchain%d" % len(visual_units)
                filters.append("[%s]setpts=PTS-STARTPTS+%s/TB[%s]" % (
                    current, _number(clip["start"]), positioned))
                visual_units.append((clip["track_index"], clip["clip_index"], positioned))
            elif clip["id"] not in transitioned_video_ids:
                visual_units.append((clip["track_index"], clip["clip_index"], video_labels[clip["id"]]))
                consumed.add(clip["id"])
        visual_labels = [item[2] for item in sorted(visual_units)]

    audio_source_labels = {}
    for clip in clips:
        if clip["muted"]:
            continue
        in_chain = clip["id"] in transitioned_audio_ids
        label = "as%d" % len(audio_source_labels)
        if clip["media"].has_audio:
            command.extend(_input_arguments(clip, fps))
            filters.append(_audio_filter(input_index, clip, label, local=in_chain))
            input_index += 1
        elif in_chain:
            filters.append("anullsrc=r=48000:cl=stereo:d=%s[%s]" % (_number(clip["duration"]), label))
        else:
            continue
        audio_source_labels[clip["id"]] = label

    consumed_audio = set()
    for clip in clips:
        if clip["muted"] or clip["id"] in consumed_audio or clip["id"] not in audio_source_labels:
            continue
        if clip["id"] in transitioned_audio_ids and clip["id"] not in audio_right_ids:
            current = audio_source_labels[clip["id"]]
            cursor = clip
            cumulative = clip["duration"]
            step = 0
            consumed_audio.add(cursor["id"])
            while cursor["id"] in audio_by_left:
                transition = audio_by_left[cursor["id"]]
                following = transition["right"]
                output_label = "ax%d_%d" % (len(audio_labels), step)
                if transition["audioCrossfade"]:
                    filters.append("[%s][%s]acrossfade=d=%s:c1=tri:c2=tri[%s]" % (
                        current, audio_source_labels[following["id"]], _number(transition["duration"]), output_label,
                    ))
                else:
                    trimmed = output_label + "trim"
                    filters.append("[%s]atrim=duration=%s,asetpts=PTS-STARTPTS[%s]" % (
                        current, _number(cumulative - transition["overlap"]), trimmed,
                    ))
                    filters.append("[%s][%s]concat=n=2:v=0:a=1[%s]" % (
                        trimmed, audio_source_labels[following["id"]], output_label,
                    ))
                current = output_label
                cumulative += following["duration"] - transition["overlap"]
                cursor = following
                consumed_audio.add(cursor["id"])
                step += 1
            positioned = "achain%d" % len(audio_labels)
            filters.append("[%s]adelay=%s:all=1[%s]" % (
                current, _milliseconds(clip["start"]), positioned))
            audio_labels.append(positioned)
        elif clip["id"] not in transitioned_audio_ids:
            audio_labels.append(audio_source_labels[clip["id"]])
            consumed_audio.add(clip["id"])

    if format != "wav":
        current = "base0"
        for index, label in enumerate(visual_labels, start=1):
            next_label = "base%d" % index
            filters.append("[%s][%s]overlay=eof_action=pass:repeatlast=0:shortest=0[%s]" % (current, label, next_label))
            current = next_label
        filters.append("[%s]trim=duration=%s,setpts=PTS-STARTPTS[%s]" % (
            current, _number(duration), "captionbase" if captions else "vout"))

    if audio_labels:
        filters.append("anullsrc=r=48000:cl=stereo:d=%s[asilent]" % _number(duration))
        filters.append("[asilent]%samix=inputs=%d:duration=first:normalize=0,alimiter=limit=0.98,atrim=duration=%s[aout]" % (
            "".join("[%s]" % label for label in audio_labels), len(audio_labels) + 1, _number(duration)))
    else:
        filters.append("anullsrc=r=48000:cl=stereo:d=%s[aout]" % _number(duration))

    if captions:
        current = "captionbase"
        caption_margin = max(32, round(height * 0.05))
        for index, (path, start, end) in enumerate(captions):
            command.extend(["-loop", "1", "-framerate", _number(fps), "-t", _number(duration), "-i", str(path)])
            following = "vout" if index == len(captions) - 1 else "caption%d" % index
            filters.append("[%s][%d:v]overlay=x=(W-w)/2:y=H-h-%d:enable='between(t,%s,%s)':shortest=0[%s]" % (
                current, input_index, caption_margin, _number(start), _number(end), following))
            current = following
            input_index += 1

    if ass_path is not None:
        from .montage_packaging import private_font_path
        filters.append("[vout]ass=filename=%s:fontsdir=%s[packaged]" % (
            str(ass_path), str(private_font_path().parent)))
        filters.append("[packaged]null[vout]")

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


def _video_filter(index: int, clip: dict, width: int, height: int, fps: float, label: str,
                  *, local: bool = False, reframe_commands_file: Optional[Path] = None) -> str:
    position = "PTS-STARTPTS" if local else "PTS-STARTPTS+%s/TB" % _number(clip["start"])
    reframe = clip.get("reframe")
    if reframe is not None:
        media = clip["media"]
        _validate_reframe_media(media)
        try:
            from .montage_reframe import ReframeError, reframe_filter, source_fingerprint
            aspect = reframe["targetAspect"]
            if aspect["width"] * height != aspect["height"] * width:
                raise ReframeError("主体裁剪目标比例与当前输出不一致。")
            framing = reframe_filter(
                reframe, source_width=media.width, source_height=media.height,
                output_width=width, output_height=height, in_point=clip["in_point"],
                speed=clip["speed"], duration=clip["duration"], source_hash=source_fingerprint(clip["source"]),
                source_timing=clip.get("source_timing"),
                crop_name="reframe_" + hashlib.sha256(str(clip["id"]).encode()).hexdigest()[:12],
                commands_path=reframe_commands_file,
                trajectory_fps=fps,
            )
        except (OSError, KeyError, ReframeError) as error:
            raise TimelineRenderError(str(error)) from error
        return (
            "[%d:v]setpts=PTS-STARTPTS,%s,setpts=(PTS-STARTPTS)/%s,trim=duration=%s,"
            "fps=%s,format=yuv420p,settb=AVTB,setpts=%s[%s]"
        ) % (index, framing, _number(clip["speed"]), _number(clip["duration"]), _number(fps), position, label)
    return (
        "[%d:v]setpts=PTS-STARTPTS,setpts=(PTS-STARTPTS)/%s,trim=duration=%s,"
        "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1,fps=%s,format=yuv420p,settb=AVTB,setpts=%s[%s]"
    ) % (
        index, _number(clip["speed"]), _number(clip["duration"]),
        width, height, width, height, _number(fps), position, label,
    )


def _audio_filter(index: int, clip: dict, label: str, *, local: bool = False) -> str:
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
        *([] if local else ["adelay=%s:all=1" % _milliseconds(clip["start"])]),
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


def _run_ffmpeg(command: List[str], cancelled: Callable[[], bool], cwd: Optional[Path] = None) -> None:
    if cancelled():
        raise TimelineRenderError("时间线渲染已取消。")
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True, cwd=cwd
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


def _verify_output(output: Path, format: str, expected: dict, ffprobe_path: str) -> dict:
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
    result = {"duration": media.duration}
    if format != "wav":
        result.update(width=media.width, height=media.height, fps=media.fps)
    return result


def _output_size(width: int, height: int, format: str) -> Tuple[int, int]:
    if format != "preview" or width <= 640:
        return width, height
    divisor = math.gcd(width, height)
    unit_width, unit_height = width // divisor, height // divisor
    factor = 640 // unit_width
    while factor > 0 and (unit_width * factor % 2 or unit_height * factor % 2):
        factor -= 1
    return (unit_width * factor, unit_height * factor) if factor else (width, height)


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
