"""User-visible batch editing paths through the project API."""
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.batch_editing_api import create_batch_editing_router
from app.batch_recommendation import recommend
from app.main import create_app


INTENT = {"x-aivre-intent": "semantic-analysis"}


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
