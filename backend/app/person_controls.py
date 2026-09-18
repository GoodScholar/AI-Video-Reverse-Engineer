"""Durable local person-control runs and their safe artifact projection."""
from __future__ import annotations
import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from uuid import uuid4

from .reference_video import validate_storage_id


PERSON_ARTIFACTS = ("pose.mp4", "mask.mp4", "overlay.mp4", "landmarks.jsonl", "quality.json", "metadata.json")
PREVIEW_ARTIFACTS = {"pose": "pose.mp4", "mask": "mask.mp4", "overlay": "overlay.mp4"}
_STREAM_CHUNK_BYTES = 64 * 1024


class PersonControlError(Exception):
    pass


class PersonArtifactStream:
    def __init__(self, descriptor: int, start: int, length: int) -> None:
        self._descriptor = descriptor
        self._remaining = length
        self._closed = False
        try:
            os.lseek(descriptor, start, os.SEEK_SET)
        except BaseException:
            self._closed = True
            os.close(descriptor)
            raise

    def __aiter__(self) -> "PersonArtifactStream":
        return self

    async def __anext__(self) -> bytes:
        if self._closed or self._remaining == 0:
            await self.aclose()
            raise StopAsyncIteration
        try:
            chunk = os.read(self._descriptor, min(_STREAM_CHUNK_BYTES, self._remaining))
        except BaseException:
            await self.aclose()
            raise
        if not chunk:
            await self.aclose()
            raise StopAsyncIteration
        self._remaining -= len(chunk)
        return chunk

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self._descriptor)


async def stream_person_artifact(stream: PersonArtifactStream) -> AsyncIterator[bytes]:
    try:
        async for chunk in stream:
            yield chunk
    finally:
        await stream.aclose()


def parse_single_byte_range(range_header: str | None, size: int) -> tuple[int, int, bool]:
    if range_header is None:
        return 0, size, False
    import re
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
    if match is None or (not match.group(1) and not match.group(2)) or size == 0:
        raise ValueError("invalid range")
    start_text, end_text = match.groups()
    if start_text:
        start = int(start_text)
        if start >= size:
            raise ValueError("unsatisfiable range")
        end = min(int(end_text), size - 1) if end_text else size - 1
        if end < start:
            raise ValueError("reversed range")
    else:
        suffix_size = int(end_text)
        if suffix_size == 0:
            raise ValueError("empty range")
        start = max(size - suffix_size, 0)
        end = size - 1
    return start, end - start + 1, True


class PersonControlStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir).resolve()

    def directory(self, project_id: str, source_id: str, preprocessing_id: str, shot_id: str) -> Path:
        for value in (project_id, source_id, preprocessing_id, shot_id):
            validate_storage_id(value)
        target = self.root / "project-files" / project_id / "person-controls" / source_id / preprocessing_id / shot_id
        _safe_descendant(self.root, target)
        return target

    def state_path(self, project_id: str, source_id: str, preprocessing_id: str, shot_id: str) -> Path:
        return self.directory(project_id, source_id, preprocessing_id, shot_id) / "state.json"

    def load(self, project_id: str, source_id: str, preprocessing_id: str, shot_id: str) -> dict[str, Any] | None:
        path = self.state_path(project_id, source_id, preprocessing_id, shot_id)
        if not path.is_file() or path.is_symlink():
            return None
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PersonControlError("人物控制任务记录损坏") from error
        if not _valid_state(parsed, source_id, preprocessing_id, shot_id):
            raise PersonControlError("人物控制任务记录无效")
        return parsed

    def save(self, project_id: str, source_id: str, preprocessing_id: str, shot_id: str, state: dict[str, Any]) -> None:
        if not _valid_state(state, source_id, preprocessing_id, shot_id):
            raise PersonControlError("人物控制任务记录无效")
        path = self.state_path(project_id, source_id, preprocessing_id, shot_id)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _atomic_json(path, state)

    def new_run(self, source_id: str, preprocessing_id: str, shot_id: str) -> dict[str, Any]:
        return {"schemaVersion": 1, "sourceId": source_id, "preprocessingId": preprocessing_id, "shotId": shot_id, "runs": []}

    def run_directory(self, project_id: str, source_id: str, preprocessing_id: str, shot_id: str, run_id: str) -> Path:
        validate_storage_id(run_id)
        result = self.directory(project_id, source_id, preprocessing_id, shot_id) / run_id
        _safe_descendant(self.root, result)
        return result

    def reconcile_interrupted(self) -> None:
        base = self.root / "project-files"
        if not base.is_dir() or base.is_symlink():
            return
        for path in base.glob("*/person-controls/*/*/*/state.json"):
            try:
                relative = path.relative_to(base)
                project_id, _, source_id, preprocessing_id, shot_id, name = relative.parts
                if name != "state.json":
                    continue
                state = self.load(project_id, source_id, preprocessing_id, shot_id)
                if state is None:
                    continue
                changed = False
                for run in state["runs"]:
                    if run["status"] in {"queued", "running"}:
                        run.update(status="failed", error="服务重启导致任务中断，请重试。")
                        changed = True
                if changed:
                    self.save(project_id, source_id, preprocessing_id, shot_id, state)
            except (OSError, ValueError, PersonControlError):
                continue


