"""Review decisions are bound to a completed preview of one saved variant version."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.batch_editing_api import create_batch_editing_router


def test_review_lifecycle_and_version_isolation(tmp_path):
    root = tmp_path / "project-files" / "p1" / "preproduction"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "shot.mp4").write_bytes(b"video")
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 0, "brief": {}, "shots": [],
        "assets": [{"id": "shot", "name": "镜头", "kind": "video", "file": "shot.mp4", "duration": 4}]}), encoding="utf-8")
    jobs = []
    broken = [False]

    class Queue:
        def submit(self, kind, project_id, handler):
            jobs.append((project_id, handler))
            return True

    def renderer(timeline, sources, output, **kwargs):
        if broken[0]:
            raise RuntimeError("渲染失败")
        output.write_bytes(b"preview")

    def client():
        app = FastAPI()
        app.include_router(create_batch_editing_router(tmp_path, lambda _: object(), Queue(), renderer=renderer))
        return TestClient(app)

    def preview(api, task_id, revision):
        response = api.post(f"{base}/{task_id}/variant/previews", json={"revision": revision})
        assert response.status_code == 202, response.text
        run_id = response.json()["variant"]["runs"][-1]["id"]
        project_id, handler = jobs.pop()
        handler(project_id)
        return run_id

    def review(api, task_id, run_id, decision, reason):
        return api.post(f"{base}/{task_id}/reviews", json={"runId": run_id, "decision": decision, "reason": reason})

    def status(api, task_id):
        return next(task for task in api.get(base).json()["tasks"] if task["id"] == task_id)

    base = "/api/projects/p1/batch-edits"
    with client() as api:
        first = api.post(base, json={"sellingPoint": "省时", "script": "操作演示"}).json()["task"]
        second = api.post(base, json={"sellingPoint": "易用", "script": "功能展示"}).json()["task"]
        for task in (first, second):
            tracks = task["variant"]["tracks"]
            tracks[0]["clips"] = [{"id": "clip", "assetId": "shot", "start": 0, "inPoint": 0, "duration": 2,
                                   "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
            assert api.put(f"{base}/{task['id']}/variant", json={"revision": 0, "tracks": tracks}).status_code == 200
        first_run = preview(api, first["id"], 1)
        second_run = preview(api, second["id"], 1)
        assert review(api, first["id"], first_run, "rejected", "字幕不准确").status_code == 200
        assert status(api, first["id"])["reviewStatus"] == "rejected"
        assert review(api, first["id"], first_run, "approved", "画面与脚本匹配").status_code == 200
        assert review(api, second["id"], second_run, "approved", "可发布").status_code == 200
        assert status(api, first["id"])["reviewStatus"] == "approved"

        edited = api.put(f"{base}/{first['id']}/content", json={"revision": 0, "sellingPoint": "省时", "script": "新脚本"})
        assert edited.status_code == 200, edited.text
        assert status(api, first["id"])["reviewStatus"] == "stale"
        assert status(api, second["id"])["reviewStatus"] == "approved"
        assert review(api, first["id"], first_run, "approved", "旧预览").status_code == 409
        assert api.put(f"{base}/{first['id']}/variant", json={"revision": 1, "tracks": tracks,
            "reviewStatus": "approved"}).status_code == 422
        assert api.put(f"{base}/{first['id']}/content", json={"revision": 0, "sellingPoint": "省时", "script": "伪造",
            "reviewStatus": "approved"}).status_code == 422

        broken[0] = True
        failed_run = preview(api, first["id"], 1)
        assert review(api, first["id"], failed_run, "approved", "失败预览").status_code == 409
        assert review(api, first["id"], first_run, "approved", "绕过失败预览").status_code == 409
        broken[0] = False
        current_run = preview(api, first["id"], 1)
        assert review(api, first["id"], current_run, "approved", "新预览").status_code == 200

        subtitles = [{"id": "cue", "start": 0, "end": 1, "text": "人工修订"}]
        assert api.put(f"{base}/{first['id']}/subtitles", json={"revision": 0, "timelineRevision": 1, "cues": subtitles}).status_code == 200
        assert status(api, first["id"])["reviewStatus"] == "stale"
        assert review(api, first["id"], current_run, "approved", "旧字幕").status_code == 409
        current_run = preview(api, first["id"], 1)
        assert review(api, first["id"], current_run, "approved", "字幕已确认").status_code == 200

        tracks[0]["clips"][0]["inPoint"] = 0.5
        assert api.put(f"{base}/{first['id']}/variant", json={"revision": 1, "tracks": tracks}).status_code == 200
        assert status(api, first["id"])["reviewStatus"] == "stale"
        current_run = preview(api, first["id"], 2)
        assert review(api, first["id"], current_run, "approved", "镜头已确认").status_code == 200

        changed_ratio = api.put(f"{base}/{first['id']}/variant", json={"revision": 2, "tracks": tracks,
            "settings": {"width": 1280, "height": 720, "fps": 30}, "aspectMode": "16:9"})
        assert changed_ratio.status_code == 200, changed_ratio.text
        assert changed_ratio.json()["variant"]["resolvedAspect"] == "16:9"
        assert status(api, first["id"])["reviewStatus"] == "stale"
        assert api.put(f"{base}/{first['id']}/variant", json={"revision": 3, "tracks": tracks,
            "settings": {"width": 720, "height": 1280, "fps": 24}}).status_code == 422
        saved = api.put(f"{base}/{first['id']}/content", json={"revision": 1, "sellingPoint": "省时", "script": "再次调整脚本"})
        assert saved.status_code == 200, saved.text
        assert status(api, first["id"])["reviewStatus"] == "stale"
        assert review(api, first["id"], current_run, "approved", "旧脚本").status_code == 409
        assert status(api, second["id"])["reviewStatus"] == "approved"

    with client() as reopened:
        first_state = status(reopened, first["id"])
        assert first_state["reviewStatus"] == "stale"
        assert [item["decision"] for item in first_state["reviews"]] == ["rejected", "approved", "approved", "approved", "approved"]
        assert status(reopened, second["id"])["reviewStatus"] == "approved"
        final_run = preview(reopened, first["id"], 3)
        assert review(reopened, first["id"], final_run, "approved", "输出已确认").status_code == 200
        assert status(reopened, first["id"])["reviewStatus"] == "approved"
