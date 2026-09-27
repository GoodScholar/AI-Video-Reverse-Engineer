"""Pure content-workflow projection behavior."""

import json
from copy import deepcopy

from app.content_workflow import project_content_workflow


def brief(*, revision=1, complete=True):
    return {
        "revision": revision,
        "productName": "晴雨杯" if complete else "",
        "facts": [{"id": "fact-1", "text": "杯盖防泼溅"}] if complete else [],
        "audience": "通勤者",
        "sellingPoints": ["便携"],
        "callToAction": "查看详情",
        "forbiddenPhrases": [],
        "assetIds": ["front", "use"] if complete else [],
        "aspectMode": "9:16",
    }


def candidates(*, revision=0, confirmed=True):
    return [
        {
            "id": f"candidate-{index}",
            "generationId": "generation-1",
            "revision": revision,
            "briefRevision": 1,
            "confirmedRevision": revision if confirmed else None,
            "sellingPoint": f"卖点 {index}",
            "beats": [],
        }
        for index in range(5)
    ]


def variant_task(task_id, *, decision=None, delivered=False, generation_status="ready"):
    settings = {"width": 720, "height": 1280, "fps": 30}
    tracks = [{"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": []}]
    run = {
        "id": f"preview-{task_id}",
        "status": "completed",
        "contentRevision": 0,
        "revision": 0,
        "subtitleRevision": 0,
        "snapshot": {"settings": settings, "tracks": tracks},
        "subtitleCues": [],
        "sources": {},
        "output": f"preview-{task_id}.mp4",
    }
    review = {
        "id": f"review-{task_id}",
        "runId": run["id"],
        "decision": decision,
        "reason": "逐条核对",
        "contentRevision": 0,
        "variantRevision": 0,
        "subtitleRevision": 0,
    }
    delivery = {
        "id": f"export-{task_id}",
        "status": "completed",
        "contentRevision": 0,
        "variantRevision": 0,
        "subtitleRevision": 0,
        "previewRunId": run["id"],
        "review": review,
        "output": f"export-{task_id}.mp4",
    }
    return {
        "id": task_id,
        "batchId": "active-parent",
        "generationId": "batch-generation",
        "generation": {"status": generation_status, "error": "编排失败" if generation_status == "failed" else None},
        "contentRevision": 0,
        "reviews": [review] if decision else [],
        "variant": {
            "id": f"variant-{task_id}",
            "revision": 0,
            "settings": settings,
            "tracks": tracks,
            "subtitles": {"revision": 0, "cues": [], "recognitions": []},
            "runs": [run] if generation_status == "ready" else [],
            "exports": [delivery] if delivered else [],
        },
    }


def active_batch(*children):
    current = candidates()
    key = json.dumps(
        ["generation-1", 1, [(candidate["id"], candidate["revision"]) for candidate in current]],
        separators=(",", ":"),
    )
    parent = {
        "id": "active-parent",
        "aigcHandoffKey": key,
        "aigcGroup": {"generationId": "generation-1", "briefRevision": 1},
    }
    return {"brief": brief(), "candidates": current}, {"tasks": [parent, *children]}


def test_projection_maps_draft_brief_and_confirmed_scripts_to_steps():
    draft = project_content_workflow({"brief": brief(complete=False), "candidates": []}, {"tasks": []}, {"front", "use"})
    brief_ready = project_content_workflow({"brief": brief(), "candidates": []}, {"tasks": []}, {"front", "use"})
    confirmed = project_content_workflow(
        {"brief": brief(), "candidates": candidates()}, {"tasks": []}, {"front", "use"}
    )

    assert draft["stage"] == "draft" and draft["currentStep"] == 0
    assert draft["completedSteps"] == [False, False, False, False]
    assert brief_ready["stage"] == "brief_ready" and brief_ready["currentStep"] == 1
    assert brief_ready["completedSteps"] == [True, False, False, False]
    assert confirmed["stage"] == "scripts_confirmed" and confirmed["currentStep"] == 2
    assert confirmed["completedSteps"] == [True, True, False, False]


def test_projection_requires_exact_handoff_key_after_candidate_revision_changes():
    current = candidates(revision=1)
    stale_key = json.dumps(
        ["generation-1", 1, [(f"candidate-{index}", 0) for index in range(5)]], separators=(",", ":")
    )
    batch = {
        "tasks": [
            {
                "id": "old-parent",
                "aigcHandoffKey": stale_key,
                "aigcGroup": {"generationId": "generation-1", "briefRevision": 1},
            },
            {"id": "old-child", "batchId": "old-parent", "generationId": "batch-generation"},
        ]
    }

    projection = project_content_workflow(
        {"brief": brief(), "candidates": current}, batch, {"front", "use"}
    )

    assert projection["activeBatchId"] is None
    assert projection["stage"] == "scripts_confirmed"


