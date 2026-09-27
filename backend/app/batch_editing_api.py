"""Project-scoped batch editing tasks and independent short-video variants."""
from __future__ import annotations

import json
import math
import os
import shutil
import stat
import tempfile
from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from .batch_recommendation import RecommendationError, recommend
from .batch_subtitles import milliseconds, parse_srt, write_srt
from .preproduction import PreproductionStore, safe_child
from .reference_video import validate_storage_id
from .timeline import validate_workspace
from .video_aspect import PRODUCT_PRESETS, ratio_label, resolve_product_aspect, valid_aspect_mode


def _valid_id(value: Any) -> bool:
    try:
        validate_storage_id(value)
        return True
    except (TypeError, ValueError):
        return False


def _validate_timeline_shape(revision: int, settings: Any, tracks: Any) -> None:
    if not isinstance(tracks, list):
        raise ValueError("短视频变体轨道无效")
    assets = {}
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get("clips"), list):
            raise ValueError("短视频变体轨道无效")
        for clip in track["clips"]:
            if isinstance(clip, dict) and isinstance(clip.get("assetId"), str):
                assets[clip["assetId"]] = {"kind": "video", "duration": 1_000_000_000}
    try:
        validate_workspace({"revision": revision, "settings": settings, "tracks": tracks}, assets)
    except (TypeError, KeyError, ValueError) as error:
        raise ValueError("短视频变体时间线无效") from error


def _validate_cues(cues: Any, tracks: list) -> list:
    duration = max((clip["start"] + clip["duration"] for track in tracks
                    if track["kind"] == "video" and not track["hidden"] for clip in track["clips"]), default=0)
    if not isinstance(cues, list) or len(cues) > 300:
        raise ValueError("字幕条目无效")
    ids = set()
    for cue in cues:
        if (not isinstance(cue, dict) or set(cue) != {"id", "start", "end", "text"}
                or not _valid_id(cue["id"]) or cue["id"] in ids
                or any(type(cue[key]) not in (int, float) or not math.isfinite(cue[key]) for key in ("start", "end"))
                or not 0 <= cue["start"] < cue["end"] <= duration + 0.002
                or milliseconds(cue["end"]) <= milliseconds(cue["start"])
                or not isinstance(cue["text"], str) or not cue["text"].strip() or len(cue["text"]) > 120):
            raise ValueError("字幕文字或时间范围无效")
        ids.add(cue["id"])
    return cues


def _subtitles(variant: dict) -> dict:
    return variant.setdefault("subtitles", {"revision": 0, "cues": [], "recognitions": []})


def _exports(variant: dict) -> list:
    return variant.setdefault("exports", [])


def review_version(task: dict) -> tuple[int, int, int]:
    variant = task["variant"]
    return (task.get("contentRevision", 0), variant["revision"], variant.get("subtitles", {}).get("revision", 0))


def review_status(task: dict) -> str:
    reviews = task.get("reviews", [])
    if not reviews:
        return "pending"
    voice = task.get("voiceover")
    if voice is not None and (voice["status"] != "completed" or voice["contentRevision"] != task.get("contentRevision", 0)):
        return "stale"
    latest = reviews[-1]
    variant = task["variant"]
    reviewed_run = next((run for run in variant["runs"] if run["id"] == latest["runId"]), None)
    if ((latest["contentRevision"], latest["variantRevision"], latest["subtitleRevision"]) != review_version(task)
            or reviewed_run is None or reviewed_run["snapshot"] != {"settings": variant["settings"], "tracks": variant["tracks"]}
            or reviewed_run.get("subtitleCues", []) != variant.get("subtitles", {}).get("cues", [])):
        return "stale"
    return latest["decision"]


def _validate_state(value: Any) -> None:
    if not isinstance(value, dict) or value.get("schemaVersion") != 1 or not isinstance(value.get("tasks"), list):
        raise ValueError("批量混剪状态无效")
    ids = set()
    for task in value["tasks"]:
        if not isinstance(task, dict) or not _valid_id(task.get("id")) or any(
            not isinstance(task.get(key), str) or not task[key].strip() for key in ("sellingPoint", "script")
        ):
            raise ValueError("批量任务无效")
        if task["id"] in ids:
            raise ValueError("批量任务编号重复")
        ids.add(task["id"])
        if type(task.get("contentRevision", 0)) is not int or task.get("contentRevision", 0) < 0:
            raise ValueError("脚本版本无效")
        if "batchId" in task or "generationId" in task or "generation" in task:
            generation = task.get("generation")
            if (not _valid_id(task.get("batchId")) or not _valid_id(task.get("generationId"))
                    or not isinstance(generation, dict) or generation.get("status") not in ("ready", "failed")
                    or (generation.get("error") is not None and not isinstance(generation["error"], str))):
                raise ValueError("批量生成记录无效")
        variant = task.get("variant")
        if not isinstance(variant, dict) or not _valid_id(variant.get("id")) or type(variant.get("revision")) is not int or variant["revision"] < 0:
            raise ValueError("短视频变体无效")
        settings, tracks, runs = variant.get("settings"), variant.get("tracks"), variant.get("runs")
        if ("aspectMode" in variant and not valid_aspect_mode(variant["aspectMode"])) or (
                "resolvedAspect" in variant and not isinstance(variant["resolvedAspect"], str)) or (
                "aspectReason" in variant and not isinstance(variant["aspectReason"], str)):
            raise ValueError("短视频变体比例无效")
        if not isinstance(runs, list):
            raise ValueError("短视频变体无效")
        _validate_timeline_shape(variant["revision"], settings, tracks)
        subtitles = variant.get("subtitles")
        if subtitles is not None:
            if (not isinstance(subtitles, dict) or type(subtitles.get("revision")) is not int or subtitles["revision"] < 0
                    or not isinstance(subtitles.get("recognitions"), list)):
                raise ValueError("字幕记录无效")
            _validate_cues(subtitles.get("cues"), tracks)
            for recognition in subtitles["recognitions"]:
                if (not isinstance(recognition, dict) or not _valid_id(recognition.get("id"))
                        or recognition.get("status") not in ("queued", "running", "completed", "failed")
                        or recognition.get("language") not in ("auto", "zh", "en", "ja", "ko")
                        or type(recognition.get("timelineRevision")) is not int or recognition["timelineRevision"] < 0
                        or type(recognition.get("subtitleRevision")) is not int or recognition["subtitleRevision"] < 0
                        or not isinstance(recognition.get("snapshot"), dict) or not isinstance(recognition.get("sources"), dict)
                        or (recognition.get("error") is not None and not isinstance(recognition["error"], str))):
                    raise ValueError("字幕识别记录无效")
                _validate_timeline_shape(recognition["timelineRevision"], recognition["snapshot"].get("settings"), recognition["snapshot"].get("tracks"))
                _validate_cues(recognition.get("cues"), recognition["snapshot"]["tracks"])
                if any(not _valid_id(asset_id) or not _valid_id(filename) for asset_id, filename in recognition["sources"].items()):
                    raise ValueError("字幕识别来源无效")
        proposal = task.get("proposal")
        if proposal is not None:
            if (not isinstance(proposal, dict) or type(proposal.get("baseRevision")) is not int
                    or proposal["baseRevision"] < 0 or proposal.get("method") != "asset-metadata-keywords"
                    or not isinstance(proposal.get("assetIds"), list)
                    or any(not _valid_id(asset_id) for asset_id in proposal["assetIds"])
                    or len(set(proposal["assetIds"])) != len(proposal["assetIds"])
                    or not isinstance(proposal.get("clips"), list) or not isinstance(proposal.get("matches"), list)
                    or len(proposal["clips"]) != len(proposal["matches"])):
                raise ValueError("短视频推荐无效")
            first_video = next((track["id"] for track in tracks if track["kind"] == "video"), None)
            proposal_tracks = [{**track, "clips": proposal["clips"]} if track["id"] == first_video else track for track in tracks]
            _validate_timeline_shape(proposal["baseRevision"], settings, proposal_tracks)
            for clip, match in zip(proposal["clips"], proposal["matches"]):
                if (not isinstance(match, dict) or match.get("clipId") != clip["id"] or match.get("assetId") != clip["assetId"]
                        or any(not isinstance(match.get(key), str) or not match[key].strip() for key in ("scriptSegment", "assetName"))
                        or not isinstance(match.get("matchedTerms"), list) or not match["matchedTerms"]
                        or any(not isinstance(term, str) or not term for term in match["matchedTerms"])
                        or not isinstance(match.get("matchedSellingPointTerms"), list)
                        or any(not isinstance(term, str) or not term for term in match["matchedSellingPointTerms"])):
                    raise ValueError("短视频推荐依据无效")
        for run in runs:
            if not isinstance(run, dict) or not _valid_id(run.get("id")) or type(run.get("revision")) is not int or run["revision"] < 0:
                raise ValueError("变体预览记录无效")
            if run.get("status") not in ("queued", "running", "completed", "failed", "cancelled") or not isinstance(run.get("snapshot"), dict) or not isinstance(run.get("sources"), dict):
                raise ValueError("变体预览记录无效")
            _validate_timeline_shape(run["revision"], run["snapshot"].get("settings"), run["snapshot"].get("tracks"))
            if any(not _valid_id(asset_id) or not _valid_id(filename) for asset_id, filename in run["sources"].items()):
                raise ValueError("变体预览来源无效")
            if run.get("error") is not None and not isinstance(run["error"], str):
                raise ValueError("变体预览记录无效")
            if run["status"] == "completed" and not _valid_id(run.get("output")):
                raise ValueError("变体预览输出无效")
            if "subtitleRevision" in run or "subtitleCues" in run:
                if type(run.get("subtitleRevision")) is not int or run["subtitleRevision"] < 0:
                    raise ValueError("预览字幕版本无效")
                _validate_cues(run.get("subtitleCues"), run["snapshot"]["tracks"])
            if type(run.get("contentRevision", 0)) is not int or run.get("contentRevision", 0) < 0:
                raise ValueError("预览脚本版本无效")
        exports = variant.get("exports", [])
        if not isinstance(exports, list):
            raise ValueError("成片记录无效")
        for delivery in exports:
            if (not isinstance(delivery, dict) or not _valid_id(delivery.get("id"))
                    or delivery.get("status") not in ("queued", "running", "completed", "failed", "cancelled")
                    or not isinstance(delivery.get("script"), str) or not delivery["script"].strip()
                    or not isinstance(delivery.get("sellingPoint"), str) or not delivery["sellingPoint"].strip()
                    or not isinstance(delivery.get("review"), dict) or not _valid_id(delivery["review"].get("id"))
                    or delivery["review"].get("decision") != "approved"
                    or not _valid_id(delivery.get("previewRunId"))
                    or any(type(delivery.get(key)) is not int or delivery[key] < 0 for key in ("contentRevision", "variantRevision", "subtitleRevision"))
                    or not isinstance(delivery.get("snapshot"), dict) or not isinstance(delivery.get("sources"), dict)
                    or not isinstance(delivery.get("assets"), list)
                    or any(not isinstance(asset, dict) or not _valid_id(asset.get("id")) or not isinstance(asset.get("name"), str)
                           or not isinstance(asset.get("kind"), str) for asset in delivery["assets"])
                    or (delivery.get("error") is not None and not isinstance(delivery["error"], str))):
                raise ValueError("成片记录无效")
            _validate_timeline_shape(delivery["variantRevision"], delivery["snapshot"].get("settings"), delivery["snapshot"].get("tracks"))
            _validate_cues(delivery.get("subtitles"), delivery["snapshot"]["tracks"])
            if any(not _valid_id(asset_id) or not _valid_id(filename) for asset_id, filename in delivery["sources"].items()):
                raise ValueError("成片素材快照无效")
            if delivery["status"] == "completed" and delivery.get("output") != delivery["id"] + ".mp4":
                raise ValueError("成片文件关联无效")
        reviews = task.get("reviews", [])
        if not isinstance(reviews, list):
            raise ValueError("审核记录无效")
        run_ids = {run["id"] for run in runs}
        for review in reviews:
            if (not isinstance(review, dict) or not _valid_id(review.get("id")) or review.get("decision") not in ("approved", "rejected")
                    or not isinstance(review.get("reason"), str) or not review["reason"].strip() or len(review["reason"]) > 1000
                    or review.get("runId") not in run_ids
                    or any(type(review.get(key)) is not int or review[key] < 0 for key in ("contentRevision", "variantRevision", "subtitleRevision"))):
                raise ValueError("审核记录无效")
    by_id = {task["id"]: task for task in value["tasks"]}
    for task in value["tasks"]:
        if "batchId" in task:
            parent = by_id.get(task["batchId"])
            if parent is None or "batchId" in parent:
                raise ValueError("批量生成记录缺少根任务")


class BatchStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, project_id: str, *parts: str) -> Path:
        return safe_child(self.root, "project-files", project_id, "batch-edits", *parts)

    def load(self, project_id: str) -> dict[str, Any]:
        path = self.path(project_id, "state.json")
        if not path.exists():
            return {"schemaVersion": 1, "tasks": []}
        if path.is_symlink() or not path.is_file():
            raise OSError("批量混剪状态文件无效")
        value = json.loads(path.read_text(encoding="utf-8"))
        _validate_state(value)
        return value

    def save(self, project_id: str, value: dict[str, Any]) -> None:
        _validate_state(value)
        path = self.path(project_id, "state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".batch-", suffix=".part")
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def new_task(selling_point: str, script: str, aspect: dict | None = None):
    aspect = aspect or {"mode": "9:16", "resolvedAspect": "9:16", "width": 720, "height": 1280,
                        "reason": "商品制作默认使用 9:16。"}
    return {"id": uuid4().hex, "sellingPoint": selling_point.strip(), "script": script.strip(),
            "contentRevision": 0, "reviews": [],
            "variant": {"id": uuid4().hex, "revision": 0,
                        "aspectMode": aspect["mode"], "resolvedAspect": aspect["resolvedAspect"],
                        "aspectReason": aspect["reason"],
                        "settings": {"width": aspect["width"], "height": aspect["height"], "fps": 30},
                        "tracks": [{"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": []},
                                   {"id": "audio", "name": "声音", "kind": "audio", "muted": False, "hidden": False, "clips": []}],
                        "subtitles": {"revision": 0, "cues": [], "recognitions": []}, "runs": [], "exports": []}}


def create_batch_editing_router(data_dir, get_project, compute_queue, *, ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", source_lock=None, renderer=None, voice_service=None):
    store = BatchStore(Path(data_dir))
    preproduction = PreproductionStore(Path(data_dir))
    lock = RLock()
    router = APIRouter(prefix="/api/projects/{project_id}/batch-edits")
    from .batch_voiceover import plan_voiceover, audio_duration, arrange_voiceover

    def voice_current(task):
        voice = task.get("voiceover")
        if voice is not None and (voice["status"] != "completed" or voice["contentRevision"] != task.get("contentRevision", 0)):
            raise HTTPException(409, detail={"code": "batch_voiceover_stale", "message": "配音未完成或脚本已修改，请按当前脚本重新生成配音。"})


    def project_for(project_id: str):
        if get_project(project_id) is None:
            raise HTTPException(404, detail={"code": "project_not_found", "message": "项目不存在。"})

    def state_for(project_id: str):
        try:
            return store.load(project_id)
        except (OSError, ValueError):
            raise HTTPException(503, detail={"code": "batch_storage_invalid", "message": "批量混剪状态无法读取。"}) from None

    def save(project_id: str, state: dict[str, Any]):
        try:
            store.save(project_id, state)
        except (OSError, ValueError):
            raise HTTPException(503, detail={"code": "batch_storage_failed", "message": "批量混剪状态无法保存。"}) from None

    def task_for(state: dict[str, Any], task_id: str):
        task = next((item for item in state["tasks"] if item["id"] == task_id), None)
        if task is None:
            raise HTTPException(404, detail={"code": "batch_task_missing", "message": "批量任务不存在。"})
        return task

    def assets_for(project_id: str):
        try:
            preparation = preproduction.load(project_id)
        except (OSError, ValueError):
            raise HTTPException(503, detail={"code": "batch_assets_unavailable", "message": "项目素材无法读取。"}) from None
        return {asset["id"]: asset for asset in preparation["assets"] if isinstance(asset, dict) and isinstance(asset.get("id"), str)}

    def public_task(project_id: str, task: dict[str, Any]):
        result = {**task, "contentRevision": task.get("contentRevision", 0), "reviews": task.get("reviews", []),
                  "reviewStatus": review_status(task), "variant": {**task["variant"]}}
        saved_ratio = ratio_label(task["variant"]["settings"]["width"], task["variant"]["settings"]["height"])
        result["variant"].setdefault("aspectMode", saved_ratio if saved_ratio in PRODUCT_PRESETS else "smart")
        result["variant"].setdefault("resolvedAspect", saved_ratio)
        result["variant"].setdefault("aspectReason", "沿用作品已保存的输出尺寸。")
        subtitles = task["variant"].get("subtitles", {"revision": 0, "cues": [], "recognitions": []})
        result["variant"]["subtitles"] = {**subtitles, "recognitions": [
            {key: value for key, value in run.items() if key not in ("snapshot", "sources")}
            for run in subtitles["recognitions"]]}
        if task.get("voiceover"):
            result["voiceover"] = {key: value for key, value in task["voiceover"].items() if key != "plan"}
        result["variant"]["runs"] = [
            {**{key: value for key, value in run.items() if key not in ("snapshot", "sources", "output", "subtitleCues")},
             "settings": run["snapshot"]["settings"],
             **({"url": f"/api/projects/{project_id}/batch-edits/{task['id']}/variant/previews/{run['id']}/output"} if run["status"] == "completed" else {})}
            for run in task["variant"]["runs"]
        ]
        result["variant"]["exports"] = [
            {**{key: value for key, value in run.items() if key not in ("sources", "output")},
             **({"url": f"/api/projects/{project_id}/batch-edits/{task['id']}/exports/{run['id']}/output"}
                if run["status"] == "completed" else {})}
            for run in task["variant"].get("exports", [])
        ]
        return result

    def regular_file(path: Path):
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("不是普通文件")
        finally:
            os.close(descriptor)

    def arrange_aigc_candidate(project_id: str, task: dict, item: dict, assets: dict):
        try:
            clips = []
            cues = []
            position = 0.0
            for beat in item["beats"]:
                asset = assets[beat["assetId"]]
                if asset["kind"] not in ("image", "video"):
                    raise ValueError("画面素材类型无效")
                regular_file(preproduction.path(project_id, "assets", asset["file"]))
                duration = min(3.0, asset["duration"]) if asset["kind"] == "video" else 3.0
                clips.append({"id": uuid4().hex, "assetId": beat["assetId"], "start": position,
                              "inPoint": 0, "duration": duration, "speed": 1, "volume": 1,
                              "fadeIn": 0, "fadeOut": 0})
                cues.append({"id": uuid4().hex, "start": position, "end": position + duration, "text": beat["text"]})
                position += duration
            validate_workspace({"revision": 0, "settings": task["variant"]["settings"],
                                "tracks": [{**task["variant"]["tracks"][0], "clips": clips},
                                           *task["variant"]["tracks"][1:]]}, assets)
            task["variant"]["tracks"][0]["clips"] = clips
            task["variant"]["subtitles"]["cues"] = cues
            task["generation"] = {"status": "ready", "error": None}
        except (OSError, ValueError, KeyError, TypeError):
            task["variant"]["tracks"][0]["clips"] = []
            task["variant"]["subtitles"]["cues"] = []
            task["generation"] = {"status": "failed", "error": "镜头素材不可用或超出时间线限制，请检查该候选的素材与镜头安排。"}

    def copy_source(source: Path, target: Path):
        descriptor = os.open(str(source), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("素材文件无效")
            with os.fdopen(descriptor, "rb") as stream, target.open("xb") as output:
                descriptor = -1
                shutil.copyfileobj(stream, output)
        finally:
            if descriptor != -1:
                os.close(descriptor)

    def snapshot_sources(project_id: str, directory: Path, tracks: list, assets: dict) -> dict:
        try:
            source_dir = directory / "sources"
            source_dir.mkdir(parents=True, exist_ok=False)
            sources = {}
            for asset_id in {clip["assetId"] for track in tracks for clip in track["clips"]}:
                filename = assets[asset_id]["file"]
                validate_storage_id(filename)
                copied_name = asset_id + Path(filename).suffix.lower()
                copy_source(preproduction.path(project_id, "assets", filename), source_dir / copied_name)
                sources[asset_id] = copied_name
            return sources
        except (OSError, ValueError, KeyError):
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(409, detail={"code": "batch_asset_unavailable", "message": "素材文件不可用。"}) from None

    def same_source_contents(left: Path, right: Path) -> bool:
        with os.fdopen(os.open(left, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as original, \
             os.fdopen(os.open(right, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as approved:
            if not stat.S_ISREG(os.fstat(original.fileno()).st_mode) or not stat.S_ISREG(os.fstat(approved.fileno()).st_mode):
                raise OSError("素材文件无效")
            while True:
                original_bytes, approved_bytes = original.read(1024 * 1024), approved.read(1024 * 1024)
                if original_bytes != approved_bytes:
                    return False
                if not original_bytes:
                    return True

    def snapshot_approved_sources(project_id: str, directory: Path, preview: dict, assets: dict) -> dict:
        try:
            source_dir = directory / "sources"
            source_dir.mkdir(parents=True, exist_ok=False)
            sources = {}
            for asset_id, preview_name in preview["sources"].items():
                filename = assets[asset_id]["file"]
                validate_storage_id(filename)
                approved = store.path(project_id, "runs", preview["id"], "sources", preview_name)
                original = preproduction.path(project_id, "assets", filename)
                if not same_source_contents(original, approved):
                    raise ValueError("素材已变化")
                copy_source(approved, source_dir / preview_name)
                sources[asset_id] = preview_name
            return sources
        except (OSError, ValueError, KeyError):
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(409, detail={"code": "batch_asset_unavailable", "message": "原素材缺失或变化，请重新预览审核。"}) from None

    def find_run(variant: dict[str, Any], run_id: str):
        run = next((item for item in variant["runs"] if item["id"] == run_id), None)
        if run is None:
            raise HTTPException(404, detail={"code": "batch_preview_missing", "message": "预览不存在。"})
        return run

    def run_cancelled(project_id: str, task_id: str, run_id: str) -> bool:
        with lock:
            try:
                state = state_for(project_id)
                run = find_run(task_for(state, task_id)["variant"], run_id)
                return run["status"] == "cancelled"
            except HTTPException:
                return True

    def find_export(variant: dict[str, Any], run_id: str):
        run = next((item for item in variant.get("exports", []) if item["id"] == run_id), None)
        if run is None:
            raise HTTPException(404, detail={"code": "batch_export_missing", "message": "成片任务不存在。"})
        return run

    def export_cancelled(project_id: str, task_id: str, run_id: str) -> bool:
        with lock:
            try:
                run = find_export(task_for(state_for(project_id), task_id)["variant"], run_id)
                return run["status"] == "cancelled"
            except HTTPException:
                return True

    def recognition_for(variant: dict, run_id: str) -> dict:
        run = next((item for item in _subtitles(variant)["recognitions"] if item["id"] == run_id), None)
        if run is None:
            raise HTTPException(404, detail={"code": "batch_recognition_missing", "message": "字幕识别任务不存在。"})
        return run

    def recognition_cancelled(project_id: str, task_id: str, run_id: str) -> bool:
        with lock:
            try:
                run = recognition_for(task_for(state_for(project_id), task_id)["variant"], run_id)
                return run["status"] != "running"
            except HTTPException:
                return True

    def process_recognition(project_id: str, task_id: str, run_id: str):
        try:
            with lock:
                state = state_for(project_id)
                run = recognition_for(task_for(state, task_id)["variant"], run_id)
                if run["status"] != "queued":
                    return
                run["status"] = "running"
                save(project_id, state)
                snapshot, names, language = run["snapshot"], dict(run["sources"]), run["language"]
            directory = store.path(project_id, "recognitions", run_id)
            sources = {asset_id: store.path(project_id, "recognitions", run_id, "sources", filename)
                       for asset_id, filename in names.items()}
            for source in sources.values():
                regular_file(source)
            from .timeline_render import inspect_timeline_media, render_timeline
            from .video_toolkit import ToolkitConfig, execute_tool
            assets = {asset_id: {"kind": "video", "name": asset_id, "duration": 1_000_000} for asset_id in names}
            issues = inspect_timeline_media({"revision": run["timelineRevision"], **snapshot}, assets,
                                            lambda asset: sources[asset["name"]], "wav", ffprobe_path)
            if any(issue["level"] == "error" for issue in issues):
                raise ValueError(next(issue["message"] for issue in issues if issue["level"] == "error"))
            video = directory / "timeline.mp4"
            render_timeline(snapshot, sources, video, format="preview", ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path,
                            cancelled=lambda: recognition_cancelled(project_id, task_id, run_id))
            execute_tool(video, directory, "subtitles", {"language": language}, ToolkitConfig.local(ffmpeg_path, ffprobe_path),
                         lambda: recognition_cancelled(project_id, task_id, run_id), lambda *_: None)
            cues = parse_srt(directory / "subtitles.srt")
            duration = max((clip["start"] + clip["duration"] for track in snapshot["tracks"]
                            if track["kind"] == "video" and not track["hidden"] for clip in track["clips"]), default=0)
            for cue in cues:
                if duration < cue["end"] <= duration + 0.5:
                    cue["end"] = duration
            cues = _validate_cues(cues, snapshot["tracks"])
            if not cues:
                raise ValueError("没有识别到语音字幕，请检查音轨后重试。")
            with lock:
                state = state_for(project_id)
                run = recognition_for(task_for(state, task_id)["variant"], run_id)
                if run["status"] == "running":
                    run.update(status="completed", error=None, cues=cues)
                    save(project_id, state)
        except Exception as error:
            with lock:
                try:
                    state = state_for(project_id)
                    run = recognition_for(task_for(state, task_id)["variant"], run_id)
                    if run["status"] in ("queued", "running"):
                        run.update(status="failed", error=str(error) or "字幕识别失败。")
                        save(project_id, state)
                except HTTPException:
                    pass

    def process(project_id: str, task_id: str, run_id: str):
        staging = None
        try:
            with lock:
                state = state_for(project_id)
                task = task_for(state, task_id)
                run = find_run(task["variant"], run_id)
                if run["status"] != "queued":
                    return
                run["status"] = "running"
                save(project_id, state)
                snapshot = run["snapshot"]
                names = dict(run["sources"])
            sources = {}
            for asset_id, filename in names.items():
                source = store.path(project_id, "runs", run_id, "sources", filename)
                regular_file(source)
                sources[asset_id] = source
            staging = store.path(project_id, "runs", run_id, "preview.mp4")
            actual_renderer = renderer
            if actual_renderer is None:
                from .timeline_render import render_timeline
                actual_renderer = render_timeline
            subtitle_cues = run.get("subtitleCues", [])
            subtitle_file = None
            if subtitle_cues:
                subtitle_file = store.path(project_id, "runs", run_id, "subtitles.srt")
                write_srt(subtitle_file, subtitle_cues)
            render_options = {"subtitles_path": subtitle_file} if subtitle_file else {}
            actual_renderer(snapshot, sources, staging, format="preview", ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path,
                            cancelled=lambda: run_cancelled(project_id, task_id, run_id), **render_options)
            regular_file(staging)
            with lock:
                state = state_for(project_id)
                run = find_run(task_for(state, task_id)["variant"], run_id)
                if run["status"] != "running":
                    return
                output = store.path(project_id, "outputs", run_id + ".mp4")
                output.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, output)
                run.update(status="completed", error=None, output=output.name)
                save(project_id, state)
        except BaseException as error:
            with lock:
                try:
                    state = state_for(project_id)
                    run = find_run(task_for(state, task_id)["variant"], run_id)
                    if run["status"] in ("queued", "running"):
                        message = str(error)
                        run.update(status="failed", error=message if message and len(message) <= 300 and "/" not in message and "\\" not in message else "预览失败，请检查素材与片段。")
                        save(project_id, state)
                except HTTPException:
                    pass
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)

    def process_export(project_id: str, task_id: str, run_id: str):
        staging = None
        try:
            with lock:
                state = state_for(project_id)
                run = find_export(task_for(state, task_id)["variant"], run_id)
                if run["status"] != "queued":
                    return
                run["status"] = "running"
                save(project_id, state)
                snapshot, names, cues = run["snapshot"], dict(run["sources"]), run["subtitles"]
            directory = store.path(project_id, "exports", run_id)
            sources = {asset_id: store.path(project_id, "exports", run_id, "sources", filename)
                       for asset_id, filename in names.items()}
            for source in sources.values():
                regular_file(source)
            subtitle_file = None
            if cues:
                subtitle_file = directory / "subtitles.srt"
                write_srt(subtitle_file, cues)
            staging = directory / "final.mp4"
            actual_renderer = renderer
            if actual_renderer is None:
                from .timeline_render import render_timeline
                actual_renderer = render_timeline
            actual_renderer(snapshot, sources, staging, format="mp4", ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path,
                            cancelled=lambda: export_cancelled(project_id, task_id, run_id),
                            **({"subtitles_path": subtitle_file} if subtitle_file else {}))
            regular_file(staging)
            with lock:
                state = state_for(project_id)
                run = find_export(task_for(state, task_id)["variant"], run_id)
                if run["status"] != "running":
                    return
                output = store.path(project_id, "deliverables", run_id + ".mp4")
                output.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, output)
                run.update(status="completed", error=None, output=output.name)
                save(project_id, state)
        except BaseException as error:
            with lock:
                try:
                    state = state_for(project_id)
                    run = find_export(task_for(state, task_id)["variant"], run_id)
                    if run["status"] in ("queued", "running"):
                        message = str(error)
                        run.update(status="failed", error=message if message and len(message) <= 300 and "/" not in message and "\\" not in message else "成片导出失败，请检查素材与片段。")
                        save(project_id, state)
                except HTTPException:
                    pass
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)

    def recover():
        base = Path(data_dir) / "project-files"
        if not base.is_dir() or base.is_symlink():
            return
        for project_dir in base.iterdir():
            if project_dir.is_symlink() or not project_dir.is_dir():
                continue
            try:
                state = store.load(project_dir.name)
                changed = False
                for task in state["tasks"]:
                    voice = task.get("voiceover")
                    if voice and voice["status"] in ("queued", "running"):
                        voice.update(status="failed", error="服务已重启，请重新生成配音。")
                        voice.pop("plan", None)
                        changed = True
                    for run in task["variant"]["runs"]:
                        if run["status"] in ("queued", "running"):
                            run.update(status="failed", error="服务重启中断了预览，可重新提交。")
                            changed = True
                    for run in task["variant"].get("subtitles", {}).get("recognitions", []):
                        if run["status"] in ("queued", "running"):
                            run.update(status="failed", error="服务重启中断了字幕识别，可重新提交。")
                            changed = True
                    for run in task["variant"].get("exports", []):
                        if run["status"] in ("queued", "running"):
                            run.update(status="failed", error="服务重启中断了成片导出，可重新提交。")
                            changed = True
                if changed:
                    store.save(project_dir.name, state)
            except (OSError, ValueError, KeyError, TypeError):
                continue

    recover()

    @router.get("")
    def list_tasks(project_id: str):
        project_for(project_id)
        with lock:
            assets = [{**{key: value for key, value in asset.items() if key != "file" and not key.startswith("_")},
                       "url": f"/api/projects/{project_id}/preproduction/assets/{asset['id']}/file"}
                      for asset in assets_for(project_id).values()]
            return {"tasks": [public_task(project_id, task) for task in state_for(project_id)["tasks"]], "assets": assets}

    @router.post("", status_code=201)
    def create_task(project_id: str, body: dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) != {"sellingPoint", "script"} or any(
            not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 4000 for key in ("sellingPoint", "script")
        ):
            raise HTTPException(422, detail={"code": "batch_input_invalid", "message": "请填写卖点和已确认脚本。"})
        task = new_task(body["sellingPoint"], body["script"])
        with lock:
            state = state_for(project_id)
            state["tasks"].append(task)
            save(project_id, state)
        return {"task": public_task(project_id, task)}

    @router.post("/from-aigc", status_code=201)
    def create_from_aigc(project_id: str, body: dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) != {"generationId"} or not _valid_id(body["generationId"]):
            raise HTTPException(422, detail={"code": "aigc_handoff_invalid", "message": "请选择一组已确认的脚本候选。"})
        from .aigc_content_api import load_confirmed_handoff
        with source_lock or nullcontext(), lock:
            brief, candidates = load_confirmed_handoff(data_dir, project_id, body["generationId"])
            handoff_key = json.dumps([body["generationId"], brief["revision"],
                                      [(item["id"], item["revision"]) for item in candidates]], separators=(",", ":"))
            state = state_for(project_id)
            assets = assets_for(project_id)
            selected_assets = [assets[asset_id] for asset_id in brief["assetIds"] if asset_id in assets]
            aspect = resolve_product_aspect(brief.get("aspectMode", "9:16"), selected_assets)

            def source_for(item):
                used_ids = list(dict.fromkeys(beat["assetId"] for beat in item["beats"]))
                fact_ids = {fact_id for beat in item["beats"] for fact_id in beat["factIds"]}
                return {"candidateId": item["id"], "candidateRevision": item["revision"],
                        "briefRevision": brief["revision"], "generationId": body["generationId"],
                        "screenCopySource": "script", "sellingPoint": item["sellingPoint"],
                        "brief": deepcopy(brief),
                        "beats": deepcopy(item["beats"]),
                        "facts": deepcopy([fact for fact in brief["facts"] if fact["id"] in fact_ids]),
                        "assets": [{"id": asset_id, "name": assets[asset_id]["name"], "kind": assets[asset_id]["kind"]}
                                   for asset_id in used_ids if asset_id in assets]}

            existing = next((task for task in state["tasks"] if task.get("aigcHandoffKey") == handoff_key), None)
            if existing is None:
                for parent in reversed(state["tasks"]):
                    if parent.get("aigcGroup") != {"generationId": body["generationId"], "briefRevision": brief["revision"]}:
                        continue
                    children = [task for task in state["tasks"] if task.get("batchId") == parent["id"]]
                    by_candidate = {task.get("aigcSource", {}).get("candidateId"): task for task in children}
                    if len(children) == 5 and all(
                        item["id"] in by_candidate and (by_candidate[item["id"]]["generation"]["status"] == "failed"
                        or by_candidate[item["id"]]["aigcSource"]["candidateRevision"] == item["revision"])
                        for item in candidates
                    ):
                        existing = parent
                        break
            if existing is not None:
                children = [task for task in state["tasks"] if task.get("batchId") == existing["id"]]
                children_by_candidate = {task["aigcSource"]["candidateId"]: task for task in children}
                skipped = []
                for item in candidates:
                    child = children_by_candidate[item["id"]]
                    if child["generation"]["status"] != "failed":
                        continue
                    if (child.get("contentRevision", 0) > 0 or child["variant"]["revision"] > 0
                            or child["variant"]["subtitles"]["revision"] > 0
                            or child["variant"]["runs"] or child["variant"].get("exports") or child.get("reviews")):
                        skipped.append(child["id"])
                        continue
                    child["sellingPoint"] = item["sellingPoint"]
                    child["script"] = "。".join(beat["text"] for beat in item["beats"])
                    child["aigcSource"] = source_for(item)
                    arrange_aigc_candidate(project_id, child, item, assets)
                if all(children_by_candidate[item["id"]]["aigcSource"]["candidateRevision"] == item["revision"]
                       for item in candidates):
                    existing["aigcHandoffKey"] = handoff_key
                save(project_id, state)
                return JSONResponse(status_code=200, content={"task": public_task(project_id, existing),
                                                              "tasks": [public_task(project_id, task) for task in children],
                                                              "skippedTaskIds": skipped})
            parent = new_task(brief["productName"] + "营销视频", "。".join(beat["text"] for beat in candidates[0]["beats"]), aspect)
            parent["aigcHandoffKey"] = handoff_key
            parent["aigcGroup"] = {"generationId": body["generationId"], "briefRevision": brief["revision"]}
            generation_id = uuid4().hex
            children = []
            for item in candidates:
                script = "。".join(beat["text"] for beat in item["beats"])
                task = new_task(item["sellingPoint"], script, aspect)
                task.update(batchId=parent["id"], generationId=generation_id,
                            generation={"status": "ready", "error": None},
                            aigcSource=source_for(item))
                arrange_aigc_candidate(project_id, task, item, assets)
                children.append(task)
            state["tasks"].extend([parent, *children])
            save(project_id, state)
            return {"task": public_task(project_id, parent), "tasks": [public_task(project_id, item) for item in children]}

    @router.put("/{task_id}/content")
    def save_content(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or set(body) != {"revision", "sellingPoint", "script"}
                or type(body["revision"]) is not int or any(
                    not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 4000
                    for key in ("sellingPoint", "script"))):
            raise HTTPException(422, detail={"code": "batch_content_invalid", "message": "卖点或脚本无效。"})
        with lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            if body["revision"] != task.get("contentRevision", 0):
                raise HTTPException(409, detail={"code": "batch_content_conflict", "message": "脚本已更新，请刷新后重试。"})
            if (body["sellingPoint"].strip(), body["script"].strip()) != (task["sellingPoint"], task["script"]):
                task.update(sellingPoint=body["sellingPoint"].strip(), script=body["script"].strip(),
                            contentRevision=task.get("contentRevision", 0) + 1)
                task.pop("proposal", None)
                save(project_id, state)
            return {"task": public_task(project_id, task)}

    @router.put("/{task_id}/subtitles")
    def save_subtitles(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or set(body) != {"revision", "timelineRevision", "cues"}
                or type(body["revision"]) is not int or type(body["timelineRevision"]) is not int):
            raise HTTPException(422, detail={"code": "batch_subtitles_invalid", "message": "字幕请求无效。"})
        with lock:
            state = state_for(project_id)
            variant = task_for(state, task_id)["variant"]
            subtitles = _subtitles(variant)
            if body["timelineRevision"] != variant["revision"]:
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "时间线已更新，请刷新后再保存字幕。"})
            if body["revision"] != subtitles["revision"]:
                raise HTTPException(409, detail={"code": "batch_subtitles_conflict", "message": "字幕已更新，请刷新后重试。"})
            try:
                cues = _validate_cues(body["cues"], variant["tracks"])
            except ValueError as error:
                raise HTTPException(422, detail={"code": "batch_subtitles_invalid", "message": str(error)}) from None
            subtitles["cues"] = cues
            subtitles["revision"] += 1
            save(project_id, state)
            return {"subtitles": public_task(project_id, task_for(state, task_id))["variant"]["subtitles"]}

    @router.post("/{task_id}/subtitles/recognitions", status_code=202)
    def recognize_subtitles(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) != {"language"} or body["language"] not in ("auto", "zh", "en", "ja", "ko"):
            raise HTTPException(422, detail={"code": "batch_recognition_invalid", "message": "请选择识别语言。"})
        from .video_toolkit import ToolkitConfig
        environment = ToolkitConfig.local(ffmpeg_path, ffprobe_path).environment()["subtitles"]
        if not environment["available"]:
            raise HTTPException(503, detail={"code": "batch_recognition_unavailable", "message": environment["message"]})
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            variant = task_for(state, task_id)["variant"]
            subtitles = _subtitles(variant)
            if any(run["status"] in ("queued", "running") for run in subtitles["recognitions"]):
                raise HTTPException(409, detail={"code": "batch_recognition_active", "message": "已有字幕识别任务正在运行。"})
            if not any(track["kind"] == "video" and not track["hidden"] and track["clips"] for track in variant["tracks"]):
                raise HTTPException(422, detail={"code": "batch_video_required", "message": "请先保存包含画面的变体。"})
            run_id = uuid4().hex
            directory = store.path(project_id, "recognitions", run_id)
            snapshot = {"settings": variant["settings"], "tracks": variant["tracks"]}
            sources = snapshot_sources(project_id, directory, variant["tracks"], assets_for(project_id))
            run = {"id": run_id, "status": "queued", "error": None, "timelineRevision": variant["revision"],
                   "subtitleRevision": subtitles["revision"], "language": body["language"], "cues": [],
                   "snapshot": snapshot, "sources": sources}
            subtitles["recognitions"].append(run)
            save(project_id, state)
            if not compute_queue.submit("batch-recognition:" + run_id, project_id,
                                        lambda pid: process_recognition(pid, task_id, run_id)):
                run.update(status="failed", error="本地处理队列不可用。")
                save(project_id, state)
            return {"subtitles": public_task(project_id, task_for(state, task_id))["variant"]["subtitles"]}

    @router.post("/{task_id}/variants/bulk", status_code=201)
    def create_bulk_variants(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) != {"items", "assetIds"}:
            raise HTTPException(422, detail={"code": "batch_bulk_invalid", "message": "请提交 5–20 条卖点和已确认脚本。"})
        items, asset_ids = body["items"], body["assetIds"]
        if (not isinstance(items, list) or not 5 <= len(items) <= 20 or any(
            not isinstance(item, dict) or set(item) != {"sellingPoint", "script"} or any(
                not isinstance(item[key], str) or not item[key].strip() or len(item[key]) > 4000 for key in ("sellingPoint", "script")
            ) for item in items
        ) or len({(item["sellingPoint"].strip(), item["script"].strip()) for item in items}) != len(items)):
            raise HTTPException(422, detail={"code": "batch_bulk_invalid", "message": "请填写 5–20 条不重复的卖点与已确认脚本。"})
        if (not isinstance(asset_ids, list) or len(asset_ids) > 80 or any(not _valid_id(asset_id) for asset_id in asset_ids)
                or len(set(asset_ids)) != len(asset_ids)):
            raise HTTPException(422, detail={"code": "batch_bulk_assets_invalid", "message": "请从项目素材中选择画面。"})
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            parent = task_for(state, task_id)
            if parent.get("batchId") is not None:
                raise HTTPException(422, detail={"code": "batch_bulk_parent_invalid", "message": "请在原批量任务中创建变体。"})
            assets = assets_for(project_id)
            if sum(assets.get(asset_id, {}).get("kind") in ("video", "image") for asset_id in asset_ids) < 2:
                raise HTTPException(422, detail={"code": "batch_assets_insufficient", "message": "至少选择两段画面素材，再批量生成。"})
            generation_id = uuid4().hex
            created = []
            signatures = set()
            for item in items:
                inherited = parent["variant"]
                task = new_task(item["sellingPoint"], item["script"], {
                    "mode": inherited.get("aspectMode", "9:16"),
                    "resolvedAspect": inherited.get("resolvedAspect", ratio_label(inherited["settings"]["width"], inherited["settings"]["height"])),
                    "reason": "继承批次已保存的视频比例。",
                    "width": inherited["settings"]["width"], "height": inherited["settings"]["height"],
                })
                task.update(batchId=task_id, generationId=generation_id, generation={"status": "ready", "error": None})
                try:
                    proposal = recommend(task, assets, asset_ids)
                    signature = tuple(clip["assetId"] for clip in proposal["clips"])
                    if signature in signatures:
                        raise RecommendationError("batch_duplicate_recommendation", "推荐镜头与本批另一条相同，请补充不同的脚本或素材描述。")
                    for asset_id in signature:
                        regular_file(preproduction.path(project_id, "assets", assets[asset_id]["file"]))
                    task["proposal"] = proposal
                    task["variant"]["tracks"][0]["clips"] = proposal["clips"]
                    signatures.add(signature)
                except RecommendationError as error:
                    task["generation"] = {"status": "failed", "error": str(error)}
                except (OSError, ValueError, KeyError, TypeError):
                    task["generation"] = {"status": "failed", "error": "推荐素材文件不可用，请检查或重新导入素材。"}
                created.append(task)
            state["tasks"].extend(created)
            save(project_id, state)
            return {"generationId": generation_id, "tasks": [public_task(project_id, task) for task in created]}

    @router.put("/{task_id}/variant")
    def save_variant(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or not {"revision", "tracks"} <= set(body) or set(body) - {"revision", "tracks", "settings", "aspectMode"}:
            raise HTTPException(422, detail={"code": "batch_variant_invalid", "message": "变体内容无效。"})
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            variant = task["variant"]
            if type(body["revision"]) is not int or body["revision"] != variant["revision"]:
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            try:
                settings, tracks = validate_workspace({"revision": variant["revision"], "settings": body.get("settings", variant["settings"]), "tracks": body["tracks"]}, assets_for(project_id))
                if settings["fps"] != 30:
                    raise ValueError("批量成片使用 30 帧/秒")
                mode = body.get("aspectMode", variant.get("aspectMode"))
                if mode is None:
                    saved_ratio = ratio_label(settings["width"], settings["height"])
                    mode = saved_ratio if saved_ratio in PRODUCT_PRESETS else "smart"
                if not valid_aspect_mode(mode):
                    raise ValueError("视频比例无效")
                resolution = None
                if mode == "smart":
                    used_ids = {clip["assetId"] for track in tracks if track["kind"] == "video" and not track["hidden"]
                                for clip in track["clips"]}
                    assets = assets_for(project_id)
                    resolution = resolve_product_aspect(mode, [assets[asset_id] for asset_id in used_ids if asset_id in assets])
                    if (settings["width"], settings["height"]) != (resolution["width"], resolution["height"]):
                        raise ValueError("输出尺寸与智能解析结果不一致")
                    resolved = resolution["resolvedAspect"]
                else:
                    resolved = ratio_label(settings["width"], settings["height"])
                    expected_width, expected_height = PRODUCT_PRESETS[mode]
                    if (settings["width"], settings["height"]) != (expected_width, expected_height):
                        raise ValueError("输出尺寸与所选视频比例不一致")
                _validate_cues(_subtitles(variant)["cues"], tracks)
            except ValueError as error:
                raise HTTPException(422, detail={"code": "batch_variant_invalid", "message": str(error)}) from None
            variant["tracks"] = tracks
            variant["settings"] = settings
            variant["aspectMode"] = mode
            variant["resolvedAspect"] = resolved
            variant["aspectReason"] = resolution["reason"] if resolution else (
                "单条作品覆盖批次视频比例。" if "aspectMode" in body else variant.get("aspectReason", "沿用作品已保存的输出尺寸。"))
            variant["revision"] += 1
            if task.get("generation", {}).get("status") == "failed" and any(
                track["kind"] == "video" and not track["hidden"] and track["clips"] for track in tracks
            ):
                task["generation"] = {"status": "ready", "error": None}
            save(project_id, state)
            return {"variant": public_task(project_id, task)["variant"]}

    @router.post("/{task_id}/recommendations")
    def create_recommendation(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or set(body) != {"revision", "assetIds"} or type(body["revision"]) is not int
                or not isinstance(body["assetIds"], list) or len(body["assetIds"]) > 80
                or any(not _valid_id(asset_id) for asset_id in body["assetIds"])
                or len(set(body["assetIds"])) != len(body["assetIds"])):
            raise HTTPException(422, detail={"code": "batch_recommendation_invalid", "message": "请选择不重复的项目素材。"})
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            if body["revision"] != task["variant"]["revision"]:
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            assets = assets_for(project_id)
            try:
                proposal = recommend(task, assets, body["assetIds"])
            except RecommendationError as error:
                raise HTTPException(422, detail={"code": error.code, "message": str(error)}) from None
            try:
                for asset_id in {clip["assetId"] for clip in proposal["clips"]}:
                    filename = assets[asset_id]["file"]
                    regular_file(preproduction.path(project_id, "assets", filename))
            except (OSError, ValueError, KeyError, TypeError):
                raise HTTPException(422, detail={"code": "batch_asset_unavailable", "message": "推荐素材文件不可用，请检查或重新导入素材。"}) from None
            task["proposal"] = proposal
            save(project_id, state)
            return {"proposal": proposal}

    def process_voiceover(project_id, task_id, job_id):
        directory = store.path(project_id, "voiceovers", job_id, task_id)
        try:
            with lock:
                state = state_for(project_id)
                task = task_for(state, task_id)
                voice = task.get("voiceover")
                if not voice or voice["id"] != job_id or voice["status"] != "queued":
                    return
                if review_version(task) != (voice["contentRevision"], voice["variantRevision"], voice["subtitleRevision"]):
                    raise ValueError("脚本、镜头或字幕已修改，未覆盖新内容，请重新生成配音。")
                voice["status"] = "running"
                save(project_id, state)
                snapshot, plan, voice_id = deepcopy(task), deepcopy(voice["plan"]), voice["voiceId"]
            directory.mkdir(parents=True, exist_ok=True)
            outputs = voice_service.synthesize(plan["texts"], voice_id, directory)
            if len(outputs) != len(plan["texts"]):
                raise ValueError("配音段落数量无效，请重试。")
            new_assets = []
            for index, output in enumerate(outputs):
                regular_file(output)
                asset_id = uuid4().hex
                new_assets.append({"id": asset_id, "file": asset_id+".wav", "kind": "audio", "role": "audio",
                                   "name": f"AI 配音 {voice_id} / 第 {index+1} 段", "duration": audio_duration(output),
                                   "notes": plan["texts"][index], "voiceId": voice_id})
            with source_lock or nullcontext(), lock:
                state = state_for(project_id)
                task = task_for(state, task_id)
                voice = task.get("voiceover")
                if (not voice or voice["id"] != job_id or voice["status"] != "running"
                        or review_version(task) != review_version(snapshot)):
                    raise ValueError("脚本、镜头或字幕已修改，未覆盖新内容，请重新生成配音。")
                preparation = preproduction.load(project_id)
                assets = {a["id"]: a for a in preparation["assets"]}
                tracks, cues = arrange_voiceover(task, plan, new_assets, assets)
                # Check current visual sources before publishing generated audio.
                for clip in plan["clips"]:
                    regular_file(preproduction.path(project_id, "assets", assets[clip["assetId"]]["file"]))
                copied = []
                try:
                    for output, asset in zip(outputs, new_assets):
                        target = preproduction.path(project_id, "assets", asset["file"])
                        copy_source(output, target)
                        copied.append(target)
                    preparation["assets"].extend(new_assets)
                    preparation["revision"] += 1
                    preproduction.save(project_id, preparation)
                except Exception:
                    for target in copied:
                        target.unlink(missing_ok=True)
                    raise
                task["variant"]["tracks"] = tracks
                task["variant"]["revision"] += 1
                subtitles = _subtitles(task["variant"])
                subtitles["cues"] = cues
                subtitles["revision"] += 1
                voice.update(status="completed", error=None, variantRevision=task["variant"]["revision"], subtitleRevision=subtitles["revision"],
                             assetIds=[asset["id"] for asset in new_assets])
                voice.pop("plan", None)
                task.pop("proposal", None)
                save(project_id, state)
        except Exception as error:
            with lock:
                try:
                    state = state_for(project_id)
                    voice = task_for(state, task_id).get("voiceover")
                    if voice and voice["id"] == job_id and voice["status"] in ("queued", "running"):
                        message = str(error) if isinstance(error, ValueError) else "本地配音失败，请检查环境、素材与脚本后重试。"
                        voice.update(status="failed", error=message)
                        voice.pop("plan", None)
                        save(project_id, state)
                except HTTPException:
                    pass
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    @router.post("/{task_id}/voiceovers", status_code=202)
    def submit_voiceovers(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (set(body) != {"voiceId", "confirmScript", "targets"} or body["confirmScript"] is not True
                or not isinstance(body["voiceId"], str) or not isinstance(body["targets"], list)
                or not 1 <= len(body["targets"]) <= 20):
            raise HTTPException(422, detail={"code": "batch_voiceover_invalid", "message": "请选择音色并确认当前已保存脚本。"})
        catalog = voice_service.catalog() if voice_service else {"available": False, "voices": []}
        if not catalog["available"]:
            raise HTTPException(503, detail={"code": "voice_service_unavailable", "message": "本地配音环境未就绪，请按 README 配置；不会自动下载。"})
        if body["voiceId"] not in {voice["id"] for voice in catalog["voices"]}:
            raise HTTPException(422, detail={"code": "voice_unknown", "message": "所选音色不存在。"})
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            owner = task_for(state, task_id)
            targets = []
            seen = set()
            assets = assets_for(project_id)
            for requested in body["targets"]:
                if (not isinstance(requested, dict) or set(requested) != {"taskId", "contentRevision", "variantRevision", "subtitleRevision"}
                        or not _valid_id(requested["taskId"]) or requested["taskId"] in seen
                        or any(type(requested[key]) is not int or requested[key] < 0 for key in ("contentRevision", "variantRevision", "subtitleRevision"))):
                    raise HTTPException(422, detail={"code": "batch_voiceover_invalid", "message": "配音任务或版本无效。"})
                seen.add(requested["taskId"])
                task = task_for(state, requested["taskId"])
                if task["id"] != owner["id"] and task.get("batchId") != owner["id"]:
                    raise HTTPException(422, detail={"code": "batch_voiceover_scope", "message": "只能处理当前任务或它的批量变体。"})
                if review_version(task) != (requested["contentRevision"], requested["variantRevision"], requested["subtitleRevision"]):
                    raise HTTPException(409, detail={"code": "batch_voiceover_conflict", "message": "脚本、镜头或字幕已更新，请保存或刷新后重新确认。"})
                if task.get("voiceover", {}).get("status") in ("queued", "running"):
                    raise HTTPException(409, detail={"code": "batch_voiceover_active", "message": "配音正在处理中。"})
                try:
                    plan = plan_voiceover(task, assets)
                    for clip in plan["clips"]:
                        regular_file(preproduction.path(project_id, "assets", assets[clip["assetId"]]["file"]))
                except (ValueError, OSError) as error:
                    raise HTTPException(422, detail={"code": "batch_voiceover_plan", "message": str(error) if isinstance(error, ValueError) else "画面文件不可用。"}) from None
                targets.append((task, plan))
            job_id = uuid4().hex
            for task, plan in targets:
                task["variant"]["revision"] += 1
                task["voiceover"] = {"id":job_id, "voiceId":body["voiceId"], "status":"queued", "error":None,
                                     "contentRevision":task.get("contentRevision", 0), "variantRevision":task["variant"]["revision"], "subtitleRevision":_subtitles(task["variant"])["revision"],
                                     "model":"Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit", "modelRevision":catalog.get("modelRevision", "unverified-local-directory"),
                                     "plan":plan, "assetIds":[]}
            save(project_id, state)
            task_ids = [task["id"] for task, _ in targets]
            def process_batch(pid):
                for target_id in task_ids:
                    process_voiceover(pid, target_id, job_id)
            if not compute_queue.submit("batch-voiceover:"+job_id, project_id, process_batch):
                for task, _ in targets:
                    task["voiceover"].update(status="failed", error="本地处理队列不可用。")
                    task["voiceover"].pop("plan", None)
                save(project_id, state)
            return {"tasks":[public_task(project_id, task) for task, _ in targets]}

    @router.post("/{task_id}/variant/previews", status_code=202)
    def submit_preview(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) != {"revision"} or type(body["revision"]) is not int:
            raise HTTPException(422, detail={"code": "batch_preview_invalid", "message": "预览请求无效。"})
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            variant = task["variant"]
            voice_current(task)
            if body["revision"] != variant["revision"]:
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "请先保存当前变体。"})
            if any(run["status"] in ("queued", "running") for run in variant["runs"]):
                raise HTTPException(409, detail={"code": "batch_preview_active", "message": "此变体已有预览任务。"})
            assets = assets_for(project_id)
            try:
                settings, tracks = validate_workspace({"revision": variant["revision"], "settings": variant["settings"], "tracks": variant["tracks"]}, assets)
            except ValueError as error:
                raise HTTPException(422, detail={"code": "batch_variant_invalid", "message": str(error)}) from None
            if not any(track["kind"] == "video" and not track["hidden"] and track["clips"] for track in tracks):
                raise HTTPException(422, detail={"code": "batch_video_required", "message": "请先添加画面片段。"})
            run_id = uuid4().hex
            run_dir = store.path(project_id, "runs", run_id)
            sources = snapshot_sources(project_id, run_dir, tracks, assets)
            run = {"id": run_id, "revision": variant["revision"], "contentRevision": task.get("contentRevision", 0), "status": "queued", "error": None,
                   "subtitleRevision": _subtitles(variant)["revision"], "subtitleCues": list(_subtitles(variant)["cues"]),
                   "snapshot": {"settings": settings, "tracks": tracks}, "sources": sources}
            variant["runs"].append(run)
            save(project_id, state)
            if not compute_queue.submit("batch-preview:" + run_id, project_id, lambda pid: process(pid, task_id, run_id)):
                run["status"] = "failed"
                run["error"] = "本地处理队列不可用。"
                save(project_id, state)
            return {"variant": public_task(project_id, task)["variant"]}

    @router.post("/{task_id}/generations/{generation_id}/previews", status_code=202)
    def submit_bulk_previews(project_id: str, task_id: str, generation_id: str):
        project_for(project_id)
        with lock:
            state = state_for(project_id)
            parent = task_for(state, task_id)
            if parent.get("batchId") is not None:
                raise HTTPException(404, detail={"code": "batch_generation_missing", "message": "批量生成记录不存在。"})
            children = [task for task in state["tasks"] if task.get("batchId") == task_id and task.get("generationId") == generation_id]
            if not children:
                raise HTTPException(404, detail={"code": "batch_generation_missing", "message": "批量生成记录不存在。"})
            children = [public_task(project_id, task) for task in children]
        results = []
        for child in children:
            if child["generation"]["status"] == "failed":
                results.append({"taskId": child["id"], "status": "failed", "error": child["generation"]["error"]})
                continue
            latest = child["variant"]["runs"][-1] if child["variant"]["runs"] else None
            if (latest and latest["revision"] == child["variant"]["revision"]
                    and latest.get("contentRevision", 0) == child.get("contentRevision", 0)
                    and latest.get("subtitleRevision", 0) == child["variant"].get("subtitles", {}).get("revision", 0)
                    and latest["status"] in ("queued", "running", "completed")):
                results.append({"taskId": child["id"], "status": latest["status"], "runId": latest["id"]})
                continue
            try:
                result = submit_preview(project_id, child["id"], {"revision": child["variant"]["revision"]})
                run = result["variant"]["runs"][-1]
                results.append({"taskId": child["id"], "status": run["status"], "runId": run["id"], "error": run["error"]})
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, dict) else {}
                message = detail.get("message", "无法提交预览。")
                with lock:
                    state = state_for(project_id)
                    variant = task_for(state, child["id"])["variant"]
                    if variant["revision"] == child["variant"]["revision"] and not any(
                        run["status"] in ("queued", "running") for run in variant["runs"]
                    ):
                        failed = {"id": uuid4().hex, "revision": variant["revision"], "status": "failed", "error": message,
                                  "snapshot": {"settings": variant["settings"], "tracks": variant["tracks"]}, "sources": {}}
                        variant["runs"].append(failed)
                        save(project_id, state)
                results.append({"taskId": child["id"], "status": "failed", "error": message})
        return {"results": results}

    @router.post("/{task_id}/reviews")
    def save_review(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or set(body) != {"runId", "decision", "reason"}
                or not _valid_id(body["runId"]) or body["decision"] not in ("approved", "rejected")
                or not isinstance(body["reason"], str) or not body["reason"].strip() or len(body["reason"]) > 1000):
            raise HTTPException(422, detail={"code": "batch_review_invalid", "message": "请选择审核结果并填写原因。"})
        with lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            voice_current(task)
            run = find_run(task["variant"], body["runId"])
            version = review_version(task)
            if (run is not task["variant"]["runs"][-1] or run["status"] != "completed"
                    or (run.get("contentRevision", 0), run["revision"], run.get("subtitleRevision", 0)) != version):
                raise HTTPException(409, detail={"code": "batch_review_preview_stale", "message": "请先完成当前版本的预览。"})
            try:
                regular_file(store.path(project_id, "outputs", run["output"]))
            except (OSError, ValueError, KeyError):
                raise HTTPException(409, detail={"code": "batch_review_preview_unavailable", "message": "预览文件不可用，请重新生成。"}) from None
            review = {"id": uuid4().hex, "runId": run["id"], "decision": body["decision"], "reason": body["reason"].strip(),
                      "contentRevision": version[0], "variantRevision": version[1], "subtitleRevision": version[2]}
            task.setdefault("reviews", []).append(review)
            save(project_id, state)
            return {"task": public_task(project_id, task)}

    @router.post("/{task_id}/exports", status_code=202)
    def submit_exports(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or set(body) != {"taskIds"} or not isinstance(body["taskIds"], list)
                or not 1 <= len(body["taskIds"]) <= 20 or any(not _valid_id(item) for item in body["taskIds"])
                or len(set(body["taskIds"])) != len(body["taskIds"])):
            raise HTTPException(422, detail={"code": "batch_export_invalid", "message": "请选择 1–20 条不重复的变体。"})
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            parent = task_for(state, task_id)
            if "batchId" in parent:
                raise HTTPException(422, detail={"code": "batch_export_parent_invalid", "message": "请从批量任务选择变体。"})
            children = []
            for child_id in body["taskIds"]:
                child = task_for(state, child_id)
                if child.get("batchId") != task_id:
                    raise HTTPException(422, detail={"code": "batch_export_selection_invalid", "message": "所选变体不属于此批量任务。"})
                version = review_version(child)
                review = child.get("reviews", [])[-1] if child.get("reviews") else None
                preview = child["variant"]["runs"][-1] if child["variant"]["runs"] else None
                if (review_status(child) != "approved" or review is None or preview is None
                        or preview["status"] != "completed"
                        or child["variant"]["settings"].get("fps") != 30
                        or preview["snapshot"] != {"settings": child["variant"]["settings"], "tracks": child["variant"]["tracks"]}
                        or preview.get("subtitleCues", []) != child["variant"].get("subtitles", {}).get("cues", [])
                        or (preview.get("contentRevision", 0), preview["revision"], preview.get("subtitleRevision", 0)) != version):
                    raise HTTPException(409, detail={"code": "batch_export_not_approved", "message": "仅能导出当前版本已预览并通过审核的变体。"})
                try:
                    if preview["output"] != preview["id"] + ".mp4":
                        raise ValueError("预览文件关联无效")
                    regular_file(store.path(project_id, "outputs", preview["output"]))
                except (OSError, ValueError, KeyError):
                    raise HTTPException(409, detail={"code": "batch_export_preview_unavailable", "message": "当前预览文件不可用，请重新生成。"}) from None
                children.append((child, review, preview, version))
            assets = assets_for(project_id)
            results = []
            for child, review, preview, version in children:
                variant = child["variant"]
                previous = _exports(variant)[-1] if _exports(variant) else None
                if (previous and previous["status"] in ("queued", "running", "completed")
                        and (previous["contentRevision"], previous["variantRevision"], previous["subtitleRevision"]) == version):
                    results.append({"taskId": child["id"], "runId": previous["id"], "status": previous["status"]})
                    continue
                run_id = uuid4().hex
                snapshot = {"settings": variant["settings"], "tracks": variant["tracks"]}
                asset_ids = {clip["assetId"] for track in variant["tracks"] for clip in track["clips"]}
                manifest = [{"id": asset_id, "name": assets.get(asset_id, {}).get("name", asset_id),
                             "kind": assets.get(asset_id, {}).get("kind", "unknown")} for asset_id in sorted(asset_ids)]
                run = {"id": run_id, "status": "queued", "error": None, "contentRevision": version[0],
                       "variantRevision": version[1], "subtitleRevision": version[2], "previewRunId": preview["id"],
                       "review": dict(review), "sellingPoint": child["sellingPoint"], "script": child["script"],
                       "snapshot": snapshot, "subtitles": list(_subtitles(variant)["cues"]), "assets": manifest, "sources": {}}
                try:
                    run["sources"] = snapshot_approved_sources(project_id, store.path(project_id, "exports", run_id),
                                                                preview, assets)
                except HTTPException:
                    run.update(status="failed", error="原素材不可用，无法重新导出。")
                _exports(variant).append(run)
                save(project_id, state)
                if run["status"] == "queued" and not compute_queue.submit("batch-export:" + run_id, project_id,
                                                                          lambda pid, cid=child["id"], rid=run_id: process_export(pid, cid, rid)):
                    run.update(status="failed", error="本地处理队列不可用。")
                    save(project_id, state)
                results.append({"taskId": child["id"], "runId": run_id, "status": run["status"], "error": run["error"]})
            return {"results": results}

    @router.post("/{task_id}/exports/{run_id}/cancel")
    def cancel_export(project_id: str, task_id: str, run_id: str):
        project_for(project_id)
        with lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            run = find_export(task["variant"], run_id)
            if run["status"] in ("queued", "running"):
                run.update(status="cancelled", error="成片导出已取消。")
                save(project_id, state)
            elif run["status"] != "cancelled":
                raise HTTPException(409, detail={"code": "batch_export_not_active", "message": "此成片任务已结束，无法取消。"})
            return {"task": public_task(project_id, task)}

    @router.get("/{task_id}/exports/{run_id}/output")
    def download_export(project_id: str, task_id: str, run_id: str):
        project_for(project_id)
        with lock:
            run = find_export(task_for(state_for(project_id), task_id)["variant"], run_id)
            if run["status"] != "completed":
                raise HTTPException(409, detail={"code": "batch_export_unavailable", "message": "成片尚不可用。"})
            try:
                if run["output"] != run_id + ".mp4":
                    raise ValueError("成片关联无效")
                path = store.path(project_id, "deliverables", run["output"])
                regular_file(path)
            except (OSError, ValueError, KeyError):
                raise HTTPException(409, detail={"code": "batch_export_unavailable", "message": "成片文件不可用。"}) from None
        return FileResponse(path, media_type="video/mp4", filename=f"short-video-{run_id}.mp4")

    @router.post("/{task_id}/variant/previews/{run_id}/cancel")
    def cancel_preview(project_id: str, task_id: str, run_id: str):
        project_for(project_id)
        with lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            run = find_run(task["variant"], run_id)
            if run["status"] in ("queued", "running"):
                run.update(status="cancelled", error="预览已取消。")
                save(project_id, state)
            elif run["status"] != "cancelled":
                raise HTTPException(409, detail={"code": "batch_preview_not_active", "message": "此预览已结束，无法取消。"})
            return {"variant": public_task(project_id, task)["variant"]}

    @router.get("/{task_id}/variant/previews/{run_id}/output")
    def download_preview(project_id: str, task_id: str, run_id: str):
        project_for(project_id)
        with lock:
            run = find_run(task_for(state_for(project_id), task_id)["variant"], run_id)
            if run["status"] != "completed":
                raise HTTPException(409, detail={"code": "batch_preview_unavailable", "message": "预览尚不可用。"})
            try:
                output = run["output"]
                if output != run_id + ".mp4":
                    raise ValueError("输出关联无效")
                path = store.path(project_id, "outputs", output)
                regular_file(path)
            except (OSError, ValueError, KeyError):
                raise HTTPException(409, detail={"code": "batch_preview_unavailable", "message": "预览文件不可用。"}) from None
        return FileResponse(path, media_type="video/mp4", filename="preview.mp4")

    return router
