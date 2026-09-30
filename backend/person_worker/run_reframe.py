"""Local MediaPipe EfficientDet subject detector for montage reframing.

Protocol: JSON result on stdout only.  The caller owns durable state and
cancellation; this process never uploads media or downloads model weights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

MODEL_SHA256 = "2e04c53bfeac0ac2a30c057c7e2a777594ce39baaac35a92f74fb1e8c4fc4e0b"
DETECTOR_VERSION = "mediapipe-object-detector-0.10.21"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="本地对象检测，用于主体满幅裁剪")
    value.add_argument("--input", required=True)
    value.add_argument("--model", required=True)
    value.add_argument("--start", required=True, type=float)
    value.add_argument("--end", required=True, type=float)
    value.add_argument("--target-width", required=True, type=int)
    value.add_argument("--target-height", required=True, type=int)
    value.add_argument("--sample-fps", type=float, default=2.0)
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        source, model = Path(args.input), Path(args.model)
        if not source.is_file() or not model.is_file() or sha256(model) != MODEL_SHA256:
            raise ValueError("输入或固定检测模型不可用。")
        if (not math.isfinite(args.start) or not math.isfinite(args.end) or args.start < 0 or args.end <= args.start
                or args.target_width <= 0 or args.target_height <= 0 or not math.isfinite(args.sample_fps)
                or not .1 <= args.sample_fps <= 10):
            raise ValueError("主体检测区间或采样率无效。")
        result = analyze(source, model, args.start, args.end, args.target_width, args.target_height, args.sample_fps)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "message": str(error)[:300] or "本地主体检测失败。"}, ensure_ascii=False), file=sys.stderr)
        return 1


def analyze(source: Path, model: Path, start: float, end: float, target_width: int, target_height: int,
            sample_fps: float) -> dict:
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise ValueError("无法打开主体检测视频。")
    width, height = round(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    if width <= 0 or height <= 0 or not math.isfinite(fps) or fps <= 0:
        capture.release()
        raise ValueError("主体检测视频元数据无效。")
    options = vision.ObjectDetectorOptions(
        base_options=python.BaseOptions(model_asset_path=str(model)), running_mode=vision.RunningMode.VIDEO,
        score_threshold=.35, max_results=10,
    )
    samples, reasons, last_timestamp = [], [], -1
    expected = max(2, round((end - start) * sample_fps) + 1)
    times = [start + (end - start) * index / (expected - 1) for index in range(expected)]
    try:
        with vision.ObjectDetector.create_from_options(options) as detector:
            for time_seconds in times:
                capture.set(cv2.CAP_PROP_POS_MSEC, time_seconds * 1000)
                ok, frame = capture.read()
                if not ok:
                    reasons.append("部分源时间无法解码")
                    continue
                actual_time = max(time_seconds, capture.get(cv2.CAP_PROP_POS_MSEC) / 1000 - 1 / fps)
                timestamp = max(last_timestamp + 1, round(actual_time * 1000))
                last_timestamp = timestamp
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                detected = detector.detect_for_video(image, timestamp)
                boxes = []
                for detection in detected.detections:
                    category = detection.categories[0] if detection.categories else None
                    box = detection.bounding_box
                    if category is None or not category.category_name or box.width <= 0 or box.height <= 0:
                        continue
                    boxes.append({"x": max(0, box.origin_x / width), "y": max(0, box.origin_y / height),
                                  "width": min(1, box.width / width), "height": min(1, box.height / height),
                                  "label": category.category_name, "confidence": float(category.score)})
                person = [box for box in boxes if box["label"].lower() == "person"]
                if len(person) != 1:
                    reasons.append("未检测到唯一人物主体" if not person else "检测到多个可能主体")
                    continue
                subject = person[0]
                samples.append({"timeSeconds": round(actual_time, 6), "boxes": boxes,
                                "center": {"x": round(subject["x"] + subject["width"] / 2, 6),
                                           "y": round(subject["y"] + subject["height"] / 2, 6)},
                                "confidence": round(subject["confidence"], 6)})
    finally:
        capture.release()
    reasons = list(dict.fromkeys(reasons))
    if len(samples) != len(times):
        reasons.append("检测结果不连续，需人工确认构图")
    status = "ready" if not reasons else "review_required"
    return {"schemaVersion": 1, "sourceFingerprint": sha256(source), "sourceWidth": width, "sourceHeight": height,
            "targetAspect": {"width": target_width, "height": target_height}, "detectorVersion": DETECTOR_VERSION,
            "modelSha256": MODEL_SHA256, "samples": samples, "uncertaintyReasons": reasons, "status": status,
            "override": {"mode": "auto", "keyframes": []}}


if __name__ == "__main__":
    raise SystemExit(main())