def test_projection_uses_latest_legacy_batch_only_when_aigc_has_no_activity():
    older_child = variant_task("older-child")
    older_child.update(batchId="older-parent", generationId="older-generation")
    older_child["variant"]["runs"] = []
    legacy_child = variant_task("legacy-child")
    legacy_child.update(batchId="legacy-parent", generationId="legacy-generation")
    legacy_child["variant"]["runs"] = []
    batch = {
        "tasks": [
            {"id": "older-parent"},
            older_child,
            {"id": "legacy-parent"},
            legacy_child,
        ]
    }

    projection = project_content_workflow(
        {"brief": brief(complete=False), "candidates": []}, batch, set()
    )

    assert projection["activeBatchId"] == "legacy-parent"
    assert projection["activeGenerationId"] == "legacy-generation"
    assert projection["stage"] == "scripts_confirmed"


def test_current_versions_roll_back_stale_preview_review_and_delivery():
    child = variant_task("changed", decision="approved", delivered=True)
    child["contentRevision"] = 1
    aigc, batch = active_batch(child)

    projection = project_content_workflow(aigc, batch, {"front", "use"})
    item = projection["variants"][0]

    assert item["stage"] == "scripts_confirmed"
    assert {issue["code"] for issue in item["issues"]} >= {"preview_stale", "review_stale", "export_stale"}


def test_mixed_variants_keep_independent_review_and_delivery_stages():
    aigc, batch = active_batch(
        variant_task("approved", decision="approved"),
        variant_task("rejected", decision="rejected"),
        variant_task("pending"),
    )

    projection = project_content_workflow(aigc, batch, {"front", "use"})

    assert projection["stage"] == "review_pending"
    assert [item["stage"] for item in projection["variants"]] == ["approved", "rejected", "review_pending"]
    assert projection["counts"] == {"total": 3, "pending": 1, "approved": 1, "rejected": 1, "delivered": 0, "failed": 0}


def test_current_preview_without_voiceover_remains_reviewable():
    aigc, batch = active_batch(variant_task("silent"))

    projection = project_content_workflow(aigc, batch, {"front", "use"})

    assert projection["variants"][0]["stage"] == "review_pending"
    assert projection["variants"][0]["currentPreviewRunId"] == "preview-silent"


def test_legacy_batch_with_current_preview_without_voiceover_remains_reviewable():
    child = variant_task("legacy-silent")
    child["batchId"] = "legacy-parent"

    projection = project_content_workflow(
        {"brief": brief(complete=False), "candidates": []},
        {"tasks": [{"id": "legacy-parent"}, child]},
        set(),
    )

    assert projection["activeBatchId"] == "legacy-parent"
    assert projection["stage"] == "review_pending"
    assert projection["variants"][0]["currentPreviewRunId"] == "preview-legacy-silent"


def test_failed_variant_reports_issue_without_overwriting_siblings():
    aigc, batch = active_batch(
        variant_task("failed", generation_status="failed"),
        variant_task("approved", decision="approved"),
    )

    projection = project_content_workflow(aigc, batch, {"front", "use"})

    assert projection["counts"]["failed"] == 1
    assert projection["variants"][0]["issues"][0]["code"] == "generation_failed"
    assert projection["variants"][1]["stage"] == "approved"


def test_projection_keeps_voice_ready_after_timeline_or_subtitle_edits():
    child = variant_task("voice-ready")
    child["voiceover"] = {
        "id": "voice", "voiceId": "serena", "status": "completed", "error": None,
        "contentRevision": 0, "variantRevision": 0, "subtitleRevision": 0,
    }
    child["variant"]["revision"] = 1
    child["variant"]["subtitles"]["revision"] = 1
    aigc, batch = active_batch(child)

    projection = project_content_workflow(aigc, batch, {"front", "use"})
    item = projection["variants"][0]

    assert item["stage"] == "voice_ready"
    assert "voiceover_stale" not in {issue["code"] for issue in item["issues"]}
    assert "preview_stale" in {issue["code"] for issue in item["issues"]}


def test_projection_requires_review_for_a_new_preview_of_the_same_version():
    child = variant_task("new-preview", decision="approved")
    latest_preview = deepcopy(child["variant"]["runs"][0])
    latest_preview["id"] = "preview-new-preview-2"
    latest_preview["output"] = "preview-new-preview-2.mp4"
    child["variant"]["runs"].append(latest_preview)
    aigc, batch = active_batch(child)

    projection = project_content_workflow(aigc, batch, {"front", "use"})
    item = projection["variants"][0]

    assert item["stage"] == "review_pending"
    assert item["reviewStatus"] == "stale"
    assert item["currentPreviewRunId"] == "preview-new-preview-2"
    assert "review_stale" in {issue["code"] for issue in item["issues"]}


def test_projection_keeps_delivery_after_the_same_preview_is_reapproved():
    child = variant_task("reapproved", decision="approved", delivered=True)
    latest_review = deepcopy(child["reviews"][0])
    latest_review["id"] = "review-reapproved-2"
    child["reviews"].append(latest_review)
    aigc, batch = active_batch(child)

    projection = project_content_workflow(aigc, batch, {"front", "use"})
    item = projection["variants"][0]

    assert item["stage"] == "delivered"
    assert item["currentExportRunId"] == "export-reapproved"
    assert "export_stale" not in {issue["code"] for issue in item["issues"]}
