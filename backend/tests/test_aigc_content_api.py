"""Project-scoped AI marketing content creation through the public API."""

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.aigc_content_api import _validate_candidates, create_aigc_content_router
from app.analysis_providers.base import ProviderResult
from app.batch_editing_api import create_batch_editing_router
from app.main import create_app


INTENT = {"x-aivre-intent": "semantic-analysis"}


def test_generation_rejects_identical_screen_copy_with_different_selling_points():
    brief = {"facts": [{"id": "f1", "text": "杯盖防泼溅"}], "assetIds": ["front", "use"], "forbiddenPhrases": []}
    response = {"candidates": [{"sellingPoint": f"卖点 {index}", "beats": [
        {"text": "杯盖防泼溅", "factIds": ["f1"], "assetId": "front"},
        {"text": "通勤随手带", "factIds": ["f1"], "assetId": "use"},
    ]} for index in range(5)]}

    with pytest.raises(HTTPException) as error:
        _validate_candidates(json.dumps(response), brief)

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "aigc_candidates_duplicate"


def test_project_starts_with_an_empty_marketing_brief(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "商品短视频"}).json()["id"]

        response = client.get(f"/api/projects/{project_id}/aigc-content")

        assert response.status_code == 200, response.text
        assert response.json()["brief"] == {"revision": 0, "productName": "", "facts": [], "audience": "",
                                             "sellingPoints": [], "callToAction": "", "forbiddenPhrases": [], "assetIds": [],
                                             "aspectMode": "9:16"}
        assert response.json()["candidates"] == []


def test_team_can_save_and_reopen_a_product_brief_without_leaking_to_other_projects(tmp_path):
    brief = {"productName": "晴雨杯", "facts": [{"id": "fact-water", "text": "杯盖防泼溅"}],
             "audience": "通勤者", "sellingPoints": ["随身携带"], "callToAction": "查看详情",
             "forbiddenPhrases": ["绝对防水"], "assetIds": [], "aspectMode": "21:9"}
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "商品短视频"}).json()["id"]
        base = f"/api/projects/{project_id}/aigc-content"

        saved = client.put(base + "/brief", json={"revision": 0, "brief": brief}, headers=INTENT)

        assert saved.status_code == 200, saved.text
        assert saved.json()["brief"] == {**brief, "revision": 1}
        assert client.get(base).json()["brief"] == {**brief, "revision": 1}
        assert client.put(base + "/brief", json={"revision": 0, "brief": brief}, headers=INTENT).status_code == 409
        other_id = client.post("/api/projects", json={"name": "其他商品"}).json()["id"]
        assert client.get(f"/api/projects/{other_id}/aigc-content").json()["brief"]["productName"] == ""


