"""Batch creation keeps each generated variant independently editable."""
import json
import shutil
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.batch_editing_api import create_batch_editing_router
from app.main import create_app


INTENT = {"x-aivre-intent": "semantic-analysis"}
CASES = [
    ("省时", "快速启动"),
    ("耐用", "防水测试"),
    ("续航", "持久续航"),
    ("智能", "智能提醒"),
    ("便携", "轻便携带"),
]


def prepare_project(client, tmp_path):
    project_id = client.post("/api/projects", json={"name": "批量短视频"}).json()["id"]
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    assets = []
    for index, (_, script) in enumerate(CASES):
        asset_id = f"shot{index}"
        filename = f"{asset_id}.mp4"
        (root / "assets" / filename).write_bytes(b"video")
        assets.append({"id": asset_id, "name": script, "kind": "video", "file": filename, "duration": 3})
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [], "assets": assets}), encoding="utf-8")
    base = f"/api/projects/{project_id}/batch-edits"
    parent = client.post(base, json={"sellingPoint": "批量任务", "script": "人工初稿"}, headers=INTENT).json()["task"]
    return base, parent


def test_bulk_request_creates_five_independent_script_variants(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        base, parent = prepare_project(client, tmp_path)
        items = [{"sellingPoint": point, "script": script} for point, script in CASES]
        url = f"{base}/{parent['id']}/variants/bulk"

        created = client.post(url, json={"items": items, "assetIds": [f"shot{i}" for i in range(5)]}, headers=INTENT)

        assert created.status_code == 201, created.text
        result = created.json()
        assert len(result["generationId"]) > 0
        assert len(result["tasks"]) == 5
        assert [task["sellingPoint"] for task in result["tasks"]] == [point for point, _ in CASES]
        assert [task["variant"]["tracks"][0]["clips"][0]["assetId"] for task in result["tasks"]] == [f"shot{i}" for i in range(5)]
        assert all(task["batchId"] == parent["id"] and task["generationId"] == result["generationId"] for task in result["tasks"])
        assert all(task["generation"]["status"] == "ready" and task["variant"]["revision"] == 0 for task in result["tasks"])
        assert len(client.get(base).json()["tasks"]) == 6

        first = result["tasks"][0]
        tracks = first["variant"]["tracks"]
        tracks[0]["clips"][0]["duration"] = 1
        saved = client.put(f"{base}/{first['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        repeated = client.post(url, json={"items": items, "assetIds": [f"shot{i}" for i in range(5)]}, headers=INTENT)
        assert repeated.status_code == 201, repeated.text
        assert repeated.json()["generationId"] != result["generationId"]
        reopened = client.get(base).json()["tasks"]
        assert reopened[1]["variant"]["tracks"][0]["clips"][0]["duration"] == 1
        assert reopened[2]["variant"]["revision"] == 0
        assert reopened[0]["variant"] == parent["variant"]


def test_bulk_state_rejects_orphaned_child(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        base, parent = prepare_project(client, tmp_path)
        project_id = base.split("/")[3]
        created = client.post(f"{base}/{parent['id']}/variants/bulk", json={
            "items": [{"sellingPoint": point, "script": script} for point, script in CASES],
            "assetIds": [f"shot{i}" for i in range(5)],
        }, headers=INTENT)
        assert created.status_code == 201, created.text
        path = tmp_path / "project-files" / project_id / "batch-edits" / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["tasks"][1]["batchId"] = "missing-parent"
        path.write_text(json.dumps(state), encoding="utf-8")
        response = client.get(base)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "batch_storage_invalid"


def test_bulk_limits_and_partial_match_failure_are_actionable(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        base, parent = prepare_project(client, tmp_path)
        url = f"{base}/{parent['id']}/variants/bulk"
        items = [{"sellingPoint": point, "script": script} for point, script in CASES]
        for invalid in (items[:4], items * 5, [items[0]] * 5):
            response = client.post(url, json={"items": invalid, "assetIds": [f"shot{i}" for i in range(5)]}, headers=INTENT)
            assert response.status_code == 422
        insufficient = client.post(url, json={"items": items, "assetIds": ["shot0"]}, headers=INTENT)
        assert insufficient.status_code == 422
        assert "两段" in insufficient.json()["detail"]["message"]
        assert client.get(base).json()["tasks"] == [parent]

        items[2]["script"] = "不存在的内容"
        created = client.post(url, json={"items": items, "assetIds": [f"shot{i}" for i in range(5)]}, headers=INTENT)
        assert created.status_code == 201, created.text
        tasks = created.json()["tasks"]
        assert [task["generation"]["status"] for task in tasks] == ["ready", "ready", "failed", "ready", "ready"]
        assert "第 1 段" in tasks[2]["generation"]["error"]
        assert tasks[2]["variant"]["tracks"][0]["clips"] == []
        assert tasks[3]["variant"]["tracks"][0]["clips"][0]["assetId"] == "shot3"
        manual_tracks = tasks[2]["variant"]["tracks"]
        manual_tracks[0]["clips"] = [{"id": "manual", "assetId": "shot2", "start": 0, "inPoint": 0, "duration": 2,
                                      "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        saved = client.put(f"{base}/{tasks[2]['id']}/variant", json={"revision": 0, "tracks": manual_tracks}, headers=INTENT)
        assert saved.status_code == 200, saved.text
        fixed = client.get(base).json()["tasks"][3]
        assert fixed["generation"]["status"] == "ready"
        assert fixed["generation"]["error"] is None


def test_bulk_previews_isolate_failure_cancellation_and_retry(tmp_path):
    project_id = "p1"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    assets = []
    for index, (_, script) in enumerate(CASES):
        asset_id = f"shot{index}"
        filename = f"{asset_id}.mp4"
        (root / "assets" / filename).write_bytes(b"video")
        assets.append({"id": asset_id, "name": script, "kind": "video", "file": filename, "duration": 3})
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 0, "brief": {}, "shots": [], "assets": assets}), encoding="utf-8")
    jobs = []
    broken = {"shot1"}
    running_cancel = {}

    class Queue:
        def submit(self, kind, pid, handler):
            jobs.append((pid, handler))
            return True

    def renderer(timeline, sources, output, **kwargs):
        if next(iter(sources)) in broken:
            raise RuntimeError("编码失败")
        if next(iter(sources)) == "shot3":
            cancelled = client.post(running_cancel["url"], json={})
            assert cancelled.status_code == 200
            assert kwargs["cancelled"]()
        output.write_bytes(b"preview")

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: object(), Queue(), renderer=renderer))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        parent = client.post(base, json={"sellingPoint": "批量任务", "script": "人工初稿"}).json()["task"]
        items = [{"sellingPoint": point, "script": script} for point, script in CASES]
        created = client.post(f"{base}/{parent['id']}/variants/bulk", json={"items": items, "assetIds": [f"shot{i}" for i in range(5)]}).json()
        children = created["tasks"]
        preview_url = f"{base}/{parent['id']}/generations/{created['generationId']}/previews"

        submitted = client.post(preview_url, json={})

        assert submitted.status_code == 202, submitted.text
        assert [item["status"] for item in submitted.json()["results"]] == ["queued"] * 5
        assert len(jobs) == 5
        pending = client.get(base).json()["tasks"][1:]
        running_cancel["url"] = f"{base}/{children[3]['id']}/variant/previews/{pending[3]['variant']['runs'][0]['id']}/cancel"
        run_id = pending[0]["variant"]["runs"][0]["id"]
        cancelled = client.post(f"{base}/{children[0]['id']}/variant/previews/{run_id}/cancel", json={})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["variant"]["runs"][0]["status"] == "cancelled"
        for pid, handler in jobs[:]:
            handler(pid)
        jobs.clear()
        states = client.get(base).json()["tasks"][1:]
        assert [task["variant"]["runs"][0]["status"] for task in states] == ["cancelled", "failed", "completed", "cancelled", "completed"]
        assert states[1]["generation"]["status"] == "ready"
        assert client.get(f"{base}/{children[2]['id']}/variant/previews/{states[2]['variant']['runs'][0]['id']}/output").content == b"preview"
        assert client.get(f"{base}/{children[3]['id']}/variant/previews/{states[3]['variant']['runs'][0]['id']}/output").status_code == 409

        broken.clear()
        retried = client.post(f"{base}/{children[1]['id']}/variant/previews", json={"revision": 0})
        assert retried.status_code == 202, retried.text
        pid, handler = jobs.pop()
        handler(pid)
        states = client.get(base).json()["tasks"][1:]
        assert [run["status"] for run in states[1]["variant"]["runs"]] == ["failed", "completed"]
        assert states[2]["variant"]["runs"][0]["status"] == "completed"
        resumed = client.post(preview_url, json={}).json()["results"]
        assert resumed[0]["status"] == "queued"
        assert resumed[2]["status"] == "completed"

    restarted = FastAPI()
    restarted.include_router(create_batch_editing_router(tmp_path, lambda _: object(), Queue(), renderer=renderer))
    with TestClient(restarted) as client:
        states = client.get(base).json()["tasks"][1:]
        assert states[0]["variant"]["runs"][-1]["status"] == "failed"
        assert "重启" in states[0]["variant"]["runs"][-1]["error"]
        assert states[2]["variant"]["runs"][0]["status"] == "completed"


def test_twenty_variant_load_keeps_independent_ids_and_revisions(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "大批量"}).json()["id"]
        root = tmp_path / "project-files" / project_id / "preproduction"
        (root / "assets").mkdir(parents=True)
        assets = []
        items = []
        for index in range(20):
            asset_id = f"shot{index}"
            (root / "assets" / f"{asset_id}.mp4").write_bytes(b"video")
            assets.append({"id": asset_id, "name": f"clip{index:02d}", "kind": "video", "file": f"{asset_id}.mp4", "duration": 2})
            items.append({"sellingPoint": f"卖点{index}", "script": f"clip{index:02d}"})
        (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [], "assets": assets}), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"
        parent = client.post(base, json={"sellingPoint": "批量", "script": "初稿"}, headers=INTENT).json()["task"]

        response = client.post(f"{base}/{parent['id']}/variants/bulk",
                               json={"items": items, "assetIds": [asset["id"] for asset in assets]}, headers=INTENT)

        assert response.status_code == 201, response.text
        tasks = response.json()["tasks"]
        assert len(tasks) == 20
        assert len({task["id"] for task in tasks}) == 20
        assert len({task["variant"]["id"] for task in tasks}) == 20
        assert all(task["generation"]["status"] == "ready" and task["variant"]["revision"] == 0 for task in tasks)
        assert len(client.get(base).json()["tasks"]) == 21


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="需要本地 FFmpeg")
def test_real_bulk_previews_keep_two_scripts_and_retry_one_missing_source(tmp_path):
    project_id = "p1"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    assets = []
    colors = ("red", "blue", "green", "yellow", "white")
    for index, ((_, script), color) in enumerate(zip(CASES, colors)):
        asset_id = f"shot{index}"
        filename = f"{asset_id}.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"color=c={color}:s=64x48:r=24:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(root / "assets" / filename)], check=True)
        assets.append({"id": asset_id, "name": script, "kind": "video", "file": filename, "duration": 1})
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 0, "brief": {}, "shots": [], "assets": assets}), encoding="utf-8")
    jobs = []

    class Queue:
        def submit(self, kind, pid, handler):
            jobs.append((pid, handler))
            return True

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: object(), Queue()))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        parent = client.post(base, json={"sellingPoint": "批量", "script": "初稿"}).json()["task"]
        created = client.post(f"{base}/{parent['id']}/variants/bulk", json={
            "items": [{"sellingPoint": point, "script": script} for point, script in CASES],
            "assetIds": [asset["id"] for asset in assets],
        }).json()
        children = created["tasks"]
        source = root / "assets" / "shot2.mp4"
        missing = source.read_bytes()
        source.unlink()
        preview_url = f"{base}/{parent['id']}/generations/{created['generationId']}/previews"
        started = client.post(preview_url, json={})
        assert started.status_code == 202, started.text
        assert [item["status"] for item in started.json()["results"]] == ["queued", "queued", "failed", "queued", "queued"]
        failed_item = client.get(base).json()["tasks"][3]
        assert failed_item["variant"]["runs"][-1]["status"] == "failed"
        assert "素材文件不可用" in failed_item["variant"]["runs"][-1]["error"]
        for pid, handler in jobs[:2]:
            handler(pid)
        states = client.get(base).json()["tasks"][1:]
        assert states[0]["variant"]["runs"][0]["status"] == "completed"
        assert states[1]["variant"]["runs"][0]["status"] == "completed"
        outputs = [client.get(f"{base}/{task['id']}/variant/previews/{state['variant']['runs'][0]['id']}/output").content
                   for task, state in zip(children[:2], states[:2])]
        assert outputs[0] != outputs[1]
        source.write_bytes(missing)
        retried = client.post(preview_url, json={})
        assert [item["status"] for item in retried.json()["results"]] == ["completed", "completed", "queued", "queued", "queued"]
        pid, handler = jobs[-1]
        handler(pid)
        assert [run["status"] for run in client.get(base).json()["tasks"][3]["variant"]["runs"]] == ["failed", "completed"]
