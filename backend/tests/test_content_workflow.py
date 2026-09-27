"""Pure content-workflow projection behavior."""

import json

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
    batch = {
        "tasks": [
            {"id": "older-parent"},
            {"id": "older-child", "batchId": "older-parent", "generationId": "older-generation"},
            {"id": "legacy-parent"},
            {"id": "legacy-child", "batchId": "legacy-parent", "generationId": "legacy-generation"},
        ]
    }

    projection = project_content_workflow(
        {"brief": brief(complete=False), "candidates": []}, batch, set()
    )

    assert projection["activeBatchId"] == "legacy-parent"
    assert projection["activeGenerationId"] == "legacy-generation"
    assert projection["stage"] == "scripts_confirmed"
