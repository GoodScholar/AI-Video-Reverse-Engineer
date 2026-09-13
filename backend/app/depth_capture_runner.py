import json
import logging
import math
import re
import shutil
import stat
import subprocess
import sys
import zipfile
import zlib
from array import array
from ast import literal_eval
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

from .depth_capture import DepthQualityAssessment
from .depth_capture_storage import (
    DEPTH_ARTIFACTS,
    commit_depth_artifacts,
    create_depth_capture_workspace,
    depth_capture_directory,
)
from .depth_quality import DepthQualityInput, SourceMotionSample, assess_depth_quality
from .reference_video import validate_storage_id


DEPTH_TIMEOUT_SECONDS = 900
FFMPEG_TIMEOUT_SECONDS = 180
MAX_DEPTH_SECONDS = 10
MODEL_IDENTITY = {
    "modelId": "video-depth-anything-small-relative",
    "upstreamCommit": "4f5ae23172ba60fd7bc11ef671cca678842c7072",
    "checkpointSha256": "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609",
}
NORMALIZATION_PERCENTILE_POLICY = {
    "scope": "clip",
    "lowerPercentile": 2,
    "upperPercentile": 98,
}
Device = Literal["cuda", "mps", "cpu"]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class _DepthFrames:
    values: array
    frame_count: int
    height: int
    width: int


class DepthCaptureFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = True


@dataclass(frozen=True)
class DepthCaptureRequest:
    data_dir: Path
    project_id: str
    capture_id: str
    source_reference_video_id: str
    input_video: Path
    checkpoint: Path
    upstream_root: Path
    execution_device: Device
    expected_duration_seconds: float

    def __post_init__(self) -> None:
        if type(self.expected_duration_seconds) not in (int, float) or not math.isfinite(self.expected_duration_seconds) or self.expected_duration_seconds <= 0:
            raise ValueError("expected_duration_seconds 必须是正的有限数值")


@dataclass(frozen=True)
class DepthCaptureRunResult:
    directory: Path
    executionDevice: Device
    frameCount: int
    width: int
    height: int
    frameRate: float
    qualityAssessment: DepthQualityAssessment


def run_depth_capture(
    request: DepthCaptureRequest,
    worker_python: str,
    worker_script: Path,
    ffmpeg_path: str,
    *,
    run: CommandRunner = subprocess.run,
    on_stage_started: Optional[Callable[[str], None]] = None,
    on_stage_completed: Optional[Callable[[str], None]] = None,
) -> DepthCaptureRunResult:
    stage_started = on_stage_started or (lambda _: None)
    stage_completed = on_stage_completed or (lambda _: None)
    workspace = None
    try:
        stage_started("preparing")
        _validate_request_paths(request)
        profile = _device_profile(request.execution_device)
        workspace = create_depth_capture_workspace(
            request.data_dir, request.project_id, request.capture_id,
        )
        stage_completed("preparing")
        worker_output = workspace / "worker-output"
        worker_output.mkdir(mode=0o700)
        command: list[str] = [
            worker_python, str(worker_script),
            "--input", str(request.input_video),
            "--output", str(worker_output),
            "--checkpoint", str(request.checkpoint),
            "--upstream-root", str(request.upstream_root),
            "--device", request.execution_device,
            "--target-fps", str(profile.target_fps),
            "--input-size", str(profile.input_size),
            "--max-res", str(profile.max_res),
        ]
        stage_started("estimatingDepth")
        _run_worker(command, run)
        depths, worker_metadata = _read_worker_output(worker_output, request, profile)
        source_motion_samples = _validate_source_motion_samples(worker_metadata.get("sourceMotionSamples"))
        shutil.rmtree(worker_output)
        stage_completed("estimatingDepth")
        stage_started("encoding")
        _encode_depth_variants(
            depths, frame_rate=worker_metadata["frameRate"], workspace=workspace,
            ffmpeg_path=ffmpeg_path, run=run,
        )
        stage_completed("encoding")
        stage_started("qualityAssessment")
        quality_assessment = assess_depth_quality(DepthQualityInput(
            values=depths.values,
            frame_count=depths.frame_count,
            height=depths.height,
            width=depths.width,
            frame_rate=worker_metadata["frameRate"],
            expected_duration_seconds=request.expected_duration_seconds,
            source_motion_samples=source_motion_samples,
        ), input_fully_validated=True)
        _write_final_metadata(workspace, worker_metadata, quality_assessment)
        _write_manifest(workspace, request.source_reference_video_id)
        directory = commit_depth_artifacts(
            workspace,
            data_dir=request.data_dir,
            project_id=request.project_id,
            capture_id=request.capture_id,
            source_reference_video_id=request.source_reference_video_id,
        )
        stage_completed("qualityAssessment")
        return DepthCaptureRunResult(
            directory=directory,
            executionDevice=request.execution_device,
            frameCount=worker_metadata["frameCount"],
            width=worker_metadata["width"],
            height=worker_metadata["height"],
            frameRate=worker_metadata["frameRate"],
            qualityAssessment=quality_assessment,
        )
    except DepthCaptureFailure:
        if workspace is not None:
            _discard_workspace(workspace, request)
        raise
    except (OSError, ValueError, json.JSONDecodeError) as error:
        if workspace is not None:
            _discard_workspace(workspace, request)
        raise DepthCaptureFailure("depth_capture_storage_failed", "深度捕捉结果无法安全保存，请重试。") from error


