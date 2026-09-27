"""Read-only projection of content-production progress from saved facts."""

from __future__ import annotations

import json
from typing import Any, Literal

from .batch_production import current_version as review_version, review_status


ContentWorkflowStage = Literal[
    "draft",
    "brief_ready",
    "scripts_confirmed",
    "voice_ready",
    "preview_ready",
    "review_pending",
    "approved",
    "rejected",
    "delivered",
]


def _step(stage: ContentWorkflowStage) -> tuple[int, list[bool]]:
    if stage == "draft":
        return 0, [False, False, False, False]
    if stage == "brief_ready":
        return 1, [True, False, False, False]
    if stage in ("scripts_confirmed", "voice_ready"):
        return 2, [True, True, False, False]
    return 3, [True, True, True, stage == "delivered"]


def _result(stage: ContentWorkflowStage, generation_id: str | None, batch_id: str | None) -> dict[str, Any]:
    current_step, completed_steps = _step(stage)
    return {
        "stage": stage,
        "currentStep": current_step,
        "completedSteps": completed_steps,
        "activeGenerationId": generation_id,
        "activeBatchId": batch_id,
        "counts": {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "delivered": 0, "failed": 0},
        "variants": [],
        "issues": [],
    }


def _brief_ready(brief: dict[str, Any], visual_asset_ids: set[str]) -> bool:
    selected = brief.get("assetIds", [])
    return bool(
        str(brief.get("productName", "")).strip()
        and brief.get("facts")
        and isinstance(selected, list)
        and len(selected) >= 2
        and all(asset_id in visual_asset_ids for asset_id in selected)
    )


