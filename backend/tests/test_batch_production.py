"""Batch production commands own versioned preview, review, and delivery rules."""
from copy import deepcopy

import pytest

from app import batch_production as production
from app.batch_production import (
    BatchProductionError,
    complete_voiceover,
    create_batch,
    create_batch_variants,
    export_approved_version,
    queue_voiceover,
    review_current_version,
    review_status,
    save_content,
    save_subtitles,
    save_variant,
    start_voiceover,
    submit_preview,
    validate_voiceover_target,
)


def ids(*values):
    iterator = iter(values)
    return lambda: next(iterator)


def video_tracks():
    return [
        {"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False,
         "clips": [{"id": "clip", "assetId": "shot", "start": 0, "inPoint": 0, "duration": 2,
                    "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]},
        {"id": "audio", "name": "声音", "kind": "audio", "muted": False, "hidden": False, "clips": []},
    ]


def completed_preview(task, preview_id="preview"):
    content_revision, variant_revision, subtitle_revision = production.current_version(task)
    return {
        "id": preview_id,
        "revision": variant_revision,
        "contentRevision": content_revision,
        "subtitleRevision": subtitle_revision,
        "subtitleCues": deepcopy(task["variant"]["subtitles"]["cues"]),
        "status": "completed",
        "error": None,
        "output": preview_id + ".mp4",
        "snapshot": {
            "settings": deepcopy(task["variant"]["settings"]),
            "tracks": deepcopy(task["variant"]["tracks"]),
        },
        "sources": {"shot": "shot.mp4"},
    }


def approved_review(task, preview, review_id="review"):
    content_revision, variant_revision, subtitle_revision = production.current_version(task)
    return {
        "id": review_id,
        "runId": preview["id"],
        "decision": "approved",
        "reason": "确认",
        "contentRevision": content_revision,
        "variantRevision": variant_revision,
        "subtitleRevision": subtitle_revision,
    }


def test_inspection_keeps_completed_voice_current_across_non_voice_edits():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    task["variant"].update(revision=4, tracks=video_tracks())
    task["variant"]["subtitles"].update(
        revision=2, cues=[{"id": "cue", "start": 0, "end": 1, "text": "人工字幕"}]
    )
    task["voiceover"] = {
        "id": "voice", "voiceId": "serena", "status": "completed", "error": None,
        "contentRevision": 0, "variantRevision": 2, "subtitleRevision": 1,
        "model": "qwen", "modelRevision": "local", "assetIds": ["voice-asset"],
    }

    lifecycle = production.inspect_variant(task)

    assert lifecycle.voice.status == "current"
    assert lifecycle.voice.reason is None
    assert lifecycle.voice.record["id"] == "voice"
    assert lifecycle.preview.status == "absent"


def test_inspection_stales_review_when_a_new_preview_replaces_the_reviewed_artifact():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    task["variant"].update(revision=2, tracks=video_tracks())
    task["variant"]["subtitles"]["revision"] = 1
    first = completed_preview(task, "preview-1")
    second = completed_preview(task, "preview-2")
    task["variant"]["runs"].extend([first, second])
    task["reviews"].append(approved_review(task, first))

    lifecycle = production.inspect_variant(task)

    assert lifecycle.preview.status == "current"
    assert lifecycle.preview.record["id"] == "preview-2"
    assert lifecycle.review.status == "stale"
    assert lifecycle.review.reason == "review_stale"
    assert lifecycle.review.record is None


def test_inspection_keeps_delivery_current_after_the_same_preview_is_reapproved():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    task["variant"].update(revision=2, tracks=video_tracks())
    preview = completed_preview(task)
    first_review = approved_review(task, preview, "review-1")
    latest_review = approved_review(task, preview, "review-2")
    task["variant"]["runs"].append(preview)
    task["reviews"].extend([first_review, latest_review])
    task["variant"]["exports"].append({
        "id": "delivery", "status": "completed", "contentRevision": 0,
        "variantRevision": 2, "subtitleRevision": 0, "previewRunId": "preview",
        "review": first_review, "output": "delivery.mp4",
    })

    lifecycle = production.inspect_variant(task)

    assert lifecycle.review.status == "approved"
    assert lifecycle.review.record["id"] == "review-2"
    assert lifecycle.delivery.status == "current"
    assert lifecycle.delivery.record["id"] == "delivery"


def test_inspection_reads_missing_legacy_revision_fields_as_zero():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    task["variant"]["tracks"] = video_tracks()
    preview = completed_preview(task)
    preview.pop("contentRevision")
    preview.pop("subtitleRevision")
    task["variant"]["runs"].append(preview)
    task["reviews"].append(approved_review(task, preview))

    lifecycle = production.inspect_variant(task)

    assert lifecycle.preview.status == "current"
    assert lifecycle.preview.record["id"] == "preview"
    assert lifecycle.review.status == "approved"


def test_commands_share_one_frozen_edit_snapshot_for_preview_review_and_export():
    task = create_batch("  省时  ", "  操作演示  ", id_factory=ids("task", "variant"))
    assets = {"shot": {"id": "shot", "name": "镜头", "kind": "video", "duration": 4}}

    save_variant(task, expected_revision=0, tracks=video_tracks(), settings={"width": 720, "height": 1280, "fps": 30},
                 aspect_mode="9:16", assets=assets)
    preview = submit_preview(task, expected_revision=1, assets=assets,
                             snapshot_sources=lambda run_id, tracks, available: {"shot": "shot.mp4"},
                             id_factory=ids("preview"))
    preview.update(status="completed", output="preview.mp4")
    review = review_current_version(task, run_id="preview", decision="approved", reason="  内容确认  ",
                                    preview_available=lambda run: True, id_factory=ids("review"))
    delivery, created = export_approved_version(
        task, assets=assets, snapshot_sources=lambda run_id, approved, available: {"shot": "shot.mp4"},
        id_factory=ids("delivery"),
    )

    assert task["id"] == "task" and task["sellingPoint"] == "省时" and task["script"] == "操作演示"
    assert review["reason"] == "内容确认" and review_status(task) == "approved"
    assert created is True
    assert delivery["snapshot"] == preview["snapshot"] == {
        "settings": {"width": 720, "height": 1280, "fps": 30},
        "tracks": video_tracks(),
    }
    assert delivery["snapshot"] is not preview["snapshot"]
    assert delivery["previewRunId"] == "preview"


def test_review_and_export_reject_a_preview_after_the_edit_version_changes():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    assets = {"shot": {"id": "shot", "name": "镜头", "kind": "video", "duration": 4}}
    save_variant(task, expected_revision=0, tracks=video_tracks(), settings={"width": 720, "height": 1280, "fps": 30},
                 aspect_mode="9:16", assets=assets)
    preview = submit_preview(task, expected_revision=1, assets=assets,
                             snapshot_sources=lambda *_: {"shot": "shot.mp4"}, id_factory=ids("preview"))
    preview.update(status="completed", output="preview.mp4")
    review_current_version(task, run_id="preview", decision="approved", reason="内容确认",
                           preview_available=lambda run: True, id_factory=ids("review"))
    task["contentRevision"] += 1

    assert review_status(task) == "stale"
    with pytest.raises(BatchProductionError) as error:
        export_approved_version(task, assets=assets, snapshot_sources=lambda *_: {}, id_factory=ids("delivery"))
    assert error.value.code == "batch_export_not_approved"


def test_saving_changed_content_advances_its_version_and_invalidates_the_review():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    task["reviews"].append({"id": "review", "runId": "missing", "decision": "approved", "reason": "旧审核",
                            "contentRevision": 0, "variantRevision": 0, "subtitleRevision": 0})

    changed = save_content(task, expected_revision=0, selling_point="  更省时  ", script="  新脚本  ")

    assert changed is True
    assert (task["sellingPoint"], task["script"], task["contentRevision"]) == ("更省时", "新脚本", 1)
    assert review_status(task) == "stale"
    with pytest.raises(BatchProductionError) as error:
        save_content(task, expected_revision=0, selling_point="冲突", script="冲突")
    assert error.value.code == "batch_content_conflict"


def test_batch_creation_owns_parent_scope_aspect_inheritance_and_generation_links():
    parent = create_batch("批量", "初稿", {"mode": "16:9", "resolvedAspect": "16:9", "width": 1280,
        "height": 720, "reason": "已选择"}, id_factory=ids("parent", "parent-variant"))
    assets = {"shot-a": {"kind": "video"}, "shot-b": {"kind": "image"}}
    created_ids = ids("generation", "child-1", "variant-1", "child-2", "variant-2")

    generation_id, children = create_batch_variants(
        parent, [{"sellingPoint": "卖点一", "script": "脚本一"}, {"sellingPoint": "卖点二", "script": "脚本二"}],
        ["shot-a", "shot-b"], assets=assets,
        build_proposal=lambda task, selected: {"clips": [{"assetId": selected[0] if task["sellingPoint"] == "卖点一" else selected[1]}]},
        id_factory=created_ids,
    )

    assert generation_id == "generation"
    assert [child["batchId"] for child in children] == ["parent", "parent"]
    assert [child["generationId"] for child in children] == ["generation", "generation"]
    assert [child["variant"]["settings"] for child in children] == [
        {"width": 1280, "height": 720, "fps": 30}, {"width": 1280, "height": 720, "fps": 30}]


def test_subtitle_and_voiceover_commands_own_linked_version_updates():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    task["variant"].update(revision=2, tracks=video_tracks())

    subtitles = save_subtitles(task, expected_revision=0, timeline_revision=2,
                               cues=[{"id": "cue", "start": 0, "end": 1, "text": "确认字幕"}],
                               validate_cues=lambda cues, tracks: cues)
    validate_voiceover_target(task, task, (0, 2, 1))
    queue_voiceover(task, job_id="voice-job", voice_id="voice", plan={"texts": ["操作演示"]},
                    model_revision="local")
    assert start_voiceover(task, "voice-job")["status"] == "running"
    published = []
    complete_voiceover(task, job_id="voice-job", expected_version=(0, 3, 1), tracks=video_tracks(),
                       cues=[{"id": "cue-2", "start": 0, "end": 1, "text": "配音字幕"}],
                       asset_ids=["voice-asset"], publish_assets=lambda: published.append(True))

    assert subtitles["revision"] == 2
    assert published == [True]
    assert task["variant"]["revision"] == 4
    assert task["voiceover"]["variantRevision"] == 4
    assert task["voiceover"]["subtitleRevision"] == 2


def test_export_reuses_the_current_version_delivery_without_copying_sources_again():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    task["variant"].update(revision=2, tracks=video_tracks())
    preview = {
        "id": "preview", "revision": 2, "contentRevision": 0, "subtitleRevision": 0,
        "subtitleCues": [], "status": "completed", "error": None, "output": "preview.mp4",
        "snapshot": {"settings": deepcopy(task["variant"]["settings"]), "tracks": deepcopy(video_tracks())},
        "sources": {"shot": "shot.mp4"},
    }
    task["variant"]["runs"].append(preview)
    task["reviews"].append({"id": "review", "runId": "preview", "decision": "approved", "reason": "确认",
                            "contentRevision": 0, "variantRevision": 2, "subtitleRevision": 0})
    task["variant"]["exports"].append({"id": "delivery", "status": "completed", "contentRevision": 0,
        "variantRevision": 2, "subtitleRevision": 0, "previewRunId": "preview"})

    delivery, created = export_approved_version(
        task, assets={"shot": {"id": "shot", "name": "镜头", "kind": "video"}},
        snapshot_sources=lambda *_: pytest.fail("复用导出不应重新复制素材"), id_factory=ids("unused"),
    )

    assert delivery["id"] == "delivery" and created is False


def test_export_does_not_reuse_a_delivery_from_an_older_preview():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    task["variant"].update(revision=2, tracks=video_tracks())
    first_preview = completed_preview(task, "preview-1")
    first_review = approved_review(task, first_preview, "review-1")
    task["variant"]["runs"].append(first_preview)
    task["reviews"].append(first_review)
    task["variant"]["exports"].append({
        "id": "delivery-1", "status": "completed", "contentRevision": 0,
        "variantRevision": 2, "subtitleRevision": 0, "previewRunId": "preview-1",
        "review": first_review, "output": "delivery-1.mp4",
    })
    current_preview = completed_preview(task, "preview-2")
    task["variant"]["runs"].append(current_preview)
    task["reviews"].append(approved_review(task, current_preview, "review-2"))

    delivery, created = export_approved_version(
        task,
        assets={"shot": {"id": "shot", "name": "镜头", "kind": "video"}},
        snapshot_sources=lambda *_: {"shot": "shot.mp4"},
        id_factory=ids("delivery-2"),
    )

    assert created is True
    assert delivery["id"] == "delivery-2"
    assert delivery["previewRunId"] == "preview-2"


def test_export_reuses_delivery_after_same_preview_is_reapproved():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    task["variant"].update(revision=2, tracks=video_tracks())
    preview = completed_preview(task)
    first_review = approved_review(task, preview, "review-1")
    task["variant"]["runs"].append(preview)
    task["reviews"].extend([first_review, approved_review(task, preview, "review-2")])
    task["variant"]["exports"].append({
        "id": "delivery", "status": "completed", "contentRevision": 0,
        "variantRevision": 2, "subtitleRevision": 0, "previewRunId": "preview",
        "review": first_review, "output": "delivery.mp4",
    })

    delivery, created = export_approved_version(
        task,
        assets={"shot": {"id": "shot", "name": "镜头", "kind": "video"}},
        snapshot_sources=lambda *_: pytest.fail("同一预览重新批准不应重新复制素材"),
        id_factory=ids("unused"),
    )

    assert delivery["id"] == "delivery"
    assert created is False


def test_reusable_preview_preserves_legacy_revision_defaults():
    task = create_batch("省时", "操作演示", id_factory=ids("task", "variant"))
    preview = completed_preview(task)
    preview.pop("contentRevision")
    preview.pop("subtitleRevision")
    task["variant"]["runs"].append(preview)

    reusable = production.reusable_preview(task)

    assert reusable["id"] == "preview"
