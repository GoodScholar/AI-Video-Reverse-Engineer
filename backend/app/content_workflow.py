"""Read-only projection of content-production progress from saved facts."""

from __future__ import annotations

import json
from typing import Any, Literal


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
        active_batch_id, legacy_generation_id = _legacy_batch(tasks)
        if active_batch_id:
            return _result("scripts_confirmed", legacy_generation_id, active_batch_id)

    if active_batch_id:
        return _result("scripts_confirmed", generation_id, active_batch_id)
    if scripts_confirmed:
        return _result("scripts_confirmed", generation_id, None)
    if _brief_ready(brief, visual_asset_ids):
        return _result("brief_ready", generation_id, None)
    return _result("draft", generation_id, None)
