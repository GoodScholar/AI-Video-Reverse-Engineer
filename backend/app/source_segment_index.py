"""Durable, rebuildable source-segment indexes for project video assets."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .durable_runs import LOCAL_RUN_POLICY, RunConflict
from .project_assets import AssetFileUnavailableError, ProjectAssets
from .reference_video import validate_storage_id
from .video_toolkit import ToolkitConfig, ToolkitError, execute_tool, safe_child


SEGMENT_ALGORITHM_VERSION = "pyscenedetect-adaptive-v1"
KEYFRAME_MODEL_VERSION = "ffmpeg-keyframe-v1"
RUN_STATUSES = frozenset({"queued", "running", "completed", "failed", "cancelled"})


class SegmentIndexError(Exception):
    pass


class SegmentIndexCancelled(SegmentIndexError):
    pass


@dataclass(frozen=True)
class SegmentKeyframe:
    time_seconds: float
    filename: str


@dataclass(frozen=True)
class SegmentAnalysis:
    duration_seconds: float
    cuts: list[float]
    keyframes: list[SegmentKeyframe]


class StartSegmentIndexes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assetIds: list[str] = Field(min_length=1, max_length=50)

    @field_validator("assetIds")
    @classmethod
    def safe_unique_asset_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("项目素材不能重复选择")
        return [validate_storage_id(value) for value in values]


def build_segment_intervals(duration_seconds: float, cuts: Iterable[float]) -> list[tuple[float, float]]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        return []
    valid = sorted({
        round(float(cut), 6)
        for cut in cuts
        if not isinstance(cut, bool) and math.isfinite(float(cut)) and 0 < float(cut) < duration_seconds
    })
    boundaries = [0.0, *valid, round(float(duration_seconds), 6)]
    return [(start, end) for start, end in zip(boundaries, boundaries[1:]) if end > start]


def source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_source_segment_analysis(
    source: Path,
    output: Path,
    cancelled: Callable[[], bool],
    *,
    config: ToolkitConfig,
) -> SegmentAnalysis:
    """Run the licensed PySceneDetect adapter and extract one evidence frame per interval."""
    output.mkdir(parents=True, exist_ok=True)
    duration = _probe_video_duration(source, config.ffprobe)
    scene_checkpoint = output / "scenes.json"
    scene_output = output / ".scene-detection"
    try:
        cuts = _load_scene_checkpoint(scene_checkpoint, duration)
        if cuts is None:
            shutil.rmtree(scene_output, ignore_errors=True)
            scene_output.mkdir()
            execute_tool(
                source,
                scene_output,
                "scenes",
                {"threshold": 3.0, "minSceneSeconds": 0.35},
                config,
                cancelled,
                lambda _stage, _percent: None,
            )
            scene_data = json.loads((scene_output / "scenes.json").read_text(encoding="utf-8"))
            cuts = [float(value) for value in scene_data["cuts"] if 0 < float(value) < duration]
            _write_json_atomic(scene_checkpoint, {
                "schemaVersion": 1,
                "algorithmVersion": SEGMENT_ALGORITHM_VERSION,
                "duration": duration,
                "cuts": cuts,
            })
        intervals = build_segment_intervals(duration, cuts)
        if not intervals:
            raise SegmentIndexError("素材没有可索引的视频区间。")
        keyframes: list[SegmentKeyframe] = []
        for index, (start, end) in enumerate(intervals, start=1):
            if cancelled():
                raise SegmentIndexCancelled("片段索引已取消。")
            filename = f"keyframe-{index}.jpg"
            target = output / filename
            timestamp = min(max(start, (start + end) / 2), max(start, end - 0.001))
            if target.is_file() and not target.is_symlink() and target.stat().st_size > 0:
                keyframes.append(SegmentKeyframe(time_seconds=round(timestamp, 6), filename=filename))
                continue
            _run_cancellable([
                config.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y",
                "-ss", f"{timestamp:.6f}", "-i", str(source), "-map", "0:V:0",
                "-frames:v", "1", "-an",
                "-vf", "scale=w='if(gte(iw,ih),640,-2)':h='if(gte(iw,ih),-2,640)'",
                "-strict", "unofficial", "-q:v", "3", str(target),
            ], cancelled)
            if not target.is_file() or target.is_symlink() or target.stat().st_size <= 0:
                raise SegmentIndexError("关键帧证据生成失败。")
            keyframes.append(SegmentKeyframe(time_seconds=round(timestamp, 6), filename=filename))
        return SegmentAnalysis(duration_seconds=duration, cuts=cuts, keyframes=keyframes)
    except SegmentIndexCancelled:
        raise
    except (OSError, ValueError, KeyError, json.JSONDecodeError, ToolkitError, subprocess.SubprocessError) as error:
        raise SegmentIndexError(str(error) or "片段索引失败，请检查本地场景检测与 FFmpeg 环境。") from error
    finally:
        shutil.rmtree(scene_output, ignore_errors=True)


def _probe_video_duration(source: Path, ffprobe: str) -> float:
    result = subprocess.run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=duration:stream_tags=DURATION", "-of", "json", str(source),
    ], capture_output=True, timeout=30, check=True)
    value = json.loads(result.stdout)
    streams = value.get("streams")
    if not isinstance(streams, list) or not streams:
        raise ValueError("素材不包含视频流")
    stream = streams[0]
    raw_duration = stream.get("duration")
    if raw_duration is None:
        duration_tag = (stream.get("tags") or {}).get("DURATION")
        try:
            hours, minutes, seconds = str(duration_tag).split(":", 2)
            duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except (TypeError, ValueError) as error:
            raise ValueError("视频流时长不可用") from error
    else:
        duration = float(raw_duration)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("视频流时长无效")
    return duration


def _load_scene_checkpoint(path: Path, duration: float) -> list[float] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            value.get("schemaVersion") != 1
            or value.get("algorithmVersion") != SEGMENT_ALGORITHM_VERSION
            or not math.isclose(float(value["duration"]), duration, abs_tol=0.001)
            or not isinstance(value.get("cuts"), list)
        ):
            return None
        cuts = [float(item) for item in value["cuts"]]
        if cuts != sorted(set(cuts)) or any(not math.isfinite(item) or not 0 < item < duration for item in cuts):
            return None
        return cuts
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=".checkpoint-", suffix=".part")
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_cancellable(command: list[str], cancelled: Callable[[], bool]) -> None:
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    started = time.monotonic()
    try:
        while process.poll() is None:
            if cancelled():
                process.terminate()
                raise SegmentIndexCancelled("片段索引已取消。")
            if time.monotonic() - started > 120:
                process.kill()
                raise SegmentIndexError("关键帧提取超时。")
            time.sleep(0.05)
        if process.returncode:
            message = (process.stderr.read() if process.stderr else b"").decode("utf-8", errors="replace").strip()
            raise SegmentIndexError(message or "关键帧提取失败。")
    finally:
        if process.poll() is None:
            process.kill()
        if process.stderr:
            process.stderr.close()


class _IndexStore:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    def directory(self, project_id: str, asset_id: str, *parts: str) -> Path:
        return safe_child(self.root, "project-files", project_id, "source-segments", asset_id, *parts)

    def load(self, project_id: str, asset_id: str) -> dict[str, Any]:
        path = self.directory(project_id, asset_id, "state.json")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or value.get("assetId") != asset_id
                or value.get("status") not in RUN_STATUSES
                or not isinstance(value.get("runId"), str)
                or not isinstance(value.get("attemptId"), str)
                or not isinstance(value.get("candidates"), list)
            ):
                raise ValueError
            return value
        except FileNotFoundError:
            raise
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise OSError("片段索引记录损坏") from error

    def save(self, project_id: str, state: dict[str, Any]) -> None:
        asset_id = validate_storage_id(state["assetId"])
        path = self.directory(project_id, asset_id, "state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".part")
        temporary = Path(raw)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def all(self, project_id: str) -> list[dict[str, Any]]:
        root = safe_child(self.root, "project-files", project_id, "source-segments")
        values = []
        for path in root.glob("*/state.json") if root.is_dir() else []:
            values.append(self.load(project_id, path.parent.name))
        return sorted(values, key=lambda value: value["assetId"])


def create_source_segment_router(
    data_dir: Path,
    get_project: Callable[[str], Any],
    compute_queue: Any,
    *,
    runner: Callable[[Path, Path, Callable[[], bool]], SegmentAnalysis] | None = None,
    algorithm_version: str = SEGMENT_ALGORITHM_VERSION,
    keyframe_model_version: str = KEYFRAME_MODEL_VERSION,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    source_lock: Any = None,
) -> APIRouter:
    root = Path(data_dir).absolute()
    assets = ProjectAssets(root)
    store = _IndexStore(root)
    lock = source_lock or RLock()
    config = ToolkitConfig.local(ffmpeg_path, ffprobe_path)
    analyze = runner or (lambda source, output, cancelled: run_source_segment_analysis(
        source, output, cancelled, config=config,
    ))
    router = APIRouter(prefix="/api/projects/{project_id}/source-segments")

    def fail(code: str, message: str, status: int = 409) -> None:
        raise HTTPException(status, detail={"code": code, "message": message})

    def ensure_project(project_id: str) -> None:
        if get_project(project_id) is None:
            fail("project_not_found", "内容项目不存在。", 404)

    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def asset_source(project_id: str, asset_id: str) -> tuple[dict[str, Any], Path]:
        try:
            asset = assets.get(project_id, asset_id)
            if asset.get("kind") != "video":
                fail("video_asset_required", "只有项目视频素材可以建立片段索引。", 422)
            return asset, assets.file(project_id, asset)
        except KeyError:
            fail("asset_not_found", "项目素材不存在。", 404)
        except AssetFileUnavailableError:
            fail("source_missing", "项目素材文件不可用，无法建立片段索引。", 409)
        raise AssertionError("unreachable")

    def current_validity(project_id: str, state: dict[str, Any]) -> dict[str, str | None]:
        try:
            _, source = asset_source(project_id, state["assetId"])
        except HTTPException as error:
            if isinstance(error.detail, dict) and error.detail.get("code") in {"source_missing", "asset_not_found"}:
                return {"status": "stale", "reason": "source_missing"}
            return {"status": "stale", "reason": "source_changed"}
        if state.get("algorithmVersion") != algorithm_version:
            return {"status": "stale", "reason": "algorithm_changed"}
        if state.get("keyframeModelVersion") != keyframe_model_version:
            return {"status": "stale", "reason": "keyframe_model_changed"}
        try:
            if source_fingerprint(source) != state.get("sourceFingerprint"):
                return {"status": "stale", "reason": "source_changed"}
        except OSError:
            return {"status": "stale", "reason": "source_missing"}
        return {"status": "current", "reason": None}

    def public(project_id: str, state: dict[str, Any]) -> dict[str, Any]:
        value = {key: item for key, item in state.items() if not key.startswith("_")}
        validity = current_validity(project_id, state)
        if state.get("validity") != validity:
            state["validity"] = validity
            store.save(project_id, state)
        value["validity"] = validity
        value["candidateCount"] = len(state["candidates"])
        candidates = []
        for candidate in state["candidates"] if validity["status"] == "current" else []:
            item = {key: value for key, value in candidate.items() if not key.startswith("_")}
            item["keyframe"] = {
                "timeSeconds": candidate["keyframe"]["timeSeconds"],
                "url": (
                    f"/api/projects/{project_id}/source-segments/assets/{state['assetId']}"
                    f"/candidates/{candidate['id']}/keyframe"
                ),
            }
            start = candidate["inPointSeconds"]
            end = candidate["outPointSeconds"]
            item["preview"] = {
                "url": f"/api/projects/{project_id}/preproduction/assets/{state['assetId']}/file#t={start:.3f},{end:.3f}",
                "startSeconds": start,
                "endSeconds": end,
            }
            candidates.append(item)
        value["candidates"] = candidates
        return value

    def new_state(project_id: str, asset_id: str) -> dict[str, Any]:
        _asset, source = asset_source(project_id, asset_id)
        timestamp = now()
        return {
            "schemaVersion": 1,
            "assetId": asset_id,
            "runId": f"segment-run-{uuid4().hex}",
            "attemptId": f"segment-attempt-{uuid4().hex}",
            "status": "queued",
            "sourceFingerprint": source_fingerprint(source),
            "algorithmVersion": algorithm_version,
            "keyframeModelVersion": keyframe_model_version,
            "queuedAt": timestamp,
            "startedAt": None,
            "completedAt": None,
            "updatedAt": timestamp,
            "error": None,
            "validity": {"status": "current", "reason": None},
            "candidates": [],
        }

    def candidate_id(state: dict[str, Any], start: float, end: float) -> str:
        identity = json.dumps({
            "assetId": state["assetId"],
            "sourceFingerprint": state["sourceFingerprint"],
            "algorithmVersion": state["algorithmVersion"],
            "keyframeModelVersion": state["keyframeModelVersion"],
            "inPointSeconds": start,
            "outPointSeconds": end,
        }, sort_keys=True, separators=(",", ":"))
        return f"segment-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"

    def process(project_id: str, asset_id: str, run_id: str, attempt_id: str) -> None:
        output = store.directory(project_id, asset_id, "runs", run_id)
        try:
            with lock:
                state = store.load(project_id, asset_id)
                if state["runId"] != run_id or state["attemptId"] != attempt_id or state["status"] != "queued":
                    return
                state.update(status=LOCAL_RUN_POLICY.start(state["status"]), startedAt=now(), updatedAt=now())
                store.save(project_id, state)

            def cancelled() -> bool:
                with lock:
                    try:
                        current = store.load(project_id, asset_id)
                        return (
                            current["runId"] != run_id
                            or current["attemptId"] != attempt_id
                            or current["status"] == "cancelled"
                        )
                    except (FileNotFoundError, OSError):
                        return True

            _asset, source = asset_source(project_id, asset_id)
            output.mkdir(parents=True, exist_ok=True)
            analysis = analyze(source, output, cancelled)
            intervals = build_segment_intervals(analysis.duration_seconds, analysis.cuts)
            if not intervals or len(intervals) != len(analysis.keyframes):
                raise SegmentIndexError("片段区间与关键帧证据不一致。")
            with lock:
                state = store.load(project_id, asset_id)
                if state["runId"] != run_id or state["attemptId"] != attempt_id or state["status"] != "running":
                    return
                if source_fingerprint(source) != state["sourceFingerprint"]:
                    state.update(
                        status="failed", updatedAt=now(),
                        error={"code": "source_changed", "message": "索引期间源文件已变化，请重试。", "retryable": True},
                    )
                    store.save(project_id, state)
                    return
                candidates = []
                for (start, end), keyframe in zip(intervals, analysis.keyframes):
                    validate_storage_id(keyframe.filename)
                    frame = output / keyframe.filename
                    if (
                        not start <= keyframe.time_seconds < end
                        or not frame.is_file()
                        or frame.is_symlink()
                        or frame.stat().st_size <= 0
                    ):
                        raise SegmentIndexError("关键帧证据无效。")
                    start = round(start, 6)
                    end = round(min(end, analysis.duration_seconds), 6)
                    candidates.append({
                        "id": candidate_id(state, start, end),
                        "assetId": asset_id,
                        "sourceFingerprint": state["sourceFingerprint"],
                        "algorithmVersion": state["algorithmVersion"],
                        "keyframeModelVersion": state["keyframeModelVersion"],
                        "inPointSeconds": start,
                        "outPointSeconds": end,
                        "keyframe": {"timeSeconds": round(keyframe.time_seconds, 6), "_filename": keyframe.filename},
                    })
                state.update(
                    status="completed", completedAt=now(), updatedAt=now(), error=None,
                    durationSeconds=round(analysis.duration_seconds, 6), candidates=candidates,
                )
                store.save(project_id, state)
        except SegmentIndexCancelled:
            with lock:
                try:
                    state = store.load(project_id, asset_id)
                    if state["runId"] == run_id and state["attemptId"] == attempt_id and LOCAL_RUN_POLICY.active(state["status"]):
                        state.update(status="cancelled", updatedAt=now(), error=None)
                        store.save(project_id, state)
                except (FileNotFoundError, OSError, RunConflict):
                    pass
        except Exception as error:
            with lock:
                try:
                    state = store.load(project_id, asset_id)
                    if state["runId"] == run_id and state["attemptId"] == attempt_id and state["status"] == "running":
                        state.update(
                            status="failed", updatedAt=now(),
                            error={
                                "code": "segment_index_failed",
                                "message": str(error) if isinstance(error, SegmentIndexError) else "片段索引失败，请检查本地工具环境和素材。",
                                "retryable": True,
                            },
                        )
                        store.save(project_id, state)
                except (FileNotFoundError, OSError):
                    pass

    def enqueue(project_id: str, state: dict[str, Any]) -> None:
        asset_id = state["assetId"]
        run_id = state["runId"]
        attempt_id = state["attemptId"]
        owner_id = f"{project_id}:{asset_id}:{run_id}"
        if not compute_queue.submit(
            "source-segment-index",
            owner_id,
            lambda _owner: process(project_id, asset_id, run_id, attempt_id),
        ):
            state.update(
                status="failed", updatedAt=now(),
                error={"code": "queue_unavailable", "message": "本地索引队列暂不可用，请重试。", "retryable": True},
            )
            store.save(project_id, state)

    def reconcile() -> None:
        base = root / "project-files"
        if not base.is_dir() or base.is_symlink():
            return
        for state_path in base.glob("*/source-segments/*/state.json"):
            try:
                project_id = state_path.parents[2].name
                asset_id = state_path.parent.name
                state = store.load(project_id, asset_id)
                if LOCAL_RUN_POLICY.active(state["status"]):
                    validity = current_validity(project_id, state)
                    if validity["status"] == "stale":
                        state.update(
                            status="failed", updatedAt=now(), validity=validity,
                            error={"code": validity["reason"], "message": "索引输入已变化，请重新建立索引。", "retryable": True},
                        )
                        store.save(project_id, state)
                        continue
                    state.update(
                        status="queued", attemptId=f"segment-attempt-{uuid4().hex}",
                        startedAt=None, updatedAt=now(), error=None,
                    )
                    store.save(project_id, state)
                    enqueue(project_id, state)
            except (OSError, ValueError, KeyError, RunConflict):
                continue

    reconcile()

    @router.get("")
    def list_indexes(project_id: str):
        ensure_project(project_id)
        try:
            with lock:
                return {"indexes": [public(project_id, state) for state in store.all(project_id)]}
        except OSError:
            fail("segment_index_storage_unavailable", "片段索引记录无法读取。", 503)

    @router.post("", status_code=202)
    def start_indexes(project_id: str, request: StartSegmentIndexes):
        ensure_project(project_id)
        with lock:
            states = []
            for asset_id in request.assetIds:
                asset_source(project_id, asset_id)
                try:
                    existing = store.load(project_id, asset_id)
                    if LOCAL_RUN_POLICY.active(existing["status"]):
                        fail("segment_index_active", "所选素材已有片段索引正在运行。")
                except FileNotFoundError:
                    pass
                states.append(new_state(project_id, asset_id))
            try:
                for state in states:
                    store.save(project_id, state)
                    enqueue(project_id, state)
            except OSError:
                fail("segment_index_storage_unavailable", "片段索引记录无法保存。", 503)
            return {"indexes": [public(project_id, state) for state in states]}

    @router.post("/assets/{asset_id}/cancel")
    def cancel_index(project_id: str, asset_id: str):
        ensure_project(project_id)
        with lock:
            try:
                state = store.load(project_id, validate_storage_id(asset_id))
                state.update(status=LOCAL_RUN_POLICY.cancel(state["status"]), updatedAt=now(), error=None)
                store.save(project_id, state)
                return public(project_id, state)
            except FileNotFoundError:
                fail("segment_index_not_found", "片段索引不存在。", 404)
            except (ValueError, RunConflict):
                fail("segment_index_not_cancellable", "当前片段索引不能取消。")
            except OSError:
                fail("segment_index_storage_unavailable", "片段索引记录无法保存。", 503)

    @router.post("/assets/{asset_id}/retry", status_code=202)
    def retry_index(project_id: str, asset_id: str):
        ensure_project(project_id)
        with lock:
            try:
                current = store.load(project_id, validate_storage_id(asset_id))
                if LOCAL_RUN_POLICY.active(current["status"]):
                    fail("segment_index_active", "片段索引仍在运行，不能重试。")
                state = new_state(project_id, asset_id)
                store.save(project_id, state)
                enqueue(project_id, state)
                return public(project_id, state)
            except FileNotFoundError:
                fail("segment_index_not_found", "片段索引不存在。", 404)
            except OSError:
                fail("segment_index_storage_unavailable", "片段索引记录无法保存。", 503)

    @router.get("/assets/{asset_id}/candidates/{candidate_id}/keyframe")
    def read_keyframe(project_id: str, asset_id: str, candidate_id: str):
        ensure_project(project_id)
        try:
            validate_storage_id(asset_id)
            validate_storage_id(candidate_id)
            with lock:
                state = store.load(project_id, asset_id)
                if current_validity(project_id, state)["status"] != "current":
                    raise FileNotFoundError
                candidate = next(item for item in state["candidates"] if item.get("id") == candidate_id)
                filename = validate_storage_id(candidate["keyframe"]["_filename"])
                path = store.directory(project_id, asset_id, "runs", state["runId"], filename)
                if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
                    raise FileNotFoundError
            return FileResponse(path, media_type="image/jpeg")
        except (FileNotFoundError, StopIteration, KeyError, ValueError, OSError):
            fail("segment_keyframe_unavailable", "片段关键帧不可用，请重新建立索引。", 404)

    return router
