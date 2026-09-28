import json

from app.preproduction import PreproductionStore
from test_preproduction_api import BASE, setup


def legacy_workspace():
    return {
        "schemaVersion": 1,
        "revision": 7,
        "brief": {
            "theme": "雨夜追踪",
            "purpose": "预告片",
            "style": "电影感",
            "duration": 4,
            "aspect": "16:9",
            "mustPreserve": "红色外套",
        },
        "assets": [{
            "id": "asset-a",
            "name": "参考.mp4",
            "kind": "video",
            "role": "reference",
            "file": "asset-a.mp4",
        }],
        "shots": [{
            "id": "shot-a",
            "title": "跟拍",
            "duration": 4,
            "prompt": "雨夜跟拍",
            "negativePrompt": "抖动",
            "assetIds": ["asset-a"],
            "nodes": [{
                "id": "trim",
                "kind": "trim",
                "input": "asset:asset-a",
                "params": {"start": 0, "end": 4},
                "status": "completed",
                "error": None,
                "artifacts": [{"name": "output.mp4", "url": "legacy-url"}],
            }],
            "resultAssetId": None,
            "_resultVersions": [{
                "assetId": "asset-a",
                "signature": "legacy-signature",
                "reviewed": True,
                "association": {"revision": 6},
            }],
        }],
    }


def test_loading_v1_projects_a_stable_v2_model_without_rewriting_disk(tmp_path):
    store = PreproductionStore(tmp_path)
    path = store.path("project-001", "state.json")
    path.parent.mkdir(parents=True)
    legacy = legacy_workspace()
    path.write_text(json.dumps(legacy), encoding="utf-8")

    first = store.load("project-001")
    second = store.load("project-001")

    assert first == second
    assert first["schemaVersion"] == 2
    assert first["scenes"] == [{
        "id": "scene-default",
        "title": "未分场",
        "rank": "00000001",
        "description": "",
    }]
    assert first["shots"][0]["sceneId"] == "scene-default"
    assert first["shots"][0]["rank"] == "00000001"
    assert first["shots"][0]["nodes"] == legacy["shots"][0]["nodes"]
    assert first["shots"][0]["_resultVersions"] == legacy["shots"][0]["_resultVersions"]
    assert json.loads(path.read_text(encoding="utf-8"))["schemaVersion"] == 1


def test_first_v2_save_keeps_one_immutable_v1_backup(tmp_path):
    store = PreproductionStore(tmp_path)
    path = store.path("project-001", "state.json")
    backup = store.path("project-001", "state.v1.json")
    path.parent.mkdir(parents=True)
    legacy = legacy_workspace()
    path.write_text(json.dumps(legacy), encoding="utf-8")

    projected = store.load("project-001")
    projected["brief"]["theme"] = "迁移后保存"
    store.save("project-001", projected)

    assert json.loads(path.read_text(encoding="utf-8"))["schemaVersion"] == 2
    assert json.loads(backup.read_text(encoding="utf-8")) == legacy

    backup_bytes = backup.read_bytes()
    projected["brief"]["theme"] = "第二次保存"
    store.save("project-001", projected)
    assert backup.read_bytes() == backup_bytes