def _current_candidates(aigc_state: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    candidates = aigc_state.get("candidates", [])
    if not candidates:
        return None, []
    generation_id = candidates[-1].get("generationId")
    return generation_id, [candidate for candidate in candidates if candidate.get("generationId") == generation_id]


def _handoff_key(generation_id: str, brief_revision: int, candidates: list[dict[str, Any]]) -> str:
    return json.dumps(
        [generation_id, brief_revision, [(candidate["id"], candidate["revision"]) for candidate in candidates]],
        separators=(",", ":"),
    )


def _legacy_batch(tasks: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    for parent in reversed(tasks):
        if parent.get("batchId") is not None:
            continue
        children = [task for task in tasks if task.get("batchId") == parent.get("id")]
        if children:
            return parent.get("id"), children[-1].get("generationId")
    return None, None


def _voice_current(task: dict[str, Any]) -> bool:
    voice = task.get("voiceover")
    return bool(
        voice
        and voice.get("status") == "completed"
        and (voice.get("contentRevision"), voice.get("variantRevision"), voice.get("subtitleRevision"))
        == review_version(task)
    )


def _current_preview(task: dict[str, Any]) -> dict[str, Any] | None:
    variant = task["variant"]
    runs = variant.get("runs", [])
    if not runs:
        return None
    run = runs[-1]
    voice = task.get("voiceover")
    if voice is not None and not _voice_current(task):
        return None
    if (
        run.get("status") != "completed"
        or (run.get("contentRevision", 0), run.get("revision"), run.get("subtitleRevision", 0))
        != review_version(task)
        or run.get("snapshot") != {"settings": variant["settings"], "tracks": variant["tracks"]}
        or run.get("subtitleCues", []) != variant.get("subtitles", {}).get("cues", [])
    ):
        return None
    return run


def _current_export(task: dict[str, Any], preview: dict[str, Any] | None, status: str) -> dict[str, Any] | None:
    if preview is None or status != "approved":
        return None
    latest_review = task.get("reviews", [])[-1] if task.get("reviews") else None
    if latest_review is None:
        return None
    version = review_version(task)
    for delivery in reversed(task["variant"].get("exports", [])):
        if (
            delivery.get("status") == "completed"
            and (delivery.get("contentRevision"), delivery.get("variantRevision"), delivery.get("subtitleRevision")) == version
            and delivery.get("previewRunId") == preview.get("id")
            and delivery.get("review", {}).get("id") == latest_review.get("id")
        ):
            return delivery
    return None


def _variant_projection(task: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    generation = task.get("generation")
    if generation and generation.get("status") == "failed":
        issues.append({"code": "generation_failed", "message": generation.get("error") or "变体编排失败，请检查后重试。"})

    voice = task.get("voiceover")
    if voice:
        if voice.get("status") in ("queued", "running"):
            issues.append({"code": "voiceover_active", "message": "配音正在处理中。"})
        elif voice.get("status") == "failed":
            issues.append({"code": "voiceover_failed", "message": voice.get("error") or "配音失败，请重试。"})
        elif not _voice_current(task):
            issues.append({"code": "voiceover_stale", "message": "配音未覆盖当前脚本、画面或字幕版本。"})

    variant = task["variant"]
    latest_preview = variant.get("runs", [])[-1] if variant.get("runs") else None
    preview = _current_preview(task)
    if latest_preview:
        if latest_preview.get("status") in ("queued", "running"):
            issues.append({"code": "preview_active", "message": "预览正在生成。"})
        elif latest_preview.get("status") == "failed":
            issues.append({"code": "preview_failed", "message": latest_preview.get("error") or "预览生成失败，请重试。"})
        elif latest_preview.get("status") == "completed" and preview is None:
            issues.append({"code": "preview_stale", "message": "预览未覆盖当前脚本、画布、时间线或字幕版本。"})

    status = review_status(task)
    if status == "stale":
        issues.append({"code": "review_stale", "message": "旧审核已因当前内容变化而失效。"})
    elif status == "rejected":
        issues.append({"code": "review_rejected", "message": "当前版本已退回修改。"})

    exports = variant.get("exports", [])
    latest_export = exports[-1] if exports else None
    delivery = _current_export(task, preview, status)
    if latest_export:
        if latest_export.get("status") in ("queued", "running"):
            issues.append({"code": "export_active", "message": "成片正在导出。"})
        elif latest_export.get("status") == "failed":
            issues.append({"code": "export_failed", "message": latest_export.get("error") or "成片导出失败，请重试。"})
        elif latest_export.get("status") == "completed" and delivery is None:
            issues.append({"code": "export_stale", "message": "历史成片不对应当前已审核版本。"})

    if delivery is not None:
        stage: ContentWorkflowStage = "delivered"
    elif status == "approved":
        stage = "approved"
    elif status == "rejected":
        stage = "rejected"
    elif preview is not None:
        stage = "review_pending"
    elif _voice_current(task):
        stage = "voice_ready"
    else:
        stage = "scripts_confirmed"
    return {
        "taskId": task["id"],
        "stage": stage,
        "reviewStatus": status,
        "currentPreviewRunId": preview.get("id") if preview else None,
        "currentExportRunId": delivery.get("id") if delivery else None,
        "issues": issues,
    }


def _aggregate_stage(variants: list[dict[str, Any]]) -> ContentWorkflowStage:
    if not variants:
        return "scripts_confirmed"
    stages = [variant["stage"] for variant in variants]
    if all(stage == "delivered" for stage in stages):
        return "delivered"
    if all(stage in ("approved", "delivered") for stage in stages):
        return "approved"
    if all(stage == "rejected" for stage in stages):
        return "rejected"
    review_stages = {"review_pending", "approved", "rejected", "delivered"}
    if all(stage in review_stages for stage in stages):
        return "review_pending"
    if any(stage in review_stages for stage in stages):
        return "preview_ready"
    if all(stage == "voice_ready" for stage in stages):
        return "voice_ready"
    return "scripts_confirmed"


def project_content_workflow(
    aigc_state: dict[str, Any],
    batch_state: dict[str, Any],
    visual_asset_ids: set[str],
) -> dict[str, Any]:
    """Derive the current project workflow without persisting duplicate state."""
    brief = aigc_state.get("brief", {})
    generation_id, current = _current_candidates(aigc_state)
    brief_revision = brief.get("revision", 0)
    scripts_confirmed = bool(
        generation_id
        and len(current) == 5
        and all(
            candidate.get("briefRevision") == brief_revision
            and candidate.get("confirmedRevision") == candidate.get("revision")
            for candidate in current
        )
    )
    tasks = batch_state.get("tasks", [])
    active_batch_id = None
    if scripts_confirmed:
        key = _handoff_key(generation_id, brief_revision, current)
        active = next((task for task in reversed(tasks) if task.get("aigcHandoffKey") == key), None)
        active_batch_id = active.get("id") if active else None
    elif not aigc_state.get("candidates"):
        active_batch_id, generation_id = _legacy_batch(tasks)

    if active_batch_id:
        variants = [_variant_projection(task) for task in tasks if task.get("batchId") == active_batch_id]
        stage = _aggregate_stage(variants)
        result = _result(stage, generation_id, active_batch_id)
        result["variants"] = variants
        result["counts"] = {
            "total": len(variants),
            "pending": sum(item["stage"] not in ("approved", "rejected", "delivered") for item in variants),
            "approved": sum(item["stage"] == "approved" for item in variants),
            "rejected": sum(item["stage"] == "rejected" for item in variants),
            "delivered": sum(item["stage"] == "delivered" for item in variants),
            "failed": sum(any(issue["code"] in ("generation_failed", "voiceover_failed", "preview_failed", "export_failed")
                              for issue in item["issues"]) for item in variants),
        }
        result["issues"] = [
            {**issue, "taskId": item["taskId"]}
            for item in variants
            for issue in item["issues"]
        ]
        return result
    if scripts_confirmed:
        return _result("scripts_confirmed", generation_id, None)
    if _brief_ready(brief, visual_asset_ids):
        return _result("brief_ready", generation_id, None)
    return _result("draft", generation_id, None)