@dataclass(frozen=True)
class _DeviceProfile:
    target_fps: int
    input_size: int
    max_res: int


def _device_profile(device: Device) -> _DeviceProfile:
    if device == "cuda":
        return _DeviceProfile(target_fps=16, input_size=518, max_res=1280)
    if device in ("mps", "cpu"):
        return _DeviceProfile(target_fps=8, input_size=350, max_res=640)
    raise DepthCaptureFailure("depth_device_unavailable", "所选深度计算设备不受支持或不可用。")


def _validate_request_paths(request: DepthCaptureRequest) -> None:
    try:
        validate_storage_id(request.project_id)
        validate_storage_id(request.capture_id)
        validate_storage_id(request.source_reference_video_id)
    except ValueError as error:
        raise DepthCaptureFailure("depth_input_invalid", "深度捕捉请求标识无效。") from error
    root = Path(request.data_dir).absolute()
    expected_parent = root / "project-files" / request.project_id / "reference-videos"
    expected_candidates = {
        expected_parent / f"{request.source_reference_video_id}.mp4",
        expected_parent / f"{request.source_reference_video_id}.mov",
    }
    if root.is_symlink() or not root.is_dir() or request.input_video.absolute() not in expected_candidates:
        raise DepthCaptureFailure("depth_input_invalid", "输入视频不是受管的参考视频。")
    _reject_symlinks_below(root, request.input_video.absolute())
    if request.input_video.is_symlink() or not request.input_video.is_file() or not stat.S_ISREG(request.input_video.stat().st_mode):
        raise DepthCaptureFailure("depth_input_invalid", "输入视频路径无效。")
    for path, label in ((request.checkpoint, "模型检查点"),):
        if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
            raise DepthCaptureFailure("depth_input_invalid", f"{label}路径无效。")
    if request.upstream_root.is_symlink() or not request.upstream_root.is_dir():
        raise DepthCaptureFailure("depth_input_invalid", "模型源码路径无效。")