def test_disclosure_shows_exact_text_without_media_or_storage_paths(tmp_path):
    with TestClient(create_app(tmp_path, provider_registry=lambda **_: SimpleNamespace(
        analyze=lambda request: ProviderResult(rawText='{"candidates":[]}')))) as client:
        project_id = client.post("/api/projects", json={"name": "商品短视频"}).json()["id"]
        asset_root = tmp_path / "project-files" / project_id / "preproduction"
        asset_root.mkdir(parents=True)
        (asset_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [], "assets": [
            {"id": "front", "name": "杯子正面", "notes": "可见杯盖", "kind": "image", "file": "private-front.png"},
            {"id": "use", "name": "通勤演示", "notes": "放入背包", "kind": "video", "file": "private-use.mp4", "duration": 4},
        ]}), encoding="utf-8")
        brief = {"productName": "晴雨杯", "facts": [{"id": "f1", "text": "杯盖防泼溅"}], "audience": "通勤者",
                 "sellingPoints": ["携带方便"], "callToAction": "查看详情", "forbiddenPhrases": ["绝对防水"],
                 "assetIds": ["front", "use"]}
        base = f"/api/projects/{project_id}/aigc-content"
        assert client.put(base + "/brief", json={"revision": 0, "brief": brief}, headers=INTENT).status_code == 200
        visible_assets = client.get(base).json()["assets"]
        assert [(asset["id"], asset["name"]) for asset in visible_assets] == [("front", "杯子正面"), ("use", "通勤演示")]
        assert all("file" not in asset for asset in visible_assets)

        shown = client.post(base + "/disclosure", json={"provider": "local_openai_compatible", "model": "local-model"}, headers=INTENT)

        assert shown.status_code == 200, shown.text
        payload = shown.json()
        assert payload["briefRevision"] == 1 and len(payload["digest"]) == 64
        assert "杯盖防泼溅" in payload["prompt"] and "杯子正面" in payload["prompt"]
        assert "可见杯盖" in payload["prompt"] and "绝对防水" in payload["prompt"]
        assert "private-front.png" not in payload["prompt"] and "private-use.mp4" not in payload["prompt"]
        assert str(tmp_path) not in payload["prompt"]

        denied = client.post(base + "/candidates", json={"briefRevision": 1, "provider": "local_openai_compatible",
                                                       "model": "local-model", "digest": payload["digest"],
                                                       "disclosureAccepted": False}, headers=INTENT)
        assert denied.status_code == 422
        assert client.get(base).json()["candidates"] == []
        unconfigured = client.post(base + "/candidates", json={"briefRevision": 1, "provider": "local_openai_compatible",
                                                            "model": "local-model", "digest": payload["digest"],
                                                            "disclosureAccepted": True}, headers=INTENT)
        assert unconfigured.status_code == 422
        assert unconfigured.json()["detail"]["code"] == "provider_unconfigured"
        configured = client.put("/api/analysis-providers/local_openai_compatible/configuration",
                                json={"model": "local-model", "baseUrl": "http://127.0.0.1:8188/v1"}, headers=INTENT)
        assert configured.status_code == 200, configured.text
        assert configured.json()["verificationState"] == "unverified"
        unverified = client.post(base + "/candidates", json={"briefRevision": 1, "provider": "local_openai_compatible",
                                                       "model": "local-model", "digest": payload["digest"],
                                                       "disclosureAccepted": True}, headers=INTENT)
        assert unverified.status_code == 422
        assert unverified.json()["detail"]["code"] == "provider_unconfigured"


