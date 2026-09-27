"""Only approved current variants can enter durable batch delivery runs."""
import json
import shutil
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.batch_editing_api import create_batch_editing_router


def test_batch_export_gate_isolation_retry_and_history(tmp_path):
    root = tmp_path / "project-files" / "p1" / "preproduction"
    (root / "assets").mkdir(parents=True)
    for index in range(5):
        (root / "assets" / f"shot{index}.mp4").write_bytes(f"source-{index}".encode())
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 0, "brief": {}, "shots": [],
        "assets": [{"id": f"shot{i}", "name": f"卖点{i}", "kind": "video", "file": f"shot{i}.mp4", "duration": 2}
                   for i in range(5)]}), encoding="utf-8")
    jobs = []
    broken = {"shot1"}
    rendered_sources = {}

    class Queue:
        def submit(self, kind, project_id, handler):
            jobs.append((kind, project_id, handler))
            return True

    def renderer(timeline, sources, output, **kwargs):
        if kwargs["format"] == "mp4" and any(asset_id in broken for asset_id in sources):
            raise RuntimeError("导出失败")
        if kwargs["format"] == "mp4":
            rendered_sources.update({asset_id: source.read_bytes() for asset_id, source in sources.items()})
        output.write_bytes(b"delivery" if kwargs["format"] == "mp4" else b"preview")

    def client():
        app = FastAPI()
        app.include_router(create_batch_editing_router(tmp_path, lambda _: object(), Queue(), renderer=renderer))
        return TestClient(app)

    def drain():
        pending = list(jobs)
        jobs.clear()
        for _, project_id, handler in pending:
            handler(project_id)

    base = "/api/projects/p1/batch-edits"
    with client() as api:
        parent = api.post(base, json={"sellingPoint": "批量", "script": "初稿"}).json()["task"]
        items = [{"sellingPoint": f"卖点{i}", "script": f"卖点{i}"} for i in range(5)]
        created = api.post(f"{base}/{parent['id']}/variants/bulk", json={"items": items,
            "assetIds": [f"shot{i}" for i in range(5)]})
        assert created.status_code == 201, created.text
        children = created.json()["tasks"]
        export_url = f"{base}/{parent['id']}/exports"
        first, second, third = children[:3]
        assert api.post(export_url, json={"taskIds": [first["id"]]}).status_code == 409
        assert api.put(f"{base}/{first['id']}/variant", json={"revision": 0, "tracks": first["variant"]["tracks"],
            "settings": {"width": 720, "height": 1280, "fps": 24}}).status_code == 422
        for index, child in enumerate((first, second, third)):
            tracks = child["variant"]["tracks"]
            tracks[0]["clips"] = [{"id": f"clip{index}", "assetId": f"shot{index}", "start": 0, "inPoint": 0,
                                   "duration": 2, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
            saved = api.put(f"{base}/{child['id']}/variant", json={"revision": 0, "tracks": tracks})
            assert saved.status_code == 200, saved.text
            preview = api.post(f"{base}/{child['id']}/variant/previews", json={"revision": 1})
            assert preview.status_code == 202, preview.text
        drain()
        assert api.post(export_url, json={"taskIds": [first["id"]]}).status_code == 409
        for child in (first, second, third):
            run_id = next(task for task in api.get(base).json()["tasks"] if task["id"] == child["id"])["variant"]["runs"][-1]["id"]
            approved = api.post(f"{base}/{child['id']}/reviews", json={"runId": run_id,
                "decision": "approved", "reason": "内容确认"})
            assert approved.status_code == 200, approved.text
        cues = [{"id": "line", "start": 0.2, "end": 1.5, "text": "修订字幕"}]
        assert api.put(f"{base}/{first['id']}/subtitles", json={"revision": 0, "timelineRevision": 1, "cues": cues}).status_code == 200
        assert api.post(export_url, json={"taskIds": [first["id"]]}).status_code == 409
        assert api.post(f"{base}/{first['id']}/variant/previews", json={"revision": 1}).status_code == 202
        drain()
        assert api.post(export_url, json={"taskIds": [first["id"]]}).status_code == 409
        refreshed_run_id = api.get(base).json()["tasks"][1]["variant"]["runs"][-1]["id"]
        assert api.post(f"{base}/{first['id']}/reviews", json={"runId": refreshed_run_id,
            "decision": "approved", "reason": "修订字幕已确认"}).status_code == 200
        assert api.post(export_url, json={"taskIds": [first["id"], children[3]["id"]]}).status_code == 409
        state_path = tmp_path / "project-files" / "p1" / "batch-edits" / "state.json"
        saved_state = json.loads(state_path.read_text(encoding="utf-8"))
        changed_state = json.loads(json.dumps(saved_state))
        changed_state["tasks"][1]["variant"]["settings"]["fps"] = 24
        state_path.write_text(json.dumps(changed_state), encoding="utf-8")
        assert api.get(base).json()["tasks"][1]["reviewStatus"] == "stale"
        assert api.post(export_url, json={"taskIds": [first["id"]]}).status_code == 409
        state_path.write_text(json.dumps(saved_state), encoding="utf-8")

        submitted = api.post(export_url, json={"taskIds": [first["id"], second["id"], third["id"]]})
        assert submitted.status_code == 202, submitted.text
        assert [item["status"] for item in submitted.json()["results"]] == ["queued"] * 3
        queued = api.get(base).json()["tasks"]
        first_export = queued[1]["variant"]["exports"][0]
        second_export = queued[2]["variant"]["exports"][0]
        third_export = queued[3]["variant"]["exports"][0]
        assert first_export["script"] == "卖点0" and first_export["sellingPoint"] == "卖点0"
        assert first_export["assets"][0]["name"] == "卖点0"
        assert first_export["snapshot"]["settings"] == {"width": 720, "height": 1280, "fps": 30}
        assert first_export["review"]["variantRevision"] == 1
        assert api.post(f"{base}/{third['id']}/exports/{third_export['id']}/cancel", json={}).status_code == 200
        (root / "assets" / "shot0.mp4").write_bytes(b"changed after submission")
        drain()
        assert rendered_sources["shot0"] == b"source-0"
        states = api.get(base).json()["tasks"]
        assert [states[i]["variant"]["exports"][0]["status"] for i in (1, 2, 3)] == ["completed", "failed", "cancelled"]
        assert states[1]["variant"]["exports"][0]["url"]
        download = f"{base}/{first['id']}/exports/{first_export['id']}/output"
        assert api.get(download).content == b"delivery"
        assert api.get(f"{base}/{second['id']}/exports/{second_export['id']}/output").status_code == 409

        broken.clear()
        retried = api.post(export_url, json={"taskIds": [second["id"], third["id"]]})
        assert retried.status_code == 202, retried.text
        drain()
        states = api.get(base).json()["tasks"]
        assert [run["status"] for run in states[2]["variant"]["exports"]] == ["failed", "completed"]
        assert [run["status"] for run in states[3]["variant"]["exports"]] == ["cancelled", "completed"]
        assert api.get(download).content == b"delivery"
        assert api.post(export_url, json={"taskIds": [first["id"]]}).json()["results"][0]["status"] == "completed"

        changed = api.put(f"{base}/{first['id']}/content", json={"revision": 0,
            "sellingPoint": "卖点0", "script": "新脚本"})
        assert changed.status_code == 200
        assert api.post(export_url, json={"taskIds": [first["id"]]}).status_code == 409
        assert api.get(download).content == b"delivery"
        assert api.get(base).json()["tasks"][1]["variant"]["exports"][0]["script"] == "卖点0"
        changed_second = api.put(f"{base}/{second['id']}/content", json={"revision": 0,
            "sellingPoint": "卖点1", "script": "新版卖点1"})
        assert changed_second.status_code == 200
        fresh_preview = api.post(f"{base}/{second['id']}/variant/previews", json={"revision": 1})
        assert fresh_preview.status_code == 202
        drain()
        run_id = api.get(base).json()["tasks"][2]["variant"]["runs"][-1]["id"]
        assert api.post(f"{base}/{second['id']}/reviews", json={"runId": run_id,
            "decision": "approved", "reason": "新版确认"}).status_code == 200
        assert api.put(f"{base}/{third['id']}/content", json={"revision": 0,
            "sellingPoint": "卖点2", "script": "新版卖点2"}).status_code == 200
        assert api.post(f"{base}/{third['id']}/variant/previews", json={"revision": 1}).status_code == 202
        drain()
        run_id = api.get(base).json()["tasks"][3]["variant"]["runs"][-1]["id"]
        assert api.post(f"{base}/{third['id']}/reviews", json={"runId": run_id,
            "decision": "approved", "reason": "新版确认"}).status_code == 200
        interrupted = api.post(export_url, json={"taskIds": [third["id"]]})
        assert interrupted.status_code == 202 and interrupted.json()["results"][0]["status"] == "queued"

    (root / "assets" / "shot1.mp4").write_bytes(b"tampered source")
    with client() as reopened:
        states = reopened.get(base).json()["tasks"]
        assert states[2]["variant"]["exports"][-1]["status"] == "completed"
        assert states[2]["variant"]["exports"][-1]["assets"][0]["name"] == "卖点1"
        assert states[3]["variant"]["exports"][-1]["status"] == "failed"
        assert "重启" in states[3]["variant"]["exports"][-1]["error"]
        assert reopened.get(download).content == b"delivery"
        changed_source = reopened.post(export_url, json={"taskIds": [second["id"]]})
        assert changed_source.status_code == 202
        assert changed_source.json()["results"][0]["status"] == "failed"
        (root / "assets" / "shot1.mp4").unlink()
        missing = reopened.post(export_url, json={"taskIds": [second["id"]]})
        assert missing.status_code == 202 and missing.json()["results"][0]["status"] == "failed"
        assert reopened.get(base).json()["tasks"][2]["variant"]["exports"][0]["script"] == "卖点1"
        retry = reopened.post(export_url, json={"taskIds": [third["id"]]})
        assert retry.status_code == 202 and retry.json()["results"][0]["status"] == "queued"
        drain()
        assert [run["status"] for run in reopened.get(base).json()["tasks"][3]["variant"]["exports"]][-2:] == ["failed", "completed"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="需要 FFmpeg")
def test_export_uses_real_horizontal_mp4_with_saved_subtitles(tmp_path):
    root = tmp_path / "project-files" / "p1" / "preproduction"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "black.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=128x96:r=24:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
    shutil.copyfile(source, root / "assets" / "backup.mp4")
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 0, "brief": {}, "shots": [],
        "assets": [{"id": "black", "name": "黑色素材", "kind": "video", "file": "black.mp4", "duration": 2},
                   {"id": "backup", "name": "备用素材", "kind": "video", "file": "backup.mp4", "duration": 2}]}), encoding="utf-8")
    jobs = []

    class Queue:
        def submit(self, kind, project_id, handler):
            jobs.append((project_id, handler))
            return True

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: object(), Queue()))
    with TestClient(app) as api:
        base = "/api/projects/p1/batch-edits"
        parent = api.post(base, json={"sellingPoint": "批量", "script": "初稿"}).json()["task"]
        created = api.post(f"{base}/{parent['id']}/variants/bulk", json={"items": [
            {"sellingPoint": f"卖点{i}", "script": f"脚本{i}"} for i in range(5)], "assetIds": ["black", "backup"]})
        assert created.status_code == 201, created.text
        child = created.json()["tasks"][0]
        tracks = child["variant"]["tracks"]
        tracks[0]["clips"] = [{"id": "shot", "assetId": "black", "start": 0, "inPoint": 0, "duration": 2,
                               "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        assert api.put(f"{base}/{child['id']}/variant", json={"revision": 0, "tracks": tracks,
            "aspectMode": "16:9", "settings": {"width": 1280, "height": 720, "fps": 30}}).status_code == 200
        cues = [{"id": "caption", "start": 0.2, "end": 1.5, "text": "人工确认字幕"}]
        assert api.put(f"{base}/{child['id']}/subtitles", json={"revision": 0, "timelineRevision": 1, "cues": cues}).status_code == 200
        assert api.post(f"{base}/{child['id']}/variant/previews", json={"revision": 1}).status_code == 202
        project_id, handler = jobs.pop()
        handler(project_id)
        preview = next(task for task in api.get(base).json()["tasks"] if task["id"] == child["id"])["variant"]["runs"][-1]
        assert preview["status"] == "completed", preview
        assert api.post(f"{base}/{child['id']}/reviews", json={"runId": preview["id"],
            "decision": "approved", "reason": "字幕确认"}).status_code == 200
        queued = api.post(f"{base}/{parent['id']}/exports", json={"taskIds": [child["id"]]})
        assert queued.status_code == 202, queued.text
        project_id, handler = jobs.pop()
        handler(project_id)
        delivery = next(task for task in api.get(base).json()["tasks"] if task["id"] == child["id"])["variant"]["exports"][-1]
        assert delivery["status"] == "completed", delivery
        assert delivery["subtitles"] == cues
        subtitle_file = tmp_path / "project-files" / "p1" / "batch-edits" / "exports" / delivery["id"] / "subtitles.srt"
        assert "人工确认字幕" in subtitle_file.read_text(encoding="utf-8")
        output = tmp_path / "delivered.mp4"
        output.write_bytes(api.get(f"{base}/{child['id']}/exports/{delivery['id']}/output").content)
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", str(output)]))
        assert (probe["streams"][0]["width"], probe["streams"][0]["height"]) == (1280, 720)
        assert probe["streams"][0]["r_frame_rate"] == "30/1"
        frame = subprocess.check_output(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", "0.8",
                                         "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        before = subprocess.check_output(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", "0.05",
                                          "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        assert max(frame) > 30 and max(before) < max(frame)