def environment_status(worker_python: str, worker_script: Path, model: Path) -> dict[str, Any]:
    missing = []
    if not Path(worker_python).is_file() or not os.access(worker_python, os.X_OK):
        missing.append("Python 运行环境")
    if not Path(worker_script).is_file() or Path(worker_script).is_symlink():
        missing.append("人物提取程序")
    if not Path(model).is_file() or Path(model).is_symlink() or Path(model).stat().st_size <= 0:
        missing.append("姿态模型")
    if missing:
        return {"ready": False, "message": "缺少" + "、".join(missing) + "。"}
    return {"ready": True, "message": "运行文件已就绪；尚未加载模型，可开始人物控制提取。"}


def append_queued_run(state: dict[str, Any]) -> dict[str, Any]:
    current = latest_run(state)
    if current is not None and current["status"] in {"queued", "running"}:
        raise PersonControlError("该镜头的人物控制正在处理中")
    run = {"runId": str(uuid4()), "status": "queued", "error": None, "quality": None}
    state["runs"].append(run)
    return run


def latest_run(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not state or not state.get("runs"):
        return None
    return state["runs"][-1]


def projected_run(state: dict[str, Any] | None, environment: dict[str, Any], *, base_url: str) -> dict[str, Any]:
    run = latest_run(state)
    if run is None:
        return {"status": "not_started", "error": None, "quality": None, "runId": None, "outputs": {}}
    outputs = {}
    if run["status"] == "completed":
        for kind in PREVIEW_ARTIFACTS:
            outputs[kind] = f"{base_url}/{kind}?runId={run['runId']}"
    return {"status": run["status"], "error": run.get("error"), "quality": run.get("quality"), "runId": run["runId"], "outputs": outputs}


def control_status(state: dict[str, Any] | None, environment: dict[str, Any]) -> str:
    run = latest_run(state)
    if run is not None and run["status"] == "completed":
        quality = run.get("quality") or {}
        if quality.get("status") == "passed":
            return "available"
        if quality.get("status") == "review_required":
            return "review_required"
        return "failed"
    if not environment["ready"]:
        return "unavailable"
    if run is not None and run["status"] in {"queued", "running"}:
        return "running"
    if run is not None and run["status"] == "failed":
        return "failed"
    return "missing" if environment["ready"] else "unavailable"


def execute_person_run(
    *, store: PersonControlStore, project_id: str, source_id: str, preprocessing_id: str, shot_id: str, run_id: str,
    source: Path, start_seconds: float, end_seconds: float, ffmpeg_path: str, worker_python: str,
    worker_script: Path, model: Path, is_current: Callable[[], bool], timeout_seconds: int = 600,
    ffprobe_path: str = "ffprobe",
) -> tuple[dict[str, Any] | None, str | None]:
    """Create and commit a run. The caller owns durable status transitions."""
    if not environment_status(worker_python, worker_script, model)["ready"]:
        return None, "人物控制运行环境不可用。"
    if not is_current():
        return None, "参考视频或镜头已变化，未写入过期结果。"
    target = store.run_directory(project_id, source_id, preprocessing_id, shot_id, run_id)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=target.parent))
    normalized = workspace / "normalized.mp4"
    try:
        duration = end_seconds - start_seconds
        if not math.isfinite(duration) or duration <= 0:
            return None, "镜头时间范围无效。"
        check = subprocess.run([worker_python, str(worker_script), "--check", "--model", str(model)],
            capture_output=True, timeout=90, check=False)
        if check.returncode != 0:
            return None, "人物控制运行环境自检失败。"
        encoded = subprocess.run([
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", f"{start_seconds:.6f}", "-i", str(source),
            "-map", "0:v:0", "-an", "-t", f"{duration:.6f}", "-vf",
            f"trim=start=0:end={duration:.6f},setpts=PTS-STARTPTS,fps=8,scale='if(gt(iw,ih),min(640,iw),-2)':'if(gt(iw,ih),-2,min(640,ih))':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(normalized),
        ], capture_output=True, timeout=90, check=False)
        if encoded.returncode != 0 or not _ordinary_file(normalized):
            return None, "镜头视频标准化失败。"
        output = workspace / "output"
        output.mkdir(mode=0o700)
        result = subprocess.run([
            worker_python, str(worker_script), "--input", str(normalized), "--output", str(output), "--model", str(model), "--ffmpeg", ffmpeg_path,
        ], capture_output=True, timeout=timeout_seconds, check=False)
        if result.returncode != 0:
            return None, "人物控制提取失败，请重试。"
        input_shape = _probe_video(normalized, ffprobe_path)
        quality = validate_run_artifacts(output, ffprobe_path=ffprobe_path)
        metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
        if input_shape is None or (metadata["width"], metadata["height"], metadata["frameRate"], metadata["frameCount"]) != input_shape:
            raise PersonControlError("人物控制输出与输入镜头不一致")
        if not is_current():
            return None, "参考视频或镜头已变化，未写入过期结果。"
        if target.exists():
            return None, "人物控制输出目录已存在。"
        os.replace(output, target)
        return quality, None
    except (OSError, subprocess.TimeoutExpired, PersonControlError):
        return None, "人物控制提取失败或超时，请重试。"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def validate_run_artifacts(directory: Path, *, ffprobe_path: str = "ffprobe") -> dict[str, Any]:
    if directory.is_symlink() or not directory.is_dir():
        raise PersonControlError("人物控制产物不可用")
    for name in PERSON_ARTIFACTS:
        if not _ordinary_file(directory / name):
            raise PersonControlError("人物控制产物不完整")
    try:
        quality = json.loads((directory / "quality.json").read_text(encoding="utf-8"))
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PersonControlError("人物控制质量信息无效") from error
    if not _valid_quality(quality) or not _valid_metadata(metadata):
        raise PersonControlError("人物控制质量信息无效")
    if metadata["frameCount"] != quality["frameCount"] or not _valid_landmarks(directory / "landmarks.jsonl", metadata["frameCount"], metadata["frameRate"], quality):
        raise PersonControlError("人物控制帧信息无效")
    for name in ("pose.mp4", "mask.mp4", "overlay.mp4"):
        if _probe_video(directory / name, ffprobe_path) != (metadata["width"], metadata["height"], metadata["frameRate"], metadata["frameCount"]):
            raise PersonControlError("人物控制视频产物无效")
    return quality


