"""User-visible batch editing paths through the project API."""
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.batch_editing_api import _shared_montage_usage, create_batch_editing_router
from app.batch_recommendation import recommend
from app.main import create_app
from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint


INTENT = {"x-aivre-intent": "semantic-analysis"}


def test_shared_montage_usage_counts_each_sibling_once_and_ignores_deleted_or_stale_selections():
    sibling = {
        "id": "task-b",
        "variant": {"tracks": [{"clips": [{
            "id": "clip-a", "assetId": "asset-a", "inPoint": 1, "duration": 2,
        }]}]},
        "montageProposals": [
            {
                "status": "applied",
                "appliedSelections": [
                    {"segmentId": "segment-a", "candidateId": "candidate-a", "clipId": "clip-a",
                     "assetId": "asset-a", "inPoint": 1, "duration": 2},
                    {"segmentId": "segment-b", "candidateId": "candidate-b", "clipId": "clip-b",
                     "assetId": "asset-b", "inPoint": 1, "duration": 2},
                ],
            },
            {
                "status": "draft",
                "segments": [{"selectedCandidateId": "candidate-a"}],
            },
        ],
    }

    legacy_sibling = {
        "id": "task-c",
        "contentRevision": 1,
        "variant": {"tracks": [{"clips": [{"id": "legacy-a"}]}]},
        "montageProposals": [
            {
                "status": "applied",
                "segments": [
                    {"selectedCandidateId": "candidate-c"},
                    {"selectedCandidateId": "candidate-d"},
                ],
                "appliedClipIds": ["legacy-a", "legacy-deleted"],
            },
            {
                "status": "draft",
                "inputSnapshot": {"contentRevision": 0},
                "segments": [{"selectedCandidateId": "stale-candidate"}],
            },
        ],
    }
    replaced_sibling = {
        "id": "task-d",
        "variant": {"tracks": [{"clips": [{
            "id": "clip-x", "assetId": "replacement", "inPoint": 0, "duration": 2,
        }]}]},
        "montageProposals": [{
            "status": "applied",
            "appliedSelections": [{
                "segmentId": "segment-x", "candidateId": "candidate-x", "clipId": "clip-x",
                "assetId": "original", "inPoint": 1, "duration": 2,
            }],
        }],
    }

    assert _shared_montage_usage([{"id": "task-a"}, sibling, legacy_sibling, replaced_sibling], "task-a") == {
        "candidate-a": 1, "candidate-c": 1,
    }


class MontageVectorizer:
    version = "montage-test-v1"
    evidence_basis = "测试关键帧视觉证据"

    def embed_text(self, query):
        return [1.0, 0.0] if "杯盖" in query else [0.0, 1.0]

    def embed_image(self, path):
        with Image.open(path) as image:
            red, _green, blue = image.convert("RGB").resize((1, 1)).getpixel((0, 0))
        return [1.0, 0.0] if red > blue else [0.0, 1.0]


def install_montage_indexes(root, project_id):
    from app.source_segment_index import KEYFRAME_MODEL_VERSION, SEGMENT_ALGORITHM_VERSION, source_fingerprint

    preproduction = root / "project-files" / project_id / "preproduction"
    (preproduction / "assets").mkdir(parents=True)
    assets = []
    for asset_id, name, color in (("lid", "杯盖防泼溅", "red"), ("commute", "通勤携带", "blue")):
        filename = f"{asset_id}.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color={color}:size=64x48:rate=24:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(preproduction / "assets" / filename),
        ], check=True, capture_output=True, text=True)
        assets.append({"id": asset_id, "name": name, "kind": "video", "file": filename, "duration": 6})
    (preproduction / "state.json").write_text(json.dumps({
        "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [], "assets": assets,
    }), encoding="utf-8")
    for asset, color in zip(assets, ((220, 20, 20), (20, 20, 220))):
        source = preproduction / "assets" / asset["file"]
        run_id = f"run-{asset['id']}"
        directory = root / "project-files" / project_id / "source-segments" / asset["id"]
        keyframe = directory / "runs" / run_id / "keyframe-1.jpg"
        keyframe.parent.mkdir(parents=True)
        Image.new("RGB", (160, 90), color).save(keyframe)
        fingerprint = source_fingerprint(source)
        candidate_id = f"segment-{asset['id']}"
        (directory / "state.json").write_text(json.dumps({
            "schemaVersion": 1, "assetId": asset["id"], "runId": run_id, "attemptId": f"attempt-{asset['id']}",
            "status": "completed", "sourceFingerprint": fingerprint,
            "algorithmVersion": SEGMENT_ALGORITHM_VERSION, "keyframeModelVersion": KEYFRAME_MODEL_VERSION,
            "queuedAt": "2026-09-30T00:00:00+00:00", "startedAt": "2026-09-30T00:00:01+00:00",
            "completedAt": "2026-09-30T00:00:02+00:00", "updatedAt": "2026-09-30T00:00:02+00:00",
            "error": None, "validity": {"status": "current", "reason": None}, "durationSeconds": 6,
            "candidates": [{"id": candidate_id, "assetId": asset["id"], "sourceFingerprint": fingerprint,
                "algorithmVersion": SEGMENT_ALGORITHM_VERSION, "keyframeModelVersion": KEYFRAME_MODEL_VERSION,
                "inPointSeconds": 1.0, "outPointSeconds": 5.0,
                "keyframe": {"timeSeconds": 3.0, "_filename": "keyframe-1.jpg"}}],
        }), encoding="utf-8")


class DeferredComputeQueue:
    """Keep proposal analysis jobs controllable so adoption races are reproducible."""

    def __init__(self):
        self.jobs = []

    def submit(self, kind, project_id, handler):
        self.jobs.append((kind, project_id, handler))
        return True

    def is_active(self, _kind, _project_id):
        return False

    def is_project_active(self, _project_id):
        return False

    def shutdown(self):
        pass


def _proposal_reframe_app(root):
    queue = DeferredComputeQueue()

    def analyzer(path, start, end, aspect):
        return {
            "schemaVersion": 1, "sourceFingerprint": source_fingerprint(path),
            "sourceWidth": 64, "sourceHeight": 48, "targetAspect": aspect,
            "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
            "samples": [
                {"timeSeconds": start, "boxes": [], "center": {"x": .35, "y": .5}, "confidence": .9},
                {"timeSeconds": end, "boxes": [], "center": {"x": .65, "y": .5}, "confidence": .9},
            ],
            "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []},
        }

    return create_app(root, segment_visual_vectorizer=MontageVectorizer(),
                      local_compute_queue=queue, reframe_analyzer=analyzer), queue


def _create_montage_proposal(client, root, *, script="展示杯盖防泼溅。", narration=None):
    project_id = client.post("/api/projects", json={"name": "主体混剪回归"}).json()["id"]
    install_montage_indexes(root, project_id)
    queries = ["展示杯盖防泼溅"] if script.count("。") <= 1 else ["展示杯盖防泼溅", "展示通勤携带效果"]
    searches = [client.post(f"/api/projects/{project_id}/segment-searches", json={"query": query, "limit": 2},
                            headers=INTENT).json() for query in queries]
    base = f"/api/projects/{project_id}/batch-edits"
    task = client.post(base, json={"sellingPoint": "防泼溅", "script": script}, headers=INTENT).json()["task"]
    narration = narration or [{"durationSeconds": 2, "wordBoundariesSeconds": []} for _ in searches]
    proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
        "revision": 0, "searchIds": [item["id"] for item in searches], "narration": narration, "beatTimes": [],
    }, headers=INTENT).json()["proposal"]
    return project_id, base, task, proposal


