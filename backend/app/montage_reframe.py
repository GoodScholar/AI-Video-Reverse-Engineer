"""Local, evidence-preserving subject reframe analysis and FFmpeg adaptation.

The trajectory helpers deliberately have no MediaPipe dependency.  The worker
owns inference; this module validates its JSON result again at every storage
and render boundary.  The `sendcmd` shape is adapted from OpenShorts
`reframe_v2.py` (MIT, commit 89f030934388d5167b3b0ede164ef893dad09ae9), but
uses neither its cloud integrations nor YOLO code.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import subprocess
import tempfile
import sys
import time
from dataclasses import dataclass
from statistics import median
from typing import Any


DETECTOR_VERSION = "mediapipe-object-detector-0.10.21"
SCHEMA_VERSION = 1
MODEL_SHA256 = "2e04c53bfeac0ac2a30c057c7e2a777594ce39baaac35a92f74fb1e8c4fc4e0b"
_OVERRIDE_MODES = frozenset(("auto", "center", "manual", "replace"))
_TIMING_TICKS_PER_SECOND = 1_000_000


@dataclass(frozen=True)
class SourceTiming:
    """Server-probed decoded-display timing, expressed from the first frame."""
    elapsed: tuple[float, ...]
    durations: tuple[float, ...]
    cfr: bool
    reason: str | None

    def worker_payload(self) -> dict[str, Any]:
        return {"cfr": self.cfr, "reason": self.reason,
                "frames": [{"elapsedSeconds": point, "durationSeconds": duration}
                           for point, duration in zip(self.elapsed, self.durations)]}


def probe_source_timing(source: Path, ffprobe_path: str) -> SourceTiming:
    """Probe real display PTS; fps metadata is deliberately not timing evidence."""
    command = [ffprobe_path, "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_streams",
               "-show_entries", "frame=best_effort_timestamp_time,pkt_duration_time:stream=start_time,duration",
               "-of", "json", str(source)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return SourceTiming((), (), False, "无法可靠探测源视频逐帧时间")
    if result.returncode:
        return SourceTiming((), (), False, "无法可靠探测源视频逐帧时间")
    try:
        payload = json.loads(result.stdout)
        frames = payload.get("frames") or []
        stream = (payload.get("streams") or [{}])[0]
        pts = [_finite_float(frame.get("best_effort_timestamp_time")) for frame in frames]
    except (ValueError, TypeError, AttributeError):
        return SourceTiming((), (), False, "源视频缺少逐帧显示时间")
    if len(pts) < 2 or any(point is None for point in pts):
        return SourceTiming((), (), False, "源视频缺少逐帧显示时间")
    absolute = tuple(pts)  # type: ignore[arg-type]
    if any(right <= left for left, right in zip(absolute, absolute[1:])):
        return SourceTiming((), (), False, "源视频逐帧时间重复或倒序")
    intervals = [right - left for left, right in zip(absolute, absolute[1:])]
    interval = median(intervals)
    tolerance = max(.001, interval * .03)
    if interval <= 0 or any(abs(item - interval) > tolerance for item in intervals):
        return SourceTiming((), (), False, "源视频为可变帧率或帧间隔不可信")
    last_duration = _finite_float(frames[-1].get("pkt_duration_time")) if isinstance(frames[-1], dict) else None
    if last_duration is None or last_duration <= 0:
        start = _finite_float(stream.get("start_time")) if isinstance(stream, dict) else None
        duration = _finite_float(stream.get("duration")) if isinstance(stream, dict) else None
        candidate = start + duration - absolute[-1] if start is not None and duration is not None else None
        if candidate is None or candidate <= 0 or abs(candidate - interval) > tolerance:
            return SourceTiming((), (), False, "源视频末帧时长无法可靠确定")
        last_duration = candidate
    if abs(last_duration - interval) > tolerance:
        return SourceTiming((), (), False, "源视频末帧时长与逐帧时间不一致")
    return SourceTiming(tuple(point - absolute[0] for point in absolute),
                        tuple(intervals + [last_duration]), True, None)


class ReframeError(ValueError):
    """A reframe result cannot safely be accepted or rendered."""


# Adapted directly from OpenShorts reframe_v2.py, commit
# 89f030934388d5167b3b0ede164ef893dad09ae9 (MIT; see THIRD_PARTY_NOTICES).
# The original accepts per-frame x positions.  This is retained verbatim for
# one coordinate, then called once each for x and y with a per-clip crop name.
def dedupe_sendcmd_lines(xs, fps, target="crop@c"):
    """sendcmd lines setting one crop coordinate, deduped to change-points."""
    lines = []
    prev = None
    for i, x in enumerate(xs):
        if x != prev:
            lines.append(f"{i / fps:.4f} {target} x {x};")
            prev = x
    return lines


def source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_local_detector(source: Path, *, start: float, end: float, target_width: int, target_height: int,
                       source_timing: SourceTiming | None = None,
                       media_kind: str = "video",
                       worker_python: str | None = None, model_path: Path | None = None,
                       worker_path: Path | None = None,
                       cancelled=lambda: False) -> dict[str, Any]:
    """Execute the pinned local worker; it never fetches weights or uploads media."""
    root = Path(__file__).resolve().parents[1]
    worker = worker_path or root / "person_worker" / "run_reframe.py"
    model = model_path or Path(os.environ.get("AIVRE_REFRAME_MODEL", root / "person_worker" / "models" / "efficientdet_lite0.tflite"))
    runtime = worker_python or os.environ.get("AIVRE_PERSON_WORKER_PYTHON") or str(root / "person_worker" / ".venv" / "bin" / "python")
    if not Path(runtime).is_file() or not model.is_file():
        raise ReframeError("本地主体检测环境或固定模型未就绪；不会自动下载。")
    if media_kind not in ("video", "image"):
        raise ReframeError("主体检测媒体类型无效。")
    command = [runtime, str(worker), "--input", str(source), "--model", str(model), "--start", f"{start:.6f}",
               "--end", f"{end:.6f}", "--media-kind", media_kind, "--target-width", str(target_width), "--target-height", str(target_height)]
    process = None
    try:
        # Spool streams while polling: a large successful JSON result must
        # never fill an OS pipe before the parent drains it.
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout, tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr, tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as timing_file:
            if source_timing is not None:
                json.dump(source_timing.worker_payload(), timing_file, separators=(",", ":"))
                timing_file.flush()
                command.extend(("--timing-json", timing_file.name))
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True, start_new_session=True)
            deadline = time.monotonic() + 300
            while process.poll() is None:
                if cancelled() or time.monotonic() >= deadline:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    raise ReframeError("本地主体检测已取消或超时。")
                time.sleep(.05)
            stdout.seek(0)
            payload = stdout.read()
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReframeError("本地主体检测无法启动或超时。") from error
    if process is None or process.returncode != 0:
        raise ReframeError("本地主体检测失败，请检查模型与源视频。")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise ReframeError("本地主体检测返回无效结果。") from error


def pending_reframe(*, target_width: int, target_height: int, reason: str) -> dict[str, Any]:
    """The only valid pre-analysis state; it is deliberately not renderable."""
    return {
        "schemaVersion": SCHEMA_VERSION, "sourceFingerprint": "", "sourceWidth": 0, "sourceHeight": 0,
        "targetAspect": {"width": target_width, "height": target_height},
        "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
        "samples": [], "uncertaintyReasons": [reason], "status": "review_required",
        "override": {"mode": "auto", "keyframes": []},
    }


def retain_server_reframe_facts(existing_tracks: list, submitted_tracks: list) -> list:
    """PUT may express a human override, never detector facts or auto approval."""
    existing = {clip.get("id"): clip for track in existing_tracks for clip in track.get("clips", [])}
    for track in submitted_tracks:
        for clip in track.get("clips", []):
            previous = existing.get(clip.get("id"), {})
            submitted = clip.get("reframe")
            stored = previous.get("reframe")
            # A reframe fact is tied to the decoded source *and* the source-time
            # range.  A clip id survives candidate replacement, so retaining the
            # old fact by id would make the new asset render with stale evidence.
            if isinstance(stored, dict) and any(clip.get(key) != previous.get(key)
                                                for key in ("assetId", "inPoint", "duration", "speed")):
                aspect = stored.get("targetAspect")
                if not isinstance(aspect, dict) or not _positive_int(aspect.get("width")) or not _positive_int(aspect.get("height")):
                    raise ReframeError("已存主体裁剪缺少可用目标比例。")
                clip["reframe"] = pending_reframe(
                    target_width=aspect["width"], target_height=aspect["height"],
                    reason="素材或源时间已改变，请重新主体分析或设置人工构图。",
                )
                continue
            if submitted is None:
                if isinstance(stored, dict):
                    clip["reframe"] = stored
                continue
            if not isinstance(stored, dict):
                raise ReframeError("主体检测事实只能由服务端分析写入。")
            if {key: value for key, value in submitted.items() if key != "override"} != {
                    key: value for key, value in stored.items() if key != "override"}:
                raise ReframeError("主体检测事实或状态不能由客户端修改。")
            clip["reframe"] = {**stored, "override": submitted["override"]}
    return submitted_tracks


def validate_stored_reframe(value: Any, *, still_image: bool = False) -> dict[str, Any]:
    """Validate persisted shape without trusting it as render authorization."""
    required_keys = {
        "schemaVersion", "sourceFingerprint", "sourceWidth", "sourceHeight", "targetAspect",
        "detectorVersion", "modelSha256", "samples", "uncertaintyReasons", "status", "override",
    }
    optional_timing = {"frameDurationSeconds", "samplingIntervalSeconds"}
    if not isinstance(value, dict) or not required_keys <= set(value) or set(value) - required_keys - optional_timing:
        raise ReframeError("主体裁剪数据结构无效。")
    if value["schemaVersion"] != SCHEMA_VERSION or value["status"] not in ("ready", "review_required"):
        raise ReframeError("主体裁剪状态无效。")
    aspect = value["targetAspect"]
    if (not isinstance(aspect, dict) or set(aspect) != {"width", "height"}
            or not _positive_int(aspect["width"]) or not _positive_int(aspect["height"])):
        raise ReframeError("目标比例无效。")
    if not isinstance(value["sourceFingerprint"], str) or len(value["sourceFingerprint"]) not in (0, 64):
        raise ReframeError("主体裁剪源指纹无效。")
    if (not _nonnegative_int(value["sourceWidth"]) or not _nonnegative_int(value["sourceHeight"])
            or not isinstance(value["detectorVersion"], str) or not isinstance(value["modelSha256"], str)
            or not isinstance(value["uncertaintyReasons"], list) or any(
                not isinstance(item, str) or not item.strip() or len(item) > 300 for item in value["uncertaintyReasons"]
            )):
        raise ReframeError("主体裁剪字段无效。")
    samples = _samples(value["samples"])
    override = _override(value["override"])
    if still_image:
        _validate_still_image_override(override)
    if value["status"] == "ready" and (value["uncertaintyReasons"] or not samples or not value["sourceFingerprint"]
                                         or not value["sourceWidth"] or not value["sourceHeight"]):
        raise ReframeError("完成的主体裁剪必须有源身份和检测证据。")
    if value["status"] == "ready" and not _valid_auto_subject_facts(
            samples, value["sourceWidth"], value["sourceHeight"], aspect, still_image=still_image):
        raise ReframeError("自动构图必须有每帧唯一可信 person 检测框且裁窗包含主体。")
    if value["status"] == "review_required" and not value["uncertaintyReasons"]:
        raise ReframeError("待人工主体裁剪必须说明不确定原因。")
    return {**value, "targetAspect": dict(aspect), "samples": samples,
            "uncertaintyReasons": list(value["uncertaintyReasons"]), "override": override}


def normalize_reframe(value: Any, *, source_fingerprint: str, source_width: int, source_height: int,
                      source_timing: SourceTiming | None = None, required_start: float | None = None,
                      required_end: float | None = None, still_image: bool = False) -> dict[str, Any]:
    """Validate a persisted worker result against the current decoded source."""
    required_keys = {
        "schemaVersion", "sourceFingerprint", "sourceWidth", "sourceHeight", "targetAspect",
        "detectorVersion", "modelSha256", "samples", "uncertaintyReasons", "status", "override",
    }
    optional_timing = {"frameDurationSeconds", "samplingIntervalSeconds"}
    if not isinstance(value, dict) or not required_keys <= set(value) or set(value) - required_keys - optional_timing:
        raise ReframeError("主体裁剪数据结构无效。")
    if value["schemaVersion"] != SCHEMA_VERSION or value["sourceFingerprint"] != source_fingerprint:
        raise ReframeError("主体裁剪结果与当前素材不匹配。")
    if value["sourceWidth"] != source_width or value["sourceHeight"] != source_height or source_width <= 0 or source_height <= 0:
        raise ReframeError("主体裁剪结果的源尺寸无效。")
    aspect = value["targetAspect"]
    if (not isinstance(aspect, dict) or set(aspect) != {"width", "height"}
            or not _positive_int(aspect["width"]) or not _positive_int(aspect["height"])):
        raise ReframeError("目标比例无效。")
    if value["detectorVersion"] != DETECTOR_VERSION or value["modelSha256"] != MODEL_SHA256:
        raise ReframeError("主体检测器版本或模型校验不匹配。")
    if value["status"] not in ("ready", "review_required") or not isinstance(value["uncertaintyReasons"], list) or any(
        not isinstance(item, str) or not item.strip() or len(item) > 300 for item in value["uncertaintyReasons"]
    ):
        raise ReframeError("主体裁剪状态无效。")
    override = _override(value["override"])
    try:
        samples = _samples(value["samples"])
    except ReframeError:
        # A worker result is not persisted verbatim.  Bad detector geometry is
        # an uncertainty requiring a human decision, while a stored record with
        # the same corruption remains rejected by validate_stored_reframe.
        if value["status"] == "ready" and not still_image:
            return _review_required(value, [], override, "自动构图必须有每帧唯一可信 person 检测框且裁窗包含主体")
        raise
    if value["status"] == "ready":
        if value["uncertaintyReasons"] or not samples:
            raise ReframeError("完成的主体裁剪必须有连续检测证据。")
        if not _valid_auto_subject_facts(samples, source_width, source_height, aspect, still_image=still_image):
            return _review_required(value, samples, override, "自动构图必须有每帧唯一可信 person 检测框且裁窗包含主体")
        if not still_image and not _source_samples_cover(source_timing, samples, required_start, required_end):
            return _review_required(value, samples, override, "需重新分析源时间，自动构图未获可信逐帧时间授权")
    elif not value["uncertaintyReasons"]:
        raise ReframeError("待人工主体裁剪必须说明不确定原因。")
    return {**value, "targetAspect": {"width": aspect["width"], "height": aspect["height"]},
            "samples": samples, "uncertaintyReasons": list(value["uncertaintyReasons"]), "override": override}


def reframe_filter(value: dict[str, Any], *, source_width: int, source_height: int, output_width: int,
                   output_height: int, in_point: float, speed: float, duration: float,
                   source_hash: str | None = None, source_timing: SourceTiming | None = None, crop_name: str = "reframe", commands_path: Path | None = None,
                   trajectory_fps: float = 30, still_image: bool = False) -> str:
    """Return `sendcmd,crop,scale` for source-time samples before setpts speed.

    Output command times are local clip times; each one maps to
    ``inPoint + outputTime * speed`` before interpolating its source samples.
    """
    if (not isinstance(crop_name, str) or not crop_name.replace("_", "").isalnum()
            or not all(_finite_number(item) for item in (in_point, speed, duration, trajectory_fps))
            or speed <= 0 or duration <= 0 or not 1 <= trajectory_fps <= 60):
        raise ReframeError("主体裁剪的片段时间无效。")
    source_hash = source_hash or value.get("sourceFingerprint")
    end = in_point + duration * speed
    stored = validate_stored_reframe(value, still_image=still_image)
    # `center`/`manual` is an explicit user risk decision.  A pre-analysis
    # record intentionally has no fingerprint or dimensions, so bind that
    # decision to the currently probed source here without manufacturing a
    # detector result or changing its stored evidence.
    if (not stored["sourceFingerprint"] and stored["status"] == "review_required"
            and stored["override"]["mode"] != "auto"):
        if stored["targetAspect"]["width"] * output_height != stored["targetAspect"]["height"] * output_width:
            raise ReframeError("主体裁剪目标比例与当前输出不一致。")
        result = {**stored, "sourceFingerprint": source_hash, "sourceWidth": source_width, "sourceHeight": source_height}
    else:
        result = normalize_reframe(stored, source_fingerprint=source_hash, source_width=source_width,
                                   source_height=source_height,
                                   source_timing=source_timing,
                                   required_start=in_point if stored["override"]["mode"] == "auto" else None,
                                   required_end=end if stored["override"]["mode"] == "auto" else None,
                                   still_image=still_image)
    override = result["override"]
    if result["status"] != "ready" and override["mode"] == "auto":
        raise ReframeError("主体构图待人工确认，不能渲染。")
    crop_width, crop_height = crop_geometry(source_width, source_height, output_width, output_height)
    frame_count = max(1, round(duration * trajectory_fps))
    times = [index / trajectory_fps for index in range(frame_count + 1)]
    times[-1] = duration
    positions = [_crop_position(_center_at(result, in_point + time * speed), source_width, source_height,
                                crop_width, crop_height) for time in times]
    # `times` is output-frame aligned, so the adapted OpenShorts helper uses
    # the render fps without losing source-time interpolation.
    commands = dedupe_sendcmd_lines([item[0] for item in positions], trajectory_fps, f"crop@{crop_name}")
    commands += [line.replace(" x ", " y ") for line in dedupe_sendcmd_lines(
        [item[1] for item in positions], trajectory_fps, f"crop@{crop_name}")]
    if commands_path is not None:
        commands_path.write_text("\n".join(commands) + "\n", encoding="ascii")
        sendcmd = "sendcmd=f=" + commands_path.name
    else:
        escaped = "\\;".join(command.removesuffix(";") for command in commands) + "\\;"
        sendcmd = f"sendcmd=c='{escaped}'"
    return (f"{sendcmd},crop@{crop_name}={crop_width}:{crop_height}:0:0,"
            f"scale={output_width}:{output_height}:flags=lanczos,setsar=1")


def crop_geometry(source_width: int, source_height: int, output_width: int, output_height: int) -> tuple[int, int]:
    if not all(_positive_int(item) for item in (source_width, source_height, output_width, output_height)):
        raise ReframeError("主体裁剪尺寸无效。")
    target = output_width / output_height
    if source_width / source_height > target:
        width, height = int(source_height * target), source_height
    else:
        width, height = source_width, int(source_width / target)
    width -= width % 2
    height -= height % 2
    if width <= 0 or height <= 0:
        raise ReframeError("主体裁剪窗口无效。")
    return width, height


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _timing_ticks(value: Any) -> int | None:
    """Use the persisted worker/ffprobe six-decimal PTS timebase for boundaries."""
    try:
        parsed = Decimal(str(value)) * _TIMING_TICKS_PER_SECOND
        return int(parsed.to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def _review_required(value: dict[str, Any], samples: list[dict[str, Any]], override: dict[str, Any], reason: str) -> dict[str, Any]:
    reasons = list(value["uncertaintyReasons"])
    if reason not in reasons:
        reasons.append(reason)
    return {**value, "samples": samples, "uncertaintyReasons": reasons,
            "status": "review_required", "override": override}


def _valid_auto_subject_facts(samples: list[dict[str, Any]], source_width: int, source_height: int,
                              target_aspect: dict[str, int], *, still_image: bool) -> bool:
    """Every automatic sample must name exactly one visible person in its FFmpeg crop."""
    if not samples or (still_image and (len(samples) != 1 or samples[0]["timeSeconds"] != 0)):
        return False
    crop_width, crop_height = crop_geometry(source_width, source_height, target_aspect["width"], target_aspect["height"])
    for sample in samples:
        boxes = sample["boxes"]
        if sample["confidence"] <= 0 or len(boxes) != 1:
            return False
        box = boxes[0]
        if box["label"] != "person" or box["confidence"] <= 0:
            return False
        crop_x, crop_y = _crop_position((sample["center"]["x"], sample["center"]["y"]),
                                        source_width, source_height, crop_width, crop_height)
        left, top = box["x"] * source_width, box["y"] * source_height
        right, bottom = (box["x"] + box["width"]) * source_width, (box["y"] + box["height"]) * source_height
        # This uses the identical even-sized crop and clamped position that
        # FFmpeg receives, so a ready fact cannot point outside the render window.
        if not (crop_x <= left and right <= crop_x + crop_width and crop_y <= top and bottom <= crop_y + crop_height):
            return False
    return True


def _validate_still_image_override(override: dict[str, Any]) -> None:
    if override["mode"] not in ("manual", "replace"):
        return
    keys = override["keyframes"]
    if len(keys) != 1 or keys[0]["timeSeconds"] != 0:
        raise ReframeError("静态图片人工裁剪必须恰好有一个 0 秒关键帧。")


def _source_samples_cover(timing: SourceTiming | None, samples: list[dict[str, Any]],
                          start: float | None, end: float | None) -> bool:
    """Authorize auto framing only from the server's real PTS table."""
    if timing is None or not timing.cfr or len(timing.elapsed) != len(timing.durations) or not timing.elapsed:
        return False
    timing_points = [_timing_ticks(point) for point in timing.elapsed]
    timing_durations = [_timing_ticks(duration) for duration in timing.durations]
    if any(point is None for point in timing_points) or any(duration is None or duration <= 0 for duration in timing_durations):
        return False
    sample_points = [_timing_ticks(sample["timeSeconds"]) for sample in samples]
    if any(point is None or point not in timing_points for point in sample_points):
        return False
    if start is None or end is None:
        return True
    start_tick, end_tick = _timing_ticks(start), _timing_ticks(end)
    if not (_finite_number(start) and _finite_number(end) and 0 <= start < end and start_tick is not None and end_tick is not None):
        return False
    intersecting = [index for index, (pts, duration) in enumerate(zip(timing_points, timing_durations))
                    if pts < end_tick and pts + duration > start_tick]
    if not intersecting:
        return False
    first, last = intersecting[0], intersecting[-1]
    if sample_points[0] != timing_points[first] or sample_points[-1] != timing_points[last]:
        return False
    max_gap = _timing_ticks(.5) + max(timing_durations) + _timing_ticks(.001)
    if any(right - left > max_gap for left, right in zip(sample_points, sample_points[1:])):
        return False
    return timing_points[first] + timing_durations[first] > start_tick and timing_points[last] < end_tick