def open_preview(store: PersonControlStore, *, project_id: str, source_id: str, preprocessing_id: str, shot_id: str, run_id: str, kind: str, ffprobe_path: str = "ffprobe") -> tuple[int, int]:
    if kind not in PREVIEW_ARTIFACTS:
        raise PersonControlError("人物控制预览类型无效")
    return open_artifact(store, project_id=project_id, source_id=source_id, preprocessing_id=preprocessing_id, shot_id=shot_id, run_id=run_id, artifact=PREVIEW_ARTIFACTS[kind], ffprobe_path=ffprobe_path)


def open_artifact(store: PersonControlStore, *, project_id: str, source_id: str, preprocessing_id: str, shot_id: str, run_id: str, artifact: str, ffprobe_path: str = "ffprobe") -> tuple[int, int]:
    if artifact not in PERSON_ARTIFACTS:
        raise PersonControlError("人物控制产物不可用")
    state = store.load(project_id, source_id, preprocessing_id, shot_id)
    run = latest_run(state)
    if run is None or run["runId"] != run_id or run["status"] != "completed":
        raise PersonControlError("人物控制产物不可用")
    directory = store.run_directory(project_id, source_id, preprocessing_id, shot_id, run_id)
    validate_run_artifacts(directory, ffprobe_path=ffprobe_path)
    target = directory / artifact
    descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size <= 0:
            raise PersonControlError("人物控制预览不可用")
        return descriptor, details.st_size
    except BaseException:
        os.close(descriptor)
        raise