def test_team_can_create_a_batch_task_and_reopen_its_script(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "客户短视频"}).json()["id"]
        base = f"/api/projects/{project_id}/batch-edits"

        created = client.post(base, json={"sellingPoint": "省时", "script": "先展示操作，再展示结果"}, headers=INTENT)

        assert created.status_code == 201, created.text
        task = created.json()["task"]
        assert task["sellingPoint"] == "省时"
        assert task["script"] == "先展示操作，再展示结果"
        assert task["variant"]["revision"] == 0
        reopened = client.get(base).json()["tasks"]
        assert [(item["id"], item["script"]) for item in reopened] == [(task["id"], "先展示操作，再展示结果")]
        other_id = client.post("/api/projects", json={"name": "另一客户"}).json()["id"]
        other_workspace = client.get(f"/api/projects/{other_id}/batch-edits").json()
        assert other_workspace["tasks"] == [] and other_workspace["assets"] == []
        assert client.put(f"/api/projects/{other_id}/batch-edits/{task['id']}/variant",
                          json={"revision": 0, "tracks": task["variant"]["tracks"]}, headers=INTENT).status_code == 404


def test_variant_edits_keep_other_tasks_and_project_timeline_unchanged(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "客户短视频"}).json()["id"]
        asset_root = tmp_path / "project-files" / project_id / "preproduction"
        (asset_root / "assets").mkdir(parents=True)
        (asset_root / "assets" / "shot.mp4").write_bytes(b"video")
        (asset_root / "state.json").write_text(json.dumps({
            "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
            "assets": [{"id": "shot", "name": "展示素材", "kind": "video", "file": "shot.mp4", "duration": 4}],
        }), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        available = client.get(base).json()["assets"]
        assert [(asset["id"], asset["name"], asset["kind"]) for asset in available] == [("shot", "展示素材", "video")]
        assert "file" not in available[0]
        first = client.post(base, json={"sellingPoint": "省时", "script": "演示操作"}, headers=INTENT).json()["task"]
        second = client.post(base, json={"sellingPoint": "易用", "script": "展示界面"}, headers=INTENT).json()["task"]
        variant = first["variant"]
        tracks = variant["tracks"]
        tracks[0]["clips"] = [{"id": "clip-a", "assetId": "shot", "start": 0, "inPoint": 1, "duration": 2,
                               "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        path = f"{base}/{first['id']}/variant"

        saved = client.put(path, json={"revision": 0, "tracks": tracks}, headers=INTENT)

        assert saved.status_code == 200, saved.text
        assert saved.json()["variant"]["revision"] == 1
        reopened = client.get(base).json()["tasks"]
        assert reopened[0]["variant"]["tracks"][0]["clips"][0]["inPoint"] == 1
        assert reopened[1]["id"] == second["id"] and reopened[1]["variant"]["revision"] == 0
        assert client.get(f"/api/projects/{project_id}/timeline").json()["revision"] == 0
        assert client.put(path, json={"revision": 0, "tracks": tracks}, headers=INTENT).status_code == 409
        for revision in ("0", False, 0.0):
            invalid_revision = client.put(path, json={"revision": revision, "tracks": tracks}, headers=INTENT)
            assert invalid_revision.status_code == 409
            assert invalid_revision.json()["detail"]["code"] == "batch_variant_conflict"
        tracks[0]["clips"][0]["assetId"] = "missing"
        assert client.put(path, json={"revision": 1, "tracks": tracks}, headers=INTENT).status_code == 422


def test_script_recommendation_uses_asset_notes_and_never_replaces_saved_variant(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "营销视频"}).json()["id"]
        asset_root = tmp_path / "project-files" / project_id / "preproduction"
        (asset_root / "assets").mkdir(parents=True)
        for name in ("speed", "water", "battery"):
            (asset_root / "assets" / f"{name}.mp4").write_bytes(b"video")
        (asset_root / "state.json").write_text(json.dumps({
            "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
            "assets": [
                {"id": "speed", "name": "快速启动", "notes": "快速启动，节省等待时间", "kind": "video", "file": "speed.mp4", "duration": 4},
                {"id": "water", "name": "防水测试", "notes": "防水测试，雨天拍摄", "kind": "video", "file": "water.mp4", "duration": 4},
                {"id": "battery", "name": "续航展示", "notes": "持久续航，户外使用", "kind": "video", "file": "battery.mp4", "duration": 4},
            ],
        }), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        first = client.post(base, json={"sellingPoint": "省时", "script": "快速启动。持久续航。"}, headers=INTENT).json()["task"]
        second = client.post(base, json={"sellingPoint": "户外", "script": "防水测试。持久续航。"}, headers=INTENT).json()["task"]
        picks = ["speed", "water", "battery"]
        first_response = client.post(f"{base}/{first['id']}/recommendations", json={"revision": 0, "assetIds": picks}, headers=INTENT)
        second_response = client.post(f"{base}/{second['id']}/recommendations", json={"revision": 0, "assetIds": picks}, headers=INTENT)
        assert first_response.status_code == 200, first_response.text
        assert second_response.status_code == 200, second_response.text
        first_proposal = first_response.json()["proposal"]
        second_proposal = second_response.json()["proposal"]
        assert [item["assetId"] for item in first_proposal["matches"]] == ["speed", "battery"]
        assert [item["assetId"] for item in second_proposal["matches"]] == ["water", "battery"]
        assert first_proposal["matches"][0]["scriptSegment"] == "快速启动"
        assert "快速" in first_proposal["matches"][0]["matchedTerms"]
        assert first_proposal["method"] == "asset-metadata-keywords"
        assert first_proposal["assetIds"] == picks
        reopened = client.get(base).json()["tasks"]
        assert reopened[0]["proposal"]["matches"] == first_proposal["matches"]
        assert reopened[0]["variant"]["tracks"][0]["clips"] == []

        tracks = first["variant"]["tracks"]
        tracks[0]["clips"] = first_proposal["clips"]
        tracks[0]["clips"][0]["duration"] = 2
        saved = client.put(f"{base}/{first['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        again = client.post(f"{base}/{first['id']}/recommendations", json={"revision": 1, "assetIds": picks}, headers=INTENT)
        assert again.status_code == 200, again.text
        reopened = client.get(base).json()["tasks"]
        assert reopened[0]["variant"]["tracks"][0]["clips"][0]["duration"] == 2
        assert reopened[0]["variant"]["revision"] == 1
        assert reopened[1]["variant"]["revision"] == 0


def test_recommendation_explains_missing_evidence_and_insufficient_assets(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "营销视频"}).json()["id"]
        asset_root = tmp_path / "project-files" / project_id / "preproduction"
        (asset_root / "assets").mkdir(parents=True)
        (asset_root / "state.json").write_text(json.dumps({
            "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
            "assets": [{"id": "plain", "name": "clip.mp4", "kind": "video", "file": "plain.mp4", "duration": 4},
                       {"id": "other", "name": "other.mp4", "kind": "video", "file": "other.mp4", "duration": 4},
                       {"id": "audio", "name": "旁白", "kind": "audio", "file": "voice.mp3", "duration": 4}],
        }), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "防水", "script": "防水测试。"}, headers=INTENT).json()["task"]
        url = f"{base}/{task['id']}/recommendations"
        assert client.post(url, json={"revision": 0, "assetIds": ["plain"]}, headers=INTENT).json()["detail"]["code"] == "batch_assets_insufficient"
        result = client.post(url, json={"revision": 0, "assetIds": ["plain", "audio"]}, headers=INTENT)
        assert result.status_code == 422
        assert result.json()["detail"]["code"] == "batch_assets_insufficient"
        assert "画面" in result.json()["detail"]["message"]
        unmatched = client.post(url, json={"revision": 0, "assetIds": ["plain", "other"]}, headers=INTENT)
        assert unmatched.status_code == 422
        assert unmatched.json()["detail"]["code"] == "batch_match_unavailable"
        assert "第 1 段" in unmatched.json()["detail"]["message"]
        assert client.get(base).json()["tasks"][0].get("proposal") is None
        damaged = json.loads((asset_root / "state.json").read_text(encoding="utf-8"))
        damaged["assets"][0].update(name="防水测试", duration="broken")
        (asset_root / "state.json").write_text(json.dumps(damaged), encoding="utf-8")
        invalid_duration = client.post(url, json={"revision": 0, "assetIds": ["plain", "other"]}, headers=INTENT)
        assert invalid_duration.status_code == 422
        assert invalid_duration.json()["detail"]["code"] == "batch_asset_unavailable"
        damaged["assets"][0]["duration"] = 4
        (asset_root / "state.json").write_text(json.dumps(damaged), encoding="utf-8")
        missing_file = client.post(url, json={"revision": 0, "assetIds": ["plain", "other"]}, headers=INTENT)
        assert missing_file.status_code == 422
        assert missing_file.json()["detail"]["code"] == "batch_asset_unavailable"
        other = client.post(base, json={"sellingPoint": "防水", "script": "防水测试。"}, headers=INTENT).json()["task"]
        assert client.post(f"{base}/{other['id']}/recommendations", json={"revision": 1, "assetIds": ["plain", "audio"]}, headers=INTENT).status_code == 409


def test_recommendation_rejects_script_without_content(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "营销视频"}).json()["id"]
        root = tmp_path / "project-files" / project_id / "preproduction"
        root.mkdir(parents=True)
        (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 0, "brief": {}, "shots": [], "assets": [
            {"id": "a", "name": "防水", "kind": "image", "file": "a.png"},
            {"id": "b", "name": "防水", "kind": "image", "file": "b.png"},
        ]}), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "防水", "script": "。。。"}, headers=INTENT).json()["task"]
        result = client.post(f"{base}/{task['id']}/recommendations",
                             json={"revision": 0, "assetIds": ["a", "b"]}, headers=INTENT)
        assert result.status_code == 422
        assert result.json()["detail"]["code"] == "batch_script_empty"


