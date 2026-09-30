"""Project-scoped batch editing tasks and independent short-video variants."""
from __future__ import annotations

import hashlib
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

from .batch_production import (
    BatchProductionError,
    apply_aigc_handoff,
    complete_voiceover,
    create_batch as new_task,
    clear_legacy_cue_migration,
    current_delivery_output,
    current_preview_output,
    create_batch_variants,
    current_version as review_version,
    export_approved_versions,
    review_current_version,
    review_status,
    has_ambiguous_packaging_dependency,
    reusable_preview,
    queue_voiceover,
    save_content as save_batch_content,
    save_subtitles as save_batch_subtitles,
    save_variant as save_batch_variant,
    submit_preview as submit_batch_preview,
    subtitles_for as _subtitles,
    start_voiceover,
    validate_voiceover_target,
)
from .batch_recommendation import RecommendationError, recommend
from .batch_subtitles import milliseconds, parse_srt, write_srt
from .durable_runs import LOCAL_RUN_POLICY, freeze_run_input
from .montage_planner import MontagePlanningError, plan_montage, split_script_segments
from .preproduction import safe_child
from .project_assets import ProjectAssets
from .reference_video import validate_storage_id
from .render_inputs import RenderInputError, RenderInputPreparation
from .segment_retrieval import PlanningSearchError, load_planning_search
from .timeline import validate_workspace
from .video_aspect import PRODUCT_PRESETS, ratio_label, valid_aspect_mode


def _valid_id(value: Any) -> bool:
    try:
        validate_storage_id(value)
        return True
    except (TypeError, ValueError):
        return False


def _run_input(run: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "contentRevision", "variantRevision", "subtitleRevision", "timelineRevision",
        "snapshot", "subtitleCues", "subtitles", "sources", "language",
    )
    return {key: deepcopy(run[key]) for key in keys if key in run}


def _complete_local_run(run: dict[str, Any], result: dict[str, Any]) -> None:
    frozen = freeze_run_input(_run_input(run))
    completion = LOCAL_RUN_POLICY.complete(
        run["status"], frozen_input=frozen, current_input=_run_input(run), result=result,
    )
    run.update(status=completion.status, error=None, **completion.result)


def _validate_timeline_shape(revision: int, settings: Any, tracks: Any, transitions: Any = None) -> None:
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
        validate_workspace({"revision": revision, "settings": settings, "tracks": tracks,
                            "transitions": [] if transitions is None else transitions}, assets)
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


def _validate_media_properties(media: Any, message: str) -> None:
    if (not isinstance(media, dict)
            or type(media.get("width")) is not int or media["width"] <= 0
            or type(media.get("height")) is not int or media["height"] <= 0
            or type(media.get("fps")) not in (int, float) or media["fps"] <= 0
            or type(media.get("duration")) not in (int, float) or media["duration"] <= 0):
        raise ValueError(message)


def _shared_montage_usage(batch_members: list[dict[str, Any]], current_task_id: str) -> dict[str, int]:
    usage: dict[str, int] = {}
    for sibling in (item for item in batch_members if item["id"] != current_task_id):
        sibling_proposals = sibling.get("montageProposals", [])
        current_clips = {
            clip["id"]: clip for track in sibling["variant"]["tracks"] for clip in track.get("clips", [])
        }
        current_clip_ids = set(current_clips)
        candidate_ids = set()
        latest_draft = next((item for item in reversed(sibling_proposals)
                             if item.get("status") == "draft"
                             and item.get("inputSnapshot", {}).get("contentRevision")
                             == sibling.get("contentRevision", 0)), None)
        if latest_draft is not None:
            candidate_ids.update(
                item["selectedCandidateId"] for item in latest_draft.get("segments", [])
                if item.get("selectedCandidateId")
            )
        for applied_proposal in (item for item in sibling_proposals if item.get("status") == "applied"):
            selections = applied_proposal.get("appliedSelections")
            if isinstance(selections, list):
                segments_by_id = {item.get("id"): item for item in applied_proposal.get("segments", [])}
                for selection in selections:
                    current_clip = current_clips.get(selection.get("clipId"))
                    segment_clip = (segments_by_id.get(selection.get("segmentId")) or {}).get("selectedClip") or {}
                    expected = {
                        "assetId": selection.get("assetId", segment_clip.get("assetId")),
                        "inPoint": selection.get("inPoint", segment_clip.get("inPoint")),
                        "duration": selection.get("duration", segment_clip.get("duration")),
                    }
                    identity_matches = current_clip is not None and all(
                        expected[key] is None or current_clip.get(key) == expected[key]
                        for key in expected
                    )
                    if identity_matches and selection.get("candidateId"):
                        candidate_ids.add(selection["candidateId"])
            else:
                segments = applied_proposal.get("segments", [])
                applied_clip_ids = applied_proposal.get("appliedClipIds")
                if isinstance(applied_clip_ids, list) and len(applied_clip_ids) == len(segments):
                    candidate_ids.update(
                        segment["selectedCandidateId"] for segment, clip_id in zip(segments, applied_clip_ids)
                        if clip_id in current_clip_ids and segment.get("selectedCandidateId")
                    )
                else:
                    candidate_ids.update(
                        segment["selectedCandidateId"] for segment in segments
                        if (segment.get("selectedClip") or {}).get("id") in current_clip_ids
                        and segment.get("selectedCandidateId")
                    )
        for candidate_id in candidate_ids:
            usage[candidate_id] = usage.get(candidate_id, 0) + 1
    return usage


def _migrate_legacy_recognition_cues(value: Any) -> bool:
    """Namespace completed legacy ``line-1`` cues before strict validation.

    Only unambiguous references are rewritten. Ambiguous history remains
    readable, explicitly marked, and never guessed at render time.
    """
    if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
        return False
    changed = False
    for task in value["tasks"]:
        variant = task.get("variant") if isinstance(task, dict) else None
        subtitles = variant.get("subtitles") if isinstance(variant, dict) else None
        recognitions = subtitles.get("recognitions") if isinstance(subtitles, dict) else None
        if not isinstance(recognitions, list):
            continue
        candidates: dict[str, list[str]] = {}
        pending: list[tuple[dict[str, Any], dict[str, Any], str, str]] = []
        for recognition in recognitions:
            if (not isinstance(recognition, dict) or recognition.get("status") != "completed"
                    or recognition.get("legacyCueAmbiguity") is True):
                continue
            run_id, cues = recognition.get("id"), recognition.get("cues")
            if not isinstance(run_id, str) or not isinstance(cues, list):
                continue
            for cue in cues:
                if not isinstance(cue, dict) or not isinstance(cue.get("id"), str) or cue["id"].startswith(run_id + "-"):
                    continue
                old_id = cue["id"]
                new_id = run_id + "-" + old_id
                candidates.setdefault(old_id, []).append(new_id)
                pending.append((recognition, cue, old_id, new_id))
        mapping = {old_id: names[0] for old_id, names in candidates.items() if len(names) == 1}
        ambiguous = {old_id for old_id, names in candidates.items() if len(names) > 1}
        for recognition, cue, old_id, new_id in pending:
            if old_id in mapping:
                cue["id"] = new_id
                changed = True
            elif old_id in ambiguous:
                recognition["legacyCueAmbiguity"] = True
                changed = True
        if ambiguous:
            marker = {
                "code": "legacy_cue_ambiguity", "cueIds": sorted(ambiguous),
                "runIds": sorted({recognition["id"] for recognition, _cue, old_id, _new_id in pending
                                  if old_id in ambiguous}),
            }
            if subtitles.get("migrationRequired") != marker:
                subtitles["migrationRequired"] = marker
                changed = True
        if not mapping:
            continue
        def rewrite_cues(cues):
            nonlocal changed
            if not isinstance(cues, list):
                return
            for cue in cues:
                if isinstance(cue, dict) and cue.get("id") in mapping:
                    cue["id"] = mapping[cue["id"]]
                    changed = True
        def rewrite_packaging(snapshot):
            nonlocal changed
            package = snapshot.get("packaging") if isinstance(snapshot, dict) else None
            for element in package.get("elements", []) if isinstance(package, dict) else []:
                if isinstance(element, dict) and element.get("cueId") in mapping:
                    element["cueId"] = mapping[element["cueId"]]
                    changed = True
        rewrite_cues(subtitles.get("cues"))
        rewrite_packaging(variant)
        rewrite_packaging(task.get("proposal"))
        for proposal in task.get("montageProposals", []):
            if isinstance(proposal, dict):
                rewrite_packaging(proposal)
        for run in variant.get("runs", []):
            if isinstance(run, dict):
                rewrite_cues(run.get("subtitleCues"))
                rewrite_packaging(run.get("snapshot"))
        for delivery in variant.get("exports", []):
            if isinstance(delivery, dict):
                rewrite_cues(delivery.get("subtitles"))
                rewrite_packaging(delivery.get("snapshot"))
    return changed


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
        if "cta" in task and (not isinstance(task["cta"], str) or not task["cta"].strip() or len(task["cta"]) > 120):
            raise ValueError("CTA 无效")
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
        _validate_timeline_shape(variant["revision"], settings, tracks, variant.get("transitions", []))
        if variant.get("packaging") is not None:
            from .montage_packaging import validate_packaging
            validate_packaging(variant["packaging"], variant.get("subtitles", {}).get("cues", []), tracks, batch=True)
        if variant.get("packagingSource") not in (None, "manual", "proposal"):
            raise ValueError("包装来源无效")
        subtitles = variant.get("subtitles")
        if subtitles is not None:
            if (not isinstance(subtitles, dict) or type(subtitles.get("revision")) is not int or subtitles["revision"] < 0
                    or not isinstance(subtitles.get("recognitions"), list)):
                raise ValueError("字幕记录无效")
            marker = subtitles.get("migrationRequired")
            if marker is not None and (not isinstance(marker, dict) or marker.get("code") != "legacy_cue_ambiguity"
                                       or not isinstance(marker.get("cueIds"), list) or not marker["cueIds"]
                                       or not isinstance(marker.get("runIds"), list) or not marker["runIds"]
                                       or any(not _valid_id(item) for item in [*marker["cueIds"], *marker["runIds"]])):
                raise ValueError("字幕迁移状态无效")
            _validate_cues(subtitles.get("cues"), tracks)
            for recognition in subtitles["recognitions"]:
                if (not isinstance(recognition, dict) or not _valid_id(recognition.get("id"))
                        or recognition.get("status") not in ("queued", "running", "completed", "failed", "cancelled")
                        or recognition.get("language") not in ("auto", "zh", "en", "ja", "ko")
                        or type(recognition.get("timelineRevision")) is not int or recognition["timelineRevision"] < 0
                        or type(recognition.get("subtitleRevision")) is not int or recognition["subtitleRevision"] < 0
                        or not isinstance(recognition.get("snapshot"), dict) or not isinstance(recognition.get("sources"), dict)
                        or (recognition.get("error") is not None and not isinstance(recognition["error"], str))):
                    raise ValueError("字幕识别记录无效")
                if ("cueIds" in recognition and (not isinstance(recognition["cueIds"], list)
                                                 or len(set(recognition["cueIds"])) != len(recognition["cueIds"])
                                                 or any(not _valid_id(cue_id) for cue_id in recognition["cueIds"]))):
                    raise ValueError("字幕识别输入无效")
                if ("adoptionRequired" in recognition and recognition["adoptionRequired"] is not True
                        and recognition["adoptionRequired"] is not False):
                    raise ValueError("字幕识别采用状态无效")
                if "adoptionConflict" in recognition and (not isinstance(recognition["adoptionConflict"], str)
                                                            or not recognition["adoptionConflict"]):
                    raise ValueError("字幕识别采用状态无效")
                _validate_timeline_shape(recognition["timelineRevision"], recognition["snapshot"].get("settings"), recognition["snapshot"].get("tracks"), recognition["snapshot"].get("transitions", []))
                _validate_cues(recognition.get("cues"), recognition["snapshot"]["tracks"])
                if "legacyCueAmbiguity" in recognition and recognition["legacyCueAmbiguity"] is not True:
                    raise ValueError("字幕识别迁移状态无效")
                if recognition.get("status") == "completed" and any(
                    not cue["id"].startswith(recognition["id"] + "-") and recognition.get("legacyCueAmbiguity") is not True
                    for cue in recognition["cues"]
                ):
                    raise ValueError("字幕识别 cue 身份无效")
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
            _validate_timeline_shape(proposal["baseRevision"], settings, proposal_tracks, proposal.get("transitions", []))
            for clip, match in zip(proposal["clips"], proposal["matches"]):
                if (not isinstance(match, dict) or match.get("clipId") != clip["id"] or match.get("assetId") != clip["assetId"]
                        or any(not isinstance(match.get(key), str) or not match[key].strip() for key in ("scriptSegment", "assetName"))
                        or not isinstance(match.get("matchedTerms"), list) or not match["matchedTerms"]
                        or any(not isinstance(term, str) or not term for term in match["matchedTerms"])
                        or not isinstance(match.get("matchedSellingPointTerms"), list)
                        or any(not isinstance(term, str) or not term for term in match["matchedSellingPointTerms"])):
                    raise ValueError("短视频推荐依据无效")
        for montage_proposal in task.get("montageProposals", []):
            if not isinstance(montage_proposal, dict):
                raise ValueError("混剪提案无效")
            package = montage_proposal.get("packaging")
            if package is not None:
                from .montage_packaging import validate_packaging
                proposal_tracks = [{**track, "clips": montage_proposal.get("clips", [])}
                                   if track.get("kind") == "video" else track for track in tracks]
                validate_packaging(package, [], proposal_tracks)
        for run in runs:
            if not isinstance(run, dict) or not _valid_id(run.get("id")) or type(run.get("revision")) is not int or run["revision"] < 0:
                raise ValueError("变体预览记录无效")
            if run.get("status") not in ("queued", "running", "completed", "failed", "cancelled") or not isinstance(run.get("snapshot"), dict) or not isinstance(run.get("sources"), dict):
                raise ValueError("变体预览记录无效")
            _validate_timeline_shape(run["revision"], run["snapshot"].get("settings"), run["snapshot"].get("tracks"), run["snapshot"].get("transitions", []))
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
            if run.get("snapshot", {}).get("packaging") is not None:
                from .montage_packaging import validate_packaging
                validate_packaging(run["snapshot"]["packaging"], run.get("subtitleCues", []),
                                   run["snapshot"]["tracks"], batch=True)
            if type(run.get("contentRevision", 0)) is not int or run.get("contentRevision", 0) < 0:
                raise ValueError("预览脚本版本无效")
            if "media" in run:
                _validate_media_properties(run["media"], "预览媒体属性无效")
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
            _validate_timeline_shape(delivery["variantRevision"], delivery["snapshot"].get("settings"), delivery["snapshot"].get("tracks"), delivery["snapshot"].get("transitions", []))
            _validate_cues(delivery.get("subtitles"), delivery["snapshot"]["tracks"])
            if delivery.get("snapshot", {}).get("packaging") is not None:
                from .montage_packaging import validate_packaging
                validate_packaging(delivery["snapshot"]["packaging"], delivery.get("subtitles", []),
                                   delivery["snapshot"]["tracks"], batch=True)
            if any(not _valid_id(asset_id) or not _valid_id(filename) for asset_id, filename in delivery["sources"].items()):
                raise ValueError("成片素材快照无效")
            if delivery["status"] == "completed" and delivery.get("output") != delivery["id"] + ".mp4":
                raise ValueError("成片文件关联无效")
            if "media" in delivery:
                _validate_media_properties(delivery["media"], "成片媒体属性无效")
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

    def load(self, project_id: str, *, persist_migration: bool = True) -> dict[str, Any]:
        path = self.path(project_id, "state.json")
        if not path.exists():
            return {"schemaVersion": 1, "tasks": []}
        if path.is_symlink() or not path.is_file():
            raise OSError("批量混剪状态文件无效")
        value = json.loads(path.read_text(encoding="utf-8"))
        migrated = _migrate_legacy_recognition_cues(value)
        _validate_state(value)
        if migrated and persist_migration:
            self.save(project_id, value)
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