def _valid_state(value: Any, source_id: str, preprocessing_id: str, shot_id: str) -> bool:
    if not isinstance(value, dict) or value.get("schemaVersion") != 1 or value.get("sourceId") != source_id or value.get("preprocessingId") != preprocessing_id or value.get("shotId") != shot_id or not isinstance(value.get("runs"), list):
        return False
    for run in value["runs"]:
        if not isinstance(run, dict) or not isinstance(run.get("runId"), str) or _unsafe_id(run["runId"]) or not isinstance(run.get("status"), str) or run["status"] not in {"queued", "running", "completed", "failed"} or not isinstance(run.get("error"), (str, type(None))) or not isinstance(run.get("quality"), (dict, type(None))):
            return False
    return True


def _valid_quality(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("status"), str) or value["status"] not in {"passed", "review_required", "failed"} or not isinstance(value.get("frameCount"), int) or value["frameCount"] <= 0 or not isinstance(value.get("detectedFrameCount"), int) or not 0 <= value["detectedFrameCount"] <= value["frameCount"] or not isinstance(value.get("message"), str):
        return False
    names = ("missingTimesSeconds", "multiplePersonTimesSeconds", "lowConfidenceTimesSeconds", "invalidLandmarkTimesSeconds", "unusableMaskTimesSeconds")
    if any(not isinstance(value.get(name), list) or any(not isinstance(item, (int, float)) or not math.isfinite(item) or item < 0 for item in value[name]) for name in names):
        return False
    if value["status"] == "passed":
        return value["detectedFrameCount"] == value["frameCount"] and not any(value[name] for name in names)
    return True


def _valid_metadata(value: Any) -> bool:
    sha = value.get("modelSha256") if isinstance(value, dict) else None
    return isinstance(value, dict) and value.get("schemaVersion") == 1 and value.get("scope") == "single_person" and value.get("poseFormat") == "mediapipe33" and isinstance(value.get("model"), str) and bool(value["model"]) and isinstance(sha, str) and len(sha) == 64 and all(character in "0123456789abcdef" for character in sha.lower()) and isinstance(value.get("width"), int) and value["width"] > 0 and isinstance(value.get("height"), int) and value["height"] > 0 and isinstance(value.get("frameCount"), int) and value["frameCount"] > 0 and isinstance(value.get("frameRate"), (int, float)) and math.isfinite(value["frameRate"]) and value["frameRate"] > 0