def test_confirmed_disclosure_generates_five_saved_candidates(tmp_path):
    project_id = "marketing-1"
    asset_root = tmp_path / "project-files" / project_id / "preproduction"
    asset_root.mkdir(parents=True)
    (asset_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [], "assets": [
        {"id": "front", "name": "杯盖展示", "notes": "防泼溅", "kind": "image", "file": "front.png"},
        {"id": "use", "name": "通勤展示", "notes": "背包收纳", "kind": "video", "file": "use.mp4", "duration": 4},
    ]}), encoding="utf-8")
    calls = []

    def generate(provider, model, prompt, schema):
        calls.append((provider, model, prompt))
        if len(calls) == 2:
            changed = json.loads((asset_root / "state.json").read_text(encoding="utf-8"))
            changed["assets"][0]["notes"] = "新的商品说明"
            (asset_root / "state.json").write_text(json.dumps(changed), encoding="utf-8")
        return json.dumps({"candidates": [{"sellingPoint": f"卖点 {index}", "beats": [
            {"text": f"杯盖防泼溅 {index}", "factIds": ["f1"], "assetId": "front"},
            {"text": "通勤随手带", "factIds": ["f1"], "assetId": "use"},
        ]} for index in range(5)]})

    app = FastAPI()
    app.include_router(create_aigc_content_router(tmp_path, lambda _: True, script_generator=generate))
    base = f"/api/projects/{project_id}/aigc-content"
    brief = {"productName": "晴雨杯", "facts": [{"id": "f1", "text": "杯盖防泼溅"}], "audience": "通勤者",
             "sellingPoints": ["携带方便"], "callToAction": "查看详情", "forbiddenPhrases": ["绝对防水"],
             "assetIds": ["front", "use"]}
    with TestClient(app) as client:
        assert client.put(base + "/brief", json={"revision": 0, "brief": brief}).status_code == 200
        disclosure = client.post(base + "/disclosure", json={"provider": "local_openai_compatible", "model": "local-model"}).json()

        response = client.post(base + "/candidates", json={"briefRevision": disclosure["briefRevision"],
            "provider": disclosure["provider"], "model": disclosure["model"], "digest": disclosure["digest"],
            "disclosureAccepted": True})

        assert response.status_code == 201, response.text
        candidates = response.json()["candidates"]
        assert len(candidates) == 5
        assert candidates[0]["beats"][0]["assetId"] == "front"
        assert candidates[0]["briefRevision"] == 1
        assert client.get(base).json()["candidates"] == candidates
        assert len(calls) == 1 and calls[0][2] == disclosure["prompt"]

        stale = client.post(base + "/candidates", json={"briefRevision": disclosure["briefRevision"],
            "provider": disclosure["provider"], "model": disclosure["model"], "digest": disclosure["digest"],
            "disclosureAccepted": True})
        assert stale.status_code == 409
        assert client.get(base).json()["candidates"] == candidates

        first_url = f"{base}/candidates/{candidates[0]['id']}"
        confirmed = client.post(first_url + "/confirm", json={"revision": 0})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["candidate"]["confirmedRevision"] == 0
        edited = client.put(first_url, json={"revision": 0, "sellingPoint": "新卖点",
            "beats": candidates[0]["beats"]})
        assert edited.status_code == 200, edited.text
        assert edited.json()["candidate"]["revision"] == 1
        assert edited.json()["candidate"]["confirmedRevision"] is None
        assert client.post(first_url + "/confirm", json={"revision": 0}).status_code == 409
        reopened = client.get(base).json()["candidates"]
        assert reopened[0]["sellingPoint"] == "新卖点"
        assert reopened[1]["sellingPoint"] == "卖点 1"


