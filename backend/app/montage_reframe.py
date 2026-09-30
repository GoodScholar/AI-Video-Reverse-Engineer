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
from pathlib import Path
import subprocess
import sys
from typing import Any


DETECTOR_VERSION = "mediapipe-object-detector-0.10.21"
SCHEMA_VERSION = 1
MODEL_SHA256 = "2e04c53bfeac0ac2a30c057c7e2a777594ce39baaac35a92f74fb1e8c4fc4e0b"
_OVERRIDE_MODES = frozenset(("auto", "center", "manual", "replace"))


class ReframeError(ValueError):
    """A reframe result cannot safely be accepted or rendered."""


def source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_local_detector(source: Path, *, start: float, end: float, target_width: int, target_height: int,
                       worker_python: str | None = None, model_path: Path | None = None) -> dict[str, Any]:
    """Execute the pinned local worker; it never fetches weights or uploads media."""
    root = Path(__file__).resolve().parents[1]
    worker = root / "person_worker" / "run_reframe.py"
    model = model_path or Path(os.environ.get("AIVRE_REFRAME_MODEL", root / "person_worker" / "models" / "efficientdet_lite0.tflite"))
    runtime = worker_python or os.environ.get("AIVRE_PERSON_WORKER_PYTHON") or str(root / "person_worker" / ".venv" / "bin" / "python")
    if not Path(runtime).is_file() or not model.is_file():
        raise ReframeError("本地主体检测环境或固定模型未就绪；不会自动下载。")
    command = [runtime, str(worker), "--input", str(source), "--model", str(model), "--start", f"{start:.6f}",
               "--end", f"{end:.6f}", "--target-width", str(target_width), "--target-height", str(target_height)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReframeError("本地主体检测无法启动或超时。") from error
    if completed.returncode != 0:
        raise ReframeError("本地主体检测失败，请检查模型与源视频。")
    try:
        return json.loads(completed.stdout)
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


def validate_stored_reframe(value: Any) -> dict[str, Any]:
    """Validate persisted shape without trusting it as render authorization."""
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion", "sourceFingerprint", "sourceWidth", "sourceHeight", "targetAspect",
        "detectorVersion", "modelSha256", "samples", "uncertaintyReasons", "status", "override",
    }:
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
    if value["status"] == "ready" and (not samples or not value["sourceFingerprint"]
                                         or not value["sourceWidth"] or not value["sourceHeight"]):
        raise ReframeError("完成的主体裁剪必须有源身份和检测证据。")
    if value["status"] == "review_required" and not value["uncertaintyReasons"]:
        raise ReframeError("待人工主体裁剪必须说明不确定原因。")
    return {**value, "targetAspect": dict(aspect), "samples": samples,
            "uncertaintyReasons": list(value["uncertaintyReasons"]), "override": override}


def normalize_reframe(value: Any, *, source_fingerprint: str, source_width: int, source_height: int,
                      required_start: float | None = None, required_end: float | None = None) -> dict[str, Any]:
    """Validate a persisted worker result against the current decoded source."""
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion", "sourceFingerprint", "sourceWidth", "sourceHeight", "targetAspect",
        "detectorVersion", "modelSha256", "samples", "uncertaintyReasons", "status", "override",
    }:
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
    samples = _samples(value["samples"])
    override = _override(value["override"])
    if value["status"] == "ready":
        if value["uncertaintyReasons"] or not samples:
            raise ReframeError("完成的主体裁剪必须有连续检测证据。")
        if required_start is not None and required_end is not None and (
            samples[0]["timeSeconds"] > required_start + .001 or samples[-1]["timeSeconds"] < required_end - .001
        ):
            raise ReframeError("主体裁剪结果未覆盖当前素材区间。")
    elif not value["uncertaintyReasons"]:
        raise ReframeError("待人工主体裁剪必须说明不确定原因。")
    return {**value, "targetAspect": {"width": aspect["width"], "height": aspect["height"]},
            "samples": samples, "uncertaintyReasons": list(value["uncertaintyReasons"]), "override": override}


def reframe_filter(value: dict[str, Any], *, source_width: int, source_height: int, output_width: int,
                   output_height: int, in_point: float, speed: float, duration: float,
                   source_hash: str | None = None, crop_name: str = "reframe", commands_path: Path | None = None) -> str:
    """Return `sendcmd,crop,scale` for source-time samples before setpts speed.

    Output command times are local clip times; each one maps to
    ``inPoint + outputTime * speed`` before interpolating its source samples.
    """
    if (not isinstance(crop_name, str) or not crop_name.replace("_", "").isalnum()
            or not all(_finite_number(item) for item in (in_point, speed, duration)) or speed <= 0 or duration <= 0):
        raise ReframeError("主体裁剪的片段时间无效。")
    source_hash = source_hash or value.get("sourceFingerprint")
    end = in_point + duration * speed
    result = normalize_reframe(value, source_fingerprint=source_hash, source_width=source_width,
                               source_height=source_height,
                               required_start=in_point if value.get("override", {}).get("mode") == "auto" else None,
                               required_end=end if value.get("override", {}).get("mode") == "auto" else None)
    override = result["override"]
    if result["status"] != "ready" and override["mode"] == "auto":
        raise ReframeError("主体构图待人工确认，不能渲染。")
    crop_width, crop_height = crop_geometry(source_width, source_height, output_width, output_height)
    times = sorted({0.0, duration, *[_clip_time(sample["timeSeconds"], in_point, speed, duration)
                                     for sample in result["samples"]]})
    positions = [_crop_position(_center_at(result, in_point + time * speed), source_width, source_height,
                                crop_width, crop_height) for time in times]
    commands = []
    last = None
    for time, (x, y) in zip(times, positions):
        if (x, y) != last:
            commands.extend((f"{time:.4f} crop@{crop_name} x {x}", f"{time:.4f} crop@{crop_name} y {y}"))
            last = (x, y)
    if commands_path is not None:
        commands_path.write_text("\n".join(command + ";" for command in commands) + "\n", encoding="ascii")
        sendcmd = "sendcmd=f=" + commands_path.name
    else:
        escaped = "\\;".join(commands) + "\\;"
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


def _samples(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 600:
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
