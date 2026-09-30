"""Task 2.2 lifecycle contracts not covered by the ASS renderer tests."""
from copy import deepcopy

import pytest

from app import batch_production as production
from app.batch_production import BatchProductionError, create_batch, save_content, save_subtitles, save_variant
from app.montage_packaging import default_packaging


def _assets():
    return {"shot": {"id": "shot", "name": "镜头", "kind": "video", "duration": 4}}


def _tracks(*, reframe=False):
    clip = {"id": "clip", "assetId": "shot", "start": 0, "inPoint": 0, "duration": 2,
            "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}
    if reframe:
        from app.montage_reframe import pending_reframe
        clip["reframe"] = pending_reframe(target_width=720, target_height=1280, reason="待人工")
        clip["reframe"]["override"] = {"mode": "manual", "keyframes": [{"timeSeconds": 0, "x": .5, "y": .5}]}
    return [
        {"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": [clip]},
        {"id": "audio", "name": "声音", "kind": "audio", "muted": False, "hidden": False, "clips": []},
    ]


def _cue(cue_id="recognition-one-line-1", text="确认字幕"):
    return [{"id": cue_id, "start": 0, "end": 1, "text": text}]


def _subtitle_package(cue_id="recognition-one-line-1"):
    package = default_packaging()
    package["elements"] = [{"id": "subtitle", "kind": "subtitle", "enabled": True, "cueId": cue_id,
                            "position": "bottom", "style": "plain"}]
    return package


def test_batch_packaging_save_is_cas_atomic_preserves_reframe_and_stales_approved_preview():
    task = create_batch("卖点", "已确认脚本", id_factory=iter(["task", "variant"]).__next__)
    tracks = _tracks(reframe=True)
    task["variant"].update(tracks=deepcopy(tracks), revision=1)
    task["variant"]["subtitles"].update(revision=1, cues=_cue())
    preview = production.frozen_edit_snapshot(task)
    task["variant"]["runs"].append({"id": "preview", "status": "completed", "error": None,
        "revision": 1, "contentRevision": 0, "subtitleRevision": 1,
        "snapshot": preview["snapshot"], "subtitleCues": preview["subtitleCues"], "sources": {"shot": "shot.mp4"},
        "output": "preview.mp4"})
    task["reviews"].append({"id": "review", "runId": "preview", "decision": "approved", "reason": "确认",
                            "contentRevision": 0, "variantRevision": 1, "subtitleRevision": 1})

    saved = save_variant(task, expected_revision=1, tracks=tracks, settings=task["variant"]["settings"],
                         aspect_mode="9:16", assets=_assets(), packaging=_subtitle_package())

    assert saved["packaging"]["elements"][0]["cueId"] == "recognition-one-line-1"
    assert task["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "manual"
    assert production.inspect_variant(task).preview.status == "stale"
    with pytest.raises(BatchProductionError, match="变体已更新"):
        save_variant(task, expected_revision=1, tracks=tracks, settings=task["variant"]["settings"],
                     aspect_mode="9:16", assets=_assets(), packaging=None)


def test_batch_subtitle_save_rejects_removing_a_packaging_cue_reference():
    task = create_batch("卖点", "已确认脚本", id_factory=iter(["task", "variant"]).__next__)
    task["variant"].update(tracks=_tracks(), revision=1, packaging=_subtitle_package())
    task["variant"]["subtitles"].update(revision=1, cues=_cue())

    with pytest.raises(BatchProductionError, match="字幕 cueId"):
        save_subtitles(task, expected_revision=1, timeline_revision=1, cues=_cue("recognition-two-line-1"),
                       validate_cues=lambda value, _tracks: value)

    assert task["variant"]["subtitles"]["cues"] == _cue()


def test_script_change_cannot_leave_a_dangling_batch_packaging_cue():
    task = create_batch("卖点", "旧脚本", id_factory=iter(["task", "variant"]).__next__)
    task["variant"].update(tracks=_tracks(), revision=1, packaging=_subtitle_package())
    task["variant"]["subtitles"].update(revision=1, cues=_cue())

    with pytest.raises(BatchProductionError, match="包装字幕引用"):
        save_content(task, expected_revision=0, selling_point="卖点", script="新脚本")

    assert task["script"] == "旧脚本"


def test_initial_proposal_packaging_uses_confirmed_copy_without_inventing_cta():
    package = production.initial_proposal_packaging(
        script="已确认的脚本标题。后续说明", selling_point="已确认卖点", duration=6, cta=None,
    )

    assert package["templateId"] == "commercial-clean"
    assert {item["kind"] for item in package["elements"]} == {"title", "selling-point", "keyword"}
    assert all("立即购买" not in item["text"] for item in package["elements"])
    assert package["elements"][0]["text"] == "已确认的脚本标题"

    with_cta = production.initial_proposal_packaging(
        script="已确认的脚本标题。后续说明", selling_point="已确认卖点", duration=6, cta="立即咨询",
    )
    assert [item["text"] for item in with_cta["elements"] if item["kind"] == "cta"] == ["立即咨询"]


def test_backup_rejects_a_batch_snapshot_with_a_dangling_packaging_cue(tmp_path):
    import json
    from app.batch_editing_api import BatchStore

    task = create_batch("卖点", "脚本", id_factory=iter(["task", "variant"]).__next__)
    task["variant"].update(tracks=_tracks(), revision=1)
    task["variant"]["runs"] = [{"id": "preview", "status": "failed", "error": "失败", "revision": 1,
        "contentRevision": 0, "subtitleRevision": 0, "snapshot": {"settings": task["variant"]["settings"],
        "tracks": _tracks(), "packaging": _subtitle_package()}, "subtitleCues": [], "sources": {"shot": "shot.mp4"}}]
    path = BatchStore(tmp_path).path("project", "state.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schemaVersion": 1, "tasks": [task]}), encoding="utf-8")

    with pytest.raises(ValueError):
        BatchStore(tmp_path).load("project")