def test_only_five_current_confirmed_candidates_enter_batch_editing(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "晴雨杯营销"}).json()["id"]
        project_root = tmp_path / "project-files" / project_id
        asset_root = project_root / "preproduction"
        (asset_root / "assets").mkdir(parents=True)
        assets = [{"id": "front", "name": "杯盖防泼溅", "notes": "杯盖防泼溅", "kind": "image", "file": "front.png", "width": 1920, "height": 1080},
                  {"id": "use", "name": "通勤收纳", "notes": "通勤收纳", "kind": "video", "file": "use.mp4", "duration": 4, "width": 1280, "height": 720}]
        for asset in assets:
            (asset_root / "assets" / asset["file"]).write_bytes(b"material")
        (asset_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
                                                           "assets": assets}), encoding="utf-8")
        brief = {"revision": 1, "productName": "晴雨杯", "facts": [{"id": "f1", "text": "杯盖防泼溅"}],
                 "audience": "通勤者", "sellingPoints": ["防泼溅"], "callToAction": "查看详情",
                 "forbiddenPhrases": ["绝对防水"], "assetIds": ["front", "use"], "aspectMode": "smart"}
        candidates = [{"id": f"c{index}", "generationId": "g1", "revision": 0, "briefRevision": 1,
                       "confirmedRevision": 0, "sellingPoint": f"卖点 {index}", "beats": [
                           {"text": f"杯盖防泼溅 {index}", "factIds": ["f1"], "assetId": "front"},
                           {"text": "通勤收纳", "factIds": ["f1"], "assetId": "use"}],
                       "provider": "local_openai_compatible", "model": "local-model", "promptDigest": "digest"}
                      for index in range(5)]
        content_root = project_root / "aigc-content"
        content_root.mkdir(parents=True)
        (content_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "brief": brief, "candidates": candidates}), encoding="utf-8")
        base = f"/api/projects/{project_id}/batch-edits"

        duplicated = json.loads(json.dumps(candidates))
        duplicated[1]["sellingPoint"] = duplicated[0]["sellingPoint"]
        duplicated[1]["beats"] = duplicated[0]["beats"]
        (content_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "brief": brief, "candidates": duplicated}), encoding="utf-8")
        assert client.post(base + "/from-aigc", json={"generationId": "g1"}, headers=INTENT).status_code == 422
        (content_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "brief": brief, "candidates": candidates}), encoding="utf-8")

        created = client.post(base + "/from-aigc", json={"generationId": "g1"}, headers=INTENT)

        assert created.status_code == 201, created.text
        tasks = client.get(base).json()["tasks"]
        assert len(tasks) == 6
        children = [task for task in tasks if task.get("batchId") == created.json()["task"]["id"]]
        assert len(children) == 5
        assert children[0]["script"] == "杯盖防泼溅 0。通勤收纳"
        assert [clip["assetId"] for clip in children[0]["variant"]["tracks"][0]["clips"]] == ["front", "use"]
        assert [(cue["start"], cue["end"], cue["text"]) for cue in children[0]["variant"]["subtitles"]["cues"]] == [
            (0, 3, "杯盖防泼溅 0"), (3, 6, "通勤收纳")]
        assert children[0]["variant"]["aspectMode"] == "smart"
        assert children[0]["variant"]["resolvedAspect"] == "16:9"
        assert children[0]["variant"]["settings"] == {"width": 1280, "height": 720, "fps": 30}
        assert "主视觉素材" in children[0]["variant"]["aspectReason"]
        saved_smart = client.put(f"{base}/{children[0]['id']}/variant", json={
            "revision": 0, "tracks": children[0]["variant"]["tracks"], "aspectMode": "smart",
            "settings": {"width": 1280, "height": 720, "fps": 30}}, headers=INTENT)
        assert saved_smart.status_code == 200, saved_smart.text
        assert "主视觉素材" in saved_smart.json()["variant"]["aspectReason"]
        assert children[0]["aigcSource"]["candidateId"] == "c0"
        assert children[0]["aigcSource"]["brief"] == brief
        assert children[0]["aigcSource"]["beats"] == candidates[0]["beats"]
        assert children[0]["aigcSource"]["assets"] == [
            {"id": "front", "name": "杯盖防泼溅", "kind": "image"},
            {"id": "use", "name": "通勤收纳", "kind": "video"}]
        again = client.post(base + "/from-aigc", json={"generationId": "g1"}, headers=INTENT)
        assert again.status_code == 200 and again.json()["task"]["id"] == created.json()["task"]["id"]
        assert len(client.get(base).json()["tasks"]) == 6
        edited = client.put(f"/api/projects/{project_id}/aigc-content/candidates/c0",
            json={"revision": 0, "sellingPoint": "新卖点", "beats": [
                {**candidates[0]["beats"][0], "text": "新版文案"}, candidates[0]["beats"][1]]}, headers=INTENT)
        assert edited.status_code == 200, edited.text
        original = next(task for task in client.get(base).json()["tasks"] if task["id"] == children[0]["id"])
        assert original["aigcSource"]["beats"] == candidates[0]["beats"]
        revised_brief = {**brief, "audience": "新的受众"}
        assert client.put(f"/api/projects/{project_id}/aigc-content/brief",
            json={"revision": 1, "brief": {key: value for key, value in revised_brief.items() if key != "revision"}},
            headers=INTENT).status_code == 200
        original = next(task for task in client.get(base).json()["tasks"] if task["id"] == children[0]["id"])
        assert original["aigcSource"]["brief"] == brief