def _valid_landmarks(path: Path, frame_count: int, frame_rate: float, quality: dict[str, Any]) -> bool:
    count = 0
    detected = 0
    frame_times: list[float] = []
    missing: list[float] = []
    multiple: list[float] = []
    low_confidence: list[float] = []
    invalid: list[float] = []
    try:
        with path.open(encoding="utf-8") as file:
            for line in file:
                row = json.loads(line)
                time_seconds = row.get("timeSeconds") if isinstance(row, dict) else None
                if not isinstance(time_seconds, (int, float)) or not math.isfinite(time_seconds) or abs(float(time_seconds) - count / frame_rate) > 0.001:
                    return False
                frame_times.append(float(time_seconds))
                person_count = row.get("personCount")
                landmarks, presence, visibility = row.get("landmarks33"), row.get("presence"), row.get("visibility")
                if not isinstance(person_count, int) or person_count < 0:
                    return False
                if landmarks is None:
                    if presence is not None or visibility is not None:
                        return False
                    missing.append(float(time_seconds))
                else:
                    valid, invalid_frame, low_confidence_frame = _valid_landmark_values(landmarks, presence, visibility)
                    if not valid or person_count == 0:
                        return False
                    detected += 1
                    if person_count > 1:
                        multiple.append(float(time_seconds))
                    if invalid_frame:
                        invalid.append(float(time_seconds))
                    if low_confidence_frame:
                        low_confidence.append(float(time_seconds))
                count += 1
    except (OSError, ValueError):
        return False
    return count == frame_count and detected == quality["detectedFrameCount"] and _times_match(missing, quality["missingTimesSeconds"]) and _times_match(multiple, quality["multiplePersonTimesSeconds"]) and _times_match(low_confidence, quality["lowConfidenceTimesSeconds"]) and _times_match(invalid, quality["invalidLandmarkTimesSeconds"]) and all(any(abs(recorded - reported) <= 0.001 for recorded in frame_times) for reported in quality["unusableMaskTimesSeconds"])


def _valid_landmark_values(landmarks: Any, presence: Any, visibility: Any) -> tuple[bool, bool, bool]:
    if not isinstance(landmarks, list) or len(landmarks) != 33 or not isinstance(presence, list) or len(presence) != 33 or not isinstance(visibility, list) or len(visibility) != 33:
        return False, False, False
    invalid = False
    for point, point_presence, point_visibility in zip(landmarks, presence, visibility):
        if not isinstance(point, dict) or any(not isinstance(point.get(name), (int, float)) or not math.isfinite(point[name]) for name in ("x", "y", "z", "presence", "visibility")) or not isinstance(point_presence, (int, float)) or not math.isfinite(point_presence) or not isinstance(point_visibility, (int, float)) or not math.isfinite(point_visibility) or point_presence != point["presence"] or point_visibility != point["visibility"]:
            return False, False, False
        sentinel = point["x"] == -1.0 and point["y"] == -1.0 and point["z"] == 0.0 and point["presence"] == 0.0 and point["visibility"] == 0.0
        if not sentinel and not (0.0 <= point["x"] <= 1.0 and 0.0 <= point["y"] <= 1.0 and 0.0 <= point["presence"] <= 1.0 and 0.0 <= point["visibility"] <= 1.0):
            return False, False, False
        invalid = invalid or sentinel
    low_confidence = any(min(landmarks[index]["presence"], landmarks[index]["visibility"]) < 0.5 for index in (11, 12, 23, 24))
    return True, invalid, low_confidence


def _times_match(expected: list[float], actual: list[Any]) -> bool:
    return len(expected) == len(actual) and all(abs(left - float(right)) <= 0.001 for left, right in zip(expected, actual))


def _probe_video(path: Path, ffprobe_path: str) -> tuple[int, int, float, int] | None:
    try:
        result = subprocess.run([ffprobe_path, "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=width,height,avg_frame_rate,nb_read_frames", "-of", "json", str(path)], capture_output=True, timeout=30, check=False)
        streams = json.loads(result.stdout).get("streams", []) if result.returncode == 0 else []
        stream = streams[0] if len(streams) == 1 else None
        rate = stream.get("avg_frame_rate", "0/0") if isinstance(stream, dict) else "0/0"
        numerator, denominator = (int(part) for part in rate.split("/", 1))
        frame_rate = numerator / denominator
        frames = int(stream["nb_read_frames"])
        width, height = int(stream["width"]), int(stream["height"])
        if width <= 0 or height <= 0 or frames <= 0 or not math.isfinite(frame_rate) or frame_rate <= 0:
            return None
        return width, height, frame_rate, frames
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError, subprocess.TimeoutExpired):
        return None


def _ordinary_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size > 0
    except OSError:
        return False


def _safe_descendant(root: Path, path: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise PersonControlError("人物控制路径不安全") from error
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise PersonControlError("人物控制路径不安全")


def _unsafe_id(value: str) -> bool:
    try:
        validate_storage_id(value)
    except ValueError:
        return True
    return False


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".state-", suffix=".part", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.flush(); os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