def test_legacy_recognition_cues_migrate_to_their_run_namespace_idempotently(tmp_path):
    import json
    from app.batch_editing_api import BatchStore

    task = create_batch("卖点", "脚本", id_factory=iter(["task", "variant"]).__next__)
    task["variant"].update(tracks=_tracks(), revision=1)
    legacy = {"id": "recognition", "status": "completed", "error": None, "language": "zh",
              "timelineRevision": 1, "subtitleRevision": 0, "cues": _cue("line-1"),
              "snapshot": {"settings": task["variant"]["settings"], "tracks": _tracks()},
              "sources": {"shot": "shot.mp4"}}
    task["variant"]["subtitles"].update(cues=_cue("line-1"), recognitions=[legacy])
    path = BatchStore(tmp_path).path("project", "state.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schemaVersion": 1, "tasks": [task]}), encoding="utf-8")

    first = BatchStore(tmp_path).load("project")
    second = BatchStore(tmp_path).load("project")

    assert first["tasks"][0]["variant"]["subtitles"]["recognitions"][0]["cues"][0]["id"] == "recognition-line-1"
    assert first["tasks"][0]["variant"]["subtitles"]["cues"][0]["id"] == "recognition-line-1"
    assert second == first


def test_legacy_recognition_migration_rejects_ambiguous_current_cue(tmp_path):
    import json
    from app.batch_editing_api import BatchStore

    task = create_batch("卖点", "脚本", id_factory=iter(["task", "variant"]).__next__)
    task["variant"].update(tracks=_tracks(), revision=1)
    first = {"id": "recognition-one", "status": "completed", "error": None, "language": "zh",
             "timelineRevision": 1, "subtitleRevision": 0, "cues": _cue("line-1"),
             "snapshot": {"settings": task["variant"]["settings"], "tracks": _tracks()}, "sources": {"shot": "shot.mp4"}}
    second = {**deepcopy(first), "id": "recognition-two"}
    task["variant"]["subtitles"].update(cues=_cue("line-1"), recognitions=[first, second])
    path = BatchStore(tmp_path).path("project", "state.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schemaVersion": 1, "tasks": [task]}), encoding="utf-8")

    with pytest.raises(ValueError, match="身份歧义"):
        BatchStore(tmp_path).load("project")


def test_save_content_keeps_or_clears_only_explicit_cta_and_invalidates_completed_artifacts():
    task = create_batch("卖点", "脚本", id_factory=iter(["task", "variant"]).__next__)
    task["variant"].update(tracks=_tracks(), revision=1)

    assert save_content(task, expected_revision=0, selling_point="卖点", script="脚本二", cta="立即咨询")
    assert task["cta"] == "立即咨询" and task["contentRevision"] == 1
    assert save_content(task, expected_revision=1, selling_point="卖点", script="脚本三")
    assert task["cta"] == "立即咨询"
    assert save_content(task, expected_revision=2, selling_point="卖点", script="脚本四", cta=None)
    assert "cta" not in task
    with pytest.raises(BatchProductionError, match="脚本已更新"):
        save_content(task, expected_revision=2, selling_point="卖点", script="脚本四", cta="过期")