@pytest.mark.parametrize("edited_failed_task", [False, True])
def test_aigc_handoff_keeps_valid_variants_when_one_source_file_is_missing(tmp_path, edited_failed_task):
    project_id = "marketing-partial"
    root = tmp_path / "project-files" / project_id
    asset_root = root / "preproduction"
    (asset_root / "assets").mkdir(parents=True)
    for name in ("front.png", "use.mp4"):
        (asset_root / "assets" / name).write_bytes(b"material")
    assets = [{"id": asset_id, "name": asset_id, "notes": "", "kind": kind, "file": name, **extra}
              for asset_id, kind, name, extra in (("front", "image", "front.png", {}),
                                                  ("use", "video", "use.mp4", {"duration": 4}),
                                                  ("missing", "image", "missing.png", {}))]
    (asset_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
                                                       "assets": assets}), encoding="utf-8")
    brief = {"revision": 1, "productName": "晴雨杯", "facts": [{"id": "f1", "text": "杯盖防泼溅"}],
             "audience": "通勤者", "sellingPoints": ["通勤"], "callToAction": "查看详情", "forbiddenPhrases": [],
             "assetIds": ["front", "use", "missing"]}
    candidates = [{"id": f"c{index}", "generationId": "g1", "revision": 0, "briefRevision": 1,
                   "confirmedRevision": 0, "sellingPoint": f"卖点 {index}", "beats": [
                       {"text": f"杯盖防泼溅 {index}", "factIds": ["f1"], "assetId": "front"},
                       {"text": "通勤使用", "factIds": ["f1"], "assetId": "missing" if index == 4 or (edited_failed_task and index == 3) else "use"}]}
                  for index in range(5)]
    content_root = root / "aigc-content"
    content_root.mkdir(parents=True)
    (content_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "brief": brief,
                                                           "candidates": candidates}), encoding="utf-8")
    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), None))
    with TestClient(app) as client:
        result = client.post(f"/api/projects/{project_id}/batch-edits/from-aigc", json={"generationId": "g1"})

        assert result.status_code == 201, result.text
        children = result.json()["tasks"]
        assert [task["generation"]["status"] for task in children] == (["ready"] * 3 + ["failed"] * 2 if edited_failed_task
                                                                    else ["ready"] * 4 + ["failed"])
        assert all(task["variant"]["tracks"][0]["clips"] for task in children[:3])
        assert "素材不可用" in children[4]["generation"]["error"]
        assert str(tmp_path) not in children[4]["generation"]["error"]
        if edited_failed_task:
            changed = client.put(f"/api/projects/{project_id}/batch-edits/{children[4]['id']}/content",
                json={"revision": 0, "sellingPoint": "人工卖点", "script": "人工改写的脚本"})
            assert changed.status_code == 200, changed.text
        (asset_root / "assets" / "missing.png").write_bytes(b"material")
        retried = client.post(f"/api/projects/{project_id}/batch-edits/from-aigc", json={"generationId": "g1"})
        if edited_failed_task:
            assert retried.status_code == 200, retried.text
            assert retried.json()["skippedTaskIds"] == [children[4]["id"]]
            assert [task["generation"]["status"] for task in retried.json()["tasks"]] == ["ready"] * 4 + ["failed"]
            preserved = next(task for task in client.get(f"/api/projects/{project_id}/batch-edits").json()["tasks"]
                             if task["id"] == children[4]["id"])
            assert preserved["script"] == "人工改写的脚本"
            assert preserved["generation"]["status"] == "failed"
            assert len(client.get(f"/api/projects/{project_id}/batch-edits").json()["tasks"]) == 6
            return
        assert retried.status_code == 200, retried.text
        assert [task["id"] for task in retried.json()["tasks"]] == [task["id"] for task in children]
        assert [task["generation"]["status"] for task in retried.json()["tasks"]] == ["ready"] * 5
        assert len(client.get(f"/api/projects/{project_id}/batch-edits").json()["tasks"]) == 6