def _samples(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 640:
        raise ReframeError("主体检测采样无效。")
    normalized = []
    previous = -1.0
    for sample in value:
        if not isinstance(sample, dict) or set(sample) != {"timeSeconds", "boxes", "center", "confidence"}:
            raise ReframeError("主体检测采样无效。")
        time = sample["timeSeconds"]
        confidence = sample["confidence"]
        center = sample["center"]
        if not _finite_number(time) or time < 0 or time <= previous or not _finite_number(confidence) or not 0 <= confidence <= 1:
            raise ReframeError("主体检测采样时间或置信度无效。")
        if not isinstance(center, dict) or set(center) != {"x", "y"} or not all(_unit_number(center[key]) for key in ("x", "y")):
            raise ReframeError("主体检测中心无效。")
        boxes = sample["boxes"]
        if not isinstance(boxes, list) or len(boxes) > 50:
            raise ReframeError("主体检测框无效。")
        for box in boxes:
            if (not isinstance(box, dict) or set(box) != {"x", "y", "width", "height", "label", "confidence"}
                    or not all(_unit_number(box[key]) for key in ("x", "y"))
                    or not all(_finite_number(box[key]) and 0 < box[key] <= 1 for key in ("width", "height"))
                    or box["x"] + box["width"] > 1 + 1e-9 or box["y"] + box["height"] > 1 + 1e-9
                    or not isinstance(box["label"], str) or not box["label"].strip()
                    or not _finite_number(box["confidence"]) or not 0 <= box["confidence"] <= 1):
                raise ReframeError("主体检测框无效。")
        normalized.append({"timeSeconds": float(time), "boxes": [dict(item) for item in boxes],
                           "center": {"x": float(center["x"]), "y": float(center["y"])},
                           "confidence": float(confidence)})
        previous = time
    return normalized


def _override(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"mode", "keyframes"} or value["mode"] not in _OVERRIDE_MODES:
        raise ReframeError("主体裁剪人工覆盖无效。")
    keys = value["keyframes"]
    if not isinstance(keys, list) or len(keys) > 100:
        raise ReframeError("主体裁剪人工关键帧无效。")
    previous = -1.0
    normalized = []
    for key in keys:
        if (not isinstance(key, dict) or set(key) != {"timeSeconds", "x", "y"}
                or not _finite_number(key["timeSeconds"]) or key["timeSeconds"] < 0 or key["timeSeconds"] <= previous
                or not _unit_number(key["x"]) or not _unit_number(key["y"])):
            raise ReframeError("主体裁剪人工关键帧无效。")
        normalized.append({"timeSeconds": float(key["timeSeconds"]), "x": float(key["x"]), "y": float(key["y"])})
        previous = key["timeSeconds"]
    if value["mode"] in ("auto", "center") and keys:
        raise ReframeError("自动或居中裁剪不能带人工关键帧。")
    if value["mode"] in ("manual", "replace") and not keys:
        raise ReframeError("手工裁剪需要至少一个关键帧。")
    return {"mode": value["mode"], "keyframes": normalized}


def _center_at(value: dict[str, Any], source_time: float) -> tuple[float, float]:
    override = value["override"]
    if override["mode"] == "center":
        return .5, .5
    points = ([(item["timeSeconds"], item["x"], item["y"]) for item in override["keyframes"]]
              if override["mode"] in ("manual", "replace") else
              [(item["timeSeconds"], item["center"]["x"], item["center"]["y"]) for item in value["samples"]])
    if not points:
        return .5, .5
    if source_time <= points[0][0]:
        return points[0][1], points[0][2]
    if source_time >= points[-1][0]:
        return points[-1][1], points[-1][2]
    for left, right in zip(points, points[1:]):
        if left[0] <= source_time <= right[0]:
            fraction = (source_time - left[0]) / (right[0] - left[0])
            return left[1] + (right[1] - left[1]) * fraction, left[2] + (right[2] - left[2]) * fraction
    return points[-1][1], points[-1][2]


def _crop_position(center: tuple[float, float], width: int, height: int, crop_width: int, crop_height: int) -> tuple[int, int]:
    x = min(max(round(center[0] * width - crop_width / 2), 0), width - crop_width)
    y = min(max(round(center[1] * height - crop_height / 2), 0), height - crop_height)
    return x - x % 2, y - y % 2


def _clip_time(source_time: float, in_point: float, speed: float, duration: float) -> float:
    return min(max((source_time - in_point) / speed, 0.0), duration)


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _unit_number(value: Any) -> bool:
    return _finite_number(value) and 0 <= value <= 1