def test_montage_proposal_is_traceable_and_applies_through_variant_revision_rules(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "可解释混剪"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        searches = [client.post(
            f"/api/projects/{project_id}/segment-searches",
            json={"query": query, "limit": 2}, headers=INTENT,
        ).json() for query in ("展示杯盖防泼溅", "展示通勤携带效果")]
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={
            "sellingPoint": "通勤防泼溅", "script": "展示杯盖防泼溅。展示通勤携带效果。",
        }, headers=INTENT).json()["task"]

        proposal_request = {
            "revision": 0,
            "searchIds": [item["id"] for item in searches],
            "narration": [
                {"durationSeconds": 2.4, "wordBoundariesSeconds": [1.2, 2.4]},
                {"durationSeconds": 2.0, "wordBoundariesSeconds": [1.0, 2.0]},
            ],
            "beatTimes": [2.18, 4.4],
        }
        created = client.post(f"{base}/{task['id']}/montage-proposals", json=proposal_request, headers=INTENT)

        assert created.status_code == 201, created.text
        proposal = created.json()["proposal"]
        assert [item["text"] for item in proposal["segments"]] == ["展示杯盖防泼溅", "展示通勤携带效果"]
        assert [item["suggestion"]["candidateId"] for item in proposal["segments"]] == ["segment-lid", "segment-commute"]
        assert proposal["inputSnapshot"]["segments"][0]["searchId"] == searches[0]["id"]
        assert proposal["revision"] == 0
        assert proposal["transitions"][0]["template"] == "dissolve"
        assert proposal["segments"][0]["suggestion"]["sceneId"] == "lid:segment-lid"
        reopened = client.get(base).json()["tasks"][0]["montageProposals"][0]
        assert reopened["id"] == proposal["id"]

        abandoned = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "abandon",
        }, headers=INTENT)
        assert abandoned.status_code == 200
        assert abandoned.json()["variant"]["revision"] == 0
        assert abandoned.json()["proposal"]["status"] == "abandoned"

        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json=proposal_request, headers=INTENT).json()["proposal"]
        assert proposal["id"] != abandoned.json()["proposal"]["id"]
        assert len(client.get(base).json()["tasks"][0]["montageProposals"]) == 2
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert applied.status_code == 200, applied.text
        assert applied.json()["variant"]["revision"] == 1
        assert [clip["assetId"] for clip in applied.json()["variant"]["tracks"][0]["clips"]] == ["lid", "commute"]
        assert applied.json()["variant"]["transitions"] == proposal["transitions"]
        conflict = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 1, "strategy": "overwrite",
        }, headers=INTENT)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "batch_variant_conflict"

        regenerated = client.post(f"{base}/{task['id']}/montage-proposals", json={
            **proposal_request, "revision": 1,
        }, headers=INTENT).json()["proposal"]
        merged = client.post(f"{base}/{task['id']}/montage-proposals/{regenerated['id']}/apply", json={
            "revision": 1, "proposalRevision": 0, "strategy": "merge",
        }, headers=INTENT)
        assert merged.status_code == 200, merged.text
        merged_clips = merged.json()["variant"]["tracks"][0]["clips"]
        assert len(merged_clips) == 4
        assert len({clip["id"] for clip in merged_clips}) == 4


def test_editing_proposal_does_not_convert_old_transition_handle_into_narration_time(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "转场人工调整"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        searches = [client.post(f"/api/projects/{project_id}/segment-searches",
                               json={"query": query, "limit": 2}, headers=INTENT).json()
                    for query in ("展示杯盖", "展示通勤")]
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "方便", "script": "展示杯盖。展示通勤。"}, headers=INTENT).json()["task"]
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 0, "searchIds": [item["id"] for item in searches],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}] * 2,
            "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        assert proposal["clips"][0]["duration"] == 2.35
        edited = client.put(f"{base}/{task['id']}/montage-proposals/{proposal['id']}", json={
            "revision": 0, "segments": [{"segmentId": item["id"], "candidateId": item["selectedCandidateId"],
                "inPointSeconds": item["selectedClip"]["inPoint"], "durationSeconds": item["selectedClip"]["duration"]}
                for item in proposal["segments"]],
        }, headers=INTENT)
        assert edited.status_code == 200, edited.text
        current = edited.json()["proposal"]
        assert [(clip["start"], clip["duration"]) for clip in current["clips"]] == [(0, 2), (2, 2)]
        assert current["transitions"][0]["template"] == "cut"
        assert current["segments"][0]["suggestionClip"]["duration"] == 2.35