def test_aigc_handoff_isolates_candidate_with_deleted_asset_record(tmp_path):
    project_id = "marketing-removed-asset"
    root = tmp_path / "project-files" / project_id
    asset_root = root / "preproduction"
    (asset_root / "assets").mkdir(parents=True)
    for name in ("front.png", "use.mp4"):
        (asset_root / "assets" / name).write_bytes(b"material")
    (asset_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [{"id": "front", "name": "杯子", "kind": "image", "file": "front.png"},
                   {"id": "use", "name": "演示", "kind": "video", "file": "use.mp4", "duration": 4}]}), encoding="utf-8")
    brief = {"revision": 1, "productName": "晴雨杯", "facts": [{"id": "f1", "text": "杯盖防泼溅"}],
             "audience": "通勤者", "sellingPoints": ["通勤"], "callToAction": "查看详情", "forbiddenPhrases": [],
             "assetIds": ["front", "use", "removed"]}
    candidates = [{"id": f"c{index}", "generationId": "g1", "revision": 0, "briefRevision": 1,
                   "confirmedRevision": 0, "sellingPoint": f"卖点 {index}", "beats": [
                       {"text": f"杯盖防泼溅 {index}", "factIds": ["f1"], "assetId": "front"},
                       {"text": "通勤使用", "factIds": ["f1"], "assetId": "removed" if index == 4 else "use"}]}
                  for index in range(5)]
    content_root = root / "aigc-content"
    content_root.mkdir(parents=True)
    (content_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "brief": brief,
        "candidates": candidates}), encoding="utf-8")
    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), None))
    app.include_router(create_aigc_content_router(tmp_path, lambda _: SimpleNamespace(id=project_id)))
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project_id}/batch-edits/from-aigc", json={"generationId": "g1"})
        assert response.status_code == 201, response.text
        assert [task["generation"]["status"] for task in response.json()["tasks"]] == ["ready"] * 4 + ["failed"]
        original_children = response.json()["tasks"]
        edited = client.put(f"/api/projects/{project_id}/aigc-content/candidates/c4",
            json={"revision": 0, "sellingPoint": "卖点 4", "beats": [candidates[4]["beats"][0],
                {**candidates[4]["beats"][1], "assetId": "use"}]})
        assert edited.status_code == 200, edited.text
        assert client.post(f"/api/projects/{project_id}/aigc-content/candidates/c4/confirm",
            json={"revision": 1}).status_code == 200
        retried = client.post(f"/api/projects/{project_id}/batch-edits/from-aigc", json={"generationId": "g1"})
        assert retried.status_code == 200, retried.text
        assert [task["id"] for task in retried.json()["tasks"]] == [task["id"] for task in original_children]
        assert [task["generation"]["status"] for task in retried.json()["tasks"]] == ["ready"] * 5
        assert retried.json()["tasks"][4]["aigcSource"]["candidateRevision"] == 1
        assert retried.json()["tasks"][0]["variant"] == original_children[0]["variant"]
        assert len(client.get(f"/api/projects/{project_id}/batch-edits").json()["tasks"]) == 6


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="需要本地 FFmpeg")
def test_aigc_screen_copy_reaches_preview_and_approved_mp4(tmp_path):
    project_id = "marketing-real"
    root = tmp_path / "project-files" / project_id
    asset_root = root / "preproduction"
    (asset_root / "assets").mkdir(parents=True)
    assets = []
    for name in ("opening", "detail"):
        source = asset_root / "assets" / f"{name}.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=black:s=128x96:r=24:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(source)], check=True)
        assets.append({"id": name, "name": name, "notes": name, "kind": "video", "file": source.name, "duration": 1})
    (asset_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
                                                       "assets": assets}), encoding="utf-8")
    brief = {"revision": 1, "productName": "晴雨杯", "facts": [{"id": "f1", "text": "杯盖防泼溅"}],
             "audience": "通勤者", "sellingPoints": ["通勤"], "callToAction": "查看详情", "forbiddenPhrases": [],
             "assetIds": ["opening", "detail"]}
    candidates = [{"id": f"candidate-{index}", "generationId": "generation", "revision": 0,
                   "briefRevision": 1, "confirmedRevision": 0, "sellingPoint": f"卖点 {index}", "beats": [
                       {"text": f"杯盖防泼溅 {index}", "factIds": ["f1"], "assetId": "opening"},
                       {"text": "通勤使用", "factIds": ["f1"], "assetId": "detail"}]}
                  for index in range(5)]
    content_root = root / "aigc-content"
    content_root.mkdir(parents=True)
    (content_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "brief": brief, "candidates": candidates}), encoding="utf-8")
    jobs = []

    class Queue:
        def submit(self, kind, pid, handler):
            jobs.append((pid, handler))
            return True

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: SimpleNamespace(id=project_id), Queue()))
    base = f"/api/projects/{project_id}/batch-edits"
    with TestClient(app) as client:
        parent = client.post(base + "/from-aigc", json={"generationId": "generation"}).json()["task"]
        children = [task for task in client.get(base).json()["tasks"] if task.get("batchId") == parent["id"]]
        previews = []
        for child in children[:2]:
            submitted = client.post(f"{base}/{child['id']}/variant/previews", json={"revision": 0})
            assert submitted.status_code == 202, submitted.text
            pid, handler = jobs.pop(0)
            handler(pid)
            run = next(task for task in client.get(base).json()["tasks"] if task["id"] == child["id"])["variant"]["runs"][-1]
            assert run["status"] == "completed", run
            previews.append(client.get(f"{base}/{child['id']}/variant/previews/{run['id']}/output").content)
        assert previews[0] != previews[1]
        first = children[0]
        current_run = next(task for task in client.get(base).json()["tasks"] if task["id"] == first["id"])["variant"]["runs"][-1]
        assert client.post(f"{base}/{first['id']}/reviews", json={"runId": current_run["id"],
            "decision": "approved", "reason": "文案已核对"}).status_code == 200
        exported = client.post(f"{base}/{parent['id']}/exports", json={"taskIds": [first["id"]]})
        assert exported.status_code == 202, exported.text
        pid, handler = jobs.pop(0)
        handler(pid)
        delivery = next(task for task in client.get(base).json()["tasks"] if task["id"] == first["id"])["variant"]["exports"][-1]
        assert delivery["status"] == "completed" and delivery["subtitles"][0]["text"] == "杯盖防泼溅 0"
        output = tmp_path / "aigc-delivery.mp4"
        output.write_bytes(client.get(f"{base}/{first['id']}/exports/{delivery['id']}/output").content)
        frame = subprocess.check_output(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", "0.5",
                                         "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        assert max(frame) > 30
        second = children[1]
        second_run = next(task for task in client.get(base).json()["tasks"] if task["id"] == second["id"])["variant"]["runs"][-1]
        assert client.post(f"{base}/{second['id']}/reviews", json={"runId": second_run["id"],
            "decision": "approved", "reason": "第二条文案已核对"}).status_code == 200
        assert client.post(f"{base}/{parent['id']}/exports", json={"taskIds": [second["id"]]}).status_code == 202
        pid, handler = jobs.pop(0)
        handler(pid)
        second_delivery = next(task for task in client.get(base).json()["tasks"] if task["id"] == second["id"])["variant"]["exports"][-1]
        assert second_delivery["status"] == "completed" and second_delivery["subtitles"][0]["text"] == "杯盖防泼溅 1"
        second_output = tmp_path / "aigc-delivery-second.mp4"
        second_output.write_bytes(client.get(f"{base}/{second['id']}/exports/{second_delivery['id']}/output").content)
        second_frame = subprocess.check_output(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", "0.5",
                                                "-i", str(second_output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        assert max(second_frame) > 30 and second_frame != frame
