"""仅由独立 Python 3.11 worker 环境调用的 Video Depth Anything CLI。"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

UPSTREAM_COMMIT = "4f5ae23172ba60fd7bc11ef671cca678842c7072"
CHECKPOINT_SHA256 = "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609"
MPS_MEMORY_FRACTION = 0.5
MODEL_IDENTITY = {
    "modelId": "video-depth-anything-small-relative",
    "upstreamCommit": UPSTREAM_COMMIT,
    "checkpointSha256": CHECKPOINT_SHA256,
}
NORMALIZATION_PERCENTILE_POLICY = {
    "scope": "clip",
    "lowerPercentile": 2,
    "upperPercentile": 98,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="隔离运行 Video Depth Anything Small relative-depth 推理")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--upstream-root", required=True)
    parser.add_argument("--device", choices=("cuda", "mps", "cpu"), required=True)
    parser.add_argument("--target-fps", type=int, choices=(8, 16, 24), required=True)
    parser.add_argument("--input-size", type=int, choices=(350, 420, 518), required=True)
    parser.add_argument("--max-res", type=int, choices=(640, 960, 1280), required=True)
    parser.add_argument("--output-short-side", type=int, choices=(480, 720), required=True)
    parser.add_argument("--backbone-microbatch", type=int, choices=(1, 2, 4), required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = _require_regular(Path(args.input), "输入视频")
    checkpoint = _require_regular(Path(args.checkpoint), "模型检查点")
    output = _require_empty_directory(Path(args.output))
    upstream = _require_checkout(Path(args.upstream_root))
    _verify_sha256(checkpoint)

    global np
    if args.device == "mps":
        # Torch 2.1 的 MPS 后端缺少部分上采样算子；仅让这些算子回退 CPU。
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    import cv2
    import numpy as np
    import torch

    _require_device(torch, args.device)
    sys.path.insert(0, str(upstream))
    from video_depth_anything.video_depth import VideoDepthAnything

    model = VideoDepthAnything(
        encoder="vits", features=64, out_channels=[48, 96, 192, 384],
    )
    state = torch.load(checkpoint, map_location="cpu")
    model.load_state_dict(state, strict=True)
    model = model.to(args.device).eval()
    _install_backbone_microbatch(model, torch, args.backbone_microbatch)
    _infer_chunked_to_gray(
        cv2, torch, model, input_path, output, args.device, args.target_fps,
        args.input_size, args.max_res, args.output_short_side, args.backbone_microbatch,
    )
    return 0


def _require_regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label}必须是存在的普通文件")
    return path.resolve()


def _require_empty_directory(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
        raise ValueError("输出目录必须是已创建的空目录，且不能是符号链接")
    return path.resolve()


def _require_checkout(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("上游源码目录无效")
    commit = _checkout_commit(path)
    if commit != UPSTREAM_COMMIT:
        raise ValueError("上游源码不是要求的固定提交")
    return path.resolve()


def _checkout_commit(root: Path) -> str:
    try:
        head = _git_output(root, ["rev-parse", "HEAD"])
        status = _git_output(root, ["status", "--porcelain=v1", "--untracked-files=all"])
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("无法验证上游 Git checkout") from error
    if status:
        raise ValueError("上游 Git checkout 必须保持干净")
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise ValueError("上游源码提交标识无效")
    return head


def _git_output(root: Path, arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True,
        timeout=5, check=False, shell=False,
    )
    if result.returncode != 0:
        raise OSError("Git 命令失败")
    return result.stdout.strip()


def _verify_sha256(checkpoint: Path) -> None:
    digest = hashlib.sha256()
    with checkpoint.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != CHECKPOINT_SHA256:
        raise ValueError("模型检查点 SHA-256 不匹配")


def _require_device(torch, device: str) -> None:
    available = {
        "cuda": bool(torch.cuda.is_available()),
        "mps": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()),
        "cpu": True,
    }
    if not available[device]:
        raise RuntimeError(f"requested device unavailable: {device}")


def _read_frames(cv2, input_path: Path, target_fps: int, max_res: int) -> tuple[np.ndarray, float]:
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise ValueError("无法打开输入视频")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    if not math.isfinite(source_fps) or source_fps <= 0:
        capture.release()
        raise ValueError("输入视频帧率无效")
    frame_count = round(float(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
    if frame_count <= 0:
        capture.release()
        raise ValueError("输入视频帧数无效")
    selected_indices, effective_fps = _sample_frame_indices(source_fps, target_fps, frame_count)
    frames: list[np.ndarray] = []
    index = 0
    next_selected = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if next_selected < len(selected_indices) and index == selected_indices[next_selected]:
                height, width = frame.shape[:2]
                target_width, target_height = _even_dimensions(width, height, max_res)
                if (target_width, target_height) != (width, height):
                    frame = cv2.resize(
                        frame, (target_width, target_height), interpolation=cv2.INTER_AREA,
                    )
                frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                next_selected += 1
            index += 1
    finally:
        capture.release()
    if len(frames) != len(selected_indices):
        raise ValueError("输入视频帧数在读取期间发生变化")
    return np.asarray(frames), effective_fps


def _output_dimensions(width: int, height: int, short_side: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("输入视频尺寸无效")
    scale = short_side / min(width, height)
    scaled_width = max(2, int(round(width * scale)))
    scaled_height = max(2, int(round(height * scale)))
    return scaled_width + scaled_width % 2, scaled_height + scaled_height % 2


def _chunked_frames(cv2, input_path: Path, max_res: int, *, chunk_size: int = 64, overlap: int = 8):
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise ValueError("无法打开输入视频")
    fps = _validated_worker_fps(float(capture.get(cv2.CAP_PROP_FPS)))
    previous: list = []
    try:
        while True:
            fresh = []
            while len(fresh) < chunk_size - len(previous):
                ok, frame = capture.read()
                if not ok:
                    break
                height, width = frame.shape[:2]
                target_width, target_height = _even_dimensions(width, height, max_res)
                if (target_width, target_height) != (width, height):
                    frame = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
                fresh.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if not fresh:
                break
            frames = previous + fresh
            yield np.asarray(frames), fps, len(previous)
            previous = frames[-overlap:]
    finally:
        capture.release()


def _align_depth_chunk(depths, prior_tail):
    if prior_tail is None:
        return depths
    overlap = min(len(prior_tail), len(depths))
    if not overlap:
        return depths
    left = prior_tail[-overlap:].astype(np.float32, copy=False)
    right = depths[:overlap].astype(np.float32, copy=False)
    left_mean, right_mean = float(left.mean()), float(right.mean())
    left_std, right_std = float(left.std()), float(right.std())
    scale = left_std / right_std if left_std > 1e-6 and right_std > 1e-6 else 1.0
    if not math.isfinite(scale):
        scale = 1.0
    return depths.astype(np.float32, copy=False) * scale + (left_mean - right_mean * scale)


def _install_backbone_microbatch(model, torch, microbatch: int) -> None:
    """拆分无跨帧状态的 DINO 空间 backbone；32 帧 temporal head 保持原调用。"""
    original = model.pretrained.get_intermediate_layers

    def get_intermediate_layers(frames, *args, **kwargs):
        if len(frames) <= microbatch:
            return original(frames, *args, **kwargs)
        parts = [
            original(frames[start:start + microbatch], *args, **kwargs)
            for start in range(0, len(frames), microbatch)
        ]
        if kwargs.get("return_class_token", False):
            return tuple(
                tuple(torch.cat([part[layer][item] for part in parts], dim=0) for item in range(2))
                for layer in range(len(parts[0]))
            )
        return tuple(torch.cat([part[layer] for part in parts], dim=0) for layer in range(len(parts[0])))

    model.pretrained.get_intermediate_layers = get_intermediate_layers


def _infer_chunked_to_gray(cv2, torch, model, input_path: Path, output: Path, device: str, target_fps: int, input_size: int, max_res: int, output_short_side: int, backbone_microbatch: int) -> None:
    chunks: list[Path] = []
    source_motion: list[dict[str, float]] = []
    prior_tail = None
    prior_frame = None
    frame_rate = None
    output_size = None
    inference_size = None
    frame_count = 0
    for index, (frames, fps, overlap) in enumerate(_chunked_frames(cv2, input_path, max_res)):
        frame_rate = fps if frame_rate is None else frame_rate
        inference_size = (int(frames.shape[2]), int(frames.shape[1]))
        output_size = _output_dimensions(*inference_size, output_short_side)
        for frame in frames[overlap:]:
            if prior_frame is not None:
                source_motion.append({"timestampSeconds": frame_count / fps, "magnitude": float(np.abs(frame.astype(np.float32) - prior_frame.astype(np.float32)).mean() / 255.0)})
            prior_frame = frame
            frame_count += 1
        with torch.no_grad():
            values, worker_fps = _infer_depths(torch, model, frames, fps, input_size, device)
        if not math.isclose(_validated_worker_fps(worker_fps), fps, rel_tol=0, abs_tol=1e-6):
            raise ValueError("模型输出帧率与输入时间线不一致")
        values = np.asarray(values)
        if values.ndim != 3 or values.shape[0] != len(frames) or not np.isfinite(values).all():
            raise ValueError("模型深度输出无效")
        values = _align_depth_chunk(values, prior_tail)
        if not np.isfinite(values).all():
            raise ValueError("模型深度输出包含非有限值")
        prior_tail = values[-8:].copy()
        values = values[overlap:]
        if len(values) != len(frames) - overlap:
            raise ValueError("模型输出帧数与输入不一致")
        path = output / f".depth-chunk-{index:04d}.npy"
        np.save(path, values)
        chunks.append(path)
    if not chunks or frame_rate is None or output_size is None or inference_size is None:
        raise ValueError("输入视频不包含可读取的视频帧")
    samples = np.concatenate([np.load(path, mmap_mode="r").reshape(-1)[::max(1, np.load(path, mmap_mode="r").size // 8192)] for path in chunks])
    low, high = (float(value) for value in np.percentile(samples, _normalization_percentiles()))
    plan = _normalization_plan(low, high)
    gray = output / "depths.gray"
    with gray.open("wb") as target:
        for path in chunks:
            values = np.load(path, mmap_mode="r")
            for frame in values:
                normalized = np.full_like(frame, 0.5, dtype=np.float32) if plan is None else np.clip(_relative_scale(frame, *plan), 0.0, 1.0)
                resized = cv2.resize(normalized, output_size, interpolation=cv2.INTER_LINEAR)
                target.write(np.rint(resized * 255).astype(np.uint8).tobytes())
            path.unlink()
    _write_output(output, frame_count, output_size, inference_size, frame_rate, device, target_fps, input_size, max_res, output_short_side, backbone_microbatch, source_motion)


def _sample_frame_indices(source_fps: float, target_fps: int, frame_count: int) -> tuple[list[int], float]:
    effective_fps = min(source_fps, float(target_fps))
    if not math.isfinite(effective_fps) or effective_fps <= 0 or frame_count <= 0:
        raise ValueError("输入视频采样参数无效")
    last_source_time = (frame_count - 1) / source_fps
    sample_count = math.floor(last_source_time * effective_fps + 1e-9) + 1
    indices = [min(frame_count - 1, math.floor(index * source_fps / effective_fps)) for index in range(sample_count)]
    if len(indices) != len(set(indices)):
        raise ValueError("输入视频采样出现重复帧")
    return indices, effective_fps


def _even_dimensions(width: int, height: int, max_res: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("输入视频尺寸无效")
    scale = min(1.0, max_res / max(width, height))
    scaled_width = max(2, int(math.floor(width * scale)))
    scaled_height = max(2, int(math.floor(height * scale)))
    return scaled_width - scaled_width % 2, scaled_height - scaled_height % 2


def _normalize_depths(depths: np.ndarray, *, numpy_module=None) -> np.ndarray:
    numpy_module = np if numpy_module is None else numpy_module
    if depths.ndim != 3 or not depths.size or not numpy_module.issubdtype(depths.dtype, numpy_module.number):
        raise ValueError("模型深度输出无效")
    depths = depths.astype(numpy_module.float32, copy=False)
    if not numpy_module.isfinite(depths).all():
        raise ValueError("模型深度输出包含非有限值")
    low, high = (float(value) for value in numpy_module.percentile(depths, _normalization_percentiles()))
    plan = _normalization_plan(low, high)
    if plan is None:
        return numpy_module.full_like(depths, 0.5, dtype=numpy_module.float32)
    return numpy_module.clip(_relative_scale(depths, *plan), 0.0, 1.0)


def _normalization_percentiles() -> tuple[float, float]:
    return (2.0, 98.0)


def _normalization_plan(low: float, high: float) -> tuple[float, float] | None:
    if not math.isfinite(low) or not math.isfinite(high):
        raise ValueError("模型深度归一化边界无效")
    return (low, high) if high > low else None


def _relative_scale(values, low: float, high: float):
    return (values - low) / (high - low)


def _source_motion_samples(frames: np.ndarray, worker_fps: float, *, numpy_module=None) -> list[dict[str, float]]:
    numpy_module = np if numpy_module is None else numpy_module
    if not math.isfinite(worker_fps) or worker_fps <= 0:
        raise ValueError("来源运动帧率无效")
    magnitudes = []
    for index in range(1, len(frames)):
        difference = numpy_module.abs(frames[index].astype(numpy_module.float32) - frames[index - 1].astype(numpy_module.float32))
        magnitudes.append(float(difference.mean() / 255.0))
    return _source_motion_metadata(magnitudes, worker_fps)


def _source_motion_metadata(magnitudes, worker_fps: float) -> list[dict[str, float]]:
    worker_fps = _validated_worker_fps(worker_fps)
    samples = []
    for index, magnitude in enumerate(magnitudes, start=1):
        if not math.isfinite(float(magnitude)):
            raise ValueError("来源运动幅度无效")
        samples.append({
            "timestampSeconds": index / worker_fps,
            "magnitude": max(0.0, min(1.0, float(magnitude))),
        })
    return samples


def _validated_worker_fps(value) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("输出帧率无效")
    return float(value)


def _infer_depths(torch, model, frames, frame_rate: float, input_size: int, device: str):
    fp32 = device in ("cpu", "mps")
    if device != "mps":
        return model.infer_video_depth(
            frames, frame_rate, input_size=input_size, device=device, fp32=fp32,
        )
    original_autocast = torch.autocast
    had_instance_autocast = "autocast" in getattr(torch, "__dict__", {})
    # 使用 Metal 推荐工作集的一半，避免默认分配上限高于机器物理内存。
    torch.mps.set_per_process_memory_fraction(MPS_MEMORY_FRACTION)
    try:
        torch.autocast = lambda *args, **kwargs: contextlib.nullcontext()
        return model.infer_video_depth(
            frames, frame_rate, input_size=input_size, device=device, fp32=True,
        )
    finally:
        if had_instance_autocast:
            torch.autocast = original_autocast
        else:
            delattr(torch, "autocast")
        # 上游已将结果转回 CPU；每段释放空闲缓存，避免长片持续挤占统一内存。
        torch.mps.synchronize()
        torch.mps.empty_cache()


def _write_output(
    output: Path,
    frame_count: int,
    output_size: tuple[int, int],
    inference_size: tuple[int, int],
    frame_rate: float,
    device: str,
    target_fps: int,
    input_size: int,
    max_res: int,
    output_short_side: int,
    backbone_microbatch: int,
    source_motion_samples: list[dict[str, float]],
) -> None:
    if not math.isfinite(frame_rate) or frame_rate <= 0:
        raise ValueError("输出帧率无效")
    metadata = {
        "schemaVersion": 1,
        "modelIdentity": MODEL_IDENTITY,
        "device": device,
        "targetFps": target_fps,
        "inputSize": input_size,
        "maxRes": max_res,
        "outputShortSide": output_short_side,
        "frameCount": frame_count,
        "width": output_size[0],
        "height": output_size[1],
        "inferenceDimensions": {"width": inference_size[0], "height": inference_size[1]},
        "outputDimensions": {"width": output_size[0], "height": output_size[1]},
        "chunkFrames": 64,
        "chunkOverlapFrames": 8,
        "backboneMicrobatch": backbone_microbatch,
        "timelineMode": "cfr_opencv",
        "mpsFallbackEnabled": device == "mps" and os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1",
        "mpsMemoryFraction": MPS_MEMORY_FRACTION if device == "mps" else None,
        "frameRate": frame_rate,
        "depthMin": 0.0,
        "depthMax": 1.0,
        "finite": True,
        "normalizationDirection": "near_white_far_black",
        "normalizationPercentilePolicy": NORMALIZATION_PERCENTILE_POLICY,
        "sourceMotionSamples": source_motion_samples,
    }
    (output / "worker-metadata.json").write_text(json.dumps(metadata, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