def test_montage_proposal_replacement_preserves_suggestion_and_stale_source_blocks_apply(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "人工微调"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        search = client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": "展示杯盖防泼溅", "limit": 2,
        }, headers=INTENT).json()
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "防泼溅", "script": "展示杯盖防泼溅。"}, headers=INTENT).json()["task"]
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 0, "searchIds": [search["id"]],
            "narration": [{"durationSeconds": 2.0, "wordBoundariesSeconds": [2.0]}], "beatTimes": [2.05],
        }, headers=INTENT).json()["proposal"]
        assert proposal["segments"][0]["timing"]["beatSnap"] == 2.05
        original_clip = proposal["segments"][0]["selectedClip"]

        changed = client.put(f"{base}/{task['id']}/montage-proposals/{proposal['id']}", json={
            "revision": 0,
            "segments": [{"segmentId": proposal["segments"][0]["id"], "candidateId": "segment-commute",
                "inPointSeconds": 1.5, "durationSeconds": 2.0}],
        }, headers=INTENT)

        assert changed.status_code == 200, changed.text
        adjusted = changed.json()["proposal"]
        assert adjusted["segments"][0]["suggestion"]["candidateId"] == "segment-lid"
        assert adjusted["segments"][0]["selectedCandidateId"] == "segment-commute"
        assert adjusted["segments"][0]["selectedClip"]["inPoint"] == 1.5
        assert adjusted["segments"][0]["selectedClip"]["duration"] == 2.0
        assert adjusted["segments"][0]["timing"]["beatSnap"] is None
        assert "人工时长调整改变了节奏位置，已取消原节拍吸附" in adjusted["segments"][0]["reasons"]
        assert any("人工选择候选 segment-commute" in reason for reason in adjusted["segments"][0]["reasons"])
        assert any("当前候选本地视觉相关度" in reason for reason in adjusted["segments"][0]["reasons"])
        assert adjusted["revision"] == 1

        restored = client.put(f"{base}/{task['id']}/montage-proposals/{proposal['id']}", json={
            "revision": 1,
            "segments": [{"segmentId": proposal["segments"][0]["id"], "candidateId": "segment-lid",
                "inPointSeconds": original_clip["inPoint"], "durationSeconds": original_clip["duration"]}],
        }, headers=INTENT)
        assert restored.status_code == 200, restored.text
        restored_proposal = restored.json()["proposal"]
        assert restored_proposal["segments"][0]["selectedClip"]["id"] == original_clip["id"]
        assert restored_proposal["revision"] == 2

        source = tmp_path / "project-files" / project_id / "preproduction" / "assets" / "lid.mp4"
        source.write_bytes(b"changed-source")
        stale = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 2, "strategy": "overwrite",
        }, headers=INTENT)
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "montage_proposal_stale"


def test_montage_gap_can_be_reallocated_and_short_proposal_cannot_be_applied(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "缺口补齐"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        search = client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": "展示杯盖防泼溅", "limit": 2,
        }, headers=INTENT).json()
        restricted_search = client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": "展示杯盖防泼溅", "limit": 2,
        }, headers=INTENT).json()
        restricted_path = (tmp_path / "project-files" / project_id / "segment-retrieval" / "searches"
                           / f"{restricted_search['id']}.json")
        restricted_state = json.loads(restricted_path.read_text(encoding="utf-8"))
        restricted_state["localCandidates"] = [
            item for item in restricted_state["localCandidates"] if item["candidateId"] == "segment-lid"
        ]
        restricted_path.write_text(json.dumps(restricted_state), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={
            "sellingPoint": "防泼溅", "script": "展示杯盖防泼溅。展示杯盖防泼溅。",
        }, headers=INTENT).json()["task"]
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 0, "searchIds": [search["id"], restricted_search["id"]],
            "narration": [
                {"durationSeconds": 2.0, "wordBoundariesSeconds": [2.0]},
                {"durationSeconds": 2.0, "wordBoundariesSeconds": [2.0]},
            ], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]

        assert proposal["segments"][1]["status"] == "gap"
        assert [item["candidate"]["candidateId"] for item in proposal["segments"][1]["alternatives"]] == ["segment-lid"]
        changed = client.put(f"{base}/{task['id']}/montage-proposals/{proposal['id']}", json={
            "revision": 0,
            "segments": [
                {"segmentId": "script-segment-1", "candidateId": "segment-commute",
                    "inPointSeconds": 1.0, "durationSeconds": 2.0},
                {"segmentId": "script-segment-2", "candidateId": "segment-lid",
                    "inPointSeconds": 1.0, "durationSeconds": 2.0},
            ],
        }, headers=INTENT)
        assert changed.status_code == 200, changed.text
        assert [item["status"] for item in changed.json()["proposal"]["segments"]] == ["planned", "planned"]
        assert changed.json()["proposal"]["unmet"] == []

        short_task = client.post(base, json={"sellingPoint": "时长门禁", "script": "展示杯盖防泼溅。"}, headers=INTENT).json()["task"]
        short = client.post(f"{base}/{short_task['id']}/montage-proposals", json={
            "revision": 0, "searchIds": [search["id"]],
            "narration": [{"durationSeconds": 5.0, "wordBoundariesSeconds": [5.0]}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        assert short["unmet"]
        refused = client.post(f"{base}/{short_task['id']}/montage-proposals/{short['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert refused.status_code == 422
        assert refused.json()["detail"]["code"] == "montage_proposal_incomplete"


def test_montage_merge_appends_after_existing_manual_edit(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "合并提案"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        search = client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": "展示杯盖防泼溅", "limit": 2,
        }, headers=INTENT).json()
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "防泼溅", "script": "展示杯盖防泼溅。"}, headers=INTENT).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [{
            "id": "manual-clip", "assetId": "commute", "start": 0, "inPoint": 0, "duration": 1,
            "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0,
        }]
        saved = client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 1, "searchIds": [search["id"]],
            "narration": [{"durationSeconds": 2.0, "wordBoundariesSeconds": [1.0, 2.0]}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]

        merged = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 1, "proposalRevision": 0, "strategy": "merge",
        }, headers=INTENT)

        assert merged.status_code == 200, merged.text
        clips = merged.json()["variant"]["tracks"][0]["clips"]
        assert [clip["id"] for clip in clips[:1]] == ["manual-clip"]
        assert clips[1]["start"] == 1
        assert merged.json()["variant"]["revision"] == 2


def test_montage_beat_spacing_keeps_existing_voiceover_and_subtitles_aligned(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "节拍配音同步"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        preproduction = tmp_path / "project-files" / project_id / "preproduction"
        asset_state = json.loads((preproduction / "state.json").read_text(encoding="utf-8"))
        asset_state["assets"].extend([
            {"id": "voice-a", "name": "第一段配音", "kind": "audio", "file": "voice-a.wav", "duration": 2},
            {"id": "voice-b", "name": "第二段配音", "kind": "audio", "file": "voice-b.wav", "duration": 2},
        ])
        (preproduction / "state.json").write_text(json.dumps(asset_state), encoding="utf-8")
        searches = [client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": query, "limit": 2,
        }, headers=INTENT).json() for query in ("展示杯盖防泼溅", "展示通勤携带效果")]
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={
            "sellingPoint": "同步", "script": "展示杯盖防泼溅。展示通勤携带效果。",
        }, headers=INTENT).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [
            {"id": "old-a", "assetId": "lid", "start": 0, "inPoint": 1, "duration": 2,
             "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0},
            {"id": "old-b", "assetId": "commute", "start": 2, "inPoint": 1, "duration": 2,
             "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0},
        ]
        tracks.append({"id": "voiceover", "name": "AI 配音", "kind": "audio", "muted": False, "hidden": False,
                       "clips": [
                           {"id": "voice-clip-a", "assetId": "voice-a", "start": 0, "inPoint": 0, "duration": 2,
                            "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0},
                           {"id": "voice-clip-b", "assetId": "voice-b", "start": 2, "inPoint": 0, "duration": 2,
                            "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0},
                       ]})
        saved = client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        subtitles = client.put(f"{base}/{task['id']}/subtitles", json={
            "revision": 0, "timelineRevision": 1, "cues": [
                {"id": "cue-a", "start": 0, "end": 2, "text": "展示杯盖防泼溅"},
                {"id": "cue-b", "start": 2, "end": 4, "text": "展示通勤携带效果"},
            ],
        }, headers=INTENT)
        assert subtitles.status_code == 200, subtitles.text
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 1, "searchIds": [item["id"] for item in searches],
            "narration": [
                {"durationSeconds": 2, "wordBoundariesSeconds": [], "source": "measured_voiceover"},
                {"durationSeconds": 2, "wordBoundariesSeconds": [], "source": "measured_voiceover"},
            ], "beatTimes": [2.05, 4.1],
        }, headers=INTENT).json()["proposal"]

        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 1, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)

        assert applied.status_code == 200, applied.text
        variant = applied.json()["variant"]
        video = next(track for track in variant["tracks"] if track["id"] == "video")
        voice = next(track for track in variant["tracks"] if track["id"] == "voiceover")
        assert [clip["start"] for clip in video["clips"]] == [0, 2.05]
        assert [clip["start"] for clip in voice["clips"]] == [0, 2.05]
        assert [cue["start"] for cue in variant["subtitles"]["cues"]] == [0, 2.05]


def test_montage_beat_spacing_moves_saved_subtitles_without_a_voiceover_track(tmp_path):
    with TestClient(create_app(tmp_path, segment_visual_vectorizer=MontageVectorizer())) as client:
        project_id = client.post("/api/projects", json={"name": "节拍字幕同步"}).json()["id"]
        install_montage_indexes(tmp_path, project_id)
        searches = [client.post(f"/api/projects/{project_id}/segment-searches", json={
            "query": query, "limit": 2,
        }, headers=INTENT).json() for query in ("展示杯盖防泼溅", "展示通勤携带效果")]
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={
            "sellingPoint": "同步", "script": "展示杯盖防泼溅。展示通勤携带效果。",
        }, headers=INTENT).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [
            {"id": "old-a", "assetId": "lid", "start": 0, "inPoint": 1, "duration": 2,
             "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0},
            {"id": "old-b", "assetId": "commute", "start": 2, "inPoint": 1, "duration": 2,
             "speed": 1, "volume": 0, "fadeIn": 0, "fadeOut": 0},
        ]
        saved = client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        subtitles = client.put(f"{base}/{task['id']}/subtitles", json={
            "revision": 0, "timelineRevision": 1, "cues": [
                {"id": "cue-a", "start": 0, "end": 2, "text": "展示杯盖防泼溅"},
                {"id": "cue-b", "start": 2, "end": 4, "text": "展示通勤携带效果"},
            ],
        }, headers=INTENT)
        assert subtitles.status_code == 200, subtitles.text
        proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 1, "searchIds": [item["id"] for item in searches],
            "narration": [
                {"durationSeconds": 2, "wordBoundariesSeconds": [1, 2], "source": "provided"},
                {"durationSeconds": 2, "wordBoundariesSeconds": [1, 2], "source": "provided"},
            ], "beatTimes": [2.05, 4.1],
        }, headers=INTENT).json()["proposal"]

        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 1, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)

        assert applied.status_code == 200, applied.text
        variant = applied.json()["variant"]
        video = next(track for track in variant["tracks"] if track["id"] == "video")
        assert [clip["start"] for clip in video["clips"]] == [0, 2.05]
        assert [cue["start"] for cue in variant["subtitles"]["cues"]] == [0, 2.05]