def create_batch_editing_router(data_dir, get_project, compute_queue, *, ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", source_lock=None, renderer=None, voice_service=None, segment_reranker=None, segment_vectorizer_version=None, reframe_analyzer=None):
    store = BatchStore(Path(data_dir))
    project_assets = ProjectAssets(Path(data_dir))
    render_inputs = RenderInputPreparation(project_assets)
    lock = RLock()
    router = APIRouter(prefix="/api/projects/{project_id}/batch-edits")
    from .batch_voiceover import plan_voiceover, audio_duration, arrange_voiceover

    def recover_reframe_analysis():
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
                    for record in task.get("variant", {}).get("reframeAnalysis", {}).values():
                        if record.get("status") in ("queued", "running"):
                            record.update(status="failed", error="服务重启中断了主体分析，可重新提交。")
                            changed = True
                    for record in task.get("variant", {}).get("subtitles", {}).get("recognitions", []):
                        if record.get("status") in ("queued", "running"):
                            record.update(status="failed", error="服务重启中断了字幕识别，可重新提交。")
                            changed = True
                if changed:
                    store.save(project_dir.name, state)
            except (OSError, ValueError):
                continue

    recover_reframe_analysis()

    def production_call(command, *args, **kwargs):
        try:
            return command(*args, **kwargs)
        except BatchProductionError as error:
            raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message}) from None

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
            return project_assets.index(project_id)
        except (OSError, ValueError):
            raise HTTPException(503, detail={"code": "batch_assets_unavailable", "message": "项目素材无法读取。"}) from None

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

    def prepare_packaging_proof(snapshot: dict, cues: list, *, batch: bool):
        packaging = snapshot.get("packaging") if isinstance(snapshot, dict) else None
        if packaging is None:
            return None
        from .montage_packaging import PackagingError, PackagingProofConflict, prepare_packaging_proof as prepare
        try:
            return prepare(snapshot, cues, batch=batch)
        except PackagingProofConflict as error:
            raise BatchProductionError("batch_packaging_conflict", str(error)) from error
        except PackagingError as error:
            raise BatchProductionError("batch_packaging_invalid", str(error)) from error

    def consume_packaging_proof(proof, snapshot: dict, cues: list, *, batch: bool) -> None:
        from .montage_packaging import PackagingError, PackagingProofConflict, consume_packaging_proof as consume
        try:
            consume(proof, snapshot, cues, batch=batch)
        except PackagingProofConflict as error:
            raise BatchProductionError("batch_packaging_conflict", str(error)) from error
        except PackagingError as error:
            raise BatchProductionError("batch_packaging_invalid", str(error)) from error

    def packaging_proof_key(snapshot: dict, cues: list, *, batch: bool) -> str:
        from .montage_packaging import PackagingError, packaging_proof_key as key
        try:
            return key(snapshot, cues, batch=batch)
        except PackagingError as error:
            raise BatchProductionError("batch_packaging_invalid", str(error)) from error

    def clip_for(task: dict[str, Any], clip_id: str, proposal_id: str | None = None):
        if proposal_id is not None:
            proposal = montage_proposal_for(task, proposal_id)
            clip = next((item.get("selectedClip") for item in proposal["segments"]
                         if (item.get("selectedClip") or {}).get("id") == clip_id), None)
            if clip is None:
                raise ValueError("混剪提案片段不存在。")
            return clip
        clip = next((clip for track in task["variant"]["tracks"] for clip in track.get("clips", [])
                     if clip.get("id") == clip_id), None)
        if clip is None:
            raise ValueError("变体片段不存在。")
        return clip

    def schedule_reframes(project_id: str, task_id: str, clip_ids: list[str], *, proposal_id: str | None = None,
                          expected_revision: int | None = None, staged_state: dict | None = None,
                          expected_state: dict | None = None, reuse_existing: bool = False):
        """One existing compute job per submit; late workers may only write same clip identity."""
        run_id = uuid4().hex
        with source_lock or nullcontext(), lock:
            persisted = state_for(project_id)
            if expected_state is not None and not batch_state_cas_equal(persisted, expected_state):
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            state = deepcopy(staged_state) if staged_state is not None else persisted
            if expected_state is not None:
                merge_analysis_progress(persisted, state, expected_state)
            task = task_for(state, task_id)
            variant = task["variant"]
            if expected_revision is not None and variant["revision"] != expected_revision:
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            assets = assets_for(project_id)
            proposal = montage_proposal_for(task, proposal_id) if proposal_id is not None else None
            request = {"variantRevision": variant["revision"],
                       "proposal": None if proposal is None else {
                           key: proposal.get(key) for key in ("id", "revision", "status")
                       }, "clips": []}
            for clip_id in clip_ids:
                clip = clip_for(task, clip_id, proposal_id)
                asset = assets.get(clip["assetId"])
                if asset is None or asset.get("kind") not in ("video", "image"):
                    raise ValueError("主体裁剪仅支持可用图片或视频素材。")
                try:
                    source = project_assets.file(project_id, asset)
                    source_fact = source_identity(source)
                except (OSError, ValueError, KeyError):
                    raise HTTPException(409, detail={"code": "reframe_source_unavailable", "message": "主体分析素材不可用。"}) from None
                request["clips"].append({
                    "id": clip_id,
                    "identity": {key: clip[key] for key in ("assetId", "inPoint", "duration", "speed")},
                    "asset": reframe_asset_identity(asset), "source": source_fact,
                    "targetAspect": {"width": variant["settings"]["width"], "height": variant["settings"]["height"]},
                    "stillImage": asset.get("kind") == "image",
                })

        source_requests = {}
        for item in request["clips"]:
            source_key = source_stat_identity(item["source"])
            item["sourceKey"] = source_key
            source_request = source_requests.setdefault(source_key, {
                "path": Path(item["source"]["path"]), "needsTiming": False,
            })
            source_request["needsTiming"] = source_request["needsTiming"] or not item["stillImage"]
        try:
            source_proofs = {
                path: prove_reframe_source(source_request["path"], needs_timing=source_request["needsTiming"])
                for path, source_request in source_requests.items()
            }
        except SourceProofConflict:
            raise HTTPException(409, detail={"code": "reframe_source_unavailable", "message": "主体分析素材在校验期间变化或不可用，请刷新后重试。"}) from None

        with source_lock or nullcontext(), lock:
            persisted = state_for(project_id)
            if expected_state is not None and not batch_state_cas_equal(persisted, expected_state):
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            state = deepcopy(staged_state) if staged_state is not None else persisted
            if expected_state is not None:
                merge_analysis_progress(persisted, state, expected_state)
            task = task_for(state, task_id)
            variant = task["variant"]
            if variant["revision"] != request["variantRevision"]:
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            current_proposal = montage_proposal_for(task, proposal_id) if proposal_id is not None else None
            current_proposal_identity = None if current_proposal is None else {
                key: current_proposal.get(key) for key in ("id", "revision", "status")
            }
            if current_proposal_identity != request["proposal"]:
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "混剪提案已更新，请刷新后重试。"})
            assets = assets_for(project_id)
            confirmed = []
            for item in request["clips"]:
                try:
                    clip = clip_for(task, item["id"], proposal_id)
                except ValueError:
                    raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体片段已更新，请刷新后重试。"}) from None
                asset = assets.get(item["identity"]["assetId"])
                if asset is None or reframe_asset_identity(asset) != item["asset"]:
                    raise HTTPException(409, detail={"code": "reframe_source_unavailable", "message": "主体分析素材引用已变化，请刷新后重试。"})
                try:
                    source = project_assets.file(project_id, asset)
                    current_source_identity = source_identity(source)
                except (OSError, ValueError, KeyError):
                    raise HTTPException(409, detail={"code": "reframe_source_unavailable", "message": "主体分析素材不可用。"}) from None
                source_fact = source_proofs[item["sourceKey"]]
                if (current_source_identity != item["source"]
                        or source_stat_identity(current_source_identity) != source_stat_identity(source_fact["identity"])):
                    raise HTTPException(409, detail={"code": "reframe_source_unavailable", "message": "主体分析素材已变化，请刷新后重试。"})
                if {key: clip.get(key) for key in item["identity"]} != item["identity"]:
                    raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体片段已更新，请刷新后重试。"})
                aspect = {"width": variant["settings"]["width"], "height": variant["settings"]["height"]}
                if aspect != item["targetAspect"]:
                    raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体输出比例已更新，请刷新后重试。"})
                confirmed.append((item, clip, source, source_fact, aspect))

            from .montage_reframe import pending_reframe
            analysis = variant.setdefault("reframeAnalysis", {})
            progressed = analysis_progressed_for(persisted, expected_state, task_id) if expected_state is not None else set()
            jobs = []
            for item, clip, source, source_fact, aspect in confirmed:
                identity = {**item["identity"], "sourceFingerprint": source_fact["fingerprint"], "targetAspect": aspect}
                existing = analysis.get(item["id"])
                force_retry = False
                reuse_active = False
                if item["id"] in progressed:
                    if existing is None or existing.get("status") == "cancelled":
                        raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "主体分析状态已更新，请刷新后重试。"})
                    if existing.get("identity") == identity:
                        if existing.get("status") in ("queued", "running", "completed"):
                            reuse_active = True
                        # A late failed draft is retryable; it must not be
                        # silently retained as an adopted terminal record.
                        else:
                            force_retry = True
                    if existing.get("status") in ("queued", "running"):
                        if existing.get("identity") != identity:
                            raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "主体分析状态已更新，请刷新后重试。"})
                elif existing is not None and existing.get("status") in ("queued", "running"):
                    if existing.get("identity") == identity:
                        reuse_active = True
                    else:
                        # This staged apply tentatively marked the old draft as
                        # adopted before the current source proof was available.
                        # A different full identity must start fresh, never adopt it.
                        existing.pop("adopted", None)
                        force_retry = True
                previous_reframe = clip.get("reframe", {})
                previous_fingerprint = previous_reframe.get("sourceFingerprint")
                facts_match = (previous_fingerprint == source_fact["fingerprint"]
                               and previous_fingerprint
                               and previous_reframe.get("targetAspect") == aspect
                               and (previous_reframe.get("sourceWidth"), previous_reframe.get("sourceHeight"))
                               == tuple(source_fact["size"]))
                if reuse_existing and facts_match and not force_retry:
                    # A full lock-free proof confirms that these saved facts
                    # (including a deliberate manual decision) still bind the source.
                    continue
                pending = pending_reframe(target_width=aspect["width"], target_height=aspect["height"],
                                          reason=("候选或源区间已改变，需重新主体分析"
                                                  if proposal_id is not None else "已提交本地主体分析"))
                override = previous_reframe.get("override")
                if ((not previous_fingerprint or previous_fingerprint == source_fact["fingerprint"])
                        and isinstance(override, dict)
                        and override.get("mode") != "auto"):
                    pending["override"] = deepcopy(override)
                clip["reframe"] = pending
                if reuse_active:
                    continue
                analysis[item["id"]] = {"id": run_id, "status": "queued", "error": None, "proposalId": proposal_id,
                                        "identity": identity}
                jobs.append((item["id"], identity, source, dict(aspect),
                             (None if item["stillImage"] else source_fact["timing"], source_fact["size"]), item["stillImage"],
                             source_fact["identity"], item["asset"]))
            save(project_id, state)

        if not jobs:
            return

        def process(_pid):
            completed, failures = {}, {}
            def cancelled(clip_id):
                with lock:
                    record = task_for(state_for(project_id), task_id)["variant"].get("reframeAnalysis", {}).get(clip_id)
                    return record is None or record.get("id") != run_id or record.get("status") == "cancelled"
            with source_lock or nullcontext(), lock:
                state = state_for(project_id)
                task = task_for(state, task_id)
                for clip_id, *_ in jobs:
                    record = task["variant"].get("reframeAnalysis", {}).get(clip_id)
                    if record is not None and record.get("id") == run_id and record.get("status") == "queued":
                        record["status"] = "running"
                save(project_id, state)
            for clip_id, identity, source, aspect, (source_timing, trusted_size), still_image, _proof_identity, _proof_asset in jobs:
                try:
                    with lock:
                        record = task_for(state_for(project_id), task_id)["variant"].get("reframeAnalysis", {}).get(clip_id)
                        if record is None or record.get("id") != run_id or record.get("status") == "cancelled":
                            continue
                    from .montage_reframe import normalize_reframe, run_local_detector
                    if not source.is_file():
                        raise ValueError("分析期间素材已变化，请重新主体分析。")
                    if not all(type(value) is int and value > 0 for value in trusted_size):
                        raise ValueError("无法获取可信源尺寸，不能完成主体分析。")
                    raw = reframe_analyzer(source, 0 if still_image else identity["inPoint"], 0 if still_image else identity["inPoint"] + identity["duration"] * identity["speed"], aspect) if reframe_analyzer else run_local_detector(
                        source, start=0 if still_image else identity["inPoint"], end=0 if still_image else identity["inPoint"] + identity["duration"] * identity["speed"],
                        target_width=aspect["width"], target_height=aspect["height"], source_timing=source_timing,
                        media_kind="image" if still_image else "video", cancelled=lambda cid=clip_id: cancelled(cid))
                    completed[clip_id] = normalize_reframe(raw, source_fingerprint=identity["sourceFingerprint"],
                        source_width=trusted_size[0], source_height=trusted_size[1],
                        source_timing=source_timing,
                        required_start=None if still_image else identity["inPoint"], required_end=None if still_image else identity["inPoint"] + identity["duration"] * identity["speed"], still_image=still_image)
                except Exception as error:
                    failures[clip_id] = str(error)[:300] or "本地主体分析失败。"
            current_source_facts = {}
            for clip_id, _identity, source, _aspect, _probe, _still_image, _proof_identity, _proof_asset in jobs:
                if str(source) in current_source_facts:
                    continue
                try:
                    current_source_facts[str(source)] = stable_source_fact(source)
                except (SourceProofConflict, OSError, ValueError):
                    current_source_facts[str(source)] = None
            with source_lock or nullcontext(), lock:
                state = state_for(project_id)
                task = task_for(state, task_id)
                changed = False
                renderable_changed = False
                for clip_id, identity, _source, _aspect, _probe, _still_image, proof_identity, proof_asset in jobs:
                    record = task["variant"].get("reframeAnalysis", {}).get(clip_id)
                    if record is None or record.get("id") != run_id or record.get("identity") != identity or record.get("status") == "cancelled":
                        continue
                    try:
                        clip = clip_for(task, clip_id, proposal_id)
                    except ValueError:
                        continue
                    current = {key: clip.get(key) for key in ("assetId", "inPoint", "duration", "speed")}
                    current_source_fact = current_source_facts.get(str(_source))
                    try:
                        current_asset = assets_for(project_id).get(identity["assetId"])
                        current_source = project_assets.file(project_id, current_asset) if current_asset else None
                        current_source_identity = source_identity(current_source) if current_source else None
                    except (OSError, ValueError, KeyError):
                        current_source_identity = None
                    if (current != {key: identity[key] for key in current}
                            or current_source_fact is None
                            or current_asset is None or reframe_asset_identity(current_asset) != proof_asset
                            or current_source_identity != proof_identity
                            or current_source_identity != {key: current_source_fact[key] for key in proof_identity}
                            or current_source_fact["fingerprint"] != identity["sourceFingerprint"]):
                        record.update(status="failed", error="分析期间素材已变化，请重新主体分析。")
                        changed = True
                        continue
                    if {"width": task["variant"]["settings"]["width"], "height": task["variant"]["settings"]["height"]} != identity["targetAspect"]:
                        record.update(status="failed", error="分析期间输出比例已变化，请重新主体分析。")
                        changed = True
                        continue
                    if clip_id in completed:
                        # A user override survives a late automatic result; boxes/reasons stay from the worker.
                        override = clip.get("reframe", {}).get("override")
                        result = completed[clip_id]
                        if isinstance(override, dict) and override.get("mode") != "auto":
                            result = {**result, "override": deepcopy(override)}
                        clip["reframe"] = result
                        # A draft job retained through adoption is the single
                        # authoritative analysis.  Publish it to the adopted
                        # variant too, preserving any later human override.
                        adopted = None
                        if proposal_id is not None and record.get("adopted"):
                            adopted = next((item for track in task["variant"]["tracks"] for item in track.get("clips", [])
                                            if item.get("id") == clip_id), None)
                        if adopted is not None:
                            adopted_identity = {key: adopted.get(key) for key in ("assetId", "inPoint", "duration", "speed")}
                            try:
                                adopted_asset = assets_for(project_id).get(adopted_identity["assetId"])
                                adopted_source = project_assets.file(project_id, adopted_asset) if adopted_asset else None
                                adopted_fact = current_source_facts.get(str(adopted_source)) if adopted_source else None
                                adopted_hash = (adopted_fact["fingerprint"] if adopted_fact is not None
                                                and source_identity(adopted_source) == {key: adopted_fact[key] for key in adopted_fact if key != "fingerprint"}
                                                else None)
                            except (OSError, ValueError, KeyError):
                                adopted_hash = None
                            if adopted_identity == {key: identity[key] for key in adopted_identity} and adopted_hash == identity["sourceFingerprint"]:
                                adopted_override = adopted.get("reframe", {}).get("override")
                                adopted["reframe"] = {**completed[clip_id], **(
                                    {"override": deepcopy(adopted_override)}
                                    if isinstance(adopted_override, dict) and adopted_override.get("mode") != "auto" else {})}
                                renderable_changed = True
                            else:
                                record.update(status="failed", error="采用后变体片段或素材已变化，请重新主体分析。")
                                changed = True
                                continue
                        record.update(status="completed", error=None)
                        renderable_changed = renderable_changed or proposal_id is None
                    else:
                        record.update(status="failed", error=failures.get(clip_id, "本地主体分析失败。"))
                    changed = True
                if changed:
                    # A proposal is not a versioned renderable variant; adopted analysis increments the variant once.
                    if renderable_changed:
                        task["variant"]["revision"] += 1
                    save(project_id, state)
        key = "batch-reframe:" + run_id
        if not compute_queue.submit(key, project_id, process):
            with source_lock or nullcontext(), lock:
                state = state_for(project_id)
                task = task_for(state, task_id)
                for clip_id, *_ in jobs:
                    record = task["variant"].get("reframeAnalysis", {}).get(clip_id)
                    if record is not None and record.get("id") == run_id:
                        record.update(status="failed", error="本地处理队列不可用。")
                save(project_id, state)

    def commit_staged_batch_state(project_id: str, expected_state: dict, staged_state: dict) -> None:
        """Persist a lock-free proposal staging result only if its base is still current."""
        with source_lock or nullcontext(), lock:
            persisted = state_for(project_id)
            if not batch_state_cas_equal(persisted, expected_state):
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            merge_analysis_progress(persisted, staged_state, expected_state)
            save(project_id, staged_state)

    def batch_state_cas_equal(current: dict, expected: dict) -> bool:
        """Ignore asynchronous analysis progress but protect every editable business fact."""
        def comparable(state):
            state = deepcopy(state)
            for task in state.get("tasks", []):
                task.get("variant", {}).pop("reframeAnalysis", None)
            return state
        return comparable(current) == comparable(expected)

    def merge_analysis_progress(current: dict, staged: dict, expected: dict) -> None:
        """Keep concurrent cancel/worker facts authoritative while committing staged edits."""
        expected_tasks = {task.get("id"): task for task in expected.get("tasks", [])}
        current_tasks = {task.get("id"): task for task in current.get("tasks", [])}
        for task in staged.get("tasks", []):
            task_id = task.get("id")
            current_task, expected_task = current_tasks.get(task_id), expected_tasks.get(task_id)
            if current_task is None or expected_task is None:
                continue
            current_analysis = current_task.get("variant", {}).get("reframeAnalysis", {})
            expected_analysis = expected_task.get("variant", {}).get("reframeAnalysis", {})
            staged_analysis = task.get("variant", {}).setdefault("reframeAnalysis", {})
            progressed = {clip_id for clip_id, record in current_analysis.items()
                          if record != expected_analysis.get(clip_id)}
            for clip_id in progressed:
                current_record = current_analysis[clip_id]
                staged_record = staged_analysis.get(clip_id, {})
                if (staged_record.get("adopted") is True
                        and staged_record.get("id") == current_record.get("id")
                        and staged_record.get("identity") == current_record.get("identity")):
                    staged_analysis[clip_id] = {**deepcopy(current_record), "adopted": True}
                else:
                    staged_analysis[clip_id] = deepcopy(current_record)
            for clip_id in progressed:
                current_clip = next((clip for track in current_task["variant"]["tracks"]
                                     for clip in track.get("clips", []) if clip.get("id") == clip_id), None)
                if current_clip is None:
                    current_clip = next((segment.get("selectedClip") for proposal in current_task.get("montageProposals", [])
                                         for segment in proposal.get("segments", [])
                                         if (segment.get("selectedClip") or {}).get("id") == clip_id), None)
                if current_clip is not None:
                    staged_clip = next((clip for track in task["variant"]["tracks"]
                                        for clip in track.get("clips", []) if clip.get("id") == clip_id), None)
                    if staged_clip is None:
                        staged_clip = next((segment.get("selectedClip") for proposal in task.get("montageProposals", [])
                                            for segment in proposal.get("segments", [])
                                            if (segment.get("selectedClip") or {}).get("id") == clip_id), None)
                    if staged_clip is None:
                        continue
                    if "reframe" in current_clip:
                        staged_clip["reframe"] = deepcopy(current_clip["reframe"])

    def analysis_progressed_for(current: dict, expected: dict, task_id: str) -> set[str]:
        current_task = next((task for task in current.get("tasks", []) if task.get("id") == task_id), {})
        expected_task = next((task for task in expected.get("tasks", []) if task.get("id") == task_id), {})
        current_analysis = current_task.get("variant", {}).get("reframeAnalysis", {})
        expected_analysis = expected_task.get("variant", {}).get("reframeAnalysis", {})
        return {clip_id for clip_id, record in current_analysis.items() if record != expected_analysis.get(clip_id)}

    def regular_file(path: Path):
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("不是普通文件")
        finally:
            os.close(descriptor)

    def snapshot_sources(project_id: str, directory: Path, tracks: list, assets: dict) -> dict:
        try:
            return render_inputs.freeze_current(project_id, directory, tracks, assets)
        except RenderInputError:
            raise HTTPException(409, detail={"code": "batch_asset_unavailable", "message": "素材文件不可用。"}) from None

    def snapshot_approved_sources(project_id: str, directory: Path, preview: dict, assets: dict) -> dict:
        try:
            return render_inputs.freeze_approved(
                project_id,
                directory,
                store.path(project_id, "runs", preview["id"]),
                preview["sources"],
                assets,
            )
        except (RenderInputError, KeyError, ValueError):
            raise HTTPException(409, detail={"code": "batch_asset_unavailable", "message": "原素材缺失或变化，请重新预览审核。"}) from None

    def reframe_clip_ids(run: dict) -> set[str]:
        snapshot = run.get("snapshot")
        tracks = snapshot.get("tracks") if isinstance(snapshot, dict) else None
        return {clip.get("id") for track in tracks or [] if isinstance(track, dict)
                for clip in track.get("clips", []) if isinstance(clip, dict) and clip.get("reframe") is not None}

    def source_identity(path: Path) -> dict[str, Any]:
        path = Path(path)
        regular_file(path)
        item = os.stat(path)
        if not stat.S_ISREG(item.st_mode):
            raise OSError("不是普通文件")
        return {"path": str(path), "dev": item.st_dev, "ino": item.st_ino, "size": item.st_size,
                "mtimeNs": item.st_mtime_ns, "ctimeNs": item.st_ctime_ns}

    def source_stat_identity(fact: dict[str, Any]) -> tuple:
        return tuple(fact[key] for key in ("dev", "ino", "size", "mtimeNs", "ctimeNs"))

    def stable_source_fact(path: Path) -> dict[str, Any]:
        """Bind a full content hash to an unchanged real file, outside project locks."""
        from .montage_reframe import source_fingerprint
        try:
            before = source_identity(path)
            fingerprint = source_fingerprint(path)
            after = source_identity(path)
        except (OSError, ValueError) as error:
            raise SourceProofConflict("素材在校验期间不可读取") from error
        if before != after:
            raise SourceProofConflict("素材在校验期间变化")
        return {**before, "fingerprint": fingerprint}

    class SourceProofConflict(OSError):
        """A source changed after a proof started; clients can retry from a fresh snapshot."""

    def reframe_asset_identity(asset: dict[str, Any]) -> dict[str, Any]:
        return {key: asset.get(key) for key in ("id", "kind", "file", "sourceReferenceId")}

    def prove_reframe_source(path: Path, *, needs_timing: bool) -> dict[str, Any]:
        """Bind analysis hash, dimensions and video timing outside project/source locks."""
        from .montage_reframe import probe_source_timing, source_fingerprint

        try:
            before = source_identity(path)
            fingerprint = source_fingerprint(path)
            try:
                from .timeline_render import _probe_media
                media = _probe_media(path, ffprobe_path)
                size = (media.width, media.height)
            except Exception:
                # Keep the established worker failure for an unprobeable
                # source rather than accepting dimensions from detector output.
                size = (None, None)
            timing = probe_source_timing(path, ffprobe_path) if needs_timing else None
            after = source_identity(path)
        except (OSError, ValueError) as error:
            raise SourceProofConflict("主体分析素材在校验期间不可读取") from error
        if before != after:
            raise SourceProofConflict("主体分析素材在校验期间变化")
        return {"identity": before, "fingerprint": fingerprint, "timing": timing, "size": size}

    def safety_run_identity(task: dict, run: dict) -> dict[str, Any]:
        return {
            "taskId": task["id"],
            "version": (task.get("contentRevision", 0), task["variant"]["revision"],
                        task["variant"].get("subtitles", {}).get("revision", 0)),
            "run": deepcopy(run),
        }

    def prepare_reframe_safety(project_id: str, task: dict, run: dict, assets: dict, *, media_cache: dict,
                               frozen_timing_cache: dict, current_timing_cache: dict) -> dict | None:
        """Build a one-request delivery proof without holding either project lock."""
        clip_ids = reframe_clip_ids(run)
        if not clip_ids:
            return None
        from .timeline_render import inspect_timeline_media
        try:
            snapshot = run["snapshot"]
            frozen_directory = store.path(project_id, "exports" if run.get("previewRunId") else "runs", run["id"])
            frozen_sources = render_inputs.resolve(frozen_directory, run["sources"])
            state = {"revision": run.get("revision", task["variant"]["revision"]), **snapshot}
            referenced_assets = {clip["assetId"] for track in snapshot["tracks"] for clip in track["clips"]
                               if clip.get("id") in clip_ids}
            snapshot_assets = {clip["assetId"] for track in snapshot["tracks"] for clip in track["clips"]}
            frozen_facts = {asset_id: stable_source_fact(frozen_sources[asset_id]) for asset_id in referenced_assets}
            current_paths = {asset_id: project_assets.file(project_id, assets[asset_id]) for asset_id in snapshot_assets}
            current_facts = {asset_id: {**stable_source_fact(path), "assetFile": assets[asset_id].get("file")}
                             for asset_id, path in current_paths.items() if asset_id in referenced_assets}
            frozen_issues = inspect_timeline_media(
                state, assets, lambda asset: frozen_sources[asset["id"]], "mp4", ffprobe_path,
                media_cache=media_cache, timing_cache=frozen_timing_cache)
            current_issues = inspect_timeline_media(
                state, assets, lambda asset: current_paths[asset["id"]], "mp4", ffprobe_path,
                media_cache=media_cache, timing_cache=current_timing_cache)
            try:
                changed = (any(source_identity(frozen_sources[asset_id]) != {key: fact[key] for key in fact if key != "fingerprint"}
                               for asset_id, fact in frozen_facts.items())
                           or any(source_identity(current_paths[asset_id]) != {key: fact[key] for key in fact if key not in ("fingerprint", "assetFile")}
                                  for asset_id, fact in current_facts.items()))
            except (OSError, ValueError) as error:
                raise SourceProofConflict("素材在校验期间不可读取") from error
            if changed:
                raise SourceProofConflict("素材在校验期间变化")
        except SourceProofConflict as error:
            raise BatchProductionError("batch_export_reframe_conflict", "主体构图验证期间素材已变化，请刷新后重试。") from error
        except (KeyError, OSError, ValueError, RenderInputError) as error:
            raise BatchProductionError("batch_export_reframe_review_required",
                                       "主体构图关联素材不可验证，请重新主体分析并预览审核。") from error
        failed = next((issue for issue in [*frozen_issues, *current_issues]
                       if issue.get("level") == "error" and issue.get("clipId") in clip_ids), None)
        if failed is not None:
            raise BatchProductionError("batch_export_reframe_review_required",
                                       "主体构图或源时间已变化，请重新主体分析或选择居中/手工构图后重新预览审核。")
        return {"identity": safety_run_identity(task, run), "frozen": frozen_facts, "current": current_facts}

    def consume_reframe_safety(project_id: str, task: dict, run: dict, proof: dict | None, assets: dict) -> None:
        """CAS the lock-free proof.  This path must stay limited to state/stat checks."""
        if not reframe_clip_ids(run):
            return
        if proof is None or proof["identity"] != safety_run_identity(task, run):
            raise BatchProductionError("batch_export_reframe_conflict", "主体构图验证期间版本已变化，请刷新后重试。")
        try:
            frozen_directory = store.path(project_id, "exports" if run.get("previewRunId") else "runs", run["id"])
            frozen_sources = render_inputs.resolve(frozen_directory, run["sources"])
            for asset_id, fact in proof["frozen"].items():
                if source_identity(frozen_sources[asset_id]) != {key: fact[key] for key in fact if key != "fingerprint"}:
                    raise OSError("冻结素材已变化")
            for asset_id, fact in proof["current"].items():
                asset = assets.get(asset_id)
                if not isinstance(asset, dict) or asset.get("file") != fact["assetFile"]:
                    raise OSError("项目素材引用已变化")
                if source_identity(project_assets.file(project_id, asset)) != {
                    key: fact[key] for key in fact if key not in ("fingerprint", "assetFile")
                }:
                    raise OSError("项目素材已变化")
        except (KeyError, OSError, ValueError, RenderInputError) as error:
            raise BatchProductionError("batch_export_reframe_conflict", "主体构图验证期间素材已变化，请刷新后重试。") from error

    def prepare_approved_source_snapshot(project_id: str, run_id: str, preview: dict, assets: dict) -> dict:
        """Copy an approved export input outside locks and retain a stat-CAS proof for every source."""
        destination = store.path(project_id, "exports", run_id)
        completed = False
        try:
            approved_directory = store.path(project_id, "runs", preview["id"])
            frozen_sources = render_inputs.resolve(approved_directory, preview["sources"])
            current_paths = {asset_id: project_assets.file(project_id, assets[asset_id])
                             for asset_id in preview["sources"]}
            frozen = {asset_id: stable_source_fact(path) for asset_id, path in frozen_sources.items()}
            current = {asset_id: {**stable_source_fact(path), "assetFile": assets[asset_id].get("file")}
                       for asset_id, path in current_paths.items()}
            # A mismatch already present when this request starts remains the
            # historical failed-delivery result.  Once this proof begins,
            # however, ``freeze_approved`` is part of the CAS and every
            # replacement (including same-byte rename) is a retryable 409.
            if any(current[asset_id]["fingerprint"] != frozen[asset_id]["fingerprint"] for asset_id in frozen):
                raise BatchProductionError("batch_asset_unavailable", "原素材缺失或变化，请重新预览审核。")
            try:
                sources = render_inputs.freeze_approved(
                    project_id, destination, store.path(project_id, "runs", preview["id"]), preview["sources"], assets)
            except RenderInputError as error:
                try:
                    changed = (any(source_identity(frozen_sources[asset_id]) != {
                        key: fact[key] for key in fact if key != "fingerprint"
                    } for asset_id, fact in frozen.items()) or any(source_identity(current_paths[asset_id]) != {
                        key: fact[key] for key in fact if key not in ("fingerprint", "assetFile")
                    } for asset_id, fact in current.items()))
                except (OSError, ValueError):
                    changed = True
                if changed:
                    raise SourceProofConflict("素材在快照复制期间变化") from error
                failure = BatchProductionError("batch_asset_unavailable", "原素材缺失或变化，请重新预览审核。")
                failure.snapshot_copy_failed = True
                raise failure from error
            except (KeyError, ValueError, OSError) as error:
                raise SourceProofConflict("素材在快照复制期间变化") from error
            try:
                changed = (any(source_identity(frozen_sources[asset_id]) != {key: fact[key] for key in fact if key != "fingerprint"}
                               for asset_id, fact in frozen.items())
                           or any(source_identity(current_paths[asset_id]) != {key: fact[key] for key in fact if key not in ("fingerprint", "assetFile")}
                                  for asset_id, fact in current.items()))
            except (OSError, ValueError) as error:
                raise SourceProofConflict("素材在快照期间不可读取") from error
            if changed:
                raise SourceProofConflict("素材在快照期间变化")
            completed = True
            return {"sources": sources, "frozen": frozen, "current": current}
        except SourceProofConflict as error:
            raise BatchProductionError("batch_export_reframe_conflict", "导出准备期间素材已变化，请刷新后重试。") from error
        except BatchProductionError:
            raise
        except HTTPException:
            raise BatchProductionError("batch_asset_unavailable", "原素材缺失或变化，请重新预览审核。") from None
        except (KeyError, OSError, ValueError, RenderInputError) as error:
            raise BatchProductionError("batch_asset_unavailable", "原素材缺失或变化，请重新预览审核。") from error
        finally:
            if not completed:
                shutil.rmtree(destination, ignore_errors=True)

    def consume_source_snapshot(project_id: str, preview: dict, proof: dict, assets: dict) -> None:
        """Stat-check the lock-free approved-source copy without rereading media bytes."""
        try:
            frozen_sources = render_inputs.resolve(store.path(project_id, "runs", preview["id"]), preview["sources"])
            for asset_id, fact in proof["frozen"].items():
                if source_identity(frozen_sources[asset_id]) != {key: fact[key] for key in fact if key != "fingerprint"}:
                    raise OSError("冻结素材已变化")
            for asset_id, fact in proof["current"].items():
                asset = assets.get(asset_id)
                if not isinstance(asset, dict) or asset.get("file") != fact["assetFile"]:
                    raise OSError("项目素材引用已变化")
                if source_identity(project_assets.file(project_id, asset)) != {
                    key: fact[key] for key in fact if key not in ("fingerprint", "assetFile")
                }:
                    raise OSError("项目素材已变化")
        except (KeyError, OSError, ValueError, RenderInputError) as error:
            raise BatchProductionError("batch_export_reframe_conflict", "导出准备期间素材已变化，请刷新后重试。") from error

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

    def recognition_input_is_current(task: dict, run: dict) -> bool:
        subtitles = _subtitles(task["variant"])
        return (run["timelineRevision"] == task["variant"]["revision"]
                and run["subtitleRevision"] == subtitles["revision"]
                and run.get("cueIds", []) == [cue["id"] for cue in subtitles["cues"]])

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
                run["status"] = LOCAL_RUN_POLICY.start(run["status"])
                save(project_id, state)
                snapshot, names, language = run["snapshot"], dict(run["sources"]), run["language"]
            directory = store.path(project_id, "recognitions", run_id)
            sources = render_inputs.resolve(directory, names)
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
                    # parse_srt IDs are only local line numbers. Bind the
                    # durable cue identity to this recognition attempt so a
                    # later retry cannot inherit an old style override.
                    persisted = [{**cue, "id": f"{run_id}-{cue['id']}"} for cue in cues]
                    task = task_for(state, task_id)
                    auto_adopt = (task["variant"]["subtitles"].get("migrationRequired") is not None
                                  and recognition_input_is_current(task, run)
                                  and not has_ambiguous_packaging_dependency(task))
                    result = {"cues": persisted, "adoptionRequired": not auto_adopt}
                    if not auto_adopt:
                        result["adoptionConflict"] = "字幕在识别期间已修改，请确认候选后采用。"
                    _complete_local_run(run, result)
                    if auto_adopt:
                        subtitles = task["variant"]["subtitles"]
                        subtitles["cues"] = deepcopy(persisted)
                        subtitles["revision"] += 1
                        clear_legacy_cue_migration(task)
                    save(project_id, state)
        except Exception as error:
            with lock:
                try:
                    state = state_for(project_id)
                    run = recognition_for(task_for(state, task_id)["variant"], run_id)
                    if LOCAL_RUN_POLICY.active(run["status"]):
                        run.update(status=LOCAL_RUN_POLICY.fail(run["status"]), error=str(error) or "字幕识别失败。")
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
                run["status"] = LOCAL_RUN_POLICY.start(run["status"])
                save(project_id, state)
                snapshot = deepcopy(run["snapshot"])
                names = dict(run["sources"])
                subtitle_cues = deepcopy(run.get("subtitleCues", []))
            packaging_proof = prepare_packaging_proof(snapshot, subtitle_cues, batch=True)
            with lock:
                run = find_run(task_for(state_for(project_id), task_id)["variant"], run_id)
                if run["status"] != "running":
                    return
                consume_packaging_proof(packaging_proof, run["snapshot"], run.get("subtitleCues", []), batch=True)
            sources = render_inputs.resolve(store.path(project_id, "runs", run_id), names)
            staging = store.path(project_id, "runs", run_id, "preview.mp4")
            actual_renderer = renderer
            if actual_renderer is None:
                from .timeline_render import render_timeline
                actual_renderer = render_timeline
            subtitle_file = None
            if subtitle_cues and snapshot.get("packaging") is None:
                subtitle_file = store.path(project_id, "runs", run_id, "subtitles.srt")
                write_srt(subtitle_file, subtitle_cues)
            render_options = {"subtitles_path": subtitle_file} if subtitle_file else {}
            if snapshot.get("packaging") is not None:
                render_options["packaging_cues"] = subtitle_cues
            media = actual_renderer(snapshot, sources, staging, format="preview", ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path,
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
                result = {"output": output.name}
                if isinstance(media, dict):
                    result["media"] = media
                _complete_local_run(run, result)
                save(project_id, state)
        except BaseException as error:
            with lock:
                try:
                    state = state_for(project_id)
                    run = find_run(task_for(state, task_id)["variant"], run_id)
                    if LOCAL_RUN_POLICY.active(run["status"]):
                        message = str(error)
                        run.update(status=LOCAL_RUN_POLICY.fail(run["status"]), error=message if message and len(message) <= 300 and "/" not in message and "\\" not in message else "预览失败，请检查素材与片段。")
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
                run["status"] = LOCAL_RUN_POLICY.start(run["status"])
                save(project_id, state)
                snapshot, names, cues = deepcopy(run["snapshot"]), dict(run["sources"]), deepcopy(run["subtitles"])
            packaging_proof = prepare_packaging_proof(snapshot, cues, batch=True)
            with lock:
                run = find_export(task_for(state_for(project_id), task_id)["variant"], run_id)
                if run["status"] != "running":
                    return
                consume_packaging_proof(packaging_proof, run["snapshot"], run["subtitles"], batch=True)
            directory = store.path(project_id, "exports", run_id)
            sources = render_inputs.resolve(directory, names)
            subtitle_file = None
            if cues and snapshot.get("packaging") is None:
                subtitle_file = directory / "subtitles.srt"
                write_srt(subtitle_file, cues)
            staging = directory / "final.mp4"
            actual_renderer = renderer
            if actual_renderer is None:
                from .timeline_render import render_timeline
                actual_renderer = render_timeline
            render_options = {"subtitles_path": subtitle_file} if subtitle_file else {}
            if snapshot.get("packaging") is not None:
                render_options["packaging_cues"] = cues
            media = actual_renderer(snapshot, sources, staging, format="mp4", ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path,
                                    cancelled=lambda: export_cancelled(project_id, task_id, run_id), **render_options)
            regular_file(staging)
            with lock:
                state = state_for(project_id)
                run = find_export(task_for(state, task_id)["variant"], run_id)
                if run["status"] != "running":
                    return
                output = store.path(project_id, "deliverables", run_id + ".mp4")
                output.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, output)
                result = {"output": output.name}
                if isinstance(media, dict):
                    result["media"] = media
                _complete_local_run(run, result)
                save(project_id, state)
        except BaseException as error:
            with lock:
                try:
                    state = state_for(project_id)
                    run = find_export(task_for(state, task_id)["variant"], run_id)
                    if LOCAL_RUN_POLICY.active(run["status"]):
                        message = str(error)
                        run.update(status=LOCAL_RUN_POLICY.fail(run["status"]), error=message if message and len(message) <= 300 and "/" not in message and "\\" not in message else "成片导出失败，请检查素材与片段。")
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
                    if voice:
                        recovery = LOCAL_RUN_POLICY.recover_after_restart(voice["status"])
                        if recovery.reason == "interrupted":
                            voice.update(status=recovery.status, error="服务已重启，请重新生成配音。")
                            voice.pop("plan", None)
                            changed = True
                    for run in task["variant"]["runs"]:
                        recovery = LOCAL_RUN_POLICY.recover_after_restart(run["status"])
                        if recovery.reason == "interrupted":
                            run.update(status=recovery.status, error="服务重启中断了预览，可重新提交。")
                            changed = True
                    for run in task["variant"].get("subtitles", {}).get("recognitions", []):
                        recovery = LOCAL_RUN_POLICY.recover_after_restart(run["status"])
                        if recovery.reason == "interrupted":
                            run.update(status=recovery.status, error="服务重启中断了字幕识别，可重新提交。")
                            changed = True
                    for run in task["variant"].get("exports", []):
                        recovery = LOCAL_RUN_POLICY.recover_after_restart(run["status"])
                        if recovery.reason == "interrupted":
                            run.update(status=recovery.status, error="服务重启中断了成片导出，可重新提交。")
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
            try:
                assets = project_assets.public(project_id, include_source_reference=True)
            except (OSError, ValueError):
                raise HTTPException(503, detail={"code": "batch_assets_unavailable", "message": "项目素材无法读取。"}) from None
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
            state = state_for(project_id)
            assets = assets_for(project_id)
            created, parent, children, skipped = apply_aigc_handoff(
                state, body["generationId"], brief, candidates, assets,
                ensure_asset=lambda asset: project_assets.file(project_id, asset),
            )
            save(project_id, state)
            content = {"task": public_task(project_id, parent),
                       "tasks": [public_task(project_id, task) for task in children]}
            if not created:
                content["skippedTaskIds"] = skipped
                return JSONResponse(status_code=200, content=content)
            return content

    @router.put("/{task_id}/content")
    def save_content(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or not {"revision", "sellingPoint", "script"} <= set(body)
                or set(body) - {"revision", "sellingPoint", "script", "cta"}
                or type(body["revision"]) is not int or any(
                    not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 4000
                    for key in ("sellingPoint", "script"))
                or ("cta" in body and body["cta"] is not None and (
                    not isinstance(body["cta"], str) or not body["cta"].strip() or len(body["cta"]) > 120))):
            raise HTTPException(422, detail={"code": "batch_content_invalid", "message": "卖点或脚本无效。"})
        with lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            changed = production_call(
                save_batch_content, task, expected_revision=body["revision"],
                selling_point=body["sellingPoint"], script=body["script"],
                **({"cta": body["cta"]} if "cta" in body else {}),
            )
            if changed:
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
            task = task_for(state, task_id)
            production_call(
                save_batch_subtitles, task, expected_revision=body["revision"],
                timeline_revision=body["timelineRevision"], cues=body["cues"], validate_cues=_validate_cues,
            )
            save(project_id, state)
            return {"subtitles": public_task(project_id, task)["variant"]["subtitles"]}

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
            if any(LOCAL_RUN_POLICY.active(run["status"]) for run in subtitles["recognitions"]):
                raise HTTPException(409, detail={"code": "batch_recognition_active", "message": "已有字幕识别任务正在运行。"})
            if not any(track["kind"] == "video" and not track["hidden"] and track["clips"] for track in variant["tracks"]):
                raise HTTPException(422, detail={"code": "batch_video_required", "message": "请先保存包含画面的变体。"})
            run_id = uuid4().hex
            directory = store.path(project_id, "recognitions", run_id)
            snapshot = {"settings": variant["settings"], "tracks": variant["tracks"]}
            if variant.get("transitions"):
                snapshot["transitions"] = variant["transitions"]
            sources = snapshot_sources(project_id, directory, variant["tracks"], assets_for(project_id))
            run = {"id": run_id, "status": LOCAL_RUN_POLICY.initial_status, "error": None, "timelineRevision": variant["revision"],
                   "subtitleRevision": subtitles["revision"], "cueIds": [cue["id"] for cue in subtitles["cues"]],
                   "language": body["language"], "cues": [],
                   "snapshot": snapshot, "sources": sources}
            subtitles["recognitions"].append(run)
            save(project_id, state)
            if not compute_queue.submit("batch-recognition:" + run_id, project_id,
                                        lambda pid: process_recognition(pid, task_id, run_id)):
                run.update(status=LOCAL_RUN_POLICY.fail(run["status"]), error="本地处理队列不可用。")
                save(project_id, state)
            return {"subtitles": public_task(project_id, task_for(state, task_id))["variant"]["subtitles"]}

    @router.post("/{task_id}/subtitles/recognitions/{run_id}/cancel")
    def cancel_recognition(project_id: str, task_id: str, run_id: str, body: dict[str, Any]):
        project_for(project_id)
        if body != {}:
            raise HTTPException(422, detail={"code": "batch_recognition_invalid", "message": "字幕识别取消请求无效。"})
        with lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            run = recognition_for(task["variant"], run_id)
            if not LOCAL_RUN_POLICY.active(run["status"]):
                raise HTTPException(409, detail={"code": "batch_recognition_not_active", "message": "字幕识别未在运行。"})
            run.update(status=LOCAL_RUN_POLICY.cancel(run["status"]), error="字幕识别已取消。")
            save(project_id, state)
            return {"subtitles": public_task(project_id, task)["variant"]["subtitles"]}

    @router.post("/{task_id}/subtitles/recognitions/{run_id}/apply")
    def apply_recognition(project_id: str, task_id: str, run_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or set(body) != {"revision", "timelineRevision"}
                or type(body["revision"]) is not int or type(body["timelineRevision"]) is not int):
            raise HTTPException(422, detail={"code": "batch_recognition_invalid", "message": "字幕采用请求无效。"})
        with lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            run = recognition_for(task["variant"], run_id)
            if run.get("status") != "completed" or run.get("adoptionRequired") is not True:
                raise HTTPException(409, detail={"code": "batch_recognition_adoption_unavailable", "message": "此识别候选无需或不能采用。"})
            production_call(
                save_batch_subtitles, task, expected_revision=body["revision"],
                timeline_revision=body["timelineRevision"], cues=deepcopy(run["cues"]), validate_cues=_validate_cues,
            )
            run["adoptionRequired"] = False
            run.pop("adoptionConflict", None)
            save(project_id, state)
            return {"subtitles": public_task(project_id, task)["variant"]["subtitles"]}

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
            assets = assets_for(project_id)
            def build_proposal(task, selected_ids):
                try:
                    proposal = recommend(task, assets, selected_ids)
                    for asset_id in {clip["assetId"] for clip in proposal["clips"]}:
                        project_assets.file(project_id, assets[asset_id])
                except RecommendationError as error:
                    raise BatchProductionError(error.code, str(error), 422) from None
                return proposal
            generation_id, created = production_call(
                create_batch_variants, parent, items, asset_ids, assets=assets, build_proposal=build_proposal,
            )
            state["tasks"].extend(created)
            save(project_id, state)
            return {"generationId": generation_id, "tasks": [public_task(project_id, task) for task in created]}

    @router.put("/{task_id}/variant")
    def save_variant(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or not {"revision", "tracks"} <= set(body) or set(body) - {"revision", "tracks", "transitions", "settings", "aspectMode", "packaging"}:
            raise HTTPException(422, detail={"code": "batch_variant_invalid", "message": "变体内容无效。"})
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            variant = task["variant"]
            try:
                from .montage_reframe import ReframeError, retain_server_reframe_facts
                body = {**body, "tracks": retain_server_reframe_facts(variant["tracks"], body["tracks"])}
            except ReframeError as error:
                raise HTTPException(422, detail={"code": "batch_variant_invalid", "message": str(error)}) from None
            production_call(
                save_batch_variant, task, expected_revision=body["revision"], tracks=body["tracks"],
                settings=body.get("settings", variant["settings"]),
                aspect_mode=body.get("aspectMode", variant.get("aspectMode")), assets=assets_for(project_id),
                validate_subtitles=_validate_cues, aspect_was_explicit="aspectMode" in body,
                transitions=body.get("transitions", variant.get("transitions", [])),
                **({"packaging": body["packaging"]} if "packaging" in body else {}),
            )
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
                    project_assets.file(project_id, assets[asset_id])
            except (OSError, ValueError, KeyError, TypeError):
                raise HTTPException(422, detail={"code": "batch_asset_unavailable", "message": "推荐素材文件不可用，请检查或重新导入素材。"}) from None
            task["proposal"] = proposal
            save(project_id, state)
            return {"proposal": proposal}

    def montage_proposal_for(task: dict[str, Any], proposal_id: str) -> dict[str, Any]:
        try:
            validate_storage_id(proposal_id)
            return next(item for item in task.get("montageProposals", []) if item.get("id") == proposal_id)
        except (ValueError, StopIteration):
            raise HTTPException(404, detail={"code": "montage_proposal_missing", "message": "混剪提案不存在。"}) from None

    rerank_expectation_unset = object()

    def planning_search(project_id: str, search_id: str, expected_rerank_id: Any = rerank_expectation_unset) -> dict[str, Any]:
        try:
            kwargs = {
                "reranker": segment_reranker,
                "vectorizer_version": segment_vectorizer_version,
            }
            if expected_rerank_id is not rerank_expectation_unset:
                kwargs["expected_rerank_id"] = expected_rerank_id
            return load_planning_search(data_dir, project_id, search_id, **kwargs)
        except PlanningSearchError as error:
            status = 404 if error.code == "segment_search_not_found" else 409
            raise HTTPException(status, detail={"code": error.code, "message": error.message}) from None

    @router.post("/{task_id}/montage-proposals", status_code=201)
    def create_montage_proposal(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or set(body) != {"revision", "searchIds", "narration", "beatTimes"}
                or type(body.get("revision")) is not int
                or not isinstance(body.get("searchIds"), list)
                or not isinstance(body.get("narration"), list)
                or not isinstance(body.get("beatTimes"), list)
                or any(not _valid_id(value) for value in body["searchIds"])):
            raise HTTPException(422, detail={"code": "montage_request_invalid", "message": "混剪提案输入无效。"})
        with source_lock or nullcontext(), lock:
            expected_state = state_for(project_id)
            state = deepcopy(expected_state)
            task = task_for(state, task_id)
            if body["revision"] != task["variant"]["revision"]:
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            texts = split_script_segments(task["script"])
            if not texts or len(texts) != len(body["searchIds"]) or len(texts) != len(body["narration"]):
                raise HTTPException(422, detail={"code": "montage_script_mismatch", "message": "每个脚本段落都需要一份检索结果和旁白时长。"})
            segment_inputs = []
            for index, (text, search_id, narration) in enumerate(zip(texts, body["searchIds"], body["narration"]), start=1):
                if (not isinstance(narration, dict)
                        or set(narration) not in ({"durationSeconds", "wordBoundariesSeconds"},
                                                  {"durationSeconds", "wordBoundariesSeconds", "source"})
                        or type(narration.get("durationSeconds")) not in (int, float)
                        or not isinstance(narration.get("wordBoundariesSeconds"), list)
                        or narration.get("source", "provided") not in (
                            "provided", "measured_voiceover", "subtitle_cue", "estimated_text",
                        )):
                    raise HTTPException(422, detail={"code": "montage_narration_invalid", "message": "旁白时长或词边界无效。"})
                search = planning_search(project_id, search_id)
                if search["query"].strip() != text:
                    raise HTTPException(422, detail={"code": "montage_search_mismatch", "message": f"脚本第 {index} 段的检索文字不一致，请重新检索。"})
                segment_inputs.append({
                    "id": f"script-segment-{index}", "text": text,
                    "narrationDurationSeconds": narration["durationSeconds"],
                    "wordBoundariesSeconds": narration["wordBoundariesSeconds"],
                    "narrationSource": narration.get("source", "provided"),
                    "searchId": search["id"], "indexVersion": search["indexVersion"],
                    "retrievalVersion": search["retrievalVersion"], "vectorizerVersion": search["vectorizerVersion"],
                    "rerankId": search["rerankId"], "candidates": search["candidates"],
                })
            batch_members = [item for item in state["tasks"] if item.get("batchId") == task.get("batchId")] if task.get("batchId") else [task]
            shared_usage = _shared_montage_usage(batch_members, task["id"])
            variant_ordinal = next((position for position, item in enumerate(batch_members) if item["id"] == task["id"]), 0)
            try:
                proposal = plan_montage(
                    task_id=task["id"], variant_id=task["variant"]["id"],
                    content_revision=task.get("contentRevision", 0), variant_revision=task["variant"]["revision"],
                    segments=segment_inputs, beat_times=body["beatTimes"], shared_usage=shared_usage,
                    variant_ordinal=variant_ordinal,
                    proposal_generation=len(task.get("montageProposals", [])),
                    selling_point=task["sellingPoint"],
                    target_width=task["variant"]["settings"]["width"],
                    target_height=task["variant"]["settings"]["height"],
                )
            except MontagePlanningError as error:
                raise HTTPException(422, detail={"code": error.code, "message": error.message}) from None
            proposal.update(revision=0, status="draft", appliedVariantRevision=None)
            for item in proposal["segments"]:
                item["selectedCandidateId"] = item["suggestion"]["candidateId"] if item["suggestion"] else None
            # The proposal holds only verified task copy.  No CTA is invented:
            # it appears only when an upstream confirmed workflow supplied one.
            from .batch_production import initial_proposal_packaging
            proposal_tracks = [{**track, "clips": proposal["clips"]} if track["kind"] == "video" else track
                               for track in task["variant"]["tracks"]]
            duration = max((clip["start"] + clip["duration"] for clip in proposal["clips"]), default=.01)
            proposal["packaging"] = initial_proposal_packaging(
                script=task["script"], selling_point=task["sellingPoint"], duration=duration,
                cta=task.get("cta") if isinstance(task.get("cta"), str) else None,
            )
            from .montage_packaging import validate_packaging
            proposal["packaging"] = validate_packaging(proposal["packaging"], [], proposal_tracks)
            proposals = task.setdefault("montageProposals", [])
            proposals.append(proposal)
            staged_state = state
            response = {"proposal": deepcopy(proposal)}
        # Queue all chosen first-pass clips together; users never need to click each one.
        if proposal["clips"]:
            schedule_reframes(project_id, task_id, [clip["id"] for clip in proposal["clips"]], proposal_id=proposal["id"],
                              staged_state=staged_state, expected_state=expected_state)
        else:
            commit_staged_batch_state(project_id, expected_state, staged_state)
        return response

    @router.post("/{task_id}/variant/reframes/{clip_id}/analyze", status_code=202)
    def analyze_variant_reframe(project_id: str, task_id: str, clip_id: str, body: dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) != {"revision"} or type(body["revision"]) is not int:
            raise HTTPException(422, detail={"code": "reframe_request_invalid", "message": "主体分析请求无效。"})
        try:
            schedule_reframes(project_id, task_id, [clip_id], expected_revision=body["revision"])
        except (OSError, ValueError):
            raise HTTPException(409, detail={"code": "reframe_source_unavailable", "message": "主体分析素材不可用。"}) from None
        with lock:
            return {"variant": public_task(project_id, task_for(state_for(project_id), task_id))["variant"]}

    @router.post("/{task_id}/variant/reframes/{clip_id}/cancel")
    def cancel_variant_reframe(project_id: str, task_id: str, clip_id: str, body: dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) != {"revision"} or type(body["revision"]) is not int:
            raise HTTPException(422, detail={"code": "reframe_request_invalid", "message": "主体分析取消请求无效。"})
        with lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            if task["variant"]["revision"] != body["revision"]:
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            record = task["variant"].get("reframeAnalysis", {}).get(clip_id)
            if record is None or record.get("status") not in ("queued", "running"):
                raise HTTPException(409, detail={"code": "reframe_not_active", "message": "主体分析未在运行。"})
            record.update(status="cancelled", error=None)
            save(project_id, state)
            return {"variant": public_task(project_id, task)["variant"]}

    @router.put("/{task_id}/montage-proposals/{proposal_id}")
    def update_montage_proposal(project_id: str, task_id: str, proposal_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or set(body) != {"revision", "segments"} or type(body.get("revision")) is not int
                or not isinstance(body.get("segments"), list)):
            raise HTTPException(422, detail={"code": "montage_adjustment_invalid", "message": "提案调整无效。"})
        with source_lock or nullcontext(), lock:
            expected_state = state_for(project_id)
            state = deepcopy(expected_state)
            task = task_for(state, task_id)
            proposal = montage_proposal_for(task, proposal_id)
            if body["revision"] != proposal["revision"]:
                raise HTTPException(409, detail={"code": "montage_proposal_conflict", "message": "混剪提案已更新，请刷新后重试。"})
            if proposal.get("status") != "draft":
                raise HTTPException(409, detail={"code": "montage_proposal_closed", "message": "混剪提案已采用或放弃，请重新生成。"})
            by_segment = {item["id"]: item for item in proposal["segments"]}
            input_by_segment = {item["id"]: item for item in proposal["inputSnapshot"]["segments"]}
            old_handles = {item["leftClipId"]: item["overlap"] for item in proposal.get("transitions", [])
                           if item["enabled"]}
            # UI durations include the old overlap tail. Strip it when unchanged;
            # explicitly changed durations remain the user's requested duration.
            for item in proposal["segments"]:
                clip = item.get("selectedClip")
                if clip and clip["id"] in old_handles:
                    adjustment = next((value for value in body["segments"] if isinstance(value, dict)
                                       and value.get("segmentId") == item["id"]), None)
                    if adjustment is None:
                        clip["duration"] = round(clip["duration"] - old_handles[clip["id"]], 6)
                    elif adjustment.get("durationSeconds") == clip["duration"]:
                        adjustment["durationSeconds"] = round(clip["duration"] - old_handles[clip["id"]], 6)
            seen = set()
            for adjustment in body["segments"]:
                if (not isinstance(adjustment, dict) or set(adjustment) != {"segmentId", "candidateId", "inPointSeconds", "durationSeconds"}
                        or adjustment["segmentId"] in seen or adjustment["segmentId"] not in by_segment
                        or type(adjustment["inPointSeconds"]) not in (int, float)
                        or type(adjustment["durationSeconds"]) not in (int, float)):
                    raise HTTPException(422, detail={"code": "montage_adjustment_invalid", "message": "提案段落调整无效。"})
                seen.add(adjustment["segmentId"])
                source = input_by_segment[adjustment["segmentId"]]
                candidate = next((item for item in source["candidates"] if item.get("candidateId") == adjustment["candidateId"]), None)
                start = float(adjustment["inPointSeconds"])
                duration = float(adjustment["durationSeconds"])
                if (candidate is None or not math.isfinite(start) or not math.isfinite(duration) or duration <= 0
                        or start < candidate["inPointSeconds"] or start + duration > candidate["outPointSeconds"] + 1e-8):
                    raise HTTPException(422, detail={"code": "montage_adjustment_out_of_range", "message": "调整后的入点或时长超出候选源区间。"})
                target = by_segment[adjustment["segmentId"]]
                previous_clip = target.get("selectedClip")
                selection_changed = (
                    target.get("selectedCandidateId") != candidate["candidateId"]
                    or previous_clip is None
                    or abs(float(previous_clip["inPoint"]) - start) > 1e-8
                    or abs(float(previous_clip["duration"]) - duration) > 1e-8
                )
                clip_id = (
                    (target.get("suggestionClip") or {}).get("id")
                    or f"montage-clip-{hashlib.sha256((target['id'] + ':' + candidate['candidateId']).encode()).hexdigest()[:20]}"
                    if (target.get("suggestion") or {}).get("candidateId") == candidate["candidateId"]
                    else next((item["clip"]["id"] for item in target["alternatives"]
                               if item["candidate"]["candidateId"] == candidate["candidateId"]),
                              (target.get("selectedClip") or {}).get("id"))
                )
                target["selectedCandidateId"] = candidate["candidateId"]
                target["selectedClip"] = {
                    "id": clip_id, "assetId": candidate["assetId"], "start": (target.get("selectedClip") or {}).get("start", 0),
                    "inPoint": start, "duration": duration, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0,
                }
                previous_reframe = (previous_clip or {}).get("reframe")
                if (previous_reframe is not None and previous_clip.get("assetId") == candidate["assetId"]
                        and abs(float(previous_clip["inPoint"]) - start) < 1e-8
                        and abs(float(previous_clip["duration"]) - duration) < 1e-8
                        and float(previous_clip.get("speed", 1)) == 1
                        and previous_reframe.get("sourceFingerprint")):
                    target["selectedClip"]["reframe"] = deepcopy(previous_reframe)
                else:
                    from .montage_reframe import pending_reframe
                    target["selectedClip"]["reframe"] = pending_reframe(
                        target_width=task["variant"]["settings"]["width"],
                        target_height=task["variant"]["settings"]["height"],
                        reason="候选或源区间已改变，需重新主体分析",
                    )
                required_duration = float(target["timing"]["narrationDurationSeconds"])
                target["unmet"] = [] if duration >= required_duration - 0.001 else [
                    f"人工调整后仅有 {duration:.3f} 秒，短于旁白所需 {required_duration:.3f} 秒"
                ]
                target["status"] = "gap" if target["unmet"] else "planned"
                if selection_changed:
                    timing_reasons = [reason for reason in target.get("reasons", [])
                                      if reason.startswith(("按旁白", "镜头结束", "节拍"))]
                    target["reasons"] = timing_reasons + [
                        f"人工选择候选 {candidate['candidateId']}，系统原建议保留用于对照",
                        f"当前候选本地视觉相关度 {candidate['visualRelevance']:.3f}，素材质量 {candidate['quality']['score']:.3f}",
                        str(candidate.get("evidence", {}).get("basis") or "使用片段关键帧视觉证据"),
                        f"相似构图风险：{candidate.get('duplicateRisk', {}).get('level', 'unknown')}",
                    ]
                    if isinstance(candidate.get("rerank"), dict):
                        target["reasons"].append(
                            f"豆包复排置信度 {candidate['rerank']['confidence']:.3f}：{candidate['rerank']['reason']}"
                        )
            used_candidates = set()
            used_ranges: dict[str, list[tuple[float, float]]] = {}
            timeline_position = 0.0
            for item in proposal["segments"]:
                clip = item.get("selectedClip")
                candidate_id = item.get("selectedCandidateId")
                if clip is None or candidate_id is None:
                    timeline_position += item["timing"]["narrationDurationSeconds"]
                    continue
                source_start = float(clip["inPoint"])
                source_end = source_start + float(clip["duration"])
                if candidate_id in used_candidates or any(
                    source_start < end and source_end > start for start, end in used_ranges.get(clip["assetId"], [])
                ):
                    raise HTTPException(422, detail={"code": "montage_adjustment_overlap", "message": "同一提案不能重复或重叠使用素材片段。"})
                used_candidates.add(candidate_id)
                used_ranges.setdefault(clip["assetId"], []).append((source_start, source_end))
                clip["start"] = round(timeline_position, 6)
                timeline_position += float(clip["duration"])
                beat_snap = item["timing"].get("beatSnap")
                if beat_snap is not None and abs(float(beat_snap) - timeline_position) > 0.001:
                    item["timing"]["beatSnap"] = None
                    item["reasons"] = [reason for reason in item.get("reasons", [])
                                       if not reason.startswith("镜头结束")]
                    item["reasons"].append("人工时长调整改变了节奏位置，已取消原节拍吸附")
            proposal["clips"] = [item["selectedClip"] for item in proposal["segments"] if item.get("selectedClip")]
            proposal["transitions"] = [{
                "id": f"transition-{hashlib.sha256((proposal['id'] + ':' + left['id'] + ':' + right['id']).encode()).hexdigest()[:20]}",
                "leftClipId": left["id"], "rightClipId": right["id"], "template": "cut", "enabled": True,
                "duration": 0.0, "overlap": 0.0, "audioCrossfade": False,
                "reason": "片段候选或时长已人工调整，重新确认效果前安全回退为硬切",
            } for left, right in zip(proposal["clips"], proposal["clips"][1:])
                if abs(left["start"] + left["duration"] - right["start"]) < 1e-6]
            proposal["unmet"] = [{"segmentId": item["id"], "message": message}
                                 for item in proposal["segments"] for message in item.get("unmet", [])]
            proposal.update(revision=proposal["revision"] + 1, status="draft")
            changed_reframes = [item["selectedClip"]["id"] for item in proposal["segments"]
                                if item.get("selectedClip")]
            staged_state = state
            response = {"proposal": deepcopy(proposal)}
        if changed_reframes:
            schedule_reframes(project_id, task_id, changed_reframes, proposal_id=proposal_id,
                              staged_state=staged_state, expected_state=expected_state, reuse_existing=True)
        else:
            commit_staged_batch_state(project_id, expected_state, staged_state)
        with lock:
            return {"proposal": deepcopy(montage_proposal_for(task_for(state_for(project_id), task_id), proposal_id))}

    @router.post("/{task_id}/montage-proposals/{proposal_id}/apply")
    def apply_montage_proposal(project_id: str, task_id: str, proposal_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or set(body) != {"revision", "proposalRevision", "strategy"}
                or type(body.get("revision")) is not int or type(body.get("proposalRevision")) is not int
                or body.get("strategy") not in ("overwrite", "merge", "abandon")):
            raise HTTPException(422, detail={"code": "montage_apply_invalid", "message": "请选择覆盖、合并或放弃提案。"})
        with source_lock or nullcontext(), lock:
            expected_state = state_for(project_id)
            state = deepcopy(expected_state)
            task = task_for(state, task_id)
            proposal = montage_proposal_for(task, proposal_id)
            if body["revision"] != task["variant"]["revision"]:
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            if body["proposalRevision"] != proposal["revision"]:
                raise HTTPException(409, detail={"code": "montage_proposal_conflict", "message": "混剪提案已更新，请刷新后重试。"})
            if proposal.get("status") != "draft":
                raise HTTPException(409, detail={"code": "montage_proposal_closed", "message": "混剪提案已采用或放弃，请重新生成。"})
            if proposal["inputSnapshot"]["contentRevision"] != task.get("contentRevision", 0):
                raise HTTPException(409, detail={"code": "montage_proposal_stale", "message": "脚本已变化，请重新生成混剪提案。"})
            if body["strategy"] == "abandon":
                proposal.update(status="abandoned", revision=proposal["revision"] + 1)
                response = {"proposal": deepcopy(proposal), "variant": public_task(project_id, task)["variant"]}
                staged_state = state
            else:
                response = None
                staged_state = None
        if response is not None:
            commit_staged_batch_state(project_id, expected_state, staged_state)
            return response
        with source_lock or nullcontext(), lock:
            persisted = state_for(project_id)
            if not batch_state_cas_equal(persisted, expected_state):
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "变体已更新，请刷新后重试。"})
            state = deepcopy(expected_state)
            task = task_for(state, task_id)
            proposal = montage_proposal_for(task, proposal_id)
            if (body["revision"] != task["variant"]["revision"] or body["proposalRevision"] != proposal["revision"]
                    or proposal.get("status") != "draft"):
                raise HTTPException(409, detail={"code": "batch_variant_conflict", "message": "混剪提案已更新，请刷新后重试。"})
            # All remaining validation and construction below operates on an
            # unpersisted copy.  Source proof commits it atomically.
            try:
                for segment in proposal["inputSnapshot"]["segments"]:
                    search = planning_search(project_id, segment["searchId"], segment.get("rerankId"))
                    if search["indexVersion"] != segment["indexVersion"]:
                        raise PlanningSearchError("segment_search_stale", "片段索引已变化，请重新执行本地检索。")
            except (PlanningSearchError, HTTPException) as error:
                message = error.message if isinstance(error, PlanningSearchError) else error.detail.get("message", "提案来源已过期。")
                raise HTTPException(409, detail={"code": "montage_proposal_stale", "message": message}) from None
            if (proposal.get("unmet") or any(item.get("unmet") for item in proposal["segments"])
                    or len(proposal["clips"]) != len(proposal["segments"])):
                raise HTTPException(422, detail={"code": "montage_proposal_incomplete", "message": "提案仍有未满足段落，请先补齐候选。"})
            tracks = deepcopy(task["variant"]["tracks"])
            video_track = next((track for track in tracks if track["kind"] == "video"), None)
            if video_track is None:
                raise HTTPException(422, detail={"code": "batch_variant_invalid", "message": "变体缺少画面轨道。"})
            proposed_clips = deepcopy(proposal["clips"])
            proposed_transitions = deepcopy(proposal.get("transitions", []))
            saved_transitions = deepcopy(task["variant"].get("transitions", []))
            adjusted_subtitles = None
            if body["strategy"] == "merge" and video_track["clips"]:
                offset = max(clip["start"] + clip["duration"] for clip in video_track["clips"])
                previous_last_id = max(video_track["clips"], key=lambda clip: clip["start"] + clip["duration"])["id"]
                used_clip_ids = {clip["id"] for track in tracks for clip in track.get("clips", [])}
                remapped = []
                clip_id_map = {}
                for index, clip in enumerate(proposed_clips, start=1):
                    original_id = clip["id"]
                    clip_id = clip["id"]
                    if clip_id in used_clip_ids:
                        clip_id = f"{clip_id}-{proposal['id'][-8:]}-{index}"
                    remapped.append({**clip, "id": clip_id, "start": round(clip["start"] + offset, 6)})
                    clip_id_map[original_id] = clip_id
                    used_clip_ids.add(clip_id)
                proposed_clips = remapped
                proposed_transitions = [{
                    **item,
                    "id": f"{item['id']}-{proposal['id'][-8:]}",
                    "leftClipId": clip_id_map[item["leftClipId"]],
                    "rightClipId": clip_id_map[item["rightClipId"]],
                } for item in proposed_transitions]
                connector = {
                    "id": f"transition-{hashlib.sha256((proposal['id'] + ':merge').encode()).hexdigest()[:20]}",
                    "leftClipId": previous_last_id, "rightClipId": proposed_clips[0]["id"],
                    "template": "cut", "enabled": True, "duration": 0.0, "overlap": 0.0,
                    "audioCrossfade": False, "reason": "合并到既有编辑末尾时保持可预测的硬切边界",
                }
                video_track["clips"].extend(proposed_clips)
                target_transitions = [*saved_transitions, connector, *proposed_transitions]
            else:
                replaced_ids = {clip["id"] for clip in video_track["clips"]}
                # Reusing an identical interval keeps a deliberate manual
                # decision provisionally. schedule_reframes proves the source
                # outside this lock before the staged copy becomes visible.
                for proposed in proposed_clips:
                    matched = next((old for old in video_track["clips"]
                                    if old["assetId"] == proposed["assetId"]
                                    and abs(old["inPoint"] - proposed["inPoint"]) < 1e-8
                                    and abs(old["duration"] - proposed["duration"]) < 1e-8
                                    and abs(old["speed"] - proposed["speed"]) < 1e-8
                                    and old.get("reframe", {}).get("override", {}).get("mode") != "auto"), None)
                    if matched is not None and matched["reframe"].get("sourceFingerprint"):
                        proposed["reframe"] = deepcopy(matched["reframe"])
                video_track["clips"] = proposed_clips
                target_transitions = [item for item in saved_transitions
                                      if item["leftClipId"] not in replaced_ids and item["rightClipId"] not in replaced_ids] + proposed_transitions
                voice_track = next((track for track in tracks if track.get("id") == "voiceover"
                                    and track.get("kind") == "audio"), None)
                subtitle_timing = proposed_clips
                use_voice_timing = voice_track is not None and len(voice_track.get("clips", [])) == len(proposed_clips)
                if use_voice_timing:
                    ordered_voice = sorted(voice_track["clips"], key=lambda clip: clip["start"])
                    for audio_clip, visual_clip in zip(ordered_voice, proposed_clips):
                        audio_clip["start"] = visual_clip["start"]
                    subtitle_timing = ordered_voice
                saved_cues = sorted(task["variant"].get("subtitles", {}).get("cues", []),
                                    key=lambda cue: cue["start"])
                if len(saved_cues) == len(subtitle_timing):
                    adjusted_subtitles = [
                        {**cue, "start": timing_clip["start"],
                         "end": round(timing_clip["start"] + (
                             timing_clip["duration"] if use_voice_timing
                             else cue["end"] - cue["start"]
                         ), 6)}
                        for cue, timing_clip in zip(saved_cues, subtitle_timing)
                    ]
                    try:
                        _validate_cues(adjusted_subtitles, tracks)
                    except ValueError as error:
                        raise HTTPException(422, detail={
                            "code": "montage_subtitle_timing_invalid", "message": str(error),
                        }) from None
            production_call(
                save_batch_variant, task, expected_revision=body["revision"], tracks=tracks,
                settings=task["variant"]["settings"], aspect_mode=task["variant"].get("aspectMode"),
                assets=assets_for(project_id), validate_subtitles=_validate_cues, aspect_was_explicit=False,
                transitions=target_transitions,
                # Any saved package is assumed human-owned unless this is the
                # first proposal adoption. Reapply must never overwrite it.
                **({"packaging": proposal["packaging"]}
                   if task["variant"].get("packaging") is None and proposal.get("packaging") is not None else {}),
            )
            if task["variant"].get("packaging") == proposal.get("packaging"):
                task["variant"]["packagingSource"] = "proposal"
            if adjusted_subtitles is not None:
                task["variant"]["subtitles"]["cues"] = adjusted_subtitles
                task["variant"]["subtitles"]["revision"] += 1
            proposal.update(
                status="applied", appliedVariantRevision=task["variant"]["revision"],
                appliedClipIds=[clip["id"] for clip in proposed_clips], revision=proposal["revision"] + 1,
                appliedSelections=[{
                    "segmentId": segment["id"], "candidateId": segment["selectedCandidateId"],
                    "clipId": clip["id"], "assetId": clip["assetId"],
                    "inPoint": clip["inPoint"], "duration": clip["duration"],
                } for segment, clip in zip(proposal["segments"], proposed_clips)],
            )
            analysis = task["variant"].setdefault("reframeAnalysis", {})
            adopted_pending = []
            for clip in proposed_clips:
                record = analysis.get(clip["id"])
                identity = record.get("identity") if isinstance(record, dict) else None
                if (record is not None and record.get("proposalId") == proposal_id
                        and record.get("status") in ("queued", "running") and isinstance(identity, dict)
                        and all(clip.get(key) == identity.get(key) for key in ("assetId", "inPoint", "duration", "speed"))):
                    record["adopted"] = True
                    adopted_pending.append(clip["id"])
            # Every adopted or reused clip is proofed again outside the lock.
            # schedule_reframes retains a matching active draft without adding
            # a duplicate job, but schedules a new one when its source changed.
            missing = [clip["id"] for clip in proposed_clips]
            staged_state = state
            response = {"proposal": deepcopy(proposal), "variant": public_task(project_id, task)["variant"]}
        if missing:
            schedule_reframes(project_id, task_id, missing, staged_state=staged_state, expected_state=expected_state,
                              reuse_existing=True)
        else:
            commit_staged_batch_state(project_id, expected_state, staged_state)
        with lock:
            task = task_for(state_for(project_id), task_id)
            return {"proposal": deepcopy(montage_proposal_for(task, proposal_id)), "variant": public_task(project_id, task)["variant"]}

    def process_voiceover(project_id, task_id, job_id):
        directory = store.path(project_id, "voiceovers", job_id, task_id)
        try:
            with lock:
                state = state_for(project_id)
                task = task_for(state, task_id)
                voice = start_voiceover(task, job_id)
                if voice is None:
                    return
                save(project_id, state)
                snapshot, plan, voice_id = deepcopy(task), deepcopy(voice["plan"]), voice["voiceId"]
            directory.mkdir(parents=True, exist_ok=True)
            outputs = voice_service.synthesize(plan["texts"], voice_id, directory, project_id=project_id)
            if len(outputs) != len(plan["texts"]):
                raise ValueError("配音段落数量无效，请重试。")
            new_assets = []
            for index, output in enumerate(outputs):
                regular_file(output)
                asset_id = project_assets.new_id(None)
                new_assets.append({"id": asset_id, "file": asset_id+".wav", "kind": "audio", "role": "audio",
                                   "name": f"AI 配音 {voice_id} / 第 {index+1} 段", "duration": audio_duration(output),
                                   "notes": plan["texts"][index], "voiceId": voice_id})
            with source_lock or nullcontext(), lock:
                state = state_for(project_id)
                task = task_for(state, task_id)
                assets = assets_for(project_id)
                tracks, cues = arrange_voiceover(task, plan, new_assets, assets)
                # Check current visual sources before publishing generated audio.
                for clip in plan["clips"]:
                    project_assets.file(project_id, assets[clip["assetId"]])
                def publish_assets():
                    project_assets.add_many(project_id, zip(outputs, new_assets))
                complete_voiceover(
                    task, job_id=job_id, expected_version=review_version(snapshot), tracks=tracks, cues=cues,
                    asset_ids=[asset["id"] for asset in new_assets], publish_assets=publish_assets,
                )
                save(project_id, state)
        except Exception as error:
            with lock:
                try:
                    state = state_for(project_id)
                    voice = task_for(state, task_id).get("voiceover")
                    if voice and voice["id"] == job_id and LOCAL_RUN_POLICY.active(voice["status"]):
                        message = str(error) if isinstance(error, ValueError) else "本地配音失败，请检查环境、素材与脚本后重试。"
                        voice.update(status=LOCAL_RUN_POLICY.fail(voice["status"]), error=message)
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
        catalog = voice_service.catalog(project_id) if voice_service else {"available": False, "voices": []}
        if not catalog["available"]:
            raise HTTPException(503, detail={"code": "voice_service_unavailable", "message": "本地配音环境未就绪，请按 README 配置；不会自动下载。"})
        selected_voice = next((voice for voice in catalog["voices"] if voice["id"] == body["voiceId"]), None)
        if selected_voice is None:
            raise HTTPException(422, detail={"code": "voice_unknown", "message": "所选音色不存在。"})
        if selected_voice.get("available") is False:
            raise HTTPException(503, detail={"code": "voice_unavailable", "message": "所选音色的本地模型尚未就绪。"})
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
                production_call(
                    validate_voiceover_target, owner, task,
                    (requested["contentRevision"], requested["variantRevision"], requested["subtitleRevision"]),
                )
                try:
                    plan = plan_voiceover(task, assets)
                    for clip in plan["clips"]:
                        project_assets.file(project_id, assets[clip["assetId"]])
                except (ValueError, OSError) as error:
                    raise HTTPException(422, detail={"code": "batch_voiceover_plan", "message": str(error) if isinstance(error, ValueError) else "画面文件不可用。"}) from None
                targets.append((task, plan))
            job_id = uuid4().hex
            for task, plan in targets:
                queue_voiceover(
                    task, job_id=job_id, voice_id=body["voiceId"], plan=plan,
                    model_revision=("unverified-local-directory" if selected_voice.get("group") == "project"
                                    else catalog.get("modelRevision", "unverified-local-directory")),
                    model=("Qwen3-TTS-12Hz-0.6B-Base-8bit" if selected_voice.get("group") == "project"
                           else "Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"),
                )
            save(project_id, state)
            task_ids = [task["id"] for task, _ in targets]
            def process_batch(pid):
                for target_id in task_ids:
                    process_voiceover(pid, target_id, job_id)
            if not compute_queue.submit("batch-voiceover:"+job_id, project_id, process_batch):
                for task, _ in targets:
                    voice = task["voiceover"]
                    voice.update(status=LOCAL_RUN_POLICY.fail(voice["status"]), error="本地处理队列不可用。")
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
            assets = assets_for(project_id)
            run = production_call(
                submit_batch_preview, task, expected_revision=body["revision"], assets=assets,
                snapshot_sources=lambda run_id, tracks, available:
                    snapshot_sources(project_id, store.path(project_id, "runs", run_id), tracks, available),
            )
            save(project_id, state)
            run_id = run["id"]
            if not compute_queue.submit("batch-preview:" + run_id, project_id, lambda pid: process(pid, task_id, run_id)):
                run["status"] = LOCAL_RUN_POLICY.fail(run["status"])
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
        results = []
        for child in children:
            if child["generation"]["status"] == "failed":
                results.append({"taskId": child["id"], "status": "failed", "error": child["generation"]["error"]})
                continue
            latest = reusable_preview(child)
            if latest is not None:
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
                        snapshot = {"settings": variant["settings"], "tracks": variant["tracks"]}
                        if variant.get("transitions"):
                            snapshot["transitions"] = variant["transitions"]
                        failed = {"id": uuid4().hex, "revision": variant["revision"], "status": "failed", "error": message,
                                  "snapshot": snapshot, "sources": {}}
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
            def preview_available(run):
                try:
                    regular_file(store.path(project_id, "outputs", run["output"]))
                    return True
                except (OSError, ValueError, KeyError):
                    return False
            production_call(
                review_current_version, task, run_id=body["runId"], decision=body["decision"],
                reason=body["reason"], preview_available=preview_available,
            )
            save(project_id, state)
            return {"task": public_task(project_id, task)}

    @router.post("/{task_id}/exports", status_code=202)
    def submit_exports(project_id: str, task_id: str, body: dict[str, Any]):
        project_for(project_id)
        if (not isinstance(body, dict) or set(body) != {"taskIds"} or not isinstance(body["taskIds"], list)
                or not 1 <= len(body["taskIds"]) <= 20 or any(not _valid_id(item) for item in body["taskIds"])
                or len(set(body["taskIds"])) != len(body["taskIds"])):
            raise HTTPException(422, detail={"code": "batch_export_invalid", "message": "请选择 1–20 条不重复的变体。"})
        def preview_available(child, preview):
            try:
                if preview["output"] != preview["id"] + ".mp4":
                    raise ValueError("预览文件关联无效")
                regular_file(store.path(project_id, "outputs", preview["output"]))
                return True
            except (OSError, ValueError, KeyError):
                return False

        # The first production pass reserves any new delivery ids and discovers
        # the exact preview/delivery records that its two-stage callback will
        # consume.  It deliberately performs no media inspection or copying.
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            parent = task_for(state, task_id)
            children = [task_for(state, child_id) for child_id in body["taskIds"]]
            assets = assets_for(project_id)
            candidates: dict[tuple[str, str], tuple[dict, dict]] = {}
            planned_snapshots: dict[tuple[str, str], tuple[dict, dict]] = {}
            planned_ids: list[str] = []

            def reserve_id():
                run_id = uuid4().hex
                planned_ids.append(run_id)
                return run_id

            def reserve_sources(run_id, child, preview, _available):
                planned_snapshots[(child["id"], run_id)] = (deepcopy(child), deepcopy(preview))
                return {}

            production_call(
                export_approved_versions, parent, children, assets=assets,
                snapshot_sources=reserve_sources, preview_available=preview_available,
                reframe_safe=lambda child, run: candidates.setdefault((child["id"], run["id"]),
                                                                        (deepcopy(child), deepcopy(run))),
                id_factory=reserve_id,
            )
        media_cache, frozen_timing_cache, current_timing_cache, proofs, packaging_proofs, candidate_identities = {}, {}, {}, {}, {}, {}
        for key, (child, run) in candidates.items():
            candidate_identities[key] = safety_run_identity(child, run)
            proofs[key] = production_call(
                prepare_reframe_safety, project_id, child, run, assets,
                media_cache=media_cache, frozen_timing_cache=frozen_timing_cache,
                current_timing_cache=current_timing_cache,
            )
            snapshot, cues = run.get("snapshot", {}), run.get("subtitles", run.get("subtitleCues", []))
            proof_key = production_call(packaging_proof_key, snapshot, cues, batch=True)
            if proof_key not in packaging_proofs:
                packaging_proofs[proof_key] = production_call(prepare_packaging_proof, snapshot, cues, batch=True)
            packaging_proofs[key] = packaging_proofs[proof_key]

        def cleanup_prepared_sources(run_ids=None):
            targets = set(run_ids) if run_ids is not None else {run_id for _child_id, run_id in planned_snapshots}
            for planned_run_id in targets:
                shutil.rmtree(store.path(project_id, "exports", planned_run_id), ignore_errors=True)

        source_snapshots = {}
        try:
            for (child_id, run_id), (_child, preview) in planned_snapshots.items():
                try:
                    source_snapshots[(child_id, run_id)] = prepare_approved_source_snapshot(
                        project_id, run_id, preview, assets)
                except BatchProductionError as error:
                    # A source already different from its approved preview is
                    # a durable failed delivery record (the existing contract),
                    # not a lock-free proof/CAS conflict.  A change *during*
                    # proof preparation remains retryable and must escape.
                    if error.code != "batch_asset_unavailable":
                        raise
                    if getattr(error, "snapshot_copy_failed", False):
                        raise
                    source_snapshots[(child_id, run_id)] = error
        except BatchProductionError as error:
            cleanup_prepared_sources()
            raise HTTPException(409, detail={"code": error.code, "message": error.message}) from None
        except BaseException:
            cleanup_prepared_sources()
            raise
        reserved_ids = iter(planned_ids)

        with source_lock or nullcontext(), lock:
            try:
                state = state_for(project_id)
                parent = task_for(state, task_id)
                children = [task_for(state, child_id) for child_id in body["taskIds"]]
            except BaseException:
                cleanup_prepared_sources()
                raise

            # Report proof staleness as a retryable CAS conflict before the
            # production lifecycle can collapse it into a generic approval error.
            current_children = {child["id"]: child for child in children}
            for (child_id, run_id), identity in candidate_identities.items():
                child = current_children.get(child_id)
                current_run = next((item for item in child["variant"]["runs"]
                                    if item["id"] == run_id), None) if child else None
                if current_run is None and child is not None:
                    current_run = next((item for item in child["variant"].get("exports", [])
                                        if item["id"] == run_id), None)
                if current_run is None or identity != safety_run_identity(child, current_run):
                    cleanup_prepared_sources()
                    raise HTTPException(409, detail={"code": "batch_export_reframe_conflict",
                                                     "message": "主体构图验证期间版本已变化，请刷新后重试。"})

            def export_sources(run_id, child, preview, available):
                proof = source_snapshots.get((child["id"], run_id))
                if isinstance(proof, BatchProductionError):
                    raise proof
                if proof is None:
                    raise BatchProductionError("batch_export_reframe_conflict", "导出准备已变化，请刷新后重试。")
                consume_source_snapshot(project_id, preview, proof, available)
                return proof["sources"]

            def actual_id():
                try:
                    return next(reserved_ids)
                except StopIteration:
                    raise BatchProductionError("batch_export_reframe_conflict", "导出准备已变化，请刷新后重试。") from None

            try:
                assets = assets_for(project_id)
            except BaseException:
                cleanup_prepared_sources()
                raise
            def consume_export_proofs(child, run):
                consume_reframe_safety(project_id, child, run, proofs.get((child["id"], run["id"])), assets)
                consume_packaging_proof(packaging_proofs.get((child["id"], run["id"])), run.get("snapshot", {}),
                                        run.get("subtitles", run.get("subtitleCues", [])), batch=True)

            try:
                prepared = production_call(
                    export_approved_versions, parent, children, assets=assets,
                    snapshot_sources=export_sources, preview_available=preview_available,
                    reframe_safe=consume_export_proofs,
                    id_factory=actual_id,
                )
            except BaseException:
                cleanup_prepared_sources()
                raise
            used_snapshot_ids = {run["id"] for _child, run, created in prepared if created}
            cleanup_prepared_sources({planned_run_id for _child_id, planned_run_id in source_snapshots
                                      if planned_run_id not in used_snapshot_ids})
            results = []
            persisted_snapshot_ids = set()
            try:
                created_runs = [(child, run) for child, run, created in prepared if created]
                if created_runs:
                    # export_approved_versions appended every created run before
                    # this first atomic save, so it persists all their source
                    # directories together rather than one loop iteration at a time.
                    save(project_id, state)
                    persisted_snapshot_ids.update(used_snapshot_ids)
                for child, run, created in prepared:
                    if not created:
                        results.append({"taskId": child["id"], "runId": run["id"], "status": run["status"]})
                        continue
                    if run["status"] == "queued" and not compute_queue.submit("batch-export:" + run["id"], project_id,
                                                                              lambda pid, cid=child["id"], rid=run["id"]: process_export(pid, cid, rid)):
                        run.update(status=LOCAL_RUN_POLICY.fail(run["status"]), error="本地处理队列不可用。")
                        save(project_id, state)
                    results.append({"taskId": child["id"], "runId": run["id"], "status": run["status"], "error": run["error"]})
            except BaseException:
                cleanup_prepared_sources({planned_run_id for _child_id, planned_run_id in source_snapshots
                                          if planned_run_id not in persisted_snapshot_ids})
                raise
            return {"results": results}

    @router.post("/{task_id}/exports/{run_id}/cancel")
    def cancel_export(project_id: str, task_id: str, run_id: str):
        project_for(project_id)
        with lock:
            state = state_for(project_id)
            task = task_for(state, task_id)
            run = find_export(task["variant"], run_id)
            if LOCAL_RUN_POLICY.active(run["status"]):
                run.update(status=LOCAL_RUN_POLICY.cancel(run["status"]), error="成片导出已取消。")
                save(project_id, state)
            elif run["status"] != "cancelled":
                raise HTTPException(409, detail={"code": "batch_export_not_active", "message": "此成片任务已结束，无法取消。"})
            return {"task": public_task(project_id, task)}

    @router.get("/{task_id}/exports/{run_id}/output")
    def download_export(project_id: str, task_id: str, run_id: str):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            task = task_for(state_for(project_id), task_id)
            run = find_export(task["variant"], run_id)
            if run["status"] != "completed":
                raise HTTPException(409, detail={"code": "batch_export_unavailable", "message": "成片尚不可用。"})
            task, run, assets = deepcopy(task), deepcopy(run), deepcopy(assets_for(project_id))
            delivery_identity = safety_run_identity(task, run)
        try:
            proof = prepare_reframe_safety(project_id, task, run, assets, media_cache={}, frozen_timing_cache={},
                                           current_timing_cache={})
            packaging_proof = prepare_packaging_proof(run.get("snapshot", {}), run.get("subtitles", []), batch=True)
        except BatchProductionError as error:
            raise HTTPException(409, detail={"code": error.code, "message": error.message}) from None
        with source_lock or nullcontext(), lock:
            task = task_for(state_for(project_id), task_id)
            run = find_export(task["variant"], run_id)
            if run["status"] != "completed":
                raise HTTPException(409, detail={"code": "batch_export_unavailable", "message": "成片尚不可用。"})
            try:
                consume_reframe_safety(project_id, task, run, proof, assets_for(project_id))
                if delivery_identity != safety_run_identity(task, run):
                    raise BatchProductionError("batch_packaging_conflict", "包装验证期间交付身份已变化，请刷新后重试。")
                consume_packaging_proof(packaging_proof, run.get("snapshot", {}), run.get("subtitles", []), batch=True)
                if not current_delivery_output(task, run):
                    raise HTTPException(409, detail={"code": "batch_export_stale", "message": "成片不是当前已审核版本，请重新预览并导出。"})
                if run["output"] != run_id + ".mp4":
                    raise ValueError("成片关联无效")
                path = store.path(project_id, "deliverables", run["output"])
                regular_file(path)
            except BatchProductionError as error:
                raise HTTPException(409, detail={"code": error.code, "message": error.message}) from None
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
            if LOCAL_RUN_POLICY.active(run["status"]):
                run.update(status=LOCAL_RUN_POLICY.cancel(run["status"]), error="预览已取消。")
                save(project_id, state)
            elif run["status"] != "cancelled":
                raise HTTPException(409, detail={"code": "batch_preview_not_active", "message": "此预览已结束，无法取消。"})
            return {"variant": public_task(project_id, task)["variant"]}

    @router.get("/{task_id}/variant/previews/{run_id}/output")
    def download_preview(project_id: str, task_id: str, run_id: str):
        project_for(project_id)
        with lock:
            task = task_for(state_for(project_id), task_id)
            run = find_run(task["variant"], run_id)
            if run["status"] != "completed":
                raise HTTPException(409, detail={"code": "batch_preview_unavailable", "message": "预览尚不可用。"})
            if not current_preview_output(task, run):
                raise HTTPException(409, detail={"code": "batch_preview_stale", "message": "预览不是当前版本，请重新生成。"})
            try:
                snapshot, cues = deepcopy(run.get("snapshot", {})), deepcopy(run.get("subtitleCues", []))
                preview_identity = safety_run_identity(task, deepcopy(run))
            except (OSError, ValueError, KeyError):
                raise HTTPException(409, detail={"code": "batch_preview_unavailable", "message": "预览文件不可用。"}) from None
        try:
            packaging_proof = prepare_packaging_proof(snapshot, cues, batch=True)
        except BatchProductionError as error:
            raise HTTPException(409, detail={"code": error.code, "message": error.message}) from None
        with lock:
            task = task_for(state_for(project_id), task_id)
            run = find_run(task["variant"], run_id)
            if run["status"] != "completed" or not current_preview_output(task, run):
                raise HTTPException(409, detail={"code": "batch_preview_stale", "message": "预览不是当前版本，请重新生成。"})
            try:
                if preview_identity != safety_run_identity(task, run):
                    raise BatchProductionError("batch_packaging_conflict", "包装验证期间预览身份已变化，请刷新后重试。")
                consume_packaging_proof(packaging_proof, run.get("snapshot", {}), run.get("subtitleCues", []), batch=True)
                output = run["output"]
                if output != run_id + ".mp4":
                    raise ValueError("输出关联无效")
                path = store.path(project_id, "outputs", output)
                regular_file(path)
            except BatchProductionError as error:
                raise HTTPException(409, detail={"code": error.code, "message": error.message}) from None
            except (OSError, ValueError, KeyError):
                raise HTTPException(409, detail={"code": "batch_preview_unavailable", "message": "预览文件不可用。"}) from None
        return FileResponse(path, media_type="video/mp4", filename="preview.mp4")

    return router