def _reject_symlinks_below(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise DepthCaptureFailure("depth_input_invalid", "输入视频路径无效。") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise DepthCaptureFailure("depth_input_invalid", "输入视频路径无效。")


def _run_worker(command: list[str], run: CommandRunner) -> None:
    try:
        result = run(
            command, capture_output=True, text=True, timeout=DEPTH_TIMEOUT_SECONDS,
            check=False, shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise DepthCaptureFailure("depth_capture_timeout", "深度估计超时，请降低视频时长后重试。") from error
    except (FileNotFoundError, PermissionError) as error:
        raise DepthCaptureFailure("depth_worker_unavailable", "本地深度计算器不可用，请检查本地安装。") from error
    if result.returncode == 0:
        return
    diagnostic = (result.stderr or "")[:2000].lower()
    logging.getLogger(__name__).warning("深度 worker 失败：exitCode=%s stderrChars=%s", result.returncode, len(diagnostic))
    if "out of memory" in diagnostic or "cuda oom" in diagnostic:
        raise DepthCaptureFailure("depth_device_out_of_memory", "所选设备内存不足，无法完成深度估计。")
    if "unavailable" in diagnostic or "unsupported" in diagnostic or "not available" in diagnostic:
        raise DepthCaptureFailure("depth_device_unavailable", "所选深度计算设备不受支持或不可用。")
    raise DepthCaptureFailure("depth_worker_failed", "本地深度估计失败，请检查输入视频和本地模型配置后重试。")


def _read_worker_output(
    output: Path,
    request: DepthCaptureRequest,
    profile: _DeviceProfile,
) -> tuple[_DepthFrames, dict]:
    expected = {"depths.npz", "worker-metadata.json"}
    try:
        names = {path.name for path in output.iterdir()}
    except OSError as error:
        raise DepthCaptureFailure("depth_worker_output_invalid", "深度计算器未生成有效结果。") from error
    if names != expected or any((output / name).is_symlink() or not (output / name).is_file() for name in expected):
        raise DepthCaptureFailure("depth_worker_output_invalid", "深度计算器输出无效。")
    try:
        depths = _load_float32_depths(output / "depths.npz", profile)
        metadata = json.loads((output / "worker-metadata.json").read_text(encoding="utf-8"))
    except (
        OSError, ValueError, UnicodeError, SyntaxError, RuntimeError, NotImplementedError, zlib.error,
        json.JSONDecodeError, zipfile.BadZipFile,
    ) as error:
        raise DepthCaptureFailure("depth_worker_output_invalid", "深度计算器输出无效。") from error
    if not isinstance(metadata, dict):
        raise DepthCaptureFailure("depth_worker_output_invalid", "深度计算器输出无效。")
    if depths.frame_count < 1 or not all(math.isfinite(value) for value in depths.values):
        raise DepthCaptureFailure("depth_worker_output_invalid", "深度计算器输出无效。")
    _validate_worker_metadata(metadata, depths, request, profile)
    return depths, metadata


def _validate_worker_metadata(
    metadata: dict,
    depths: _DepthFrames,
    request: DepthCaptureRequest,
    profile: _DeviceProfile,
) -> None:
    expected = {
        "schemaVersion": 1,
        "modelIdentity": MODEL_IDENTITY,
        "device": request.execution_device,
        "targetFps": profile.target_fps,
        "inputSize": profile.input_size,
        "maxRes": profile.max_res,
        "frameCount": depths.frame_count,
        "width": depths.width,
        "height": depths.height,
        "finite": True,
        "normalizationDirection": "near_white_far_black",
        "normalizationPercentilePolicy": NORMALIZATION_PERCENTILE_POLICY,
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise DepthCaptureFailure("depth_worker_output_invalid", "深度计算器输出与请求不一致。")
    frame_rate = metadata.get("frameRate")
    depth_min = metadata.get("depthMin")
    depth_max = metadata.get("depthMax")
    if (
        type(frame_rate) not in (int, float) or not math.isfinite(frame_rate) or frame_rate <= 0
        or type(depth_min) not in (int, float) or type(depth_max) not in (int, float)
        or not math.isfinite(depth_min) or not math.isfinite(depth_max)
        or frame_rate > profile.target_fps
        or depths.frame_count > profile.target_fps * MAX_DEPTH_SECONDS
        or depths.frame_count / frame_rate > MAX_DEPTH_SECONDS + 1e-6
        or depths.width % 2 or depths.height % 2
        or max(depths.width, depths.height) > profile.max_res
        or min(depths.values) < 0 or max(depths.values) > 1
        or not math.isclose(depth_min, min(depths.values), rel_tol=1e-6, abs_tol=1e-6)
        or not math.isclose(depth_max, max(depths.values), rel_tol=1e-6, abs_tol=1e-6)
        or depth_min < 0 or depth_max > 1
    ):
        raise DepthCaptureFailure("depth_worker_output_invalid", "深度计算器输出范围无效。")


def _validate_source_motion_samples(raw_samples) -> tuple[SourceMotionSample, ...]:
    if type(raw_samples) is not list:
        raise DepthCaptureFailure("depth_worker_output_invalid", "深度计算器来源运动样本无效。")
    samples: list[SourceMotionSample] = []
    for raw_sample in raw_samples:
        if type(raw_sample) is not dict or set(raw_sample) != {"timestampSeconds", "magnitude"}:
            raise DepthCaptureFailure("depth_worker_output_invalid", "深度计算器来源运动样本无效。")
        timestamp = raw_sample["timestampSeconds"]
        magnitude = raw_sample["magnitude"]
        if (
            type(timestamp) not in (int, float) or type(magnitude) not in (int, float)
            or not math.isfinite(timestamp) or not math.isfinite(magnitude)
            or not 0.0 <= magnitude <= 1.0
        ):
            raise DepthCaptureFailure("depth_worker_output_invalid", "深度计算器来源运动样本无效。")
        samples.append(SourceMotionSample(float(timestamp), float(magnitude)))
    return tuple(samples)


def _encode_depth_variants(
    depths: _DepthFrames,
    *,
    frame_rate: float,
    workspace: Path,
    ffmpeg_path: str,
    run: CommandRunner,
) -> None:
    raw_path = workspace / ".depth-raw"
    try:
        _write_quantized_raw(raw_path, depths.values)
        height, width = depths.height, depths.width
        common: list[str] = [
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-f", "rawvideo", "-pixel_format", "gray", "-video_size", f"{width}x{height}",
            "-framerate", str(frame_rate), "-i", str(raw_path), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        ]
        _run_ffmpeg(
            common + ["-vf", "format=gray,format=rgb24", str(workspace / "depth-control.mp4")], run,
        )
        _run_ffmpeg(
            common + ["-vf", "format=rgb24,pseudocolor=preset=turbo,format=rgb24", str(workspace / "depth-preview.mp4")], run,
        )
    finally:
        try:
            raw_path.unlink()
        except (FileNotFoundError, OSError) as error:
            if not isinstance(error, FileNotFoundError):
                logging.getLogger(__name__).warning("深度原始帧临时文件清理失败：%s", type(error).__name__)


def _write_quantized_raw(path: Path, values: array) -> None:
    with path.open("wb") as file:
        for start in range(0, len(values), 65_536):
            file.write(bytearray(max(0, min(255, round(value * 255))) for value in values[start:start + 65_536]))


def _run_ffmpeg(command: list[str], run: CommandRunner) -> None:
    try:
        result = run(
            command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False, shell=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as error:
        raise DepthCaptureFailure("depth_encoding_failed", "深度视频编码失败，请检查本地 FFmpeg 后重试。") from error
    if result.returncode != 0 or not Path(command[-1]).is_file() or Path(command[-1]).stat().st_size == 0:
        raise DepthCaptureFailure("depth_encoding_failed", "深度视频编码失败，请检查本地 FFmpeg 后重试。")


def _write_final_metadata(
    workspace: Path,
    worker_metadata: dict,
    quality_assessment: DepthQualityAssessment,
) -> None:
    (workspace / "depth-metadata.json").write_text(json.dumps({
        "schemaVersion": 1,
        "algorithmVersion": 1,
        "normalizationDirection": "near_white_far_black",
        "worker": worker_metadata,
    }, separators=(",", ":")), encoding="utf-8")
    (workspace / "depth-quality.json").write_text(
        json.dumps(quality_assessment.model_dump(mode="json"), separators=(",", ":")),
        encoding="utf-8",
    )


def _write_manifest(workspace: Path, source_reference_video_id: str) -> None:
    (workspace / "manifest.json").write_text(json.dumps({
        "schemaVersion": 1,
        "algorithmVersion": 1,
        "sourceReferenceVideoId": source_reference_video_id,
        "modelIdentity": MODEL_IDENTITY,
        "normalizationDirection": "near_white_far_black",
        "files": list(DEPTH_ARTIFACTS),
    }, separators=(",", ":")), encoding="utf-8")


def _discard_workspace(workspace: Path, request: DepthCaptureRequest) -> None:
    try:
        expected_parent = depth_capture_directory(
            request.data_dir, request.project_id, request.capture_id,
        ).parent
        pattern = rf"\.{re.escape(request.capture_id)}-[0-9a-f]{{32}}"
        if workspace.parent != expected_parent or workspace.is_symlink() or not re.fullmatch(pattern, workspace.name):
            logging.getLogger(__name__).warning("拒绝清理不安全的深度捕捉工作目录")
            return
        shutil.rmtree(workspace)
    except (FileNotFoundError, OSError) as error:
        logging.getLogger(__name__).warning("深度捕捉工作目录清理失败：%s", type(error).__name__)


def _load_float32_depths(path: Path, profile: _DeviceProfile) -> _DepthFrames:
    """安全读取 worker 固定写出的单一 float32 NPY 数组，不依赖应用环境的 NumPy。"""
    max_payload = profile.target_fps * MAX_DEPTH_SECONDS * profile.max_res * profile.max_res * 4
    if path.stat().st_size > max_payload + 8192:
        raise ValueError("unsafe NPZ size")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != "depths.npy" or entries[0].is_dir():
            raise ValueError("unexpected NPZ keys")
        entry = entries[0]
        if (
            entry.compress_type != zipfile.ZIP_DEFLATED
            or entry.file_size > max_payload + 4096
            or not entry.compress_size
            or entry.file_size > entry.compress_size * 1000
        ):
            raise ValueError("unsafe NPZ size")
        with archive.open(entry) as stream:
            prefix = _read_exact(stream, 8)
            if prefix[:6] != b"\x93NUMPY":
                raise ValueError("invalid NPY header")
            version = prefix[6:8]
            header_size = 2 if version == b"\x01\x00" else 4 if version in (b"\x02\x00", b"\x03\x00") else 0
            if not header_size:
                raise ValueError("unsupported NPY version")
            header_length = int.from_bytes(_read_exact(stream, header_size), "little")
            if not 1 <= header_length <= 4096:
                raise ValueError("NPY header too large")
            header_bytes = _read_exact(stream, header_length)
            consumed = 8 + header_size
            header = literal_eval(header_bytes.decode("latin1").strip())
            if (
                not isinstance(header, dict) or header.get("descr") != "<f4" or header.get("fortran_order") is not False
                or not isinstance(header.get("shape"), tuple) or len(header["shape"]) != 3
            ):
                raise ValueError("unsupported NPY layout")
            frame_count, height, width = header["shape"]
            if any(type(value) is not int or value <= 0 for value in (frame_count, height, width)):
                raise ValueError("invalid depth dimensions")
            if frame_count > profile.target_fps * MAX_DEPTH_SECONDS or height % 2 or width % 2 or max(height, width) > profile.max_res:
                raise ValueError("depth dimensions exceed profile")
            sample_count = frame_count * height * width
            payload_size = sample_count * 4
            if entry.file_size != consumed + header_length + payload_size:
                raise ValueError("invalid depth payload")
            values = array("f")
            remaining = payload_size
            while remaining:
                chunk = _read_exact(stream, min(65_536, remaining))
                values.frombytes(chunk)
                remaining -= len(chunk)
            if stream.read(1):
                raise ValueError("unexpected depth payload")
    if sys.byteorder != "little":
        values.byteswap()
    return _DepthFrames(values, frame_count, height, width)


def _read_exact(stream, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ValueError("truncated NPZ entry")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
