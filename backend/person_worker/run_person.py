"""CPU-only MediaPipe Pose Landmarker worker for prepared CFR shot videos."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


# Keep MediaPipe's native diagnostics off the worker protocol streams.
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")


SCHEMA_VERSION = 1
MASK_THRESHOLD = 0.5
LANDMARK_CONFIDENCE_THRESHOLD = 0.5
KEY_BODY_LANDMARKS = (11, 12, 23, 24)  # left/right shoulders and hips
POSE_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8),
    (9, 10), (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21),
    (17, 19), (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24), (23, 25), (25, 27), (27, 29), (27, 31),
    (29, 31), (24, 26), (26, 28), (28, 30), (28, 32), (30, 32),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="提取 MediaPipe 33 点姿态和人体遮罩")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--model", required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        model = _require_regular(Path(args.model), "姿态模型")
        if args.check:
            _check_model(model)
            return 0
        if not args.input or not args.output:
            raise ValueError("提取模式需要 --input 和 --output")
        input_path = _require_regular(Path(args.input), "输入视频")
        output = _require_empty_directory(Path(args.output))
        run_person(input_path, output, model, args.ffmpeg)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        _emit({"status": "failed", "message": _safe_error_message(error)})
        return 1


def run_person(input_path: Path, output: Path, model: Path, ffmpeg_path: str = "ffmpeg") -> None:
    cv2, mp, vision = _runtime_modules()
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise ValueError("无法打开输入视频")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = round(float(capture.get(cv2.CAP_PROP_FRAME_WIDTH)))
    height = round(float(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    if not math.isfinite(fps) or fps <= 0 or width <= 0 or height <= 0:
        capture.release()
        raise ValueError("输入视频元数据无效")

    model_sha = sha256_file(model)
    frame_count = 0
    detected_frame_count = 0
    missing_times: list[float] = []
    multiple_times: list[float] = []
    low_confidence_times: list[float] = []
    invalid_landmark_times: list[float] = []
    unusable_mask_times: list[float] = []
    previous_timestamp = -1

    with tempfile.TemporaryDirectory(prefix=".person-", dir=output) as temporary:
        temp = Path(temporary)
        writers = _open_writers(cv2, temp, fps, width, height)
        try:
            with _landmarker(mp, vision, model) as detector, (output / "landmarks.jsonl").open("w", encoding="utf-8") as records:
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    time_seconds = round(frame_count / fps, 6)
                    timestamp_ms = max(previous_timestamp + 1, round(time_seconds * 1000))
                    previous_timestamp = timestamp_ms
                    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    with _quiet_native_stderr():
                        result = detector.detect_for_video(image, timestamp_ms)
                    person_count = len(result.pose_landmarks)
                    landmarks = _select_landmarks(result)
                    mask = _select_mask(result, width, height)

                    if person_count == 0 or landmarks is None:
                        missing_times.append(time_seconds)
                        pose_frame = _black_frame(frame.shape)
                        mask_frame = _black_frame(frame.shape)
                        overlay_frame = frame.copy()
                        record = landmark_record(time_seconds, None, person_count)
                    else:
                        detected_frame_count += 1
                        if person_count > 1:
                            multiple_times.append(time_seconds)
                        normalized, invalid = _normalize_landmarks(landmarks)
                        if invalid:
                            invalid_landmark_times.append(time_seconds)
                        if _key_body_low_confidence(normalized):
                            low_confidence_times.append(time_seconds)
                        binary_mask = threshold_mask(mask, width, height) if mask is not None else _empty_mask(width, height)
                        if mask is None or _mask_is_unusable(binary_mask):
                            unusable_mask_times.append(time_seconds)
                        pose_frame = render_pose(frame.shape, normalized)
                        mask_frame = cv2.cvtColor(binary_mask, cv2.COLOR_GRAY2BGR)
                        overlay_frame = render_overlay(frame, normalized, binary_mask)
                        record = landmark_record(time_seconds, normalized, person_count)

                    writers["pose"].write(pose_frame)
                    writers["mask"].write(mask_frame)
                    writers["overlay"].write(overlay_frame)
                    records.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")
                    frame_count += 1
                    if frame_count % 32 == 0:
                        _emit({"status": "progress", "frameCount": frame_count})
        finally:
            capture.release()
            for writer in writers.values():
                writer.release()

        if frame_count == 0:
            raise ValueError("输入视频不包含可读取的视频帧")
        for name in ("pose", "mask", "overlay"):
            _encode_h264(ffmpeg_path, temp / f"{name}.mp4", output / f"{name}.mp4")

    quality = assess_quality(
        frame_count=frame_count,
        detected_frame_count=detected_frame_count,
        missing_times_seconds=missing_times,
        multiple_person_times_seconds=multiple_times,
        low_confidence_times_seconds=low_confidence_times,
        invalid_landmark_times_seconds=invalid_landmark_times,
        unusable_mask_times_seconds=unusable_mask_times,
    )
    (output / "quality.json").write_text(json.dumps(quality, separators=(",", ":")), encoding="utf-8")
    metadata = {
        "schemaVersion": SCHEMA_VERSION,
        "model": model.name,
        "modelSha256": model_sha,
        "frameRate": fps,
        "width": width,
        "height": height,
        "frameCount": frame_count,
        "scope": "single_person",
        "poseFormat": "mediapipe33",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, separators=(",", ":")), encoding="utf-8")
    _emit({"status": quality["status"], "frameCount": frame_count})


def assess_quality(
    *,
    frame_count: int,
    detected_frame_count: int,
    missing_times_seconds: list[float],
    multiple_person_times_seconds: list[float],
    low_confidence_times_seconds: list[float] | None = None,
    invalid_landmark_times_seconds: list[float] | None = None,
    unusable_mask_times_seconds: list[float] | None = None,
) -> dict[str, Any]:
    low_confidence_times_seconds = low_confidence_times_seconds or []
    invalid_landmark_times_seconds = invalid_landmark_times_seconds or []
    unusable_mask_times_seconds = unusable_mask_times_seconds or []
    if frame_count <= 0 or detected_frame_count == 0:
        status, message = "failed", "未检测到人物，无法生成可用的人物控制素材。"
    elif any((missing_times_seconds, multiple_person_times_seconds, low_confidence_times_seconds, invalid_landmark_times_seconds, unusable_mask_times_seconds)):
        status, message = "review_required", "检测到缺失、多人或不可靠姿态/遮罩帧，需要人工检查。"
    else:
        status, message = "passed", "检测到连续单人姿态和非退化人体遮罩；请在使用前检查控制效果。"
    return {
        "status": status,
        "frameCount": frame_count,
        "detectedFrameCount": detected_frame_count,
        "missingTimesSeconds": missing_times_seconds,
        "multiplePersonTimesSeconds": multiple_person_times_seconds,
        "lowConfidenceTimesSeconds": low_confidence_times_seconds,
        "invalidLandmarkTimesSeconds": invalid_landmark_times_seconds,
        "unusableMaskTimesSeconds": unusable_mask_times_seconds,
        "message": message,
    }


def landmark_record(time_seconds: float, landmarks: list[dict[str, float]] | None, person_count: int) -> dict[str, Any]:
    if landmarks is None:
        return {"timeSeconds": time_seconds, "landmarks33": None, "presence": None, "visibility": None, "personCount": person_count}
    return {
        "timeSeconds": time_seconds,
        "landmarks33": landmarks,
        "presence": [point["presence"] for point in landmarks],
        "visibility": [point["visibility"] for point in landmarks],
        "personCount": person_count,
    }


def threshold_mask(mask: Any, width: int, height: int):
    import cv2
    import numpy as np

    values = np.asarray(mask, dtype=np.float32)
    if values.ndim == 3 and values.shape[2] == 1:
        values = values[:, :, 0]
    if values.ndim != 2 or not np.isfinite(values).all():
        return _empty_mask(width, height)
    if values.shape != (height, width):
        values = cv2.resize(values, (width, height), interpolation=cv2.INTER_LINEAR)
    return (values >= MASK_THRESHOLD).astype(np.uint8) * 255


def render_pose(frame_shape: tuple[int, ...], landmarks: list[dict[str, float]] | None):
    import cv2

    canvas = _black_frame(frame_shape)
    if landmarks is None or len(landmarks) != 33:
        return canvas
    height, width = canvas.shape[:2]
    points = [_pixel_point(point, width, height) for point in landmarks]
    for first, second in POSE_CONNECTIONS:
        if points[first] is not None and points[second] is not None:
            cv2.line(canvas, points[first], points[second], (0, 220, 0), 2, cv2.LINE_AA)
    for point in points:
        if point is not None:
            cv2.circle(canvas, point, 3, (255, 255, 255), -1, cv2.LINE_AA)
    return canvas


def render_overlay(frame, landmarks: list[dict[str, float]] | None, binary_mask):
    import cv2
    import numpy as np

    output = frame.copy()
    foreground = binary_mask > 0
    tint = np.zeros_like(output)
    tint[:, :, 1] = 180
    output[foreground] = cv2.addWeighted(output[foreground], 0.55, tint[foreground], 0.45, 0)
    pose = render_pose(frame.shape, landmarks)
    output[pose.max(axis=2) > 0] = pose[pose.max(axis=2) > 0]
    return output


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_modules():
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    return cv2, mp, vision


def _landmarker(mp, vision, model: Path):
    base_options = mp.tasks.BaseOptions(model_asset_path=str(model))
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.VIDEO,
        num_poses=2,
        output_segmentation_masks=True,
    )
    # MediaPipe/TFLite writes initialization chatter from native code directly
    # to file descriptor 2. The worker protocol reserves stdio for short JSON.
    with _quiet_native_stderr():
        return vision.PoseLandmarker.create_from_options(options)


def _check_model(model: Path) -> None:
    _, mp, vision = _runtime_modules()
    with _landmarker(mp, vision, model):
        pass
    _emit({"status": "passed", "model": model.name, "modelSha256": sha256_file(model), "mediapipeVersion": mp.__version__})


def _open_writers(cv2, directory: Path, fps: float, width: int, height: int):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writers = {name: cv2.VideoWriter(str(directory / f"{name}.mp4"), fourcc, fps, (width, height)) for name in ("pose", "mask", "overlay")}
    if not all(writer.isOpened() for writer in writers.values()):
        for writer in writers.values():
            writer.release()
        raise RuntimeError("无法初始化视频编码器")
    return writers


def _encode_h264(ffmpeg_path: str, source: Path, destination: Path) -> None:
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination)],
            capture_output=True, text=True, timeout=120, check=False, shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("FFmpeg H.264 编码器不可用") from error
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("FFmpeg H.264 编码失败")


def _select_landmarks(result) -> Iterable[Any] | None:
    return result.pose_landmarks[0] if result.pose_landmarks else None


def _select_mask(result, width: int, height: int):
    masks = result.segmentation_masks
    if not masks:
        return None
    values = masks[0].numpy_view()
    return values[:, :, 0] if getattr(values, "ndim", 0) == 3 else values


def _normalize_landmarks(landmarks: Iterable[Any]) -> tuple[list[dict[str, float]], bool]:
    normalized: list[dict[str, float]] = []
    invalid = False
    for landmark in landmarks:
        point = {key: float(getattr(landmark, key, 0.0)) for key in ("x", "y", "z", "presence", "visibility")}
        if not all(math.isfinite(value) for value in point.values()) or not (0.0 <= point["x"] <= 1.0 and 0.0 <= point["y"] <= 1.0 and 0.0 <= point["presence"] <= 1.0 and 0.0 <= point["visibility"] <= 1.0):
            invalid = True
            point = {"x": -1.0, "y": -1.0, "z": 0.0, "presence": 0.0, "visibility": 0.0}
        normalized.append(point)
    if len(normalized) != 33:
        invalid = True
        normalized = []
    return normalized, invalid


def _key_body_low_confidence(landmarks: list[dict[str, float]]) -> bool:
    return len(landmarks) != 33 or any(min(landmarks[index]["presence"], landmarks[index]["visibility"]) < LANDMARK_CONFIDENCE_THRESHOLD for index in KEY_BODY_LANDMARKS)


def _pixel_point(point: dict[str, float], width: int, height: int) -> tuple[int, int] | None:
    x, y = point.get("x"), point.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or not math.isfinite(x) or not math.isfinite(y) or not (0 <= x <= 1 and 0 <= y <= 1):
        return None
    return min(width - 1, max(0, round(x * (width - 1)))), min(height - 1, max(0, round(y * (height - 1))))


def _black_frame(frame_shape: tuple[int, ...]):
    import numpy as np

    return np.zeros(frame_shape, dtype=np.uint8)


def _empty_mask(width: int, height: int):
    import numpy as np

    return np.zeros((height, width), dtype=np.uint8)


def _mask_is_unusable(mask) -> bool:
    import numpy as np

    foreground_count = int(np.count_nonzero(mask))
    return foreground_count == 0 or foreground_count == mask.size


def _require_regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label}必须是存在的普通文件")
    return path.resolve()


def _require_empty_directory(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
        raise ValueError("输出目录必须是已创建的空目录，且不能是符号链接")
    return path.resolve()


def _safe_error_message(error: BaseException) -> str:
    if isinstance(error, ValueError):
        return str(error)
    return "人物姿态与遮罩提取失败，请检查本地模型、输入视频和 FFmpeg。"


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, separators=(",", ":"), allow_nan=False), flush=True)


@contextlib.contextmanager
def _quiet_native_stderr():
    saved_stderr = os.dup(2)
    try:
        with open(os.devnull, "w", encoding="utf-8") as sink:
            os.dup2(sink.fileno(), 2)
            yield
    finally:
        os.dup2(saved_stderr, 2)
        os.close(saved_stderr)


if __name__ == "__main__":
    raise SystemExit(main())
