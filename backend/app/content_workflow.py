"""Read-only projection of content-production progress from saved facts."""

from __future__ import annotations

import json
from typing import Any, Literal

from .batch_production import inspect_variant


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


def _variant_projection(task: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    generation = task.get("generation")
    if generation and generation.get("status") == "failed":
        issues.append({"code": "generation_failed", "message": generation.get("error") or "变体编排失败，请检查后重试。"})

    lifecycle = inspect_variant(task)
    if lifecycle.voice.reason == "voiceover_active":
        issues.append({"code": "voiceover_active", "message": "配音正在处理中。"})
    elif lifecycle.voice.reason == "voiceover_failed":
        record = lifecycle.voice.record or {}
        issues.append({"code": "voiceover_failed", "message": record.get("error") or "配音失败，请重试。"})
    elif lifecycle.voice.reason == "voiceover_stale":
        issues.append({"code": "voiceover_stale", "message": "配音未覆盖当前脚本版本。"})

    if lifecycle.preview.reason == "preview_active":
        issues.append({"code": "preview_active", "message": "预览正在生成。"})
    elif lifecycle.preview.reason == "preview_failed":
        record = lifecycle.preview.record or {}
        issues.append({"code": "preview_failed", "message": record.get("error") or "预览生成失败，请重试。"})
    elif lifecycle.preview.reason == "preview_stale":
        issues.append({"code": "preview_stale", "message": "预览未覆盖当前脚本、画布、时间线或字幕版本。"})

    status = lifecycle.review.status
    if status == "stale":
        issues.append({"code": "review_stale", "message": "旧审核已因当前内容变化而失效。"})
    elif status == "rejected":
        issues.append({"code": "review_rejected", "message": "当前版本已退回修改。"})

    if lifecycle.delivery.reason == "export_active":
        issues.append({"code": "export_active", "message": "成片正在导出。"})
    elif lifecycle.delivery.reason == "export_failed":
        record = lifecycle.delivery.record or {}
        issues.append({"code": "export_failed", "message": record.get("error") or "成片导出失败，请重试。"})
    elif lifecycle.delivery.reason == "export_stale":
        issues.append({"code": "export_stale", "message": "历史成片不对应当前已审核版本。"})

    if lifecycle.delivery.status == "current":
        stage: ContentWorkflowStage = "delivered"
    elif status == "approved":
        stage = "approved"
    elif status == "rejected":
        stage = "rejected"
    elif lifecycle.preview.status == "current":
        stage = "review_pending"
    elif lifecycle.voice.status == "current":
        stage = "voice_ready"
    else:
        stage = "scripts_confirmed"
    return {
        "taskId": task["id"],
        "stage": stage,
        "reviewStatus": status,
        "currentPreviewRunId": lifecycle.preview.record.get("id") if lifecycle.preview.status == "current" else None,
        "currentExportRunId": lifecycle.delivery.record.get("id") if lifecycle.delivery.status == "current" else None,
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
