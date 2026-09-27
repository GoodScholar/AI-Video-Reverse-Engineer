"""Batch production commands own versioned preview, review, and delivery rules."""
from copy import deepcopy

import pytest

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
        "variantRevision": 2, "subtitleRevision": 0})

    delivery, created = export_approved_version(
        task, assets={"shot": {"id": "shot", "name": "镜头", "kind": "video"}},
        snapshot_sources=lambda *_: pytest.fail("复用导出不应重新复制素材"), id_factory=ids("unused"),
    )

    assert delivery["id"] == "delivery" and created is False
