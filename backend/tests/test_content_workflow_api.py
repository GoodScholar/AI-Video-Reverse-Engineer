"""Public API for the derived content-production workflow."""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def write_saved_scripts(root, project_id):
    preparation = root / "project-files" / project_id / "preproduction"
    preparation.mkdir(parents=True)
    (preparation / "state.json").write_text(json.dumps({
        "schemaVersion": 1,
        "revision": 1,
        "brief": {},
        "shots": [],
        "assets": [
            {"id": "front", "name": "商品正面", "kind": "image", "file": "front.png"},
            {"id": "use", "name": "使用场景", "kind": "video", "file": "use.mp4", "duration": 3},
        ],
    }), encoding="utf-8")
    content = root / "project-files" / project_id / "aigc-content"
    content.mkdir(parents=True)
    brief = {
        "revision": 1,
        "productName": "晴雨杯",
        "facts": [{"id": "fact-1", "text": "杯盖防泼溅"}],
        "audience": "通勤者",
        "sellingPoints": ["便携"],
        "callToAction": "查看详情",
        "forbiddenPhrases": [],
        "assetIds": ["front", "use"],
        "aspectMode": "9:16",
    }
    candidates = [{
        "id": f"candidate-{index}",
        "generationId": "generation-1",
        "revision": 0,
        "briefRevision": 1,
        "confirmedRevision": 0,
        "sellingPoint": f"卖点 {index}",
        "beats": [],
    } for index in range(5)]
    (content / "state.json").write_text(json.dumps({
        "schemaVersion": 1, "brief": brief, "candidates": candidates,
    }), encoding="utf-8")


def test_content_workflow_get_returns_saved_projection_after_reopen(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "商品短视频"}).json()["id"]
        write_saved_scripts(tmp_path, project_id)
        url = f"/api/projects/{project_id}/content-workflow"
        response = client.get(url)

    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "scripts_confirmed"
    with TestClient(create_app(tmp_path)) as reopened:
        assert reopened.get(url).json() == response.json()


@pytest.mark.parametrize("relative", ["aigc-content/state.json", "batch-edits/state.json"])
def test_content_workflow_returns_503_for_corrupt_aigc_or_batch_state(tmp_path, relative):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "损坏状态"}).json()["id"]
        path = tmp_path / "project-files" / project_id / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", encoding="utf-8")

        response = client.get(f"/api/projects/{project_id}/content-workflow")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "content_workflow_storage_invalid"


def test_content_workflow_get_does_not_create_state_files(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "只读投影"}).json()["id"]
        state_paths = [
            tmp_path / "project-files" / project_id / "aigc-content" / "state.json",
            tmp_path / "project-files" / project_id / "batch-edits" / "state.json",
            tmp_path / "project-files" / project_id / "preproduction" / "state.json",
        ]

        response = client.get(f"/api/projects/{project_id}/content-workflow")

    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "draft"
    assert all(not path.exists() for path in state_paths)