def test_english_sentences_get_distinct_matches_and_selling_point_evidence():
    task = {"sellingPoint": "fast launch", "script": "Show product. Long battery life.", "variant": {
        "revision": 0, "settings": {"width": 720, "height": 1280, "fps": 30},
        "tracks": [{"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": []}],
    }}
    assets = {
        "slow": {"id": "slow", "name": "product overview", "kind": "video", "duration": 3},
        "fast": {"id": "fast", "name": "product fast launch", "kind": "video", "duration": 3},
        "battery": {"id": "battery", "name": "long battery life", "kind": "video", "duration": 3},
    }

    proposal = recommend(task, assets, ["slow", "fast", "battery"])

    assert [match["scriptSegment"] for match in proposal["matches"]] == ["Show product", "Long battery life"]
    assert [match["assetId"] for match in proposal["matches"]] == ["fast", "battery"]
    assert proposal["matches"][0]["matchedSellingPointTerms"] == ["fast", "launch"]


def test_recommendation_reserves_the_only_asset_that_matches_a_later_segment():
    task = {"sellingPoint": "快速", "script": "快速启动。持久续航。", "variant": {
        "revision": 0, "settings": {"width": 720, "height": 1280, "fps": 30},
        "tracks": [{"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": []}],
    }}
    assets = {
        "both": {"id": "both", "name": "快速启动持久续航", "kind": "video", "duration": 3},
        "first": {"id": "first", "name": "快速启动", "kind": "video", "duration": 3},
    }

    proposal = recommend(task, assets, ["both", "first"])

    assert [match["assetId"] for match in proposal["matches"]] == ["first", "both"]


