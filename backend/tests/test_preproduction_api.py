import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.preproduction import PreproductionStore
from app.preproduction_api import create_preproduction_router
from app.canvas_layout import CanvasLayoutStore
from app.timeline import TimelineStore


PROJECT_ID = "project-001"
BASE = f"/api/projects/{PROJECT_ID}/preproduction"


class Queue:
    def __init__(self):
        self.jobs = []

    def submit(self, kind, project_id, handler):
        self.jobs.append((project_id, handler))
        return True

    def run(self):
        project_id, handler = self.jobs.pop(0)
        handler(project_id)


def make_video(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=6:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def setup(tmp_path, *, runner=None):
    source = tmp_path / f"project-files/{PROJECT_ID}/reference-media/ref-001.mp4"
    make_video(source)
    project = SimpleNamespace(
        id=PROJECT_ID,
        referenceMedia=SimpleNamespace(
            id="ref-001", type="video", format="mp4", originalName="reference.mp4",
            durationSeconds=2.0, width=64, height=48,
        ),
        localPreprocessing=SimpleNamespace(
            id="prep-001", sourceReferenceMediaId="ref-001", mediaType="video", status="completed",
        ),
    )
    preparation = tmp_path / f"project-files/{PROJECT_ID}/local-preprocessing/prep-001"
    preparation.mkdir(parents=True)
    (preparation / "scene-changes.json").write_text(json.dumps({"sceneChanges": []}))
    queue = Queue()
    app = FastAPI()
    app.include_router(create_preproduction_router(
        tmp_path, lambda _: project, queue, runner=runner,
    ))
    return TestClient(app), queue, project


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要本地 FFmpeg")


def test_new_shot_keeps_its_custom_scene_on_legacy_workspace_save(tmp_path):
    client, _, _ = setup(tmp_path)
    store = PreproductionStore(tmp_path)
    state = store.load(PROJECT_ID)
    state["scenes"] = [{"id": "scene-custom", "title": "自定义场景", "rank": "00000001", "description": ""}]
    state["shots"] = []
    store.save(PROJECT_ID, state)
    current = client.get(BASE).json()

    saved = client.put(BASE, json={
        "revision": current["revision"],
        "brief": current["brief"],
        "shots": [{
            "id": "shot-copy", "sceneId": "scene-custom", "rank": "00000001",
            "title": "复制镜头", "duration": 2, "prompt": "", "negativePrompt": "",
            "assetIds": [], "nodes": [],
        }],
    })

    assert saved.status_code == 200, saved.text
    assert saved.json()["shots"][0]["sceneId"] == "scene-custom"


def test_save_persists_scene_ownership_and_rank_without_touching_layout_or_timeline(tmp_path):
    client, _, _ = setup(tmp_path)
    store = PreproductionStore(tmp_path)
    state = store.load(PROJECT_ID)
    state["scenes"] = [
        {"id": "scene-a", "title": "相遇", "rank": "00000001", "description": ""},
        {"id": "scene-b", "title": "追逐", "rank": "00000002", "description": ""},
    ]
    state["shots"] = [
        {"id": "shot-a", "sceneId": "scene-a", "rank": "00000001", "title": "镜头 A", "duration": 2,
         "prompt": "", "negativePrompt": "", "assetIds": [], "nodes": [], "resultAssetId": None, "_resultVersions": []},
        {"id": "shot-b", "sceneId": "scene-b", "rank": "00000002", "title": "镜头 B", "duration": 2,
         "prompt": "", "negativePrompt": "", "assetIds": [], "nodes": [], "resultAssetId": None, "_resultVersions": []},
    ]
    store.save(PROJECT_ID, state)
    layout_url = BASE + f"/layouts/project/{PROJECT_ID}"
    layout = client.get(layout_url).json()
    saved_layout = client.put(layout_url, json={
        "layoutRevision": layout["layoutRevision"], "nodes": layout["nodes"],
        "viewport": {"x": 90, "y": 40, "zoom": 1.2},
    }).json()
    timeline_store = TimelineStore(tmp_path)
    timeline = timeline_store.load(PROJECT_ID)
    timeline["revision"] = 7
    timeline_store.save(PROJECT_ID, timeline)

    current = client.get(BASE).json()
    saved = client.put(BASE, json={
        "revision": current["revision"],
        "brief": current["brief"],
        "scenes": current["scenes"],
        "shots": [
            {**current["shots"][1], "rank": "00000001", "sceneId": "scene-a"},
            {**current["shots"][0], "rank": "00000002", "sceneId": "scene-b"},
        ],
    })

    assert saved.status_code == 200, saved.text
    assert [(shot["id"], shot["sceneId"], shot["rank"]) for shot in saved.json()["shots"]] == [
        ("shot-b", "scene-a", "00000001"),
        ("shot-a", "scene-b", "00000002"),
    ]
    assert client.get(layout_url).json() == saved_layout
    assert timeline_store.load(PROJECT_ID) == timeline


def test_canvas_shot_creation_saves_content_and_initial_layout_as_one_transaction(tmp_path):
    client, _, _ = setup(tmp_path)
    current = client.get(BASE).json()

    saved = client.put(BASE, json={
        "revision": current["revision"],
        "brief": current["brief"],
        "scenes": current["scenes"],
        "shots": [{
            "id": "shot-canvas", "sceneId": "scene-default", "rank": "00000001",
            "title": "画布镜头", "duration": 3, "prompt": "", "negativePrompt": "",
            "assetIds": [], "nodes": [],
        }],
        "canvasLayout": {
            **current["canvasLayout"],
            "nodes": {
                **current["canvasLayout"]["nodes"],
                "shot:shot-canvas": {"x": 520, "y": 240},
            },
        },
    })

    assert saved.status_code == 200, saved.text
    assert [shot["id"] for shot in saved.json()["shots"]] == ["shot-canvas"]
    assert saved.json()["canvasLayout"]["layoutRevision"] == 1
    assert saved.json()["canvasLayout"]["nodes"]["shot:shot-canvas"] == {"x": 520, "y": 240}


def test_canvas_shot_creation_rolls_back_content_when_layout_save_fails(tmp_path, monkeypatch):
    client, _, _ = setup(tmp_path)
    current = client.get(BASE).json()

    def fail_layout_save(self, project_id, layout):
        raise OSError("layout unavailable")

    monkeypatch.setattr(CanvasLayoutStore, "_save", fail_layout_save)
    failed = client.put(BASE, json={
        "revision": current["revision"],
        "brief": current["brief"],
        "scenes": current["scenes"],
        "shots": [{
            "id": "shot-canvas", "sceneId": "scene-default", "rank": "00000001",
            "title": "画布镜头", "duration": 3, "prompt": "", "negativePrompt": "",
            "assetIds": [], "nodes": [],
        }],
        "canvasLayout": {
            **current["canvasLayout"],
            "nodes": {
                **current["canvasLayout"]["nodes"],
                "shot:shot-canvas": {"x": 520, "y": 240},
            },
        },
    })

    assert failed.status_code == 503
    restored = PreproductionStore(tmp_path).load(PROJECT_ID)
    assert restored["revision"] == current["revision"]
    assert restored["shots"] == []


def test_canvas_layout_persists_with_an_independent_revision(tmp_path):
    client, _, _ = setup(tmp_path)
    content = client.get(BASE).json()
    layout_url = BASE + f"/layouts/project/{PROJECT_ID}"
    layout = client.get(layout_url).json()

    saved = client.put(layout_url, json={
        "layoutRevision": layout["layoutRevision"],
        "nodes": {
            **layout["nodes"],
            "scene-default": {
                **layout["nodes"]["scene-default"],
                "x": 180,
                "y": 96,
                "width": 480,
                "height": 260,
                "collapsed": True,
            },
        },
        "viewport": {"x": -120, "y": 48, "zoom": 1.25},
    })

    assert saved.status_code == 200, saved.text
    assert saved.json()["layoutRevision"] == 1
    assert saved.json()["nodes"]["scene-default"] == {
        "x": 180,
        "y": 96,
        "width": 480,
        "height": 260,
        "collapsed": True,
    }
    assert client.get(BASE).json()["revision"] == content["revision"]
    restored = client.get(layout_url).json()
    assert restored == saved.json()
    assert client.get(BASE).json()["canvasLayout"] == saved.json()


def test_layout_and_content_cas_conflicts_are_isolated(tmp_path):
    client, _, _ = setup(tmp_path)
    current = client.get(BASE).json()
    layout_url = BASE + f"/layouts/project/{PROJECT_ID}"
    initial_layout = client.get(layout_url).json()

    first_layout = client.put(layout_url, json={
        "layoutRevision": 0,
        "nodes": initial_layout["nodes"],
        "viewport": {"x": 12, "y": 8, "zoom": 1},
    })
    stale_layout = client.put(layout_url, json={
        "layoutRevision": 0,
        "nodes": initial_layout["nodes"],
        "viewport": {"x": 20, "y": 10, "zoom": 1},
    })
    content_saved = client.put(BASE, json={
        "revision": current["revision"],
        "brief": {**current["brief"], "theme": "布局冲突后仍可保存内容"},
        "shots": current["shots"],
    })

    assert first_layout.status_code == 200
    assert stale_layout.status_code == 409
    assert stale_layout.json()["detail"]["code"] == "preproduction_layout_conflict"
    assert content_saved.status_code == 200, content_saved.text
    assert content_saved.json()["revision"] == current["revision"] + 1
    assert content_saved.json()["canvasLayout"]["layoutRevision"] == 1

    stale_content = client.put(BASE, json={
        "revision": current["revision"],
        "brief": current["brief"],
        "shots": current["shots"],
    })
    layout_after_content_conflict = client.put(layout_url, json={
        "layoutRevision": 1,
        "nodes": initial_layout["nodes"],
        "viewport": {"x": 24, "y": 12, "zoom": 1.1},
    })

    assert stale_content.status_code == 409
    assert layout_after_content_conflict.status_code == 200, layout_after_content_conflict.text
    assert layout_after_content_conflict.json()["layoutRevision"] == 2


def test_corrupt_layout_does_not_block_content_read_or_save(tmp_path):
    client, _, _ = setup(tmp_path)
    current = client.get(BASE).json()
    layout_url = BASE + f"/layouts/project/{PROJECT_ID}"
    layout = client.get(layout_url).json()
    assert client.put(layout_url, json={
        "layoutRevision": layout["layoutRevision"],
        "nodes": layout["nodes"],
        "viewport": {"x": 10, "y": 20, "zoom": 1},
    }).status_code == 200
    layout_path = (
        tmp_path
        / "project-files"
        / PROJECT_ID
        / "preproduction"
        / "canvas-layouts"
        / "project"
        / f"{PROJECT_ID}.json"
    )
    layout_path.write_text("not-json", encoding="utf-8")

    readable = client.get(BASE)
    saved = client.put(BASE, json={
        "revision": current["revision"],
        "brief": {**current["brief"], "theme": "布局损坏不阻塞内容"},
        "shots": current["shots"],
    })
    broken_layout = client.get(layout_url)

    assert readable.status_code == 200, readable.text
    assert readable.json()["canvasLayout"]["layoutRevision"] == 0
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == current["revision"] + 1
    assert broken_layout.status_code == 503
    assert broken_layout.json()["detail"]["code"] == "preproduction_layout_storage_invalid"


def test_each_canvas_scope_restores_its_own_viewport(tmp_path):
    client, _, _ = setup(tmp_path)
    current = client.get(BASE).json()
    saved_content = client.put(BASE, json={
        "revision": current["revision"],
        "brief": current["brief"],
        "shots": [{
            "id": "shot-a", "title": "镜头 A", "duration": 2,
            "prompt": "", "negativePrompt": "", "assetIds": [], "nodes": [],
        }],
    })
    assert saved_content.status_code == 200, saved_content.text
    project_url = BASE + f"/layouts/project/{PROJECT_ID}"
    shot_url = BASE + "/layouts/shot/shot-a"
    project_layout = client.get(project_url).json()
    shot_layout = client.get(shot_url).json()

    assert client.put(project_url, json={
        "layoutRevision": project_layout["layoutRevision"],
        "nodes": project_layout["nodes"],
        "viewport": {"x": 100, "y": 40, "zoom": 0.75},
    }).status_code == 200
    assert client.put(shot_url, json={
        "layoutRevision": shot_layout["layoutRevision"],
        "nodes": shot_layout["nodes"],
        "viewport": {"x": -30, "y": 15, "zoom": 1.8},
    }).status_code == 200

    assert client.get(project_url).json()["viewport"] == {"x": 100, "y": 40, "zoom": 0.75}
    assert client.get(shot_url).json()["viewport"] == {"x": -30, "y": 15, "zoom": 1.8}


def test_layout_save_discards_nodes_removed_by_independent_content_edit(tmp_path):
    client, _, _ = setup(tmp_path)
    current = client.get(BASE).json()
    created = client.put(BASE, json={
        "revision": current["revision"],
        "brief": current["brief"],
        "shots": [{
            "id": "shot-a", "title": "镜头 A", "duration": 2,
            "prompt": "", "negativePrompt": "", "assetIds": [], "nodes": [],
        }],
    }).json()
    layout_url = BASE + f"/layouts/project/{PROJECT_ID}"
    stale_layout = client.get(layout_url).json()
    assert "shot:shot-a" in stale_layout["nodes"]
    removed = client.put(BASE, json={
        "revision": created["revision"],
        "brief": created["brief"],
        "shots": [],
    })
    assert removed.status_code == 200, removed.text

    saved_layout = client.put(layout_url, json={
        "layoutRevision": stale_layout["layoutRevision"],
        "nodes": stale_layout["nodes"],
        "viewport": {"x": 5, "y": 10, "zoom": 1},
    })

    assert saved_layout.status_code == 200, saved_layout.text
    assert "shot:shot-a" not in saved_layout.json()["nodes"]
    assert client.get(BASE).json()["revision"] == removed.json()["revision"]


def test_save_uses_cas_and_marks_downstream_node_stale(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.get(BASE).json()
    imported = client.post(BASE + "/import-reference", json={"revision": state["revision"]}).json()
    reference_id = imported["assets"][0]["id"]
    saved = client.put(BASE, json={
        "revision": imported["revision"], "brief": imported["brief"],
            "shots": [{"id": "shot-a", "title": "镜头 A", "duration": 2, "prompt": "晴天", "negativePrompt": "", "assetIds": [reference_id], "nodes": [
            {"id": "trim", "kind": "trim", "input": f"asset:{reference_id}", "params": {"start": 0, "end": 1}},
            {"id": "frame", "kind": "first_frame", "input": "node:trim", "params": {}},
        ]}],
    })
    assert saved.status_code == 200, saved.text
    current = saved.json()
    stale = client.put(BASE, json={"revision": 0, "brief": current["brief"], "shots": current["shots"]})
    assert stale.status_code == 409

    changed = current["shots"]
    changed[0]["nodes"][0]["params"]["end"] = 1.5
    updated = client.put(BASE, json={"revision": current["revision"], "brief": current["brief"], "shots": changed})
    assert updated.status_code == 200
    workspace = updated.json()
    assert workspace["shots"][0]["nodes"][1]["status"] == "stale"
    assert client.post(BASE + "/package", json={"revision": workspace["revision"]}).status_code == 409


def test_rejects_invalid_node_dependency_and_does_not_mutate_reference_media(tmp_path):
    client, _, project = setup(tmp_path)
    state = client.get(BASE).json()
    imported = client.post(BASE + "/import-reference", json={"revision": state["revision"]}).json()
    reference_id = imported["assets"][0]["id"]
    bad = client.put(BASE, json={
        "revision": imported["revision"], "brief": imported["brief"],
        "shots": [{"id": "shot-a", "title": "A", "duration": 1, "prompt": "", "negativePrompt": "", "assetIds": [reference_id], "nodes": [
            {"id": "late", "kind": "first_frame", "input": "node:early", "params": {}},
            {"id": "early", "kind": "reference", "input": f"asset:{reference_id}", "params": {}},
        ]}],
    })
    assert bad.status_code == 422
    self_cycle = client.put(BASE, json={
        "revision": imported["revision"], "brief": imported["brief"],
        "shots": [{"id": "shot-b", "title": "B", "duration": 1, "prompt": "", "negativePrompt": "", "assetIds": [reference_id], "nodes": [
            {"id": "self", "kind": "first_frame", "input": "node:self", "params": {}},
        ]}],
    })
    assert self_cycle.status_code == 422
    repeated = client.post(BASE + "/import-reference", json={"revision": imported["revision"]})
    assert repeated.status_code == 200
    assert project.referenceMedia.id == "ref-001"
    assert repeated.json()["assets"][0]["id"].startswith("reference-")


def test_upload_validates_real_media_and_serves_only_owned_asset(tmp_path):
    client, _, _ = setup(tmp_path)
    invalid = client.post(BASE + "/assets?role=character", files={"file": ("bad.mp4", b"not-a-video", "video/mp4")})
    assert invalid.status_code == 422
    valid = tmp_path / "input.mp4"
    make_video(valid)
    faststart = tmp_path / "faststart.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(valid), "-c", "copy", "-movflags", "+faststart", str(faststart)], check=True)
    corrupted = bytearray(faststart.read_bytes())
    marker = corrupted.index(b"mdat")
    box_start = marker - 4
    box_size = int.from_bytes(corrupted[box_start:marker], "big")
    corrupted[marker + 4:box_start + box_size] = b"\xff" * (box_size - 8)
    unreadable = client.post(BASE + "/assets?role=motion", files={"file": ("broken.mp4", bytes(corrupted), "video/mp4")})
    assert unreadable.status_code == 422
    uploaded = client.post(BASE + "/assets?role=motion", files={"file": ("motion.mp4", valid.read_bytes(), "video/mp4")})
    assert uploaded.status_code == 200, uploaded.text
    asset = uploaded.json()["assets"][0]
    assert asset["url"] == BASE + f"/assets/{asset['id']}/file"
    assert client.get(BASE + f"/assets/{asset['id']}/file").status_code == 200
    assert client.get(BASE + "/assets/../state/file").status_code == 404


def test_result_upload_validates_its_target_before_reading_invalid_media(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.get(BASE).json()

    response = client.post(
        BASE + f"/assets?role=motion&resultForShot=missing&revision={state['revision']}",
        files={"file": ("bad.mp4", b"not-a-video", "video/mp4")},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "preproduction_shot_missing"


def test_run_waits_for_input_then_publishes_current_artifact_and_restart_fails_inflight(tmp_path):
    def runner(kind, source, destination, params, **_):
        assert source.is_file()
        (destination / "output.png").write_bytes(b"image")
        return ["output.png"]

    client, queue, project = setup(tmp_path, runner=runner)
    state = client.get(BASE).json()
    imported = client.post(BASE + "/import-reference", json={"revision": state["revision"]}).json()
    reference_id = imported["assets"][0]["id"]
    saved = client.put(BASE, json={
        "revision": imported["revision"], "brief": imported["brief"],
        "shots": [{"id": "shot-a", "title": "A", "duration": 1, "prompt": "", "negativePrompt": "", "assetIds": [reference_id], "nodes": [
            {"id": "source", "kind": "reference", "input": f"asset:{reference_id}", "params": {}},
            {"id": "frame", "kind": "first_frame", "input": "node:source", "params": {}},
        ]}],
    }).json()
    blocked = client.post(BASE + "/shots/shot-a/nodes/frame/run", json={"revision": saved["revision"]})
    assert blocked.status_code == 409
    started = client.post(BASE + "/shots/shot-a/nodes/source/run", json={"revision": saved["revision"]})
    assert started.status_code == 202
    assert client.post(BASE + "/shots/shot-a/nodes/frame/run", json={"revision": started.json()["revision"]}).status_code == 409
    queue.run()
    after_source = client.get(BASE).json()
    started_frame = client.post(BASE + "/shots/shot-a/nodes/frame/run", json={"revision": after_source["revision"]})
    assert started_frame.status_code == 202
    queue.run()
    done = client.get(BASE).json()
    node = done["shots"][0]["nodes"][1]
    assert node["status"] == "completed"
    assert client.get(BASE + "/artifacts/shot-a/frame/output.png").content == b"image"

    # Simulate persisted work from a process that was interrupted before completion.
    path = tmp_path / f"project-files/{PROJECT_ID}/preproduction/state.json"
    payload = json.loads(path.read_text())
    payload["shots"][0]["nodes"][1]["status"] = "running"
    path.write_text(json.dumps(payload))
    app = FastAPI()
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, Queue(), runner=runner))
    restarted = TestClient(app).get(BASE).json()
    assert restarted["shots"][0]["nodes"][1]["status"] == "failed"


def test_prompt_node_receives_the_complete_brief_and_shot_prompts(tmp_path):
    captured = []
    def runner(kind, source, destination, params, **_):
        assert kind == "prompt" and source is None
        captured.append(params["text"])
        (destination / "prompt.txt").write_text(params["text"])
        return ["prompt.txt"]

    client, queue, _ = setup(tmp_path, runner=runner)
    state = client.get(BASE).json()
    state["brief"].update({"theme": "主题", "purpose": "用途", "style": "风格", "duration": 12, "aspect": "16:9", "mustPreserve": "保留红伞"})
    saved = client.put(BASE, json={"revision": state["revision"], "brief": state["brief"], "shots": [{
        "id": "shot-a", "title": "A", "duration": 1, "prompt": "镜头提示", "negativePrompt": "不要模糊", "assetIds": [],
        "nodes": [{"id": "prompt", "kind": "prompt", "input": "", "params": {"text": "节点文字"}}],
    }]}).json()
    assert client.post(BASE + "/shots/shot-a/nodes/prompt/run", json={"revision": saved["revision"]}).status_code == 202
    queue.run()
    assert "保留红伞" in captured[0] and "16:9" in captured[0] and "12" in captured[0]
    assert "不要模糊" in captured[0] and "节点文字" in captured[0]


def test_import_shots_and_package_is_consistent_and_path_safe(tmp_path):
    client, queue, _ = setup(tmp_path)
    state = client.get(BASE).json()
    imported = client.post(BASE + "/import-shots", json={"revision": state["revision"]})
    assert imported.status_code == 200
    workspace = imported.json()
    shot = workspace["shots"][0]
    assert shot["assetIds"] and shot["nodes"][0]["params"] == {"start": 0.0, "end": 2.0}
    assert client.post(BASE + f"/shots/{shot['id']}/nodes/trim/run", json={"revision": workspace["revision"]}).status_code == 202
    queue.run()
    completed = client.get(BASE).json()
    repeated = client.post(BASE + "/import-shots", json={"revision": completed["revision"]})
    assert len(repeated.json()["shots"]) == 1
    package = client.post(BASE + "/package", json={"revision": repeated.json()["revision"]})
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        names = archive.namelist()
        workspace = json.loads(archive.read("workspace.json"))
        assert "workspace.json" in names
        assert any(name.startswith("assets/reference-") for name in names)
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
        assert workspace["assets"][0]["url"] in names
        assert workspace["shots"][0]["nodes"][0]["artifacts"][0]["url"] in names


def test_import_toolkit_copies_only_completed_video_outputs_once(tmp_path):
    client, _, _ = setup(tmp_path)
    directory = tmp_path / f"project-files/{PROJECT_ID}/toolkit/run-001"
    directory.mkdir(parents=True)
    make_video(directory / "output.mp4")
    (directory / "state.json").write_text(json.dumps({
        "id": "run-001", "status": "completed", "kind": "interpolate", "artifacts": ["output.mp4"],
    }))
    state = client.get(BASE).json()
    imported = client.post(BASE + "/import-toolkit", json={"revision": state["revision"]})
    assert imported.status_code == 200, imported.text
    asset = imported.json()["assets"][0]
    assert asset["role"] == "motion" and asset["kind"] == "video"
    repeated = client.post(BASE + "/import-toolkit", json={"revision": imported.json()["revision"]})
    assert repeated.status_code == 200
    assert len(repeated.json()["assets"]) == 1


def test_real_delivery_handoff_preserves_other_shot_after_local_revision(tmp_path):
    from PIL import Image
    from urllib.parse import unquote
    import re

    client, queue, project = setup(tmp_path)
    image = io.BytesIO()
    Image.new('RGB', (64, 64), 'orange').save(image, format='PNG')
    uploaded = client.post(BASE + '/assets?role=character', files={
        'file': ('角色 [橙色].png', image.getvalue(), 'image/png'),
    })
    assert uploaded.status_code == 200, uploaded.text
    state = uploaded.json()
    character = state['assets'][0]['id']
    state = client.post(BASE + '/import-reference', json={'revision': state['revision']}).json()
    motion = state['assets'][1]['id']
    state['brief'].update(theme='露营角色演示', mustPreserve='橙色外观', duration=2, aspect='4:3')
    state['shots'] = [
        {'id': shot_id, 'title': title, 'duration': 1, 'prompt': prompt, 'negativePrompt': '避免形变',
         'assetIds': [character, motion], 'nodes': [
             {'id': 'trim', 'kind': 'trim', 'input': f'asset:{motion}', 'params': {'start': 0, 'end': 1}},
             {'id': 'frame', 'kind': 'first_frame', 'input': 'node:trim', 'params': {}},
         ]}
        for shot_id, title, prompt in [('shot-a', '建立环境', '暖色露营'), ('shot-b', '角色收尾', '角色挥手')]
    ]
    def save(state):
        response = client.put(BASE, json={key: state[key] for key in ('revision', 'brief', 'shots')})
        assert response.status_code == 200, response.text
        return response.json()

    def run(shot_id, node_id):
        state = client.get(BASE).json()
        response = client.post(BASE + f'/shots/{shot_id}/nodes/{node_id}/run', json={'revision': state['revision']})
        assert response.status_code == 202, response.text
        queue.run()
        state = client.get(BASE).json()
        node = next(n for s in state['shots'] if s['id'] == shot_id for n in s['nodes'] if n['id'] == node_id)
        assert node['status'] == 'completed', node
        return state

    state = save(state)
    for shot_id in ('shot-a', 'shot-b'):
        for node_id in ('trim', 'frame'):
            state = run(shot_id, node_id)
    other = state['shots'][1]
    other_url = other['nodes'][1]['artifacts'][0]['url']
    other_bytes = client.get(other_url).content
    state['shots'][0]['nodes'][0]['params']['end'] = 0.5
    state = save(state)
    assert [n['status'] for n in state['shots'][0]['nodes']] == ['stale', 'stale']
    assert state['shots'][1] == other
    assert client.get(other_url).content == other_bytes
    assert client.post(BASE + '/package', json={'revision': state['revision']}).status_code == 409
    run('shot-a', 'trim')
    state = run('shot-a', 'frame')
    response = client.post(BASE + '/package', json={'revision': state['revision']})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert 'HANDOFF.md' in archive.namelist()
        handoff = archive.read('HANDOFF.md').decode()
        assert handoff.index('建立环境') < handoff.index('角色收尾')
        assert '暖色露营' in handoff and '角色挥手' in handoff and '橙色外观' in handoff
        assert '角色' in handoff and '0.5' in handoff
        links = re.findall(r'\]\(([^)]+)\)', handoff)
        assert links
        assert all(unquote(link) in archive.namelist() for link in links)
        assert str(tmp_path) not in handoff and '/api/projects/' not in handoff
        assert len([name for name in archive.namelist() if name.startswith('assets/')]) == 2
        exported = json.loads(archive.read('workspace.json'))
        second_frame = exported['shots'][1]['nodes'][1]['artifacts'][0]['url']
        assert archive.read(second_frame) == other_bytes
    assert project.referenceMedia.id == 'ref-001'


def test_delivery_warnings_are_actionable_and_do_not_block_export(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.get(BASE).json()
    assert any(check.get('code') == 'shots_missing' for check in state['checks'])
    state['brief']['duration'] = 5
    shot = {'id': 'shot-a', 'title': '文字镜头', 'duration': 2, 'prompt': '云海',
            'negativePrompt': '', 'assetIds': [], 'nodes': []}
    saved = client.put(BASE, json={'revision': state['revision'], 'brief': state['brief'], 'shots': [shot]})
    assert saved.status_code == 200
    state = saved.json()
    by_code = {check.get('code'): check for check in state['checks']}
    assert by_code['brief_incomplete']['level'] == 'warning'
    assert by_code['duration_mismatch']['level'] == 'warning'
    assert by_code['shot_assets_missing']['shotId'] == 'shot-a'
    assert client.post(BASE + '/package', json={'revision': state['revision']}).status_code == 200
    state['brief'].update(theme='云海', aspect='16:9', duration=2)
    state = client.put(BASE, json={'revision': state['revision'], 'brief': state['brief'], 'shots': [shot]}).json()
    assert not any(c.get('code') in ('brief_incomplete', 'duration_mismatch') for c in state['checks'])


def test_delivery_duration_ignores_floating_point_noise(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.get(BASE).json()
    state['brief'].update(theme='短片', aspect='16:9', duration=.3)
    shots = [{'id': f'shot-{i}', 'title': '', 'duration': duration, 'prompt': '天空',
              'negativePrompt': '', 'assetIds': [], 'nodes': []} for i, duration in enumerate((.1, .2))]
    state = client.put(BASE, json={'revision': state['revision'], 'brief': state['brief'], 'shots': shots}).json()
    assert not any(c.get('code') == 'duration_mismatch' for c in state['checks'])


def test_browser_style_audio_recording_without_container_duration_is_imported(tmp_path):
    client, _, _ = setup(tmp_path)
    recording = tmp_path / 'recording.webm'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.5', '-c:a', 'libopus', '-live', '1', str(recording)], check=True)
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', str(recording)]))
    assert 'duration' not in probe['format']
    response = client.post(BASE + '/assets?role=audio', files={'file': ('录音.webm', recording.read_bytes(), 'audio/webm')})
    assert response.status_code == 200, response.text
    asset = response.json()['assets'][0]
    assert asset['kind'] == 'audio' and .45 <= asset['duration'] <= .6
    assert client.get(asset['url']).status_code == 200


@pytest.mark.parametrize("input_kind,label", [("reference_video", "参考视频"), ("depth_video", "灰度深度视频"), ("white_model_video", "三维白模渲染视频")])
def test_input_kind_and_shot_result_upload_are_persisted_and_versioned(tmp_path, input_kind, label):
    client, _, project = setup(tmp_path)
    state = client.get(BASE).json()
    state = client.put(BASE, json={"revision": state["revision"], "brief": {**state["brief"], "inputKind": input_kind},
        "shots": [{"id": "shot-result", "title": "结果", "duration": 1, "nodes": [], "assetIds": []}]}).json()
    assert state["brief"]["inputKind"] == input_kind
    from PIL import Image
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, format="PNG")
    rejected = client.post(BASE + f"/assets?role=motion&resultForShot=shot-result&revision={state['revision']}", files={"file": ("not-video.png", image.getvalue(), "image/png")})
    assert rejected.status_code == 422
    unchanged = client.get(BASE).json()
    assert unchanged["revision"] == state["revision"] and unchanged["assets"] == state["assets"]
    source = tmp_path / f"project-files/{PROJECT_ID}/reference-media/ref-001.mp4"
    result = client.post(BASE + f"/assets?role=motion&resultForShot=shot-result&revision={state['revision']}", files={"file": ("result.mp4", source.read_bytes(), "video/mp4")})
    assert result.status_code == 200, result.text
    final = result.json()
    assert final["shots"][0]["resultAssetId"] == final["assets"][-1]["id"]
    assert project.referenceMedia.id == "ref-001"
    assert client.post(BASE + f"/assets?role=motion&resultForShot=shot-result&revision={state['revision']}", files={"file": ("result.mp4", source.read_bytes(), "video/mp4")}).status_code == 409
    invalid = {"revision": final["revision"], "brief": final["brief"], "shots": [{**final["shots"][0], "resultAssetId": "foreign-asset"}]}
    assert client.put(BASE, json=invalid).status_code == 422
    assert client.put(BASE, json={**invalid, "brief": {**final["brief"], "inputKind": "arbitrary"}}).status_code == 422

    package = client.post(BASE + "/package", json={"revision": final["revision"]})
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        exported = json.loads(archive.read("workspace.json"))
        assert exported["brief"]["inputKind"] == input_kind
        result_asset = next(asset for asset in exported["assets"] if asset["id"] == final["shots"][0]["resultAssetId"])
        assert archive.read(result_asset["url"])
        assert label in archive.read("HANDOFF.md").decode()


def test_result_versions_preserve_candidates_and_invalidate_review_on_plan_change(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.get(BASE).json()
    state = client.put(BASE, json={"revision": state['revision'], "brief": state['brief'], "shots": [
        {"id": "versions", "duration": 1, "title": "镜头", "prompt": "原提示", "nodes": [], "assetIds": []}]}).json()
    media = (tmp_path / f"project-files/{PROJECT_ID}/reference-media/ref-001.mp4").read_bytes()
    for name in ('first.mp4', 'second.mp4'):
        uploaded = client.post(BASE + f"/assets?role=motion&resultForShot=versions&revision={state['revision']}", files={"file": (name, media, "video/mp4")})
        assert uploaded.status_code == 200, uploaded.text
        state = uploaded.json()
    versions = state['shots'][0]['resultVersions']
    assert len(versions) == 2
    first, second = [item['assetId'] for item in versions]
    assert state['shots'][0]['resultAssetId'] == second
    assert all(not v['reviewed'] and not v['planChanged'] for v in versions)
    assert versions[0]['association']['shot']['prompt'] == '原提示'
    assert versions[0]['association']['revision'] < versions[1]['association']['revision']
    route = BASE + f'/shots/versions/results/{first}/review'
    assert client.post(route, json={'revision': 0}).status_code == 409
    state = client.post(route, json={'revision': state['revision']}).json()
    assert state['shots'][0]['resultVersions'][0]['reviewed']
    def put(current):
        result = client.put(BASE, json={key: current[key] for key in ('revision', 'brief', 'shots')})
        assert result.status_code == 200, result.text
        return result.json()
    state['shots'][0]['resultAssetId'] = first
    state['shots'][0]['title'] = '只改标题'
    state['shots'][0]['resultVersions'] = []
    state = put(state)
    assert len(state['shots'][0]['resultVersions']) == 2
    assert state['shots'][0]['resultVersions'][0]['reviewed']
    state['shots'][0]['prompt'] = '新方案'
    state = put(state)
    assert all(v['planChanged'] and not v['reviewed'] for v in state['shots'][0]['resultVersions'])
    assert all(v['association']['shot']['prompt'] == '原提示' for v in state['shots'][0]['resultVersions'])
    assert any(check.get('code') == 'result_review_required' for check in state['checks'])
    # A normal workspace PUT cannot attest human review.
    state['shots'][0]['resultVersions'] = [{'assetId': first, 'reviewed': True, 'planChanged': False}]
    state = put(state)
    assert not state['shots'][0]['resultVersions'][0]['reviewed']
    state['shots'][0]['resultAssetId'] = None
    state = put(state)
    assert len(state['shots'][0]['resultVersions']) == 2
    package = client.post(BASE + '/package', json={'revision': state['revision']})
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        public = json.loads(archive.read('workspace.json'))
        assert {asset['id'] for asset in public['assets']} == {first, second}
        for asset in public['assets']:
            assert archive.read(asset['url'])
        assert '候选 2' in archive.read('HANDOFF.md').decode()


def test_legacy_result_requires_review_and_review_rejects_unrelated_asset(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.post(BASE + '/import-reference', json={'revision': 0}).json()
    asset_id = state['assets'][0]['id']
    state = client.put(BASE, json={'revision': state['revision'], 'brief': state['brief'], 'shots': [
        {'id': 'legacy', 'duration': 1, 'prompt': '', 'assetIds': [], 'nodes': [], 'resultAssetId': asset_id}]}).json()
    assert state['shots'][0]['resultVersions'][0]['association']['shot']['id'] == 'legacy'
    path = tmp_path / f'project-files/{PROJECT_ID}/preproduction/state.json'
    raw = json.loads(path.read_text())
    raw['shots'][0].pop('_resultVersions')
    path.write_text(json.dumps(raw))
    legacy = client.get(BASE).json()
    assert legacy['shots'][0]['resultVersions'] == [{'assetId': asset_id, 'reviewed': False, 'planChanged': True}]
    assert client.post(BASE + '/shots/legacy/results/foreign/review', json={'revision': legacy['revision']}).status_code == 404
    checked = client.post(BASE + f'/shots/legacy/results/{asset_id}/review', json={'revision': legacy['revision']}).json()
    assert checked['shots'][0]['resultVersions'][0]['reviewed']
    assert 'association' not in checked['shots'][0]['resultVersions'][0]


def test_candidate_external_note_is_owned_by_candidate_and_does_not_change_association(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.get(BASE).json()
    state = client.put(BASE, json={'revision': state['revision'], 'brief': state['brief'], 'shots': [
        {'id': 'shot-a', 'duration': 1, 'prompt': '雨景', 'assetIds': [], 'nodes': []}]}).json()
    media = (tmp_path / f'project-files/{PROJECT_ID}/reference-media/ref-001.mp4').read_bytes()
    state = client.post(BASE + f"/assets?role=motion&resultForShot=shot-a&revision={state['revision']}",
                        files={'file': ('candidate.mp4', media, 'video/mp4')}).json()
    version = state['shots'][0]['resultVersions'][0]
    route = BASE + f"/shots/shot-a/results/{version['assetId']}/note"
    changed = client.put(route, json={'revision': state['revision'], 'note': '外部工具制作，参数未核实'})
    assert changed.status_code == 200, changed.text
    saved = changed.json()['shots'][0]['resultVersions'][0]
    assert saved['externalNote'] == '外部工具制作，参数未核实'
    assert saved['association'] == version['association']
    assert client.put(route, json={'revision': state['revision'], 'note': '旧版本写入'}).status_code == 409


def test_candidate_adoption_reason_stays_with_candidate_when_selection_changes(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.get(BASE).json()
    state = client.put(BASE, json={'revision': state['revision'], 'brief': state['brief'], 'shots': [
        {'id': 'shot-a', 'duration': 1, 'prompt': '雨景', 'assetIds': [], 'nodes': []}]}).json()
    media = (tmp_path / f'project-files/{PROJECT_ID}/reference-media/ref-001.mp4').read_bytes()
    for name in ('a.mp4', 'b.mp4'):
        state = client.post(BASE + f"/assets?role=motion&resultForShot=shot-a&revision={state['revision']}",
                            files={'file': (name, media, 'video/mp4')}).json()
    first, second = [version['assetId'] for version in state['shots'][0]['resultVersions']]
    route = BASE + f'/shots/shot-a/results/{first}/adoption-reason'
    saved = client.put(route, json={'revision': state['revision'], 'reason': '动作更自然'})
    assert saved.status_code == 200, saved.text
    state = saved.json()
    assert state['shots'][0]['resultVersions'][0]['adoptionReason'] == '动作更自然'
    assert 'adoptionReason' not in state['shots'][0]['resultVersions'][1]
    assert client.put(route, json={'revision': state['revision'] - 1, 'reason': '旧版本'}).status_code == 409
    state['shots'][0]['resultAssetId'] = first
    state = client.put(BASE, json={key: state[key] for key in ('revision', 'brief', 'shots')}).json()
    assert state['shots'][0]['resultVersions'][0]['adoptionReason'] == '动作更自然'
    state['shots'][0]['resultAssetId'] = second
    state = client.put(BASE, json={key: state[key] for key in ('revision', 'brief', 'shots')}).json()
    assert state['shots'][0]['resultVersions'][0]['adoptionReason'] == '动作更自然'
    state['shots'][0]['resultVersions'][0]['adoptionReason'] = '伪造覆盖'
    state = client.put(BASE, json={key: state[key] for key in ('revision', 'brief', 'shots')}).json()
    assert state['shots'][0]['resultVersions'][0]['adoptionReason'] == '动作更自然'
    package = client.post(BASE + '/package', json={'revision': state['revision']})
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert '采用理由：动作更自然' in archive.read('HANDOFF.md').decode()


def test_candidate_historical_asset_remains_named_when_file_unavailable(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.post(BASE + '/import-reference', json={'revision': 0}).json()
    asset = state['assets'][0]
    state = client.put(BASE, json={'revision': state['revision'], 'brief': state['brief'], 'shots': [
        {'id': 'shot-a', 'duration': 1, 'prompt': '旧提示', 'assetIds': [asset['id']], 'nodes': []}]}).json()
    media = (tmp_path / f'project-files/{PROJECT_ID}/reference-media/ref-001.mp4').read_bytes()
    state = client.post(BASE + f"/assets?role=motion&resultForShot=shot-a&revision={state['revision']}",
                        files={'file': ('candidate.mp4', media, 'video/mp4')}).json()
    association = state['shots'][0]['resultVersions'][0]['association']
    assert association['assets'][0]['name'] == asset['name']
    raw = json.loads((tmp_path / f'project-files/{PROJECT_ID}/preproduction/state.json').read_text())
    filename = next(item['file'] for item in raw['assets'] if item['id'] == asset['id'])
    (tmp_path / f'project-files/{PROJECT_ID}/preproduction/assets/{filename}').unlink()
    current = client.get(BASE).json()
    assert current['shots'][0]['resultVersions'][0]['association'] == association
    assert next(item for item in current['assets'] if item['id'] == asset['id'])['available'] is False
