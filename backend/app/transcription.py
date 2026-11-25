"""Durable local faster-whisper transcription for immutable project videos.

Reference adapted from Anil-matcha/AI-Youtube-Shorts-Generator at
a57bb938ba50bf9654c2d5ca2af1295163454349 (MIT): the local
``WhisperModel(...).transcribe(..., word_timestamps=True)`` boundary.  The
upstream downloader/pipeline is intentionally not imported: project assets
already own immutable files and this module persists blocks independently.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
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
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .durable_runs import LOCAL_RUN_POLICY, RunConflict
from .project_assets import AssetFileUnavailableError, ProjectAssets
from .reference_video import validate_storage_id
from .source_segment_index import source_fingerprint
from .video_toolkit import safe_child


TRANSCRIPTION_POLICY_VERSION = "faster-whisper-local-blocks-v1"
DEFAULT_BLOCK_SECONDS = 1200.0
DEFAULT_OVERLAP_SECONDS = 60.0
LONG_VIDEO_SECONDS = 1800.0
RUN_STATUSES = frozenset({"queued", "running", "completed", "failed", "cancelled"})


class TranscriptionError(Exception):
    pass


class TranscriptionCancelled(TranscriptionError):
    pass


class TranscriptionModelUnavailable(TranscriptionError):
    pass


class TranscriptionModelIdentityUnverified(TranscriptionError):
    pass


@dataclass(frozen=True)
class TranscriptionBlock:
    index: int
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class TranscriptWord:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    words: list[TranscriptWord]


@dataclass(frozen=True)
class BlockTranscript:
    segments: list[TranscriptSegment]
    words: list[TranscriptWord]


class StartTranscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assetId: str
    language: str = Field(default="zh", min_length=2, max_length=16)
    model: str = Field(default="small", min_length=1, max_length=80)

    @field_validator("assetId")
    @classmethod
    def asset_id_is_safe(cls, value: str) -> str:
        return validate_storage_id(value)


def build_transcription_blocks(duration_seconds: float) -> list[TranscriptionBlock]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        return []
    if duration_seconds <= LONG_VIDEO_SECONDS:
        return [TranscriptionBlock(1, 0.0, round(duration_seconds, 6))]
    blocks: list[TranscriptionBlock] = []
    start = 0.0
    index = 1
    while start < duration_seconds:
        end = min(start + DEFAULT_BLOCK_SECONDS, duration_seconds)
        blocks.append(TranscriptionBlock(index, round(start, 6), round(end, 6)))
        if end >= duration_seconds:
            break
        start = end - DEFAULT_OVERLAP_SECONDS
        index += 1
    return blocks


def normalize_block_transcript(
    block: TranscriptionBlock, result: dict[str, Any], *, previous_end_seconds: float = 0.0,
) -> BlockTranscript:
    """Convert only block-local timestamps once, then trim block overlap.

    A runner receives an extracted block and must return local timestamps.  No
    caller may offset these values; this single boundary prevents the historical
    second/third-block double-offset bug.
    """
    segments: list[TranscriptSegment] = []
    words: list[TranscriptWord] = []
    for raw_segment in result.get("segments", []):
        if not isinstance(raw_segment, dict):
            continue
        try:
            local_start, local_end = float(raw_segment["start"]), float(raw_segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(local_start) or not math.isfinite(local_end) or local_start < 0 or local_end <= local_start or local_end > block.end_seconds - block.start_seconds + 0.001:
            raise TranscriptionError("转录器返回了超出本地音频块范围的时间码。")
        start = max(block.start_seconds + local_start, previous_end_seconds)
        end = block.start_seconds + local_end
        text = str(raw_segment.get("text", "")).strip()
        if not text or not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        segment_words: list[TranscriptWord] = []
        for raw_word in raw_segment.get("words") or []:
            if not isinstance(raw_word, dict):
                continue
            try:
                word_start = block.start_seconds + float(raw_word["start"])
                word_end = block.start_seconds + float(raw_word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            word_text = str(raw_word.get("word", "")).strip()
            if word_text and math.isfinite(word_start) and math.isfinite(word_end) and block.start_seconds <= word_start < word_end <= block.end_seconds + 0.001 and word_end > max(word_start, previous_end_seconds):
                word = TranscriptWord(round(max(word_start, previous_end_seconds), 6), round(word_end, 6), word_text)
                segment_words.append(word)
                words.append(word)
        segments.append(TranscriptSegment(round(start, 6), round(end, 6), text, segment_words))
    return BlockTranscript(segments, words)


def _probe_duration(source: Path, ffprobe_path: str) -> float:
    try:
        output = subprocess.run([
            ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source),
        ], check=True, capture_output=True, timeout=30).stdout
        duration = float(json.loads(output).get("format", {}).get("duration"))
    except (OSError, subprocess.SubprocessError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise TranscriptionError("无法读取素材音轨时长，请检查视频和 FFmpeg。") from error
    if not math.isfinite(duration) or duration <= 0:
        raise TranscriptionError("素材音轨时长无效。")
    return duration


def run_faster_whisper_block(
    source: Path, block: TranscriptionBlock, cancelled: Callable[[], bool], work_dir: Path, settings: dict[str, str],
    *, ffmpeg_path: str = "ffmpeg", model: Any = None,
) -> dict[str, Any]:
    if cancelled():
        raise TranscriptionCancelled("转录已取消。")
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise TranscriptionModelUnavailable("faster-whisper 未安装；请配置本地模型后重试。") from error
    work_dir.mkdir(parents=True, exist_ok=True)
    audio = work_dir / f"block-{block.index:04d}.wav"
    process = None
    try:
        process = subprocess.Popen([
            ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", f"{block.start_seconds:.6f}",
            "-t", f"{block.end_seconds - block.start_seconds:.6f}", "-i", str(source), "-map", "0:a:0",
            "-ac", "1", "-ar", "16000", str(audio),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 180
        while process.poll() is None:
            if cancelled():
                process.terminate()
                raise TranscriptionCancelled("转录已取消。")
            if time.monotonic() >= deadline:
                process.kill()
                raise TranscriptionError("提取本地音轨超时。")
            time.sleep(.05)
        if process.returncode:
            raise TranscriptionError("无法提取素材音轨；素材可能损坏或不含可用音频。")
        if cancelled(): raise TranscriptionCancelled("转录已取消。")
        worker = model or WhisperModel(settings["model"], device=settings["device"], compute_type=settings["computeType"])
        raw_segments, _info = worker.transcribe(str(audio), language=settings["language"], word_timestamps=True, vad_filter=settings["vadFilter"] == "true", beam_size=int(settings["beamSize"]), condition_on_previous_text=settings["conditionOnPreviousText"] == "true")
        segments = []
        for segment in raw_segments:
            if cancelled():
                raise TranscriptionCancelled("转录已取消。")
            segments.append({
                "start": segment.start, "end": segment.end, "text": segment.text,
                "words": [{"start": word.start, "end": word.end, "word": word.word} for word in (segment.words or [])],
            })
        return {"segments": segments}
    except TranscriptionCancelled:
        raise
    except Exception as error:
        raise TranscriptionError("faster-whisper 转录失败，请检查本地模型、音轨和运行环境。") from error
    finally:
        if process is not None and process.poll() is None: process.kill()
        if process is not None and process.stderr is not None: process.stderr.close()
        audio.unlink(missing_ok=True)


class _Store:
    def __init__(self, root: Path): self.root = Path(root).absolute()

    def path(self, project_id: str, asset_id: str) -> Path:
        return safe_child(self.root, "project-files", project_id, "transcriptions", asset_id, "state.json")

    def run_dir(self, project_id: str, asset_id: str, run_id: str) -> Path:
        return safe_child(self.root, "project-files", project_id, "transcriptions", asset_id, "runs", run_id)

    def load(self, project_id: str, asset_id: str) -> dict[str, Any]:
        try:
            value = json.loads(self.path(project_id, asset_id).read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("assetId") != asset_id or value.get("status") not in RUN_STATUSES:
                raise ValueError
            return value
        except FileNotFoundError:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise OSError("转录记录损坏") from error

    def save(self, project_id: str, state: dict[str, Any]) -> None:
        _sync_runs(state)
        path = self.path(project_id, validate_storage_id(state["assetId"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".part")
        temporary = Path(raw)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def all(self, project_id: str) -> list[dict[str, Any]]:
        root = safe_child(self.root, "project-files", project_id, "transcriptions")
        return sorted((self.load(project_id, item.parent.name) for item in root.glob("*/state.json")) if root.is_dir() else [], key=lambda item: item["assetId"])


def _sync_runs(state: dict[str, Any]) -> None:
    """Persist compact historical run evidence while retaining current checkpoints."""
    blocks = state.get("blocks", [])
    completed = sum(block.get("status") == "completed" for block in blocks)
    entry = {
        "id": state["runId"], "commonRunId": state["runId"], "attemptId": state["attemptId"],
        "idempotencyKey": state.get("cacheKey"), "retryOf": state.get("previousRunId"),
        "status": state["status"], "queuedAt": state.get("queuedAt"), "startedAt": state.get("startedAt"),
        "completedAt": state.get("completedAt"), "updatedAt": state.get("updatedAt"), "error": state.get("error"),
        "completedBlocks": completed, "blockCount": len(blocks),
        "progress": round(completed / len(blocks), 4) if blocks else 0, "logs": state.get("logs", []),
    }
    runs = [item for item in state.get("runs", []) if item.get("id") != state["runId"]]
    runs.append(entry)
    state["runs"] = runs


def create_transcription_router(
    data_dir: Path, get_project: Callable[[str], Any], compute_queue: Any, *,
    runner: Callable[[Path, TranscriptionBlock, Callable[[], bool], Path, dict[str, str]], dict[str, Any]] | None = None,
    ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe", source_lock: Any = None,
    policy_version: str = TRANSCRIPTION_POLICY_VERSION, duration_probe: Callable[[Path], float] | None = None,
    model_identity_resolver: Callable[[dict[str, str]], str | None] | None = None,
) -> APIRouter:
    root, assets, store, lock = Path(data_dir).absolute(), ProjectAssets(Path(data_dir).absolute()), _Store(Path(data_dir).absolute()), source_lock or RLock()
    loaded_models: dict[str, Any] = {}
    def default_runner(source: Path, block: TranscriptionBlock, cancelled: Callable[[], bool], work_dir: Path, settings: dict[str, str]) -> dict[str, Any]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise TranscriptionModelUnavailable("faster-whisper 未安装；请配置本地模型后重试。") from error
        model_key = f"{settings['model']}:{settings['device']}:{settings['computeType']}"
        if model_key not in loaded_models:
            try:
                loaded_models[model_key] = WhisperModel(settings["model"], device=settings["device"], compute_type=settings["computeType"])
            except (OSError, RuntimeError) as error:
                raise TranscriptionModelUnavailable("本地 faster-whisper 模型不可用；请检查权重和运行环境。") from error
        model = loaded_models[model_key]
        return run_faster_whisper_block(source, block, cancelled, work_dir, settings, ffmpeg_path=ffmpeg_path, model=model)
    transcribe = runner or default_runner
    router = APIRouter(prefix="/api/projects/{project_id}/transcriptions")

    def fail(code: str, message: str, status: int = 409): raise HTTPException(status, detail={"code": code, "message": message})
    def now() -> str: return datetime.now(timezone.utc).isoformat()
    def ensure_project(project_id: str) -> None:
        if get_project(project_id) is None: fail("project_not_found", "内容项目不存在。", 404)
    def asset_source(project_id: str, asset_id: str) -> tuple[dict[str, Any], Path]:
        try:
            asset = assets.get(project_id, asset_id)
            if asset.get("kind") != "video": fail("video_asset_required", "只有项目视频素材可以转录。", 422)
            return asset, assets.file(project_id, asset)
        except KeyError: fail("asset_not_found", "项目素材不存在。", 404)
        except AssetFileUnavailableError: fail("source_missing", "项目素材文件不可用，无法转录。")
        raise AssertionError
    def settings_for(asset: dict[str, Any], source: Path, language: str, model: str, model_revision: str) -> dict[str, str]:
        identity = {"sourceSha256": source_fingerprint(source), "assetVersionId": ProjectAssets.version_id(asset), "language": language, "requestedLanguage": language, "requestedModel": model, "modelRevision": model_revision, "policyVersion": policy_version, "device": "auto", "computeType": "auto", "vadFilter": "true", "beamSize": "5", "conditionOnPreviousText": "false"}
        identity["cacheKey"] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return identity
    def valid(project_id: str, state: dict[str, Any]) -> bool:
        try:
            asset, source = asset_source(project_id, state["assetId"])
            revision = state.get("modelRevision")
            return isinstance(revision, str) and bool(revision) and all(state.get(key) == value for key, value in settings_for(asset, source, state["language"], state["model"], revision).items())
        except HTTPException: return False
    def public(project_id: str, state: dict[str, Any]) -> dict[str, Any]:
        value = {key: item for key, item in state.items() if not key.startswith("_")}
        value["validity"] = {"status": "current" if valid(project_id, state) else "stale"}
        value["completedBlocks"] = sum(block["status"] == "completed" for block in state["blocks"])
        value["blockCount"] = len(state["blocks"])
        value["progress"] = round(value["completedBlocks"] / len(state["blocks"]), 4) if state["blocks"] else 0
        return value
    def new_state(project_id: str, asset_id: str, language: str, model: str, *, carry: dict[str, Any] | None = None, history: dict[str, Any] | None = None) -> dict[str, Any]:
        asset, source = asset_source(project_id, asset_id); duration = (duration_probe or (lambda path: _probe_duration(path, ffprobe_path)))(source); timestamp = now()
        carry_blocks = {block["index"]: block for block in (carry or {}).get("blocks", []) if block.get("status") == "completed"}
        blocks = [{"index": block.index, "startSeconds": block.start_seconds, "endSeconds": block.end_seconds, "status": "completed" if block.index in carry_blocks else "queued", "segments": carry_blocks.get(block.index, {}).get("segments", []), "words": carry_blocks.get(block.index, {}).get("words", [])} for block in build_transcription_blocks(duration)]
        return {"schemaVersion": 1, "assetId": asset_id, "runId": f"transcription-run-{uuid4().hex}", "attemptId": f"transcription-attempt-{uuid4().hex}", "status": "queued", "sourceSha256": source_fingerprint(source), "assetVersionId": ProjectAssets.version_id(asset), "language": language, "requestedLanguage": language, "model": model, "requestedModel": model, "modelRevision": None, "modelIdentityStatus": "unverified", "policyVersion": policy_version, "device": "auto", "computeType": "auto", "vadFilter": "true", "beamSize": "5", "conditionOnPreviousText": "false", "cacheKey": None, "durationSeconds": duration, "blocks": blocks, "segments": [], "words": [], "queuedAt": timestamp, "startedAt": None, "completedAt": None, "updatedAt": timestamp, "error": None, "previousRunId": (history or {}).get("runId"), "runs": list((history or {}).get("runs", [])), "logs": [{"at": timestamp, "message": "已提交本地 faster-whisper 转录；模型身份待运行预检验证。"}]}
    def rebuild(state: dict[str, Any]) -> None:
        state["segments"] = [segment for block in state["blocks"] for segment in block["segments"]]
        state["words"] = [word for block in state["blocks"] for word in block["words"]]
    def enqueue(project_id: str, state: dict[str, Any]) -> None:
        run_id, attempt_id, asset_id = state["runId"], state["attemptId"], state["assetId"]
        if not compute_queue.submit("transcription", f"{project_id}:{asset_id}:{run_id}", lambda _owner: process(project_id, asset_id, run_id, attempt_id), project_id=project_id):
            state.update(status="failed", updatedAt=now(), error={"code":"queue_unavailable","message":"本地转录队列暂不可用，请重试。","retryable":True}); store.save(project_id, state)
    def process(project_id: str, asset_id: str, run_id: str, attempt_id: str) -> None:
        try:
            with lock:
                state = store.load(project_id, asset_id)
                if (state["runId"], state["attemptId"], state["status"]) != (run_id, attempt_id, "queued"): return
                state.update(status=LOCAL_RUN_POLICY.start("queued"), startedAt=now(), updatedAt=now()); store.save(project_id, state)
            def cancelled() -> bool:
                with lock:
                    try:
                        current = store.load(project_id, asset_id)
                        return current["runId"] != run_id or current["attemptId"] != attempt_id or current["status"] == "cancelled"
                    except (OSError, FileNotFoundError): return True
            _asset, source = asset_source(project_id, asset_id)
            revision = model_identity_resolver({key: state[key] for key in ("model", "device", "computeType")}) if model_identity_resolver else None
            if not revision:
                raise TranscriptionModelIdentityUnverified("本地模型修订或权重哈希尚未验证，不能作为可复现转录输入。")
            with lock:
                state = store.load(project_id, asset_id)
                state.update(**settings_for(_asset, source, state["language"], state["model"], revision), modelIdentityStatus="verified", updatedAt=now())
                store.save(project_id, state)
            while True:
                with lock:
                    state = store.load(project_id, asset_id)
                    pending = next((item for item in state["blocks"] if item["status"] != "completed"), None)
                    prior_end = max((segment["endSeconds"] for segment in state["segments"]), default=0.0)
                if pending is None: break
                block = TranscriptionBlock(pending["index"], pending["startSeconds"], pending["endSeconds"])
                raw = transcribe(source, block, cancelled, store.run_dir(project_id, asset_id, run_id), {key: state[key] for key in ("language", "model", "device", "computeType", "vadFilter", "beamSize", "conditionOnPreviousText")})
                if cancelled(): raise TranscriptionCancelled("转录已取消。")
                normalized = normalize_block_transcript(block, raw, previous_end_seconds=prior_end)
                with lock:
                    state = store.load(project_id, asset_id)
                    if state["runId"] != run_id or state["attemptId"] != attempt_id or state["status"] != "running": return
                    record = next(item for item in state["blocks"] if item["index"] == block.index)
                    record.update(status="completed", segments=[{"startSeconds": item.start_seconds,"endSeconds":item.end_seconds,"text":item.text,"words":[{"startSeconds": word.start_seconds, "endSeconds": word.end_seconds, "text": word.text} for word in item.words]} for item in normalized.segments], words=[{"startSeconds": item.start_seconds,"endSeconds":item.end_seconds,"text":item.text} for item in normalized.words])
                    rebuild(state); state["updatedAt"] = now(); store.save(project_id, state)
            with lock:
                state = store.load(project_id, asset_id)
                if state["runId"] != run_id or state["attemptId"] != attempt_id or state["status"] != "running": return
                if not valid(project_id, state): raise TranscriptionError("转录期间源素材或策略已变化，请重试。")
                if not state["words"]: raise TranscriptionError("未检测到可用语音；该素材未生成空白成功记录。")
                state.update(status="completed", completedAt=now(), updatedAt=now(), error=None); store.save(project_id, state)
        except TranscriptionCancelled:
            with lock:
                try:
                    state = store.load(project_id, asset_id)
                    if state["runId"] == run_id and state["attemptId"] == attempt_id and LOCAL_RUN_POLICY.active(state["status"]): state.update(status="cancelled", updatedAt=now(), error=None); store.save(project_id, state)
                except (OSError, FileNotFoundError, RunConflict): pass
        except Exception as error:
            with lock:
                try:
                    state = store.load(project_id, asset_id)
                    if state["runId"] == run_id and state["attemptId"] == attempt_id and state["status"] == "running":
                        code = "model_identity_unverified" if isinstance(error, TranscriptionModelIdentityUnverified) else "model_unavailable" if isinstance(error, TranscriptionModelUnavailable) else "no_speech" if "未检测到" in str(error) else "transcription_failed"
                        state.update(status="failed", updatedAt=now(), error={"code":code,"message":str(error) if isinstance(error, TranscriptionError) else "本地转录失败，请检查素材和运行环境。","retryable":code != "no_speech"}); store.save(project_id, state)
                except (OSError, FileNotFoundError): pass
    def reconcile() -> None:
        base = root / "project-files"
        for state_path in base.glob("*/transcriptions/*/state.json") if base.is_dir() else []:
            try:
                project_id, asset_id = state_path.parents[2].name, state_path.parent.name; state = store.load(project_id, asset_id)
                if LOCAL_RUN_POLICY.active(state["status"]): state.update(status="queued", attemptId=f"transcription-attempt-{uuid4().hex}", startedAt=None, updatedAt=now(), error=None); store.save(project_id, state); enqueue(project_id, state)
            except (OSError, ValueError, KeyError, RunConflict): continue
    reconcile()
    @router.get("")
    def list_transcriptions(project_id: str):
        ensure_project(project_id)
        with lock:
            try: return {"transcriptions": [public(project_id, state) for state in store.all(project_id)]}
            except OSError: fail("transcription_storage_unavailable", "转录记录无法读取。", 503)
    @router.post("", status_code=202)
    def start_transcription(project_id: str, request: StartTranscription):
        ensure_project(project_id)
        with lock:
            try:
                try:
                    existing = store.load(project_id, request.assetId)
                    if LOCAL_RUN_POLICY.active(existing["status"]): fail("transcription_active", "所选素材已有转录正在运行。")
                    asset, source = asset_source(project_id, request.assetId)
                    if existing["status"] == "completed" and valid(project_id, existing) and existing["language"] == request.language and existing["model"] == request.model: return public(project_id, existing)
                except FileNotFoundError: existing = None
                reusable = existing if existing and valid(project_id, existing) and existing.get("language") == request.language and existing.get("model") == request.model else None
                state = new_state(project_id, request.assetId, request.language, request.model, carry=reusable, history=existing)
                rebuild(state); store.save(project_id, state); enqueue(project_id, state); return public(project_id, state)
            except OSError: fail("transcription_storage_unavailable", "转录记录无法保存。", 503)
    @router.post("/assets/{asset_id}/cancel")
    def cancel_transcription(project_id: str, asset_id: str):
        ensure_project(project_id)
        with lock:
            try:
                state = store.load(project_id, validate_storage_id(asset_id)); state.update(status=LOCAL_RUN_POLICY.cancel(state["status"]), updatedAt=now(), error=None); store.save(project_id, state); return public(project_id, state)
            except FileNotFoundError: fail("transcription_not_found", "转录不存在。", 404)
            except (ValueError, RunConflict): fail("transcription_not_cancellable", "当前转录不能取消。")
    @router.post("/assets/{asset_id}/retry", status_code=202)
    def retry_transcription(project_id: str, asset_id: str):
        ensure_project(project_id)
        with lock:
            try:
                current = store.load(project_id, validate_storage_id(asset_id))
                if LOCAL_RUN_POLICY.active(current["status"]): fail("transcription_active", "转录仍在运行，不能重试。")
                state = new_state(project_id, asset_id, current["language"], current["model"], carry=current if valid(project_id, current) else None, history=current); rebuild(state); store.save(project_id, state); enqueue(project_id, state); return public(project_id, state)
            except FileNotFoundError: fail("transcription_not_found", "转录不存在。", 404)
    return router