def test_preview_uses_saved_snapshot_and_old_preview_remains_after_edit(tmp_path):
    project_id = "p1"
    asset_root = tmp_path / "project-files" / project_id / "preproduction"
    (asset_root / "assets").mkdir(parents=True)
    source = asset_root / "assets" / "shot.mp4"
    source.write_bytes(b"video")
    (asset_root / "state.json").write_text(json.dumps({
        "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "shot", "name": "展示素材", "kind": "video", "file": "shot.mp4", "duration": 4}],
    }), encoding="utf-8")
    jobs = []
    received = []

    class Queue:
        def submit(self, kind, pid, handler):
            jobs.append((pid, handler))
            return True

    broken = [False]

    def renderer(timeline, sources, output, **kwargs):
        if broken[0]:
            raise RuntimeError("编码失败")
        received.append((timeline, sources["shot"].read_bytes()))
        output.write_bytes(b"preview")

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), Queue(), renderer=renderer))
    client = TestClient(app)
    base = f"/api/projects/{project_id}/batch-edits"
    task = client.post(base, json={"sellingPoint": "省时", "script": "操作演示"}).json()["task"]
    path = f"{base}/{task['id']}/variant"
    assert client.post(path + "/previews", json={"revision": 0}).status_code == 422
    tracks = task["variant"]["tracks"]
    tracks[0]["clips"] = [{"id": "clip-a", "assetId": "shot", "start": 0, "inPoint": 1, "duration": 2,
                           "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
    assert client.put(path, json={"revision": 0, "tracks": tracks}).status_code == 200

    submitted = client.post(path + "/previews", json={"revision": 1})

    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["variant"]["runs"][0]["id"]
    assert client.get(path + f"/previews/{run_id}/output").status_code == 409
    source.write_bytes(b"changed")
    pid, job = jobs.pop()
    job(pid)
    assert received[0][1] == b"video"
    assert received[0][0]["tracks"][0]["clips"][0]["inPoint"] == 1
    assert client.get(path + f"/previews/{run_id}/output").content == b"preview"
    tracks[0]["clips"][0]["inPoint"] = 0
    assert client.put(path, json={"revision": 1, "tracks": tracks}).status_code == 200
    assert client.get(path + f"/previews/{run_id}/output").content == b"preview"
    broken[0] = True
    newer = client.post(path + "/previews", json={"revision": 2})
    assert newer.status_code == 202
    pid, job = jobs.pop()
    job(pid)
    assert client.get(base).json()["tasks"][0]["variant"]["runs"][-1]["status"] == "failed"
    assert client.get(path + f"/previews/{run_id}/output").content == b"preview"


@pytest.mark.parametrize("tasks", [
    [{"id": "task-a"}],
    [{"id": "task-a", "sellingPoint": "省时", "script": "操作", "variant": {
        "id": "variant-a", "revision": 1, "settings": {"width": 720, "height": 1280, "fps": 30},
        "tracks": [], "runs": [{"id": "run-a"}],
    }}],
    [{"id": "task-a", "sellingPoint": "省时", "script": "操作", "variant": {
        "id": "variant-a", "revision": 1, "settings": {"width": None, "height": 1280, "fps": 30},
        "tracks": [], "runs": [],
    }}],
    [{"id": "task-a", "sellingPoint": "省时", "script": "操作", "variant": {
        "id": "variant-a", "revision": 1, "settings": {"width": 720, "height": 1280, "fps": 30},
        "tracks": [{"clips": []}], "runs": [],
    }}],
])
def test_damaged_task_state_is_reported_as_unavailable(tmp_path, tasks):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "客户短视频"}).json()["id"]
        state = tmp_path / "project-files" / project_id / "batch-edits" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"schemaVersion": 1, "tasks": tasks}), encoding="utf-8")

        result = client.get(f"/api/projects/{project_id}/batch-edits")

        assert result.status_code == 503
        assert result.json()["detail"]["code"] == "batch_storage_invalid"


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="需要本地 FFmpeg")
def test_two_source_variant_renders_a_real_vertical_preview(tmp_path):
    project_id = "p1"
    asset_root = tmp_path / "project-files" / project_id / "preproduction"
    (asset_root / "assets").mkdir(parents=True)
    for color in ("red", "blue"):
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"color=c={color}:s=64x48:r=24:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(asset_root / "assets" / f"{color}.mp4")], check=True)
    (asset_root / "state.json").write_text(json.dumps({
        "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": color, "name": color, "notes": "红色画面" if color == "red" else "蓝色画面",
                    "kind": "video", "file": f"{color}.mp4", "duration": 1} for color in ("red", "blue")],
    }), encoding="utf-8")
    jobs = []

    class Queue:
        def submit(self, kind, pid, handler):
            jobs.append((pid, handler))
            return True

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), Queue()))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "色彩对比", "script": "红色画面。蓝色画面。"}).json()["task"]
        tracks = task["variant"]["tracks"]
        recommended = client.post(f"{base}/{task['id']}/recommendations", json={"revision": 0, "assetIds": ["red", "blue"]})
        assert recommended.status_code == 200, recommended.text
        tracks[0]["clips"] = recommended.json()["proposal"]["clips"]
        assert [clip["assetId"] for clip in tracks[0]["clips"]] == ["red", "blue"]
        tracks[0]["clips"][0]["duration"] = 0.5
        tracks[0]["clips"][1]["start"] = 0.5
        path = f"{base}/{task['id']}/variant"
        assert client.put(path, json={"revision": 0, "tracks": tracks}).status_code == 200
        run = client.post(path + "/previews", json={"revision": 1})
        assert run.status_code == 202, run.text
        run_id = run.json()["variant"]["runs"][0]["id"]
        pid, handler = jobs.pop()
        handler(pid)
        assert client.get(base).json()["tasks"][0]["variant"]["runs"][0]["status"] == "completed"
        preview = client.get(path + f"/previews/{run_id}/output")
        assert preview.status_code == 200
        assert client.get(path.replace("/p1/", "/p2/") + f"/previews/{run_id}/output").status_code == 404
        output = tmp_path / "preview.mp4"
        output.write_bytes(preview.content)
        metadata = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                                              "-show_entries", "format=duration", "-of", "json", str(output)],
                                             check=True, capture_output=True, text=True).stdout)
        assert metadata["streams"][0]["height"] > metadata["streams"][0]["width"]
        assert 1.4 <= float(metadata["format"]["duration"]) <= 1.6
        other = client.post(base, json={"sellingPoint": "逆序展示", "script": "蓝色画面。红色画面。"}).json()["task"]
        other_proposal = client.post(f"{base}/{other['id']}/recommendations", json={"revision": 0, "assetIds": ["red", "blue"]})
        assert other_proposal.status_code == 200, other_proposal.text
        assert [clip["assetId"] for clip in other_proposal.json()["proposal"]["clips"]] == ["blue", "red"]
        other_tracks = other["variant"]["tracks"]
        other_tracks[0]["clips"] = other_proposal.json()["proposal"]["clips"]
        other_path = f"{base}/{other['id']}/variant"
        assert client.put(other_path, json={"revision": 0, "tracks": other_tracks}).status_code == 200
        other_run = client.post(other_path + "/previews", json={"revision": 1})
        assert other_run.status_code == 202, other_run.text
        pid, handler = jobs.pop()
        handler(pid)
        other_id = other_run.json()["variant"]["runs"][0]["id"]
        other_preview = client.get(other_path + f"/previews/{other_id}/output")
        assert other_preview.status_code == 200
        assert other_preview.content != preview.content


def test_batch_reframe_repeat_cancel_retry_and_source_cas(tmp_path):
    """A newer analysis token owns the clip; cancelled and stale jobs cannot publish."""
    from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint
    project_id = "reframe-batch"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=red:size=64x48:rate=24:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True, text=True)
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "source", "name": "source", "kind": "video", "file": "source.mp4", "duration": 8}]}, ensure_ascii=False), encoding="utf-8")
    jobs = []
    class Queue:
        def submit(self, _kind, pid, handler):
            jobs.append((pid, handler)); return True
    def analyzer(path, _start, _end, aspect):
        return {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(path), "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": aspect, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
                            {"timeSeconds": 2, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}
    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), Queue(), reframe_analyzer=analyzer))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "x", "script": "x"}).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [{"id": "clip", "assetId": "source", "start": 0, "inPoint": 0, "duration": 2,
                               "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}).status_code == 200
        url = f"{base}/{task['id']}/variant/reframes/clip"
        assert client.post(url + "/analyze", json={"revision": 1}).status_code == 202
        assert client.post(url + "/analyze", json={"revision": 1}).status_code == 202
        assert client.post(url + "/cancel", json={"revision": 1}).status_code == 200
        jobs.pop(0)[1](project_id)
        assert client.get(base).json()["tasks"][0]["variant"]["reframeAnalysis"]["clip"]["status"] == "cancelled"
        assert client.post(url + "/analyze", json={"revision": 1}).status_code == 202
        jobs.pop()[1](project_id)
        variant = client.get(base).json()["tasks"][0]["variant"]
        assert variant["revision"] == 2
        assert variant["tracks"][0]["clips"][0]["reframe"]["status"] == "review_required"
        forged = variant["tracks"]
        forged[0]["clips"][0]["reframe"]["status"] = "ready"
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 2, "tracks": forged}).status_code == 422
        assert client.post(url + "/analyze", json={"revision": 2}).status_code == 202
        source.unlink()
        jobs.pop()[1](project_id)
        deleted = client.get(base).json()["tasks"][0]["variant"]["reframeAnalysis"]["clip"]
        assert deleted["status"] == "failed"
        assert "素材" in deleted["error"]