def test_get_projects_v1_as_v2_workflow_and_independent_initial_layout(tmp_path):
    client, _, _ = setup(tmp_path)
    store = PreproductionStore(tmp_path)
    path = store.path("project-001", "state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy_workspace()), encoding="utf-8")

    response = client.get(BASE)

    assert response.status_code == 200, response.text
    workspace = response.json()
    assert workspace["schemaVersion"] == 2
    assert workspace["scenes"][0]["id"] == "scene-default"
    assert workspace["shots"][0]["sceneId"] == "scene-default"
    assert workspace["shots"][0]["rank"] == "00000001"
    assert workspace["workflow"] == {
        "nodes": [
            {"id": "shot:shot-a", "type": "shot", "shotId": "shot-a"},
            {
                "id": "asset:shot-a:asset-a",
                "type": "asset",
                "assetId": "asset-a",
                "ownerShotId": "shot-a",
                "role": "reference",
            },
            {
                "id": "process:shot-a:trim",
                "type": "process",
                "ownerShotId": "shot-a",
                "processKind": "trim",
                "config": {"input": "asset:asset-a", "params": {"start": 0, "end": 4}},
                "status": "completed",
                "error": None,
                "artifacts": [{"name": "output.mp4", "url": "legacy-url"}],
            },
        ],
        "edges": [{
            "id": "edge:asset:shot-a:asset-a:process:shot-a:trim",
            "kind": "data",
            "source": {"nodeId": "asset:shot-a:asset-a", "portId": "asset"},
            "target": {"nodeId": "process:shot-a:trim", "portId": "input"},
        }],
    }
    assert workspace["canvasLayout"]["layoutRevision"] == 0
    assert workspace["canvasLayout"]["scope"] == {"type": "project", "id": "project-001"}
    assert "scene-default" in workspace["canvasLayout"]["nodes"]
    assert "shot:shot-a" in workspace["canvasLayout"]["nodes"]
    assert json.loads(path.read_text(encoding="utf-8"))["schemaVersion"] == 1


def test_legacy_edit_shape_round_trips_v2_and_preserves_server_owned_history(tmp_path):
    client, _, _ = setup(tmp_path)
    store = PreproductionStore(tmp_path)
    path = store.path("project-001", "state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = legacy_workspace()
    path.write_text(json.dumps(legacy), encoding="utf-8")
    current = client.get(BASE).json()

    saved = client.put(BASE, json={
        "revision": current["revision"],
        "brief": {**current["brief"], "theme": "新的主题"},
        "shots": current["shots"],
    })

    assert saved.status_code == 200, saved.text
    workspace = saved.json()
    assert workspace["revision"] == 8
    assert workspace["schemaVersion"] == 2
    assert workspace["shots"][0]["sceneId"] == "scene-default"
    assert workspace["shots"][0]["rank"] == "00000001"
    assert workspace["shots"][0]["nodes"][0]["status"] == "completed"
    assert workspace["shots"][0]["nodes"][0]["artifacts"] == [{"name": "output.mp4", "url": "legacy-url"}]
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["schemaVersion"] == 2
    assert stored["shots"][0]["_resultVersions"] == legacy["shots"][0]["_resultVersions"]
    assert json.loads(store.path("project-001", "state.v1.json").read_text(encoding="utf-8")) == legacy


def test_invalid_v2_scene_is_rejected_without_rewriting_storage(tmp_path):
    client, _, _ = setup(tmp_path)
    store = PreproductionStore(tmp_path)
    path = store.path("project-001", "state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    invalid = legacy_workspace()
    invalid["schemaVersion"] = 2
    invalid["scenes"] = [{"id": "scene-default", "title": "缺少 rank", "description": ""}]
    invalid["shots"][0]["sceneId"] = "scene-default"
    invalid["shots"][0]["rank"] = "00000001"
    original = json.dumps(invalid).encode()
    path.write_bytes(original)

    response = client.get(BASE)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "preproduction_storage_invalid"
    assert path.read_bytes() == original


def test_invalid_v1_shot_is_rejected_without_rewriting_storage(tmp_path):
    client, _, _ = setup(tmp_path)
    store = PreproductionStore(tmp_path)
    path = store.path("project-001", "state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    invalid = legacy_workspace()
    invalid["shots"] = [None]
    original = json.dumps(invalid).encode()
    path.write_bytes(original)

    response = client.get(BASE)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "preproduction_storage_invalid",
        "message": "前置工作台状态无法读取。",
    }
    assert path.read_bytes() == original


def test_legacy_list_reorder_updates_canonical_shot_ranks(tmp_path):
    client, _, _ = setup(tmp_path)
    store = PreproductionStore(tmp_path)
    path = store.path("project-001", "state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = legacy_workspace()
    second = json.loads(json.dumps(legacy["shots"][0]))
    second.update(id="shot-b", title="反打")
    second["nodes"][0]["id"] = "trim-b"
    legacy["shots"].append(second)
    path.write_text(json.dumps(legacy), encoding="utf-8")
    current = client.get(BASE).json()

    saved = client.put(BASE, json={
        "revision": current["revision"],
        "brief": current["brief"],
        "shots": list(reversed(current["shots"])),
    })

    assert saved.status_code == 200, saved.text
    shots = saved.json()["shots"]
    assert [(shot["id"], shot["rank"]) for shot in shots] == [
        ("shot-b", "00000001"),
        ("shot-a", "00000002"),
    ]
