"""Application commands for versioned batch-video production."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Literal
from uuid import uuid4

from .durable_runs import LOCAL_RUN_POLICY, RunConflict, freeze_run_input
from .timeline import validate_workspace
from .video_aspect import PRODUCT_PRESETS, ratio_label, resolve_product_aspect, valid_aspect_mode


class BatchProductionError(ValueError):
    """A business-rule failure that an HTTP adapter may translate."""

    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


LifecycleStatus = Literal[
    "absent", "active", "failed", "current", "stale", "pending", "approved", "rejected"
]


@dataclass(frozen=True)
class LifecycleFact:
    status: LifecycleStatus
    reason: str | None = None
    record: dict[str, Any] | None = None


@dataclass(frozen=True)
class VariantLifecycle:
    voice: LifecycleFact
    preview: LifecycleFact
    review: LifecycleFact
    delivery: LifecycleFact


def _new_id() -> str:
    return uuid4().hex


def subtitles_for(variant: dict[str, Any]) -> dict[str, Any]:
    return variant.setdefault("subtitles", {"revision": 0, "cues": [], "recognitions": []})


def exports_for(variant: dict[str, Any]) -> list[dict[str, Any]]:
    return variant.setdefault("exports", [])


def current_version(task: dict[str, Any]) -> tuple[int, int, int]:
    variant = task["variant"]
    return (task.get("contentRevision", 0), variant["revision"],
            variant.get("subtitles", {}).get("revision", 0))


def _voice_is_current(task: dict[str, Any]) -> bool:
    return _voice_fact(task).status in ("absent", "current")


def frozen_edit_snapshot(task: dict[str, Any]) -> dict[str, Any]:
    variant = task["variant"]
    content_revision, variant_revision, subtitle_revision = current_version(task)
    return {
        "contentRevision": content_revision,
        "variantRevision": variant_revision,
        "subtitleRevision": subtitle_revision,
        "snapshot": {"settings": deepcopy(variant["settings"]), "tracks": deepcopy(variant["tracks"])},
        "subtitleCues": deepcopy(variant.get("subtitles", {}).get("cues", [])),
    }


def _voice_fact(task: dict[str, Any]) -> LifecycleFact:
    voice = task.get("voiceover")
    if voice is None:
        return LifecycleFact("absent")
    if voice.get("status") in ("queued", "running"):
        return LifecycleFact("active", "voiceover_active", voice)
    if voice.get("status") == "failed":
        return LifecycleFact("failed", "voiceover_failed", voice)
    if (voice.get("status") == "completed"
            and voice.get("contentRevision", 0) == task.get("contentRevision", 0)):
        return LifecycleFact("current", record=voice)
    return LifecycleFact("stale", "voiceover_stale")


def _preview_fact(task: dict[str, Any], voice: LifecycleFact) -> LifecycleFact:
    variant = task["variant"]
    runs = variant.get("runs", [])
    if not runs:
        return LifecycleFact("absent")
    run = runs[-1]
    if run.get("status") in ("queued", "running"):
        return LifecycleFact("active", "preview_active", run)
    if run.get("status") == "failed":
        return LifecycleFact("failed", "preview_failed", run)
    frozen = frozen_edit_snapshot(task)
    if (run.get("status") != "completed"
            or voice.status not in ("absent", "current")
            or (run.get("contentRevision", 0), run.get("revision"), run.get("subtitleRevision", 0))
            != current_version(task)
            or run.get("snapshot") != frozen["snapshot"]
            or run.get("subtitleCues", []) != frozen["subtitleCues"]):
        return LifecycleFact("stale", "preview_stale")
    return LifecycleFact("current", record=run)


def _review_fact(task: dict[str, Any], preview: LifecycleFact) -> LifecycleFact:
    reviews = task.get("reviews", [])
    if not reviews:
        return LifecycleFact("pending")
    latest = reviews[-1]
    if (preview.status != "current" or preview.record is None
            or latest.get("runId") != preview.record.get("id")
            or (latest.get("contentRevision", 0), latest.get("variantRevision", 0),
                latest.get("subtitleRevision", 0)) != current_version(task)
            or latest.get("decision") not in ("approved", "rejected")):
        return LifecycleFact("stale", "review_stale")
    return LifecycleFact(latest["decision"], record=latest)


def _delivery_fact(task: dict[str, Any], preview: LifecycleFact, review: LifecycleFact) -> LifecycleFact:
    exports = task["variant"].get("exports", [])
    if not exports:
        return LifecycleFact("absent")
    version = current_version(task)
    if review.status == "approved" and preview.status == "current" and preview.record is not None:
        current = next((delivery for delivery in reversed(exports)
                        if delivery.get("status") == "completed"
                        and (delivery.get("contentRevision", 0), delivery.get("variantRevision", 0),
                             delivery.get("subtitleRevision", 0)) == version
                        and delivery.get("previewRunId") == preview.record.get("id")), None)
        if current is not None:
            return LifecycleFact("current", record=current)
    latest = exports[-1]
    if latest.get("status") in ("queued", "running"):
        return LifecycleFact("active", "export_active", latest)
    if latest.get("status") == "failed":
        return LifecycleFact("failed", "export_failed", latest)
    return LifecycleFact("stale", "export_stale")


def inspect_variant(task: dict[str, Any]) -> VariantLifecycle:
    voice = _voice_fact(task)
    preview = _preview_fact(task, voice)
    review = _review_fact(task, preview)
    delivery = _delivery_fact(task, preview, review)
    return VariantLifecycle(voice=voice, preview=preview, review=review, delivery=delivery)


def reusable_preview(task: dict[str, Any]) -> dict[str, Any] | None:
    """Return the latest preview that the bulk command may reuse."""
    runs = task["variant"].get("runs", [])
    if not runs:
        return None
    latest = runs[-1]
    if (latest.get("status") in ("queued", "running", "completed")
            and (latest.get("contentRevision", 0), latest.get("revision"),
                 latest.get("subtitleRevision", 0)) == current_version(task)):
        return latest
    return None


def review_status(task: dict[str, Any]) -> str:
    return inspect_variant(task).review.status


def create_batch(selling_point: str, script: str, aspect: dict[str, Any] | None = None,
                 *, id_factory: Callable[[], str] = _new_id) -> dict[str, Any]:
    aspect = aspect or {"mode": "9:16", "resolvedAspect": "9:16", "width": 720, "height": 1280,
                        "reason": "商品制作默认使用 9:16。"}
    return {
        "id": id_factory(), "sellingPoint": selling_point.strip(), "script": script.strip(),
        "contentRevision": 0, "reviews": [],
        "variant": {
            "id": id_factory(), "revision": 0,
            "aspectMode": aspect["mode"], "resolvedAspect": aspect["resolvedAspect"],
            "aspectReason": aspect["reason"],
            "settings": {"width": aspect["width"], "height": aspect["height"], "fps": 30},
            "tracks": [
                {"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": []},
                {"id": "audio", "name": "声音", "kind": "audio", "muted": False, "hidden": False, "clips": []},
            ],
            "subtitles": {"revision": 0, "cues": [], "recognitions": []},
            "runs": [], "exports": [],
        },
    }


def save_content(task: dict[str, Any], *, expected_revision: int, selling_point: str, script: str) -> bool:
    if expected_revision != task.get("contentRevision", 0):
        raise BatchProductionError("batch_content_conflict", "脚本已更新，请刷新后重试。")
    content = (selling_point.strip(), script.strip())
    if content == (task["sellingPoint"], task["script"]):
        return False
    task.update(sellingPoint=content[0], script=content[1],
                contentRevision=task.get("contentRevision", 0) + 1)
    task.pop("proposal", None)
    return True


def create_batch_variants(parent: dict[str, Any], items: list[dict[str, str]], asset_ids: list[str], *,
                          assets: dict[str, dict[str, Any]],
                          build_proposal: Callable[[dict[str, Any], list[str]], dict[str, Any]],
                          id_factory: Callable[[], str] = _new_id) -> tuple[str, list[dict[str, Any]]]:
    if parent.get("batchId") is not None:
        raise BatchProductionError("batch_bulk_parent_invalid", "请在原批量任务中创建变体。", 422)
    if sum(assets.get(asset_id, {}).get("kind") in ("video", "image") for asset_id in asset_ids) < 2:
        raise BatchProductionError("batch_assets_insufficient", "至少选择两段画面素材，再批量生成。", 422)
    inherited = parent["variant"]
    aspect = {
        "mode": inherited.get("aspectMode", "9:16"),
        "resolvedAspect": inherited.get(
            "resolvedAspect", ratio_label(inherited["settings"]["width"], inherited["settings"]["height"])),
        "reason": "继承批次已保存的视频比例。",
        "width": inherited["settings"]["width"], "height": inherited["settings"]["height"],
    }
    generation_id = id_factory()
    created = []
    signatures = set()
    for item in items:
        task = create_batch(item["sellingPoint"], item["script"], aspect, id_factory=id_factory)
        task.update(batchId=parent["id"], generationId=generation_id,
                    generation={"status": "ready", "error": None})
        try:
            proposal = build_proposal(task, asset_ids)
            signature = tuple(clip["assetId"] for clip in proposal["clips"])
            if signature in signatures:
                raise BatchProductionError(
                    "batch_duplicate_recommendation", "推荐镜头与本批另一条相同，请补充不同的脚本或素材描述。", 422)
            task["proposal"] = proposal
            task["variant"]["tracks"][0]["clips"] = proposal["clips"]
            signatures.add(signature)
        except BatchProductionError as error:
            task["generation"] = {"status": "failed", "error": error.message}
        except (OSError, ValueError, KeyError, TypeError):
            task["generation"] = {"status": "failed", "error": "推荐素材文件不可用，请检查或重新导入素材。"}
        created.append(task)
    return generation_id, created


def _aigc_source(generation_id: str, brief: dict[str, Any], item: dict[str, Any],
                 assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    used_ids = list(dict.fromkeys(beat["assetId"] for beat in item["beats"]))
    fact_ids = {fact_id for beat in item["beats"] for fact_id in beat["factIds"]}
    return {
        "candidateId": item["id"], "candidateRevision": item["revision"],
        "briefRevision": brief["revision"], "generationId": generation_id,
        "screenCopySource": "script", "sellingPoint": item["sellingPoint"],
        "brief": deepcopy(brief), "beats": deepcopy(item["beats"]),
        "facts": deepcopy([fact for fact in brief["facts"] if fact["id"] in fact_ids]),
        "assets": [{"id": asset_id, "name": assets[asset_id]["name"], "kind": assets[asset_id]["kind"]}
                   for asset_id in used_ids if asset_id in assets],
    }


def arrange_aigc_candidate(task: dict[str, Any], item: dict[str, Any], assets: dict[str, dict[str, Any]], *,
                           ensure_asset: Callable[[dict[str, Any]], None],
                           id_factory: Callable[[], str] = _new_id) -> None:
    try:
        clips = []
        cues = []
        position = 0.0
        for beat in item["beats"]:
            asset = assets[beat["assetId"]]
            if asset["kind"] not in ("image", "video"):
                raise ValueError("画面素材类型无效")
            ensure_asset(asset)
            duration = min(3.0, asset["duration"]) if asset["kind"] == "video" else 3.0
            clips.append({"id": id_factory(), "assetId": beat["assetId"], "start": position,
                          "inPoint": 0, "duration": duration, "speed": 1, "volume": 1,
                          "fadeIn": 0, "fadeOut": 0})
            cues.append({"id": id_factory(), "start": position, "end": position + duration, "text": beat["text"]})
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
        task["generation"] = {
            "status": "failed", "error": "镜头素材不可用或超出时间线限制，请检查该候选的素材与镜头安排。"}


def apply_aigc_handoff(state: dict[str, Any], generation_id: str, brief: dict[str, Any],
                       candidates: list[dict[str, Any]], assets: dict[str, dict[str, Any]], *,
                       ensure_asset: Callable[[dict[str, Any]], None],
                       id_factory: Callable[[], str] = _new_id) -> tuple[bool, dict[str, Any], list[dict[str, Any]], list[str]]:
    handoff_key = json.dumps([generation_id, brief["revision"],
                              [(item["id"], item["revision"]) for item in candidates]], separators=(",", ":"))
    selected_assets = [assets[asset_id] for asset_id in brief["assetIds"] if asset_id in assets]
    aspect = resolve_product_aspect(brief.get("aspectMode", "9:16"), selected_assets)
    existing = next((task for task in state["tasks"] if task.get("aigcHandoffKey") == handoff_key), None)
    if existing is None:
        for parent in reversed(state["tasks"]):
            if parent.get("aigcGroup") != {"generationId": generation_id, "briefRevision": brief["revision"]}:
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
                    or child["variant"]["subtitles"]["revision"] > 0 or child["variant"]["runs"]
                    or child["variant"].get("exports") or child.get("reviews")):
                skipped.append(child["id"])
                continue
            child["sellingPoint"] = item["sellingPoint"]
            child["script"] = "。".join(beat["text"] for beat in item["beats"])
            child["aigcSource"] = _aigc_source(generation_id, brief, item, assets)
            arrange_aigc_candidate(child, item, assets, ensure_asset=ensure_asset, id_factory=id_factory)
        if all(children_by_candidate[item["id"]]["aigcSource"]["candidateRevision"] == item["revision"]
               for item in candidates):
            existing["aigcHandoffKey"] = handoff_key
        return False, existing, children, skipped

    parent = create_batch(brief["productName"] + "营销视频",
                          "。".join(beat["text"] for beat in candidates[0]["beats"]), aspect,
                          id_factory=id_factory)
    parent["aigcHandoffKey"] = handoff_key
    parent["aigcGroup"] = {"generationId": generation_id, "briefRevision": brief["revision"]}
    batch_generation_id = id_factory()
    children = []
    for item in candidates:
        task = create_batch(item["sellingPoint"], "。".join(beat["text"] for beat in item["beats"]), aspect,
                            id_factory=id_factory)
        task.update(batchId=parent["id"], generationId=batch_generation_id,
                    generation={"status": "ready", "error": None},
                    aigcSource=_aigc_source(generation_id, brief, item, assets))
        arrange_aigc_candidate(task, item, assets, ensure_asset=ensure_asset, id_factory=id_factory)
        children.append(task)
    state["tasks"].extend([parent, *children])
    return True, parent, children, []


def save_subtitles(task: dict[str, Any], *, expected_revision: int, timeline_revision: int, cues: list,
                   validate_cues: Callable[[list, list], list]) -> dict[str, Any]:
    variant = task["variant"]
    subtitles = subtitles_for(variant)
    if timeline_revision != variant["revision"]:
        raise BatchProductionError("batch_variant_conflict", "时间线已更新，请刷新后再保存字幕。")
    if expected_revision != subtitles["revision"]:
        raise BatchProductionError("batch_subtitles_conflict", "字幕已更新，请刷新后重试。")
    try:
        saved_cues = validate_cues(cues, variant["tracks"])
    except ValueError as error:
        raise BatchProductionError("batch_subtitles_invalid", str(error), 422) from None
    subtitles["cues"] = saved_cues
    subtitles["revision"] += 1
    return subtitles


def queue_voiceover(task: dict[str, Any], *, job_id: str, voice_id: str, plan: dict[str, Any],
                    model_revision: str) -> dict[str, Any]:
    variant = task["variant"]
    variant["revision"] += 1
    voiceover = {
        "id": job_id, "voiceId": voice_id, "status": LOCAL_RUN_POLICY.initial_status, "error": None,
        "contentRevision": task.get("contentRevision", 0), "variantRevision": variant["revision"],
        "subtitleRevision": subtitles_for(variant)["revision"],
        "model": "Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit", "modelRevision": model_revision,
        "plan": plan, "assetIds": [],
    }
    task["voiceover"] = voiceover
    return voiceover


def validate_voiceover_target(owner: dict[str, Any], task: dict[str, Any],
                              requested_version: tuple[int, int, int]) -> None:
    if task["id"] != owner["id"] and task.get("batchId") != owner["id"]:
        raise BatchProductionError("batch_voiceover_scope", "只能处理当前任务或它的批量变体。", 422)
    if current_version(task) != requested_version:
        raise BatchProductionError("batch_voiceover_conflict", "脚本、镜头或字幕已更新，请保存或刷新后重新确认。")
    voice_status = task.get("voiceover", {}).get("status")
    if voice_status is not None and LOCAL_RUN_POLICY.active(voice_status):
        raise BatchProductionError("batch_voiceover_active", "配音正在处理中。")


def start_voiceover(task: dict[str, Any], job_id: str) -> dict[str, Any] | None:
    voice = task.get("voiceover")
    if not voice or voice["id"] != job_id or voice["status"] != "queued":
        return None
    if current_version(task) != (voice["contentRevision"], voice["variantRevision"], voice["subtitleRevision"]):
        raise BatchProductionError(
            "batch_voiceover_stale", "脚本、镜头或字幕已修改，未覆盖新内容，请重新生成配音。")
    voice["status"] = LOCAL_RUN_POLICY.start(voice["status"])
    return voice


def complete_voiceover(task: dict[str, Any], *, job_id: str, expected_version: tuple[int, int, int],
                       tracks: list[dict[str, Any]], cues: list[dict[str, Any]], asset_ids: list[str],
                       publish_assets: Callable[[], None]) -> dict[str, Any]:
    voice = task.get("voiceover")
    if not voice or voice["id"] != job_id:
        raise BatchProductionError(
            "batch_voiceover_stale", "脚本、镜头或字幕已修改，未覆盖新内容，请重新生成配音。")
    try:
        completion = LOCAL_RUN_POLICY.complete(
            voice["status"],
            frozen_input=freeze_run_input(expected_version),
            current_input=current_version(task),
            result={"assetIds": asset_ids},
        )
    except RunConflict:
        raise BatchProductionError(
            "batch_voiceover_stale", "脚本、镜头或字幕已修改，未覆盖新内容，请重新生成配音。") from None
    publish_assets()
    variant = task["variant"]
    variant["tracks"] = tracks
    variant["revision"] += 1
    subtitles = subtitles_for(variant)
    subtitles["cues"] = cues
    subtitles["revision"] += 1
    voice.update(status=completion.status, error=None, variantRevision=variant["revision"],
                 subtitleRevision=subtitles["revision"], assetIds=completion.result["assetIds"])
    voice.pop("plan", None)
    task.pop("proposal", None)
    return voice


def save_variant(task: dict[str, Any], *, expected_revision: int, tracks: list[dict[str, Any]],
                 settings: dict[str, Any], aspect_mode: str | None, assets: dict[str, dict[str, Any]],
                 validate_subtitles: Callable[[list, list], Any] | None = None,
                 aspect_was_explicit: bool = True) -> dict[str, Any]:
    variant = task["variant"]
    if type(expected_revision) is not int or expected_revision != variant["revision"]:
        raise BatchProductionError("batch_variant_conflict", "变体已更新，请刷新后重试。")
    try:
        settings, tracks = validate_workspace(
            {"revision": variant["revision"], "settings": settings, "tracks": tracks}, assets)
        if settings["fps"] != 30:
            raise ValueError("批量成片使用 30 帧/秒")
        mode = aspect_mode
        if mode is None:
            saved_ratio = ratio_label(settings["width"], settings["height"])
            mode = saved_ratio if saved_ratio in PRODUCT_PRESETS else "smart"
        if not valid_aspect_mode(mode):
            raise ValueError("视频比例无效")
        resolution = None
        if mode == "smart":
            used_ids = {clip["assetId"] for track in tracks if track["kind"] == "video" and not track["hidden"]
                        for clip in track["clips"]}
            resolution = resolve_product_aspect(mode, [assets[asset_id] for asset_id in used_ids if asset_id in assets])
            if (settings["width"], settings["height"]) != (resolution["width"], resolution["height"]):
                raise ValueError("输出尺寸与智能解析结果不一致")
            resolved = resolution["resolvedAspect"]
        else:
            resolved = ratio_label(settings["width"], settings["height"])
            if (settings["width"], settings["height"]) != PRODUCT_PRESETS[mode]:
                raise ValueError("输出尺寸与所选视频比例不一致")
        if validate_subtitles is not None:
            validate_subtitles(subtitles_for(variant)["cues"], tracks)
    except (KeyError, TypeError, ValueError) as error:
        raise BatchProductionError("batch_variant_invalid", str(error), 422) from None
    variant.update(tracks=tracks, settings=settings, aspectMode=mode, resolvedAspect=resolved)
    variant["aspectReason"] = (resolution["reason"] if resolution else
                               ("单条作品覆盖批次视频比例。" if aspect_was_explicit else
                                variant.get("aspectReason", "沿用作品已保存的输出尺寸。")))
    variant["revision"] += 1
    if task.get("generation", {}).get("status") == "failed" and any(
        track["kind"] == "video" and not track["hidden"] and track["clips"] for track in tracks
    ):
        task["generation"] = {"status": "ready", "error": None}
    return variant


def submit_preview(task: dict[str, Any], *, expected_revision: int, assets: dict[str, dict[str, Any]],
                   snapshot_sources: Callable[[str, list, dict], dict[str, str]],
                   id_factory: Callable[[], str] = _new_id) -> dict[str, Any]:
    variant = task["variant"]
    if not _voice_is_current(task):
        raise BatchProductionError("batch_voiceover_stale", "配音未完成或脚本已修改，请按当前脚本重新生成配音。")
    if expected_revision != variant["revision"]:
        raise BatchProductionError("batch_variant_conflict", "请先保存当前变体。")
    if any(LOCAL_RUN_POLICY.active(run["status"]) for run in variant["runs"]):
        raise BatchProductionError("batch_preview_active", "此变体已有预览任务。")
    try:
        settings, tracks = validate_workspace(
            {"revision": variant["revision"], "settings": variant["settings"], "tracks": variant["tracks"]}, assets)
    except (KeyError, TypeError, ValueError) as error:
        raise BatchProductionError("batch_variant_invalid", str(error), 422) from None
    if not any(track["kind"] == "video" and not track["hidden"] and track["clips"] for track in tracks):
        raise BatchProductionError("batch_video_required", "请先添加画面片段。", 422)
    run_id = id_factory()
    frozen = frozen_edit_snapshot(task)
    frozen["snapshot"] = {"settings": settings, "tracks": tracks}
    run = {
        "id": run_id, "revision": frozen["variantRevision"],
        "contentRevision": frozen["contentRevision"], "subtitleRevision": frozen["subtitleRevision"],
        "subtitleCues": frozen["subtitleCues"], "snapshot": frozen["snapshot"],
        "status": LOCAL_RUN_POLICY.initial_status, "error": None,
        "sources": snapshot_sources(run_id, tracks, assets),
    }
    variant["runs"].append(run)
    return run


def review_current_version(task: dict[str, Any], *, run_id: str, decision: str, reason: str,
                           preview_available: Callable[[dict[str, Any]], bool],
                           id_factory: Callable[[], str] = _new_id) -> dict[str, Any]:
    if not _voice_is_current(task):
        raise BatchProductionError("batch_voiceover_stale", "配音未完成或脚本已修改，请按当前脚本重新生成配音。")
    variant = task["variant"]
    run = next((item for item in variant["runs"] if item["id"] == run_id), None)
    if run is None:
        raise BatchProductionError("batch_preview_missing", "预览不存在。", 404)
    version = current_version(task)
    current_preview = inspect_variant(task).preview
    if current_preview.status != "current" or current_preview.record is not run:
        raise BatchProductionError("batch_review_preview_stale", "请先完成当前版本的预览。")
    if not preview_available(run):
        raise BatchProductionError("batch_review_preview_unavailable", "预览文件不可用，请重新生成。")
    review = {
        "id": id_factory(), "runId": run["id"], "decision": decision, "reason": reason.strip(),
        "contentRevision": version[0], "variantRevision": version[1], "subtitleRevision": version[2],
    }
    task.setdefault("reviews", []).append(review)
    return review


def _approved_export_context(task: dict[str, Any], preview_available: Callable[[dict[str, Any]], bool]):
    variant = task["variant"]
    version = current_version(task)
    lifecycle = inspect_variant(task)
    review = lifecycle.review.record
    preview = lifecycle.preview.record
    frozen = frozen_edit_snapshot(task)
    if (lifecycle.review.status != "approved" or review is None or preview is None
            or preview["status"] != "completed" or variant["settings"].get("fps") != 30
            or preview["snapshot"] != frozen["snapshot"]
            or preview.get("subtitleCues", []) != frozen["subtitleCues"]
            or (preview.get("contentRevision", 0), preview["revision"], preview.get("subtitleRevision", 0)) != version):
        raise BatchProductionError("batch_export_not_approved", "仅能导出当前版本已预览并通过审核的变体。")
    if not preview_available(preview):
        raise BatchProductionError("batch_export_preview_unavailable", "当前预览文件不可用，请重新生成。")
    previous = next((delivery for delivery in reversed(exports_for(variant))
                     if delivery.get("status") in ("queued", "running", "completed")
                     and (delivery.get("contentRevision", 0), delivery.get("variantRevision", 0),
                          delivery.get("subtitleRevision", 0)) == version
                     and delivery.get("previewRunId") == preview["id"]), None)
    if previous is not None:
        return version, review, preview, frozen, previous
    return version, review, preview, frozen, None


def export_approved_version(task: dict[str, Any], *, assets: dict[str, dict[str, Any]],
                            snapshot_sources: Callable[[str, dict, dict], dict[str, str]],
                            preview_available: Callable[[dict[str, Any]], bool] = lambda run: True,
                            id_factory: Callable[[], str] = _new_id) -> tuple[dict[str, Any], bool]:
    variant = task["variant"]
    version, review, preview, frozen, previous = _approved_export_context(task, preview_available)
    if previous is not None:
        return previous, False
    run_id = id_factory()
    asset_ids = {clip["assetId"] for track in frozen["snapshot"]["tracks"] for clip in track["clips"]}
    manifest = [{"id": asset_id, "name": assets.get(asset_id, {}).get("name", asset_id),
                 "kind": assets.get(asset_id, {}).get("kind", "unknown")} for asset_id in sorted(asset_ids)]
    run = {
        "id": run_id, "status": LOCAL_RUN_POLICY.initial_status, "error": None,
        "contentRevision": version[0], "variantRevision": version[1], "subtitleRevision": version[2],
        "previewRunId": preview["id"], "review": dict(review),
        "sellingPoint": task["sellingPoint"], "script": task["script"],
        "snapshot": deepcopy(preview["snapshot"]), "subtitles": deepcopy(preview.get("subtitleCues", [])),
        "assets": manifest, "sources": {},
    }
    try:
        run["sources"] = snapshot_sources(run_id, preview, assets)
    except BatchProductionError as error:
        if error.code != "batch_asset_unavailable":
            raise
        run.update(status="failed", error="原素材不可用，无法重新导出。")
    exports_for(variant).append(run)
    return run, True


def export_approved_versions(parent: dict[str, Any], selected: list[dict[str, Any]], *,
                             assets: dict[str, dict[str, Any]],
                             snapshot_sources: Callable[[str, dict, dict, dict], dict[str, str]],
                             preview_available: Callable[[dict[str, Any], dict[str, Any]], bool],
                             id_factory: Callable[[], str] = _new_id) -> list[tuple[dict[str, Any], dict[str, Any], bool]]:
    if "batchId" in parent:
        raise BatchProductionError("batch_export_parent_invalid", "请从批量任务选择变体。", 422)
    for task in selected:
        if task.get("batchId") != parent["id"]:
            raise BatchProductionError("batch_export_selection_invalid", "所选变体不属于此批量任务。", 422)
        _approved_export_context(task, lambda preview, item=task: preview_available(item, preview))

    results = []
    for task in selected:
        run, created = export_approved_version(
            task, assets=assets,
            snapshot_sources=lambda run_id, preview, available, item=task:
                snapshot_sources(run_id, item, preview, available),
            preview_available=lambda preview, item=task: preview_available(item, preview),
            id_factory=id_factory,
        )
        results.append((task, run, created))
    return results