def test_batch_reframe_never_accepts_worker_dimensions_when_the_source_probe_has_none(tmp_path, monkeypatch):
    """A batch analysis cannot become ready from self-reported worker dimensions."""
    from app import timeline_render
    project_id = "reframe-size-batch"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "source.mp4"
    source.write_bytes(b"source")
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "source", "name": "source", "kind": "video", "file": "source.mp4", "duration": 8}]}), encoding="utf-8")
    monkeypatch.setattr(timeline_render, "_probe_media", lambda *_: SimpleNamespace(width=None, height=None))
    jobs = []

    class Queue:
        def submit(self, _kind, pid, handler):
            jobs.append((pid, handler)); return True

    def analyzer(path, _start, _end, aspect):
        return {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(path), "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": aspect, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), Queue(), reframe_analyzer=analyzer))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "x", "script": "x"}).json()["task"]
        tracks = task["variant"]["tracks"]
        tracks[0]["clips"] = [{"id": "clip", "assetId": "source", "start": 0, "inPoint": 0, "duration": 2,
                               "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}).status_code == 200
        assert client.post(f"{base}/{task['id']}/variant/reframes/clip/analyze", json={"revision": 1}).status_code == 202
        jobs.pop()[1](project_id)
        record = client.get(base).json()["tasks"][0]["variant"]["reframeAnalysis"]["clip"]
        assert record["status"] == "failed"
        assert "可信源尺寸" in record["error"]


def test_completed_export_download_rechecks_real_reframe_sources_but_keeps_center_and_legacy_available(tmp_path, monkeypatch):
    """The output route must not trust a completed status when a new source probe downgrades auto framing."""
    from copy import deepcopy
    from app import montage_reframe
    from app.montage_reframe import normalize_reframe, probe_source_timing
    project_id = "reframe-delivery"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=red:size=64x48:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True, text=True)
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "source", "name": "source", "kind": "video", "file": "source.mp4", "duration": 2}]}), encoding="utf-8")
    timing = probe_source_timing(source, "ffprobe")
    assert timing.cfr
    raw = {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(source), "sourceWidth": 64, "sourceHeight": 48,
           "targetAspect": {"width": 720, "height": 1280}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
           "samples": [{"timeSeconds": point, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}
                       for point in timing.elapsed], "uncertaintyReasons": [], "status": "ready",
           "override": {"mode": "auto", "keyframes": []}}
    ready = normalize_reframe(raw, source_fingerprint=source_fingerprint(source), source_width=64, source_height=48,
                              source_timing=timing, required_start=0, required_end=2)
    assert ready["status"] == "ready"

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda value: SimpleNamespace(id=value) if value == project_id else None, DeferredComputeQueue()))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        task = client.post(base, json={"sellingPoint": "x", "script": "x"}).json()["task"]
        state_path = tmp_path / "project-files" / project_id / "batch-edits" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        stored = state["tasks"][0]
        clip = {"id": "clip", "assetId": "source", "start": 0, "inPoint": 0, "duration": 2,
                "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0, "reframe": ready}
        stored["variant"].update(revision=1, tracks=[{**stored["variant"]["tracks"][0], "clips": [clip]}, stored["variant"]["tracks"][1]])
        snapshot = {"settings": deepcopy(stored["variant"]["settings"]), "tracks": deepcopy(stored["variant"]["tracks"])}
        preview = {"id": "preview", "revision": 1, "contentRevision": 0, "subtitleRevision": 0, "subtitleCues": [],
                   "status": "completed", "error": None, "output": "preview.mp4", "snapshot": deepcopy(snapshot), "sources": {"source": "source.mp4"}}
        review = {"id": "review", "runId": "preview", "decision": "approved", "reason": "确认",
                  "contentRevision": 0, "variantRevision": 1, "subtitleRevision": 0}
        def delivery(run_id, snap):
            return {"id": run_id, "status": "completed", "error": None, "output": run_id + ".mp4",
                    "contentRevision": 0, "variantRevision": 1, "subtitleRevision": 0, "previewRunId": "preview",
                    "review": deepcopy(review), "sellingPoint": "x", "script": "x", "snapshot": snap,
                    "subtitles": [], "assets": [{"id": "source", "name": "source", "kind": "video"}], "sources": {"source": "source.mp4"}}
        stored["variant"]["runs"] = [preview]
        stored["reviews"] = [review]
        stored["variant"]["exports"] = [delivery("delivery", deepcopy(snapshot)), delivery("legacy", {**snapshot, "tracks": [{**snapshot["tracks"][0], "clips": [{key: value for key, value in clip.items() if key != "reframe"}]}, snapshot["tracks"][1]]})]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        for directory in ("runs/preview", "exports/delivery", "exports/legacy"):
            destination = tmp_path / "project-files" / project_id / "batch-edits" / directory / "sources"
            destination.mkdir(parents=True)
            shutil.copyfile(source, destination / "source.mp4")
        deliverables = tmp_path / "project-files" / project_id / "batch-edits" / "deliverables"
        deliverables.mkdir()
        (deliverables / "delivery.mp4").write_bytes(b"delivery")
        (deliverables / "legacy.mp4").write_bytes(b"legacy")
        assert client.get(f"{base}/{task['id']}/exports/delivery/output").content == b"delivery"

        original_probe = montage_reframe.probe_source_timing
        monkeypatch.setattr(montage_reframe, "probe_source_timing", lambda path, probe:
                            montage_reframe.SourceTiming((), (), False, "新 probe 发现 VFR") if path == source else original_probe(path, probe))
        blocked = client.get(f"{base}/{task['id']}/exports/delivery/output")
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "batch_export_reframe_review_required"
        assert client.get(f"{base}/{task['id']}/exports/legacy/output").content == b"legacy"

        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"][0]["variant"]["exports"][0]["snapshot"]["tracks"][0]["clips"][0]["reframe"]["override"] = {"mode": "center", "keyframes": []}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        assert client.get(f"{base}/{task['id']}/exports/delivery/output").content == b"delivery"


def test_montage_candidate_replacement_keeps_pending_reframe_and_queues_analysis(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, _task, proposal = _create_montage_proposal(client, tmp_path)
        segment = proposal["segments"][0]
        assert len(queue.jobs) == 1

        replaced = client.put(f"{base}/{_task['id']}/montage-proposals/{proposal['id']}", json={
            "revision": proposal["revision"],
            "segments": [{"segmentId": segment["id"], "candidateId": "segment-commute",
                          "inPointSeconds": 1.0, "durationSeconds": 2.0}],
        }, headers=INTENT)

        assert replaced.status_code == 200, replaced.text
        selected = replaced.json()["proposal"]["segments"][0]["selectedClip"]
        assert selected["assetId"] == "commute"
        assert selected["reframe"]["status"] == "review_required"
        assert selected["reframe"]["samples"] == []
        assert "重新主体分析" in selected["reframe"]["uncertaintyReasons"][0]
        assert len(queue.jobs) == 2

        _kind, pid, handler = queue.jobs[-1]
        handler(pid)
        task = client.get(base).json()["tasks"][0]
        selected = task["montageProposals"][0]["segments"][0]["selectedClip"]
        assert selected["reframe"]["status"] == "review_required"
        assert task["variant"]["reframeAnalysis"][selected["id"]]["status"] == "completed"


def test_two_clip_proposal_adoption_publishes_one_aggregate_reframe_revision(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, proposal = _create_montage_proposal(
            client, tmp_path, script="展示杯盖防泼溅。展示通勤携带效果。",
        )
        assert len(proposal["clips"]) == 2
        assert len(queue.jobs) == 1

        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)

        assert applied.status_code == 200, applied.text
        assert applied.json()["variant"]["revision"] == 1
        assert len(queue.jobs) == 1
        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        variant = client.get(base).json()["tasks"][0]["variant"]
        assert variant["revision"] == 2
        clips = variant["tracks"][0]["clips"]
        assert len(clips) == 2
        assert [clip["reframe"]["status"] for clip in clips] == ["review_required", "review_required"]
        assert [variant["reframeAnalysis"][clip["id"]]["status"] for clip in clips] == ["completed", "completed"]


def test_pending_proposal_apply_publishes_to_variant_without_overwriting_later_manual_reframe(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, proposal = _create_montage_proposal(client, tmp_path)
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert applied.status_code == 200, applied.text
        assert len(queue.jobs) == 1

        tracks = applied.json()["variant"]["tracks"]
        clip = tracks[0]["clips"][0]
        clip["reframe"]["override"] = {
            "mode": "manual", "keyframes": [{"timeSeconds": clip["inPoint"], "x": .2, "y": .7}],
        }
        manual = client.put(f"{base}/{task['id']}/variant", json={"revision": 1, "tracks": tracks}, headers=INTENT)
        assert manual.status_code == 200, manual.text

        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        variant = client.get(base).json()["tasks"][0]["variant"]
        stored = variant["tracks"][0]["clips"][0]["reframe"]
        assert variant["revision"] == 3
        assert stored["status"] == "review_required"
        assert stored["override"] == {"mode": "manual", "keyframes": [{"timeSeconds": 1.0, "x": .2, "y": .7}]}
        assert len(stored["samples"]) == 2


def test_reapplying_identical_source_preserves_manual_reframe_but_changed_range_or_source_does_not(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, first = _create_montage_proposal(client, tmp_path)
        first_applied = client.post(f"{base}/{task['id']}/montage-proposals/{first['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert first_applied.status_code == 200, first_applied.text
        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        tracks = client.get(base).json()["tasks"][0]["variant"]["tracks"]
        clip = tracks[0]["clips"][0]
        valid_reframe = clip["reframe"]
        clip["reframe"]["override"] = {
            "mode": "manual", "keyframes": [{"timeSeconds": clip["inPoint"], "x": .25, "y": .65}],
        }
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 2, "tracks": tracks}, headers=INTENT).status_code == 200

        same = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 3, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT)
        assert same.status_code == 201, same.text
        same_proposal = same.json()["proposal"]
        same_applied = client.post(f"{base}/{task['id']}/montage-proposals/{same_proposal['id']}/apply", json={
            "revision": 3, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert same_applied.status_code == 200, same_applied.text
        assert same_applied.json()["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "manual"

        changed_range = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 4, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        range_edit = client.put(f"{base}/{task['id']}/montage-proposals/{changed_range['id']}", json={
            "revision": 0,
            "segments": [{"segmentId": changed_range["segments"][0]["id"], "candidateId": "segment-lid",
                          "inPointSeconds": 1.5, "durationSeconds": 2.0}],
        }, headers=INTENT)
        assert range_edit.status_code == 200, range_edit.text
        range_applied = client.post(f"{base}/{task['id']}/montage-proposals/{changed_range['id']}/apply", json={
            "revision": 4, "proposalRevision": 1, "strategy": "overwrite",
        }, headers=INTENT)
        assert range_applied.status_code == 200, range_applied.text
        assert range_applied.json()["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "auto"

        tracks = range_applied.json()["variant"]["tracks"]
        clip = tracks[0]["clips"][0]
        clip["reframe"]["override"] = {
            "mode": "manual", "keyframes": [{"timeSeconds": clip["inPoint"], "x": .3, "y": .6}],
        }
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 5, "tracks": tracks}, headers=INTENT).status_code == 200
        changed_source = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 6, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        source_edit = client.put(f"{base}/{task['id']}/montage-proposals/{changed_source['id']}", json={
            "revision": 0,
            "segments": [{"segmentId": changed_source["segments"][0]["id"], "candidateId": "segment-commute",
                          "inPointSeconds": 1.0, "durationSeconds": 2.0}],
        }, headers=INTENT)
        assert source_edit.status_code == 200, source_edit.text
        source_applied = client.post(f"{base}/{task['id']}/montage-proposals/{changed_source['id']}/apply", json={
            "revision": 6, "proposalRevision": 1, "strategy": "overwrite",
        }, headers=INTENT)
        assert source_applied.status_code == 200, source_applied.text
        stored = source_applied.json()["variant"]["tracks"][0]["clips"][0]
        assert stored["assetId"] == "commute"
        assert stored["reframe"]["override"]["mode"] == "auto"


def test_proposal_same_asset_bytes_change_or_speed_change_never_reuses_manual_reframe(tmp_path, monkeypatch):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        project_id, base, task, first = _create_montage_proposal(client, tmp_path)
        assert client.post(f"{base}/{task['id']}/montage-proposals/{first['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT).status_code == 200
        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        tracks = client.get(base).json()["tasks"][0]["variant"]["tracks"]
        clip = tracks[0]["clips"][0]
        clip["reframe"]["override"] = {"mode": "manual", "keyframes": [{"timeSeconds": 1, "x": .2, "y": .7}]}
        clip["speed"] = 2
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 2, "tracks": tracks}, headers=INTENT).status_code == 200
        speed_changed = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 3, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{speed_changed['id']}/apply", json={
            "revision": 3, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert applied.status_code == 200, applied.text
        assert applied.json()["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "auto"

        byte_proposal = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 4, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        tracks = applied.json()["variant"]["tracks"]
        clip = tracks[0]["clips"][0]
        clip["speed"] = 1
        # A client may only change the current pending record's human override;
        # replaying `valid_reframe` facts would now correctly be rejected.
        clip["reframe"]["override"] = {"mode": "manual", "keyframes": [{"timeSeconds": 1, "x": .2, "y": .7}]}
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 4, "tracks": tracks}, headers=INTENT).status_code == 200
        from app import batch_editing_api
        frozen_search = batch_editing_api.load_planning_search(
            tmp_path, project_id, first["inputSnapshot"]["segments"][0]["searchId"],
        )
        source = tmp_path / "project-files" / project_id / "preproduction" / "assets" / "lid.mp4"
        source.write_bytes(b"replacement-bytes")
        # Keep proposal provenance readable solely to reach the independent
        # render identity gate under test; production normally blocks earlier
        # on this stale search too.
        monkeypatch.setattr(batch_editing_api, "load_planning_search", lambda *_args, **_kwargs: frozen_search)
        changed = client.post(f"{base}/{task['id']}/montage-proposals/{byte_proposal['id']}/apply", json={
            "revision": 5, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert changed.status_code == 200, changed.text
        assert changed.json()["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "auto"


def test_reapplying_empty_hash_manual_pending_does_not_inherit_it(tmp_path):
    app, _queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, first = _create_montage_proposal(client, tmp_path)
        applied = client.post(f"{base}/{task['id']}/montage-proposals/{first['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        tracks = applied.json()["variant"]["tracks"]
        tracks[0]["clips"][0]["reframe"]["override"] = {"mode": "manual", "keyframes": [{"timeSeconds": 1, "x": .2, "y": .7}]}
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 1, "tracks": tracks}, headers=INTENT).status_code == 200
        second = client.post(f"{base}/{task['id']}/montage-proposals", json={
            "revision": 2, "searchIds": [first["inputSnapshot"]["segments"][0]["searchId"]],
            "narration": [{"durationSeconds": 2, "wordBoundariesSeconds": []}], "beatTimes": [],
        }, headers=INTENT).json()["proposal"]
        again = client.post(f"{base}/{task['id']}/montage-proposals/{second['id']}/apply", json={
            "revision": 2, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT)
        assert again.status_code == 200, again.text
        assert again.json()["variant"]["tracks"][0]["clips"][0]["reframe"]["override"]["mode"] == "auto"


@pytest.mark.parametrize("field,value", [("assetId", "commute"), ("inPoint", 1.5), ("speed", 2)])
def test_adopted_proposal_job_never_overwrites_variant_identity_edited_after_apply(tmp_path, field, value):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        _project_id, base, task, proposal = _create_montage_proposal(client, tmp_path)
        assert client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT).status_code == 200
        tracks = client.get(base).json()["tasks"][0]["variant"]["tracks"]
        tracks[0]["clips"][0][field] = value
        assert client.put(f"{base}/{task['id']}/variant", json={"revision": 1, "tracks": tracks}, headers=INTENT).status_code == 200
        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        variant = client.get(base).json()["tasks"][0]["variant"]
        assert variant["revision"] == 2
        assert variant["tracks"][0]["clips"][0]["reframe"]["status"] == "review_required"
        assert variant["reframeAnalysis"][proposal["clips"][0]["id"]]["status"] == "failed"


def test_adopted_multi_clip_job_increments_once_when_first_result_succeeds_and_last_source_is_lost(tmp_path):
    app, queue = _proposal_reframe_app(tmp_path)
    with TestClient(app) as client:
        project_id, base, task, proposal = _create_montage_proposal(client, tmp_path, script="展示杯盖防泼溅。展示通勤携带效果。")
        assert client.post(f"{base}/{task['id']}/montage-proposals/{proposal['id']}/apply", json={
            "revision": 0, "proposalRevision": 0, "strategy": "overwrite",
        }, headers=INTENT).status_code == 200
        (tmp_path / "project-files" / project_id / "preproduction" / "assets" / "commute.mp4").unlink()
        _kind, pid, handler = queue.jobs[0]
        handler(pid)
        variant = client.get(base).json()["tasks"][0]["variant"]
        clips = variant["tracks"][0]["clips"]
        assert variant["revision"] == 2
        assert clips[0]["reframe"]["status"] == "review_required"
        assert clips[1]["reframe"]["status"] == "review_required"
