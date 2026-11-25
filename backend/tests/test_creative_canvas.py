import json
import os
import io
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.preproduction import PreproductionStore
from app.preproduction_api import create_preproduction_router
from app.creative_canvas import CreativeCanvasStore, DocumentRunStore, DocumentTextRunStore, creative_canvas_asset_references
from app.creative_canvas_api import create_creative_canvas_router
from app.image_generation import ImageGenerationError


PROJECT_ID = "project-001"
BASE = f"/api/projects/{PROJECT_ID}/creative-canvas"


class Queue:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.jobs = []
        self.active = set()

    def submit(self, run_kind, owner_id, handler, **_kwargs):
        if not self.accepted:
            return False
        self.jobs.append((run_kind, owner_id, handler))
        self.active.add((run_kind, owner_id))
        return True

    def is_active(self, run_kind, owner_id):
        return (run_kind, owner_id) in self.active

    def run(self):
        run_kind, owner_id, handler = self.jobs.pop(0)
        handler(owner_id)
        self.active.discard((run_kind, owner_id))


class Configurations:
    def __init__(self, state="configured", provider="openai-compatible"):
        self.state = state
        self.provider = provider

    def snapshot(self):
        return {
            "baseUrl": "https://images.example.test/v1",
            "provider": self.provider,
            "model": "gpt-image-1",
            "models": ["gpt-image-1"],
            "credentialState": self.state,
            "revision": "configuration-1",
            "protocolCapabilities": {"referenceImages": "multiple" if self.provider == "volcengine-ark" else "single", "sizeMode": "custom-pixels" if self.provider == "volcengine-ark" else "provider-defined"},
        }


class ImageService:
    def __init__(self, result=None):
        self.result = result or png_bytes()
        self.snapshots = []

    def generate(self, snapshot, input_images=None, input_image=None):
        self.snapshots.append({"snapshot": dict(snapshot), "inputs": list(input_images or ([] if input_image is None else [input_image]))})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


def setup(tmp_path: Path, *, configurations=None, image_service=None, text_service=None, queue=None) -> tuple[TestClient, Queue]:
    project = SimpleNamespace(id=PROJECT_ID)
    store = PreproductionStore(tmp_path)
    state = store.load(PROJECT_ID)
    state["assets"] = [
        {"id": "asset-image", "name": "image.png", "kind": "image", "role": "reference", "file": "asset-image.png"},
        {"id": "asset-video", "name": "video.mp4", "kind": "video", "role": "motion", "file": "asset-video.mp4"},
    ]
    assets_dir = store.path(PROJECT_ID, "assets")
    assets_dir.mkdir(parents=True, exist_ok=True)
    for asset in state["assets"]:
        (assets_dir / asset["file"]).write_bytes(b"asset")
    store.save(PROJECT_ID, state)

    app = FastAPI()
    queue = queue or Queue()
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, Queue()))
    app.include_router(create_creative_canvas_router(
        tmp_path, lambda _: project, source_lock=None, configurations=configurations,
        image_service=image_service, text_service=text_service, compute_queue=queue,
    ))
    return TestClient(app), queue


def document(*, revision=0, nodes=None, edges=None, groups=None):
    return {
        "revision": revision,
        "nodes": nodes or [],
        "edges": edges or [],
        "groups": groups or [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "settings": {"comfyUrl": "", "checkpoint": ""},
    }


def test_canvas_document_saves_without_shot_and_returns_project_assets(tmp_path):
    client, _ = setup(tmp_path)
    initial = client.get(BASE)
    assert initial.status_code == 200
    assert initial.json()["document"] == document()
    assert initial.json()["runs"] == []
    assert {asset["id"] for asset in initial.json()["assets"]} == {"asset-image", "asset-video"}
    assert all(asset["available"] is True for asset in initial.json()["assets"])

    saved = document(nodes=[
        {"id": "text-1", "kind": "text", "position": {"x": 12, "y": 30}, "text": "product photo"},
        {"id": "media-1", "kind": "media", "position": {"x": 20, "y": 80}, "assetId": "asset-image"},
        {"id": "generate-1", "kind": "generate", "position": {"x": 320, "y": 20}},
    ], edges=[
        {"id": "edge-text", "source": "text-1", "target": "generate-1", "sourceHandle": "text", "targetHandle": "prompt"},
        {"id": "edge-image", "source": "media-1", "target": "generate-1", "sourceHandle": "image", "targetHandle": "image"},
    ])
    response = client.put(BASE, json=saved)

    assert response.status_code == 200, response.text
    assert response.json()["document"]["revision"] == 1
    restored = CreativeCanvasStore(tmp_path).load(PROJECT_ID)
    assert [node["id"] for node in restored["nodes"]] == ["text-1", "media-1", "generate-1"]
    assert restored["nodes"][0]["text"] == "product photo"
    assert restored["nodes"][1]["assetId"] == "asset-image"
    assert restored["edges"] == saved["edges"]


def test_canvas_workspace_returns_text_model_catalog_without_secrets(tmp_path):
    class TextService:
        def catalog(self):
            return [{"providerId": "default", "modelId": "text-1", "configured": True, "configurationVersion": "v1"}]

    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    payload = client.get(BASE).json()
    assert payload["textModels"] == [{"providerId": "default", "modelId": "text-1", "configured": True, "configurationVersion": "v1"}]


def test_canvas_save_rejects_stale_document_revision(tmp_path):
    client, _ = setup(tmp_path)
    first = client.put(BASE, json=document(nodes=[
        {"id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "first"},
    ]))
    assert first.status_code == 200

    stale = client.put(BASE, json=document(nodes=[
        {"id": "text-2", "kind": "text", "position": {"x": 0, "y": 0}, "text": "stale"},
    ]))
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "creative_canvas_conflict"


def test_canvas_rejects_duplicate_ids_invalid_ports_and_foreign_assets(tmp_path):
    client, _ = setup(tmp_path)
    duplicate = document(nodes=[
        {"id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "a"},
        {"id": "text-1", "kind": "text", "position": {"x": 1, "y": 0}, "text": "b"},
    ])
    assert client.put(BASE, json=duplicate).status_code == 422

    video_to_image = document(nodes=[
        {"id": "video-1", "kind": "media", "position": {"x": 0, "y": 0}, "assetId": "asset-video"},
        {"id": "generate-1", "kind": "generate", "position": {"x": 100, "y": 0}},
    ], edges=[
        {"id": "edge-image", "source": "video-1", "target": "generate-1", "sourceHandle": "image", "targetHandle": "image"},
    ])
    assert client.put(BASE, json=video_to_image).status_code == 422

    foreign_asset = document(nodes=[
        {"id": "media-1", "kind": "media", "position": {"x": 0, "y": 0}, "assetId": "asset-other-project"},
    ])
    assert client.put(BASE, json=foreign_asset).status_code == 422


def test_canvas_allows_a_generate_node_to_have_its_own_prompt(tmp_path):
    client, _ = setup(tmp_path)
    direct_prompt = document(nodes=[
        {"id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "studio product photograph"},
    ])

    response = client.put(BASE, json=direct_prompt)
    assert response.status_code == 200, response.text
    assert response.json()["document"]["nodes"][0]["text"] == "studio product photograph"


def test_canvas_persists_ordered_multiple_references_and_generate_outputs(tmp_path):
    client, _ = setup(tmp_path)
    saved = document(nodes=[
        {"id": "text-a", "kind": "text", "position": {"x": 0, "y": 0}, "text": "first"},
        {"id": "text-b", "kind": "text", "position": {"x": 0, "y": 50}, "text": "second"},
        {"id": "media-1", "kind": "media", "position": {"x": 0, "y": 100}, "assetId": "asset-image"},
        {"id": "generate-1", "kind": "generate", "position": {"x": 300, "y": 0}},
        {"id": "generate-2", "kind": "generate", "position": {"x": 600, "y": 0},
         "referenceOrder": ["edge-image", "edge-text-b", "edge-text-a", "edge-result"]},
    ], edges=[
        {"id": "edge-text-a", "source": "text-a", "target": "generate-2", "sourceHandle": "text", "targetHandle": "prompt"},
        {"id": "edge-text-b", "source": "text-b", "target": "generate-2", "sourceHandle": "text", "targetHandle": "prompt"},
        {"id": "edge-image", "source": "media-1", "target": "generate-2", "sourceHandle": "image", "targetHandle": "image"},
        {"id": "edge-result", "source": "generate-1", "target": "generate-2", "sourceHandle": "image", "targetHandle": "image"},
    ])

    response = client.put(BASE, json=saved)

    assert response.status_code == 200, response.text
    node = response.json()["document"]["nodes"][-1]
    assert node["referenceOrder"] == ["edge-image", "edge-text-b", "edge-text-a", "edge-result"]
    assert len(response.json()["document"]["edges"]) == 4


def test_canvas_rejects_exact_duplicate_self_and_cyclic_edges(tmp_path):
    client, _ = setup(tmp_path)
    nodes = [
        {"id": "generate-a", "kind": "generate", "position": {"x": 0, "y": 0}},
        {"id": "generate-b", "kind": "generate", "position": {"x": 100, "y": 0}},
    ]
    duplicate = document(nodes=nodes, edges=[
        {"id": "edge-a", "source": "generate-a", "target": "generate-b", "sourceHandle": "image", "targetHandle": "image"},
        {"id": "edge-b", "source": "generate-a", "target": "generate-b", "sourceHandle": "image", "targetHandle": "image"},
    ])
    self_edge = document(nodes=nodes, edges=[
        {"id": "edge-self", "source": "generate-a", "target": "generate-a", "sourceHandle": "image", "targetHandle": "image"},
    ])
    cycle = document(nodes=nodes, edges=[
        {"id": "edge-a", "source": "generate-a", "target": "generate-b", "sourceHandle": "image", "targetHandle": "image"},
        {"id": "edge-b", "source": "generate-b", "target": "generate-a", "sourceHandle": "image", "targetHandle": "image"},
    ])

    assert client.put(BASE, json=duplicate).status_code == 422
    assert client.put(BASE, json=self_edge).status_code == 422
    assert client.put(BASE, json=cycle).status_code == 422


def test_canvas_rejects_sizes_outside_shared_pixel_bounds(tmp_path):
    client, _ = setup(tmp_path)
    tiny = document(nodes=[
        {"id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "params": {"size": "10x10"}},
    ])
    oversized = document(nodes=[
        {"id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "params": {"size": "8192x8192"}},
    ])

    assert client.put(BASE, json=tiny).status_code == 422
    assert client.put(BASE, json=oversized).status_code == 422


def test_canvas_persists_optional_node_geometry_and_nested_groups(tmp_path):
    client, _ = setup(tmp_path)
    saved = document(
        nodes=[
            {"id": "text-1", "kind": "text", "parentId": "group-child", "position": {"x": 12, "y": 30}, "text": "caption", "width": 360, "height": 180, "freeResize": True},
            {"id": "generate-1", "kind": "generate", "position": {"x": 440, "y": 30}},
        ],
        groups=[
            {"id": "group-root", "label": "首屏", "position": {"x": 100, "y": 20}, "width": 800, "height": 500},
            {"id": "group-child", "parentId": "group-root", "position": {"x": 40, "y": 60}, "width": 500, "height": 260},
        ],
    )

    response = client.put(BASE, json=saved)

    assert response.status_code == 200, response.text
    document_payload = response.json()["document"]
    assert document_payload["nodes"][0]["parentId"] == "group-child"
    assert document_payload["nodes"][0]["width"] == 360.0
    assert document_payload["nodes"][0]["height"] == 180.0
    assert document_payload["nodes"][0]["freeResize"] is True
    assert document_payload["groups"] == [
        {"id": "group-root", "label": "首屏", "parentId": None, "position": {"x": 100.0, "y": 20.0}, "width": 800.0, "height": 500.0},
        {"id": "group-child", "label": "", "parentId": "group-root", "position": {"x": 40.0, "y": 60.0}, "width": 500.0, "height": 260.0},
    ]


def test_canvas_rejects_invalid_group_parent_node_geometry_and_zoom(tmp_path):
    client, _ = setup(tmp_path)
    base_nodes = [
        {"id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "a"},
        {"id": "text-2", "kind": "text", "position": {"x": 120, "y": 0}, "text": "b"},
    ]

    assert client.put(BASE, json=document(nodes=[{**base_nodes[0], "parentId": "missing"}])).status_code == 422
    assert client.put(BASE, json=document(nodes=base_nodes, groups=[{"id": "group-1", "parentId": "missing"}])).status_code == 422
    assert client.put(BASE, json=document(nodes=base_nodes, groups=[{"id": "group-1", "parentId": "group-2"}, {"id": "group-2", "parentId": "group-1"}])).status_code == 422
    assert client.put(BASE, json=document(nodes=base_nodes, groups=[{"id": "text-1"}])).status_code == 422
    assert client.put(BASE, json=document(nodes=[{**base_nodes[0], "width": 63}])).status_code == 422
    assert client.put(BASE, json=document(nodes=[{**base_nodes[0], "height": 8193}])).status_code == 422
    assert client.put(BASE, json={**document(nodes=base_nodes), "viewport": {"x": 0, "y": 0, "zoom": 8}}).status_code == 200
    assert client.put(BASE, json={**document(nodes=base_nodes), "viewport": {"x": 0, "y": 0, "zoom": 8.1}}).status_code == 422


def test_canvas_reference_prevents_deleting_its_project_asset(tmp_path):
    client, _ = setup(tmp_path)
    saved = client.put(BASE, json=document(nodes=[
        {"id": "media-1", "kind": "media", "position": {"x": 0, "y": 0}, "assetId": "asset-image"},
    ]))
    assert saved.status_code == 200

    references = creative_canvas_asset_references(tmp_path, PROJECT_ID, "asset-image")
    assert references == [{"kind": "creative_canvas_media", "label": "创作画布 · 媒体节点 media-1", "nodeId": "media-1"}]

    deleted = client.post(
        f"/api/projects/{PROJECT_ID}/preproduction/assets/asset-image/delete",
        json={"revision": 0},
    )
    assert deleted.status_code == 409
    assert deleted.json()["detail"]["code"] == "asset_in_use"


def test_canvas_references_future_run_inputs_snapshots_and_outputs(tmp_path):
    client, _ = setup(tmp_path)
    state_path = CreativeCanvasStore(tmp_path).path(PROJECT_ID, "runs.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps([
        {
            "id": "run-1",
            "inputAssetIds": ["asset-image"],
            "outputAssetIds": ["asset-video"],
            "snapshot": {"inputAssetIds": ["asset-image"], "references": [{"assetId": "asset-image"}]},
        },
    ]))

    image_references = creative_canvas_asset_references(tmp_path, PROJECT_ID, "asset-image")
    video_references = creative_canvas_asset_references(tmp_path, PROJECT_ID, "asset-video")

    assert {reference["kind"] for reference in image_references} == {
        "creative_canvas_run_input", "creative_canvas_run_snapshot", "creative_canvas_run_reference",
    }
    assert video_references == [{
        "kind": "creative_canvas_run_output", "label": "创作画布 · 运行 run-1 的输出素材", "runId": "run-1",
    }]


def test_canvas_store_rejects_dangling_state_and_run_symlinks(tmp_path):
    store = CreativeCanvasStore(tmp_path)
    state_path = store.path(PROJECT_ID, "state.json")
    runs_path = store.path(PROJECT_ID, "runs.json")
    state_path.parent.mkdir(parents=True)
    os.symlink(state_path.parent / "missing-state.json", state_path)
    os.symlink(runs_path.parent / "missing-runs.json", runs_path)

    try:
        store.load(PROJECT_ID)
    except OSError:
        pass
    else:
        raise AssertionError("悬空画布状态链接不能被视为缺失状态")
    try:
        store.load_runs(PROJECT_ID)
    except OSError:
        pass
    else:
        raise AssertionError("悬空运行记录链接不能被视为缺失状态")


def generation_document(prompt="original prompt"):
    return document(nodes=[
        {"id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "text": prompt},
    ])


def test_canvas_run_freezes_prompt_installs_a_project_asset_and_allows_document_edits(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()

    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert queued.status_code == 200, queued.text
    assert queued.json()["runs"][0]["status"] == "queued"
    assert queued.json()["runs"][0]["snapshot"] == {
        "baseUrl": "https://images.example.test/v1", "model": "gpt-image-1", "modelId": "gpt-image-1", "providerId": "default",
        "provider": "openai-compatible", "revision": "configuration-1", "configVersion": "configuration-1", "capabilityVersion": "legacy-protocol", "prompt": "original prompt", "size": "1024x1024",
        "inputAssetIds": [], "references": [], "count": 1,
    }

    edited = client.put(BASE, json={**generation_document("edited prompt"), "revision": saved["document"]["revision"]})
    assert edited.status_code == 200
    queue.run()
    completed = client.get(BASE).json()
    run = completed["runs"][0]
    assert run["status"] == "completed"
    assert run["snapshot"]["prompt"] == "original prompt"
    assert len(run["outputAssetIds"]) == 1
    assert service.snapshots[0]["snapshot"]["prompt"] == "original prompt"
    assert creative_canvas_asset_references(tmp_path, PROJECT_ID, run["outputAssetIds"][0])[0]["kind"] == "creative_canvas_run_output"


def test_canvas_run_freezes_all_ordered_references_and_resolves_generated_output(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(provider="volcengine-ark"), image_service=service)
    saved = client.put(BASE, json=document(nodes=[
        {"id": "text-a", "kind": "text", "position": {"x": 0, "y": 0}, "text": "first"},
        {"id": "text-b", "kind": "text", "position": {"x": 0, "y": 50}, "text": "second"},
        {"id": "media-1", "kind": "media", "position": {"x": 0, "y": 100}, "assetId": "asset-image"},
        {"id": "generate-source", "kind": "generate", "position": {"x": 200, "y": 100}, "outputAssetId": "asset-image"},
        {"id": "generate-target", "kind": "generate", "position": {"x": 400, "y": 0}, "text": "own prompt",
         "referenceOrder": ["edge-text-b", "edge-image", "edge-text-a", "edge-generated"]},
    ], edges=[
        {"id": "edge-text-a", "source": "text-a", "target": "generate-target", "sourceHandle": "text", "targetHandle": "prompt"},
        {"id": "edge-text-b", "source": "text-b", "target": "generate-target", "sourceHandle": "text", "targetHandle": "prompt"},
        {"id": "edge-image", "source": "media-1", "target": "generate-target", "sourceHandle": "image", "targetHandle": "image"},
        {"id": "edge-generated", "source": "generate-source", "target": "generate-target", "sourceHandle": "image", "targetHandle": "image"},
    ])).json()

    queued = client.post(BASE + "/nodes/generate-target/runs", json={"revision": saved["document"]["revision"]})
    assert queued.status_code == 200, queued.text
    run = queued.json()["runs"][0]
    assert run["inputAssetIds"] == ["asset-image", "asset-image"]
    assert run["snapshot"]["prompt"] == "own prompt\n\n参考 1：\nsecond\n\n参考 3：\nfirst"
    assert [item["edgeId"] for item in run["snapshot"]["references"]] == [
        "edge-text-b", "edge-image", "edge-text-a", "edge-generated",
    ]
    queue.run()
    assert len(service.snapshots[0]["inputs"]) == 2


def test_canvas_rejects_execution_when_generated_reference_has_no_output(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    saved = client.put(BASE, json=document(nodes=[
        {"id": "generate-source", "kind": "generate", "position": {"x": 0, "y": 0}},
        {"id": "generate-target", "kind": "generate", "position": {"x": 200, "y": 0}, "text": "target"},
    ], edges=[
        {"id": "edge-result", "source": "generate-source", "target": "generate-target", "sourceHandle": "image", "targetHandle": "image"},
    ])).json()

    response = client.post(BASE + "/nodes/generate-target/runs", json={"revision": saved["document"]["revision"]})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "creative_canvas_reference_unavailable"


def test_canvas_cancelled_queued_run_never_submits_and_waits_for_queue_cleanup(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    first = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]}).json()["runs"][0]

    cancelled = client.post(BASE + "/runs/" + first["id"] + "/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["runs"][0]["status"] == "cancelled"
    assert cancelled.json()["runs"][0]["providerDisposition"] == "not_submitted"
    assert client.post(BASE + "/runs/" + first["id"] + "/cancel").status_code == 200
    second = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "creative_canvas_queue_draining"

    queue.run()
    second = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert second.status_code == 200, second.text
    queue.run()
    runs = client.get(BASE).json()["runs"]
    assert runs[0]["status"] == "cancelled"
    assert runs[1]["status"] == "completed"
    assert len(service.snapshots) == 1


def test_canvas_cancelled_running_run_archives_late_result_without_making_it_current(tmp_path):
    class LateResultService(ImageService):
        def generate(self, snapshot, input_images=None, input_image=None):
            self.cancel_response = self.cancel()
            return super().generate(snapshot, input_images=input_images, input_image=input_image)

    service = LateResultService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]}).json()["runs"][0]
    service.cancel = lambda: client.post(BASE + "/runs/" + queued["id"] + "/cancel")

    queue.run()

    payload = client.get(BASE).json()
    run = payload["runs"][0]
    assert service.cancel_response.json()["runs"][0]["status"] == "cancel_requested"
    assert run["status"] == "cancelled"
    assert run["cancellationRequested"] is True
    assert run["providerDisposition"] == "unsupported"
    assert run["billingUnknown"] is True
    assert len(run["outputAssetIds"]) == 1
    assert payload["document"]["nodes"][0].get("outputAssetId") is None
    assert creative_canvas_asset_references(tmp_path, PROJECT_ID, run["outputAssetIds"][0])[0]["kind"] == "creative_canvas_run_output"


def test_canvas_uses_latest_completed_generated_output_and_deduplicates_idempotent_run(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "source-run", "nodeId": "generate-source", "status": "completed", "cancellationRequested": False,
        "outputAssetIds": ["asset-image"], "inputAssetIds": [], "snapshot": {"inputAssetIds": []},
    }])
    saved = client.put(BASE, json=document(nodes=[
        {"id": "generate-source", "kind": "generate", "position": {"x": 0, "y": 0}, "outputAssetId": "asset-image"},
        {"id": "generate-target", "kind": "generate", "position": {"x": 200, "y": 0}, "text": "target"},
    ], edges=[
        {"id": "edge-output", "source": "generate-source", "target": "generate-target", "sourceHandle": "image", "targetHandle": "image"},
    ])).json()
    request = {"revision": saved["document"]["revision"], "idempotencyKey": "attempt-001"}

    first = client.post(BASE + "/nodes/generate-target/runs", json=request)
    assert first.status_code == 200, first.text
    run = next(item for item in first.json()["runs"] if item["nodeId"] == "generate-target")
    assert run["snapshot"]["references"][0]["sourceRunId"] == "source-run"
    queue.run()
    updated = client.put(BASE, json={**saved["document"], "revision": saved["document"]["revision"]})
    assert updated.status_code == 200, updated.text
    retry = client.post(BASE + "/nodes/generate-target/runs", json=request)

    assert retry.status_code == 200, retry.text
    assert len([item for item in retry.json()["runs"] if item["nodeId"] == "generate-target"]) == 1
    assert len(service.snapshots) == 1


def test_canvas_unknown_cancelled_run_accepts_idempotent_cancel_retry(tmp_path):
    class TimeoutAfterCancel(ImageService):
        def generate(self, snapshot, input_images=None, input_image=None):
            self.cancel()
            raise ImageGenerationError("timeout", outcome_unknown=True)

    service = TimeoutAfterCancel()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]}).json()["runs"][0]
    service.cancel = lambda: client.post(BASE + "/runs/" + queued["id"] + "/cancel")

    queue.run()
    unknown = client.get(BASE).json()["runs"][0]
    retry = client.post(BASE + "/runs/" + queued["id"] + "/cancel")

    assert unknown["status"] == "unknown"
    assert unknown["cancellationRequested"] is True
    assert retry.status_code == 200, retry.text
    repeated = retry.json()["runs"][0]
    assert repeated["status"] == "unknown"
    assert repeated["requestedAt"] == unknown["requestedAt"]


def test_canvas_run_marks_ambiguous_failure_unknown_and_refresh_does_not_resubmit(tmp_path):
    service = ImageService(ImageGenerationError("timeout", outcome_unknown=True))
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert queued.status_code == 200
    queue.run()

    unknown = client.post(BASE + "/runs/" + queued.json()["runs"][0]["id"] + "/refresh")
    assert unknown.status_code == 200
    assert unknown.json()["runs"][0]["status"] == "unknown"
    repeated = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert repeated.status_code == 409
    assert len(service.snapshots) == 1


def test_unknown_run_blocks_only_its_own_generate_node(tmp_path):
    service = ImageService(ImageGenerationError("timeout", outcome_unknown=True))
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=document(nodes=[
        {"id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "first"},
        {"id": "generate-2", "kind": "generate", "position": {"x": 0, "y": 100}, "text": "second"},
    ])).json()
    first = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert first.status_code == 200
    queue.run()

    same_node = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    other_node = client.post(BASE + "/nodes/generate-2/runs", json={"revision": saved["document"]["revision"]})
    assert same_node.status_code == 409
    assert other_node.status_code == 200, other_node.text


def test_result_asset_remains_installed_when_completed_run_record_write_fails(tmp_path, monkeypatch):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert queued.status_code == 200

    original_save = DocumentRunStore.save

    def fail_only_completed(self, project_id, runs):
        if any(run.get("status") == "completed" for run in runs):
            raise OSError("runs unavailable")
        return original_save(self, project_id, runs)

    monkeypatch.setattr(DocumentRunStore, "save", fail_only_completed)
    queue.run()

    state = PreproductionStore(tmp_path).load(PROJECT_ID)
    generated = next(asset for asset in state["assets"] if asset["id"].startswith("canvas-"))
    assert (PreproductionStore(tmp_path).path(PROJECT_ID, "assets", generated["file"])).is_file()
    run = client.get(BASE).json()["runs"][0]
    assert run["status"] == "unknown"
    assert run["error"] == "result_record_failed"
    assert run["outputAssetIds"] == [generated["id"]]


def test_canvas_run_marks_queue_rejection_failed_and_rejects_unconfigured_service(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations("unconfigured"), image_service=ImageService())
    saved = client.put(BASE, json=generation_document()).json()
    unavailable = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert unavailable.status_code == 422
    assert unavailable.json()["detail"]["code"] == "image_generation_unconfigured"

    client, _ = setup(tmp_path / "queue", configurations=Configurations(), image_service=ImageService(), queue=Queue(accepted=False))
    saved = client.put(BASE, json=generation_document()).json()
    failed = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert failed.status_code == 200
    assert failed.json()["runs"][0]["status"] == "failed"
    assert failed.json()["runs"][0]["error"] == "queue_unavailable"


def test_canvas_router_recovers_interrupted_runs_without_resubmitting(tmp_path):
    run_store = DocumentRunStore(tmp_path)
    run_store.save(PROJECT_ID, [
        {"id": "run-running", "status": "running"},
        {"id": "run-queued", "status": "queued"},
    ])
    app = FastAPI()
    app.include_router(create_creative_canvas_router(
        tmp_path, lambda _: SimpleNamespace(id=PROJECT_ID), configurations=Configurations(),
        image_service=ImageService(), compute_queue=Queue(),
    ))
    client = TestClient(app)

    states = {run["id"]: run for run in client.get(BASE).json()["runs"]}
    assert states["run-running"]["status"] == "unknown"
    assert states["run-running"]["error"] == "service_restarted"
    assert states["run-queued"]["status"] == "failed"
    assert states["run-queued"]["error"] == "queue_interrupted"


def test_main_protects_canvas_and_image_configuration_mutations(tmp_path):
    from app.main import create_app

    with TestClient(create_app(data_dir=tmp_path)) as client:
        project = client.post("/api/projects", json={"name": "画布边界"}).json()
        origin = {"Origin": "https://untrusted.example", "Content-Type": "application/json"}
        canvas = client.put(f"/api/projects/{project['id']}/creative-canvas", json=document(), headers=origin)
        configuration = client.put("/api/image-generation/configuration", json={}, headers=origin)
    assert canvas.status_code == 403
    assert configuration.status_code == 403


def test_canvas_catalog_keeps_legacy_default_and_isolates_canvas_documents(tmp_path):
    client, _ = setup(tmp_path)
    listed = client.get(BASE + "/canvases")
    assert listed.status_code == 200
    assert listed.json()["canvases"] == [{"id": "default", "name": "默认画布", "createdAt": None, "updatedAt": None, "deletedAt": None}]
    legacy = client.put(BASE, json=document(nodes=[
        {"id": "text-legacy", "kind": "text", "position": {"x": 0, "y": 0}, "text": "legacy"},
    ]))
    assert legacy.status_code == 200

    created = client.post(BASE + "/canvases", json={"name": "第二画布"})
    assert created.status_code == 201, created.text
    canvas_id = created.json()["canvas"]["id"]
    assert canvas_id != "default"
    assert client.get(BASE).json()["document"]["nodes"][0]["id"] == "text-legacy"

    other = client.put(BASE + f"?canvasId={canvas_id}", json=document(nodes=[
        {"id": "text-same", "kind": "text", "position": {"x": 0, "y": 0}, "text": "other"},
    ]))
    assert other.status_code == 200, other.text
    assert other.json()["document"]["revision"] == 1
    assert client.get(BASE + f"?canvasId={canvas_id}").json()["document"]["nodes"][0]["text"] == "other"
    assert client.get(BASE).json()["document"]["revision"] == 1


def test_canvas_catalog_isolates_same_node_runs_and_rejects_deleting_active_canvas(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    first = client.post(BASE + "/canvases", json={"name": "A"}).json()["canvas"]["id"]
    second = client.post(BASE + "/canvases", json={"name": "B"}).json()["canvas"]["id"]
    for canvas_id, prompt in ((first, "first"), (second, "second")):
        saved = client.put(BASE + f"?canvasId={canvas_id}", json=generation_document(prompt)).json()
        started = client.post(
            BASE + f"/nodes/generate-1/runs?canvasId={canvas_id}",
            json={"revision": saved["document"]["revision"], "idempotencyKey": "same-key"},
        )
        assert started.status_code == 200, started.text

    first_run = client.get(BASE + f"?canvasId={first}").json()["runs"][0]
    assert len(client.get(BASE + f"?canvasId={second}").json()["runs"]) == 1
    wrong_canvas_cancel = client.post(BASE + f"/runs/{first_run['id']}/cancel?canvasId={second}")
    assert wrong_canvas_cancel.status_code == 404
    deleting = client.delete(BASE + f"/canvases/{first}")
    assert deleting.status_code == 409
    assert deleting.json()["detail"]["code"] == "creative_canvas_runs_active"
    queue.run(); queue.run()
    assert client.delete(BASE + f"/canvases/{first}").status_code == 200
    assert client.get(BASE + f"?canvasId={first}").status_code == 404


def test_soft_deleted_canvas_keeps_asset_references_and_can_restore(tmp_path):
    client, _ = setup(tmp_path)
    canvas_id = client.post(BASE + "/canvases", json={"name": "素材来源"}).json()["canvas"]["id"]
    saved = client.put(BASE + f"?canvasId={canvas_id}", json=document(nodes=[
        {"id": "media-1", "kind": "media", "position": {"x": 0, "y": 0}, "assetId": "asset-image"},
    ]))
    assert saved.status_code == 200
    assert client.delete(BASE + f"/canvases/{canvas_id}").status_code == 200
    assert creative_canvas_asset_references(tmp_path, PROJECT_ID, "asset-image") == [{
        "kind": "creative_canvas_media", "label": "创作画布 · 素材来源 · 媒体节点 media-1", "nodeId": "media-1", "canvasId": canvas_id,
    }]
    restored = client.post(BASE + f"/canvases/{canvas_id}/restore")
    assert restored.status_code == 200
    assert client.get(BASE + f"?canvasId={canvas_id}").status_code == 200


def test_canvas_stable_model_selection_uses_explicit_capabilities_and_freezes_reference_identity(tmp_path):
    class CapabilityConfigurations(Configurations):
        def snapshot(self):
            return {
                "baseUrl": "https://ark.example.test/api/v3", "provider": "volcengine-ark", "model": "seedream",
                "models": ["seedream"], "credentialState": "configured", "revision": "configuration-2", "configVersion": "configuration-2",
                "activeProviderId": "ark-main", "protocolCapabilities": {"referenceImages": "multiple", "sizeMode": "custom-pixels"},
                "providers": [{"providerId": "ark-main", "protocol": "volcengine-ark", "baseUrl": "https://ark.example.test/api/v3", "defaultModelId": "seedream"}],
                "modelCatalog": [{"providerId": "ark-main", "modelId": "seedream", "mediaType": "image", "configured": True,
                                  "capabilitySource": "user-configured", "capabilityVersion": "configuration-2",
                                  "capabilities": {"imageSizes": ["1024x1024"], "imageRatios": ["1:1"], "maxReferences": 1}}],
            }

    service = ImageService()
    client, queue = setup(tmp_path, configurations=CapabilityConfigurations(), image_service=service)
    saved = client.put(BASE, json=document(nodes=[
        {"id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "frozen reference"},
        {"id": "generate-1", "kind": "generate", "position": {"x": 200, "y": 0}, "text": "main", "params": {
            "providerId": "ark-main", "modelId": "seedream", "configurationVersion": "configuration-2", "size": "1024x1024", "ratio": "1:1",
        }},
    ], edges=[{"id": "edge-text", "source": "text-1", "target": "generate-1", "sourceHandle": "text", "targetHandle": "prompt"}])).json()

    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert queued.status_code == 200, queued.text
    snapshot = queued.json()["runs"][0]["snapshot"]
    assert {key: snapshot[key] for key in ("providerId", "modelId", "configVersion", "capabilityVersion")} == {
        "providerId": "ark-main", "modelId": "seedream", "configVersion": "configuration-2", "capabilityVersion": "configuration-2",
    }
    assert snapshot["references"] == [{
        "edgeId": "edge-text", "source": "text-1", "sourceNodeId": "text-1", "sourceHandle": "text", "target": "generate-1", "targetHandle": "prompt",
        "order": 1, "dataType": "text", "text": "frozen reference", "textVersion": "legacy-text:text-1:r1",
    }]
    queue.run()
    assert service.snapshots[0]["snapshot"]["modelId"] == "seedream"


def test_canvas_stable_model_rejects_unknown_or_incompatible_capabilities(tmp_path):
    class UnknownCapabilityConfigurations(Configurations):
        def snapshot(self):
            return {
                "baseUrl": "https://images.example.test/v1", "provider": "openai-compatible", "model": "gpt-image-1",
                "models": ["gpt-image-1"], "credentialState": "configured", "revision": "configuration-2", "configVersion": "configuration-2",
                "activeProviderId": "openai-main", "protocolCapabilities": {"referenceImages": "single", "sizeMode": "provider-defined"},
                "providers": [{"providerId": "openai-main", "protocol": "openai-compatible", "baseUrl": "https://images.example.test/v1", "defaultModelId": "gpt-image-1"}],
                "modelCatalog": [{"providerId": "openai-main", "modelId": "gpt-image-1", "mediaType": "image", "configured": True,
                                  "capabilitySource": "unknown", "capabilityVersion": "configuration-2", "capabilities": {}}],
            }

    client, _ = setup(tmp_path, configurations=UnknownCapabilityConfigurations(), image_service=ImageService())
    saved = client.put(BASE, json=document(nodes=[
        {"id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "main", "params": {
            "providerId": "openai-main", "modelId": "gpt-image-1", "configurationVersion": "configuration-2",
        }},
    ])).json()
    rejected = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "creative_canvas_model_capabilities_unknown"



def test_canvas_legacy_explicit_model_stays_with_default_provider_after_active_switch(tmp_path):
    from app.image_generation import ImageGenerationConfigurations, ImageGenerationSettings

    class Secrets:
        def __init__(self):
            self.values = {}

        def get(self, account):
            return self.values.get(account)

        def set(self, account, value):
            self.values[account] = value

    secrets = Secrets()
    configurations = ImageGenerationConfigurations(ImageGenerationSettings(tmp_path / "image-generation.json"), secret_store=lambda: secrets)
    configurations.save(providers=[
        {"providerId": "default", "protocol": "openai-compatible", "baseUrl": "https://images.example.test/v1", "defaultModelId": "shared-id", "models": [{"modelId": "shared-id"}], "apiKey": "default-key"},
        {"providerId": "ark-main", "protocol": "volcengine-ark", "baseUrl": "https://ark.example.test/api/v3", "defaultModelId": "shared-id", "models": [{"modelId": "shared-id"}], "apiKey": "ark-key"},
    ], active_provider_id="ark-main")
    client, _ = setup(tmp_path, configurations=configurations, image_service=ImageService())
    saved = client.put(BASE, json=document(nodes=[{"id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "main", "params": {"model": "shared-id"}}])).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert queued.status_code == 200, queued.text
    snapshot = queued.json()["runs"][0]["snapshot"]
    assert {key: snapshot[key] for key in ("providerId", "provider", "baseUrl", "model", "modelId")} == {
        "providerId": "default", "provider": "openai-compatible", "baseUrl": "https://images.example.test/v1", "model": "shared-id", "modelId": "shared-id",
    }
    resolved, credential = configurations.configured_snapshot(snapshot)
    assert resolved["baseUrl"] == "https://images.example.test/v1"
    assert credential == "default-key"

def test_canvas_stable_provider_uses_selected_credential_not_unconfigured_active_provider(tmp_path):
    class CrossProviderConfigurations(Configurations):
        def snapshot(self):
            return {
                "baseUrl": "https://images.example.test/v1", "provider": "openai-compatible", "model": "gpt-image-1", "models": ["gpt-image-1"],
                "credentialState": "unconfigured", "revision": "configuration-3", "configVersion": "configuration-3", "activeProviderId": "openai-main",
                "protocolCapabilities": {"referenceImages": "single", "sizeMode": "provider-defined"},
                "providers": [
                    {"providerId": "openai-main", "protocol": "openai-compatible", "baseUrl": "https://images.example.test/v1", "defaultModelId": "gpt-image-1", "credentialState": "unconfigured"},
                    {"providerId": "ark-main", "protocol": "volcengine-ark", "baseUrl": "https://ark.example.test/api/v3", "defaultModelId": "seedream", "credentialState": "configured"},
                ],
                "modelCatalog": [{"providerId": "ark-main", "modelId": "seedream", "mediaType": "image", "configured": True,
                                  "capabilitySource": "user-configured", "capabilityVersion": "configuration-3", "capabilities": {"imageSizes": ["1024x1024"]}}],
            }

    client, queue = setup(tmp_path, configurations=CrossProviderConfigurations(), image_service=ImageService())
    saved = client.put(BASE, json=document(nodes=[{"id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "main", "params": {
        "providerId": "ark-main", "modelId": "seedream", "configurationVersion": "configuration-3", "size": "1024x1024",
    }}])).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert queued.status_code == 200, queued.text
    queue.run()


def test_canvas_run_records_stable_output_item_and_keeps_asset_reference(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    queue.run()

    run = client.get(BASE).json()["runs"][0]
    assert run["outputItems"][0]["status"] == "installed"
    assert run["outputItems"][0]["assetId"] == run["outputAssetIds"][0]
    assert run["outputItems"][0]["sourceRunId"] == run["id"]
    assert creative_canvas_asset_references(tmp_path, PROJECT_ID, run["outputAssetIds"][0])[0]["kind"] == "creative_canvas_run_output"


def test_canvas_selecting_and_removing_result_updates_current_ref_and_marks_downstream_stale(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "source-run", "canvasId": "default", "nodeId": "source", "status": "completed", "cancellationRequested": False,
        "outputAssetIds": ["asset-image"], "outputItems": [{"resultId": "result-a", "assetId": "asset-image", "mediaType": "image", "status": "installed", "sourceRunId": "source-run", "createdAt": "2026-09-30T00:00:00+00:00"}],
        "inputAssetIds": [], "snapshot": {"inputAssetIds": [], "references": []},
    }])
    saved = client.put(BASE, json=document(nodes=[
        {"id": "source", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "source"},
        {"id": "target", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "target"},
    ], edges=[{"id": "edge", "source": "source", "target": "target", "sourceHandle": "image", "targetHandle": "image"}])).json()
    revision = saved["document"]["revision"]

    selected = client.post(BASE + "/runs/source-run/outputs/result-a/select", json={"revision": revision})
    assert selected.status_code == 200, selected.text
    source, target = selected.json()["document"]["nodes"]
    assert source["currentOutputRef"] == {"runId": "source-run", "resultId": "result-a"}
    assert source["outputAssetId"] == "asset-image"
    assert target["stale"] is True

    removed = client.request("DELETE", BASE + "/runs/source-run/outputs/result-a", json={"revision": selected.json()["document"]["revision"]})
    assert removed.status_code == 200, removed.text
    source = removed.json()["document"]["nodes"][0]
    assert "currentOutputRef" not in source or source["currentOutputRef"] is None
    assert source.get("outputAssetId") is None
    assert removed.json()["runs"][0]["outputItems"][0]["status"] == "deleted"


def test_canvas_batch_runs_topologically_and_freezes_selected_result_identity(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=document(nodes=[
        {"id": "root", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "root"},
        {"id": "child", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "child"},
    ], edges=[{"id": "edge", "source": "root", "target": "child", "sourceHandle": "image", "targetHandle": "image"}])).json()
    created = client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["root", "child"]})
    assert created.status_code == 200, created.text
    batch = created.json()["batches"][0]
    assert batch["steps"][1]["dependsOn"] == ["root"]

    for _ in range(5):
        queue.run()

    payload = client.get(BASE).json()
    batch = payload["batches"][0]
    assert batch["status"] == "completed"
    assert len(service.snapshots) == 2
    child_run = next(run for run in payload["runs"] if run["nodeId"] == "child")
    reference = child_run["snapshot"]["references"][0]
    assert reference["sourceRunId"] == batch["steps"][0]["runId"]
    assert reference["sourceResultId"] == batch["steps"][0]["selectedOutputRef"]["resultId"]


def test_canvas_rejects_ratio_that_disagrees_with_explicit_pixel_size(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    response = client.put(BASE, json=document(nodes=[{
        "id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "main",
        "params": {"size": "1024x1024", "ratio": "16:9"},
    }]))

    assert response.status_code == 422


def test_canvas_retries_only_definite_failed_result_without_creating_duplicate_run(tmp_path):
    service = ImageService(ImageGenerationError("invalid_request"))
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    queue.run()
    failed = client.get(BASE).json()["runs"][0]
    result_id = failed["outputItems"][0]["resultId"]
    assert failed["status"] == "failed"
    assert failed["outputItems"][0]["status"] == "failed"

    service.result = png_bytes()
    retried = client.post(BASE + f"/runs/{failed['id']}/outputs/{result_id}/retry", json={"revision": saved["document"]["revision"]})
    assert retried.status_code == 200, retried.text
    assert retried.json()["runs"][0]["status"] == "queued"
    queue.run()
    completed = client.get(BASE).json()["runs"][0]
    assert completed["outputItems"][0]["resultId"] == result_id
    assert completed["outputItems"][0]["status"] == "installed"
    assert len(client.get(BASE).json()["runs"]) == 1
    assert len(service.snapshots) == 2


def test_canvas_batch_cancel_stops_queued_successors_without_claiming_provider_cancel(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=document(nodes=[
        {"id": "root", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "root"},
        {"id": "child", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "child"},
    ], edges=[{"id": "edge", "source": "root", "target": "child", "sourceHandle": "image", "targetHandle": "image"}])).json()
    batch = client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["root", "child"]}).json()["batches"][0]

    cancelled = client.post(BASE + f"/batches/{batch['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["batches"][0]["status"] == "cancelled"
    assert {step["status"] for step in cancelled.json()["batches"][0]["steps"]} == {"cancelled"}
    queue.run()
    assert service.snapshots == []


def test_canvas_batch_snapshot_protects_source_assets_before_dispatch(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    saved = client.put(BASE, json=document(nodes=[
        {"id": "media", "kind": "media", "position": {"x": 0, "y": 0}, "assetId": "asset-image"},
        {"id": "generate", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "main"},
    ], edges=[{"id": "edge", "source": "media", "target": "generate", "sourceHandle": "image", "targetHandle": "image"}])).json()
    created = client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["generate"]})
    assert created.status_code == 200, created.text

    references = creative_canvas_asset_references(tmp_path, PROJECT_ID, "asset-image")
    assert {reference["kind"] for reference in references} == {"creative_canvas_media", "creative_canvas_batch_snapshot"}


def test_canvas_freezes_consistent_explicit_ratio_in_run_snapshot(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    saved = client.put(BASE, json=document(nodes=[{
        "id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "main",
        "params": {"size": "1024x576", "ratio": "16:9"},
    }])).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})

    assert queued.status_code == 200, queued.text
    assert queued.json()["runs"][0]["snapshot"]["ratio"] == "16:9"


def test_canvas_count_fans_out_independent_candidate_requests_and_keeps_partial_success(tmp_path):
    class SequencedService(ImageService):
        def generate(self, snapshot, input_images=None, input_image=None):
            self.snapshots.append({"snapshot": dict(snapshot), "inputs": list(input_images or [])})
            if len(self.snapshots) == 2:
                raise ImageGenerationError("request_failed")
            return png_bytes()

    service = SequencedService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"], "count": 2})
    assert queued.status_code == 200, queued.text
    run = queued.json()["runs"][0]
    assert len(run["outputItems"]) == 2
    assert len({item["resultId"] for item in run["outputItems"]}) == 2
    assert len({item["requestId"] for item in run["outputItems"]}) == 2

    queue.run(); queue.run()
    run = client.get(BASE).json()["runs"][0]
    assert run["status"] == "completed"
    assert [item["status"] for item in run["outputItems"]].count("installed") == 1
    assert [item["status"] for item in run["outputItems"]].count("failed") == 1
    assert len(service.snapshots) == 2


def test_delete_current_output_keeps_candidate_installed_when_run_record_save_fails(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{"id":"source-run","nodeId":"source","status":"completed","outputAssetIds":["asset-image"],"outputItems":[{"resultId":"result-a","assetId":"asset-image","mediaType":"image","status":"installed","sourceRunId":"source-run","createdAt":"now"}],"inputAssetIds":[],"snapshot":{"inputAssetIds":[],"references":[]}}])
    saved=client.put(BASE,json=document(nodes=[{"id":"source","kind":"generate","position":{"x":0,"y":0},"text":"x"}])).json()
    selected=client.post(BASE+"/runs/source-run/outputs/result-a/select",json={"revision":saved["document"]["revision"]}).json()
    original=DocumentRunStore.save
    monkeypatch.setattr(DocumentRunStore,"save",lambda self,*args,**kwargs: (_ for _ in ()).throw(OSError("disk")))
    failed=client.request("DELETE",BASE+"/runs/source-run/outputs/result-a",json={"revision":selected["document"]["revision"]})
    assert failed.status_code==503
    monkeypatch.setattr(DocumentRunStore,"save",original)
    recovered=client.get(BASE).json()
    # The document may have advanced, but it never references a logically deleted item.
    assert recovered["document"]["nodes"][0].get("currentOutputRef") is None
    assert recovered["runs"][0]["outputItems"][0]["status"]=="installed"
    retried=client.request("DELETE",BASE+"/runs/source-run/outputs/result-a",json={"revision":recovered["document"]["revision"]})
    assert retried.status_code==200, retried.text
    assert retried.json()["runs"][0]["outputItems"][0]["status"]=="deleted"


def test_canvas_candidate_cancelled_while_running_keeps_run_active_then_preserves_late_result(tmp_path):
    class CancellingService(ImageService):
        client = None
        run_id = None
        result_id = None
        revision = None

        def generate(self, snapshot, input_images=None, input_image=None):
            cancelled = self.client.post(BASE + f"/runs/{self.run_id}/outputs/{self.result_id}/cancel")
            assert cancelled.status_code == 200, cancelled.text
            state = cancelled.json()["runs"][0]
            assert state["status"] == "cancel_requested"
            # A new request must not be admitted while this provider call is still in flight.
            blocked = self.client.post(BASE + "/nodes/generate-1/runs", json={"revision": self.revision})
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["detail"]["code"] == "creative_canvas_run_active"
            return super().generate(snapshot, input_images=input_images, input_image=input_image)

    service = CancellingService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]}).json()
    run = queued["runs"][0]
    service.client, service.run_id = client, run["id"]
    service.result_id, service.revision = run["outputItems"][0]["resultId"], saved["document"]["revision"]

    queue.run()
    finished = client.get(BASE).json()["runs"][0]
    assert finished["status"] == "cancelled"
    assert finished.get("cancellationRequested") is not True
    assert finished["outputItems"][0]["status"] == "installed"
    assert finished["outputItems"][0]["cancellationRequested"] is True


def test_canvas_pending_candidate_cancel_recomputes_terminal_state_without_deadlock(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"], "count": 2}).json()
    run = queued["runs"][0]
    queue.run()
    current = client.get(BASE).json()["runs"][0]
    pending = next(item for item in current["outputItems"] if item["status"] == "pending")
    cancelled = client.post(BASE + f"/runs/{run['id']}/outputs/{pending['resultId']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    state = cancelled.json()["runs"][0]
    assert state["status"] == "completed"
    assert {item["status"] for item in state["outputItems"]} == {"installed", "cancelled"}
    queue.run()
    assert len(service.snapshots) == 1
    next_run = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert next_run.status_code == 200, next_run.text


def test_canvas_partial_failure_retries_only_failed_candidate_with_original_snapshot(tmp_path):
    class SequencedService(ImageService):
        def generate(self, snapshot, input_images=None, input_image=None):
            self.snapshots.append({"snapshot": dict(snapshot), "inputs": list(input_images or [])})
            if len(self.snapshots) == 2:
                raise ImageGenerationError("request_failed")
            return png_bytes()

    service = SequencedService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    queued = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"], "count": 2}).json()
    run_id = queued["runs"][0]["id"]
    queue.run(); queue.run()
    completed = client.get(BASE).json()["runs"][0]
    failed = next(item for item in completed["outputItems"] if item["status"] == "failed")
    retried = client.post(BASE + f"/runs/{run_id}/outputs/{failed['resultId']}/retry", json={"revision": saved["document"]["revision"]})
    assert retried.status_code == 200, retried.text
    queue.run()
    retried_run = client.get(BASE).json()["runs"][0]
    assert retried_run["status"] == "completed"
    assert all(item["status"] == "installed" for item in retried_run["outputItems"])
    assert len(service.snapshots) == 3
    assert all(call["snapshot"]["count"] == 2 for call in service.snapshots)


def test_canvas_restart_marks_each_inflight_candidate_unknown_or_interrupted(tmp_path):
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "run-running", "nodeId": "generate-1", "status": "running", "outputAssetIds": [],
        "outputItems": [
            {"resultId": "a", "status": "running", "billingUnknown": False},
            {"resultId": "b", "status": "cancel_requested", "billingUnknown": True},
            {"resultId": "c", "status": "pending", "billingUnknown": False},
        ],
    }])
    app = FastAPI()
    app.include_router(create_creative_canvas_router(
        tmp_path, lambda _: SimpleNamespace(id=PROJECT_ID), configurations=Configurations(),
        image_service=ImageService(), compute_queue=Queue(),
    ))
    client = TestClient(app)
    run = client.get(BASE).json()["runs"][0]
    states = {item["resultId"]: item for item in run["outputItems"]}
    assert run["status"] == "unknown"
    assert states["a"]["status"] == "unknown"
    assert states["b"]["status"] == "unknown"
    assert states["c"]["status"] == "failed"


def test_canvas_batch_uses_uncancelled_candidate_for_dependency(tmp_path):
    class CancellingFirstRoot(ImageService):
        client = None
        cancelled = False

        def generate(self, snapshot, input_images=None, input_image=None):
            if snapshot["prompt"] == "root" and not self.cancelled:
                root_run = next(run for run in self.client.get(BASE).json()["runs"] if run["nodeId"] == "root")
                running = next(item for item in root_run["outputItems"] if item["status"] == "running")
                cancelled = self.client.post(BASE + f"/runs/{root_run['id']}/outputs/{running['resultId']}/cancel")
                assert cancelled.status_code == 200, cancelled.text
                assert cancelled.json()["runs"][0]["status"] == "cancel_requested"
                self.cancelled = True
            return super().generate(snapshot, input_images=input_images, input_image=input_image)

    service = CancellingFirstRoot()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    service.client = client
    saved = client.put(BASE, json=document(nodes=[
        {"id": "root", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "root", "params": {"count": 2}},
        {"id": "child", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "child"},
    ], edges=[{"id": "edge", "source": "root", "target": "child", "sourceHandle": "image", "targetHandle": "image"}])).json()
    created = client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["root", "child"]})
    assert created.status_code == 200, created.text
    while queue.jobs:
        queue.run()

    payload = client.get(BASE).json()
    batch = payload["batches"][0]
    root_run = next(run for run in payload["runs"] if run["nodeId"] == "root")
    assert root_run["status"] == "completed"
    cancelled = next(item for item in root_run["outputItems"] if item["cancellationRequested"])
    selected = batch["steps"][0]["selectedOutputRef"]
    assert selected["resultId"] != cancelled["resultId"]
    assert batch["status"] == "completed"
    child_run = next(run for run in payload["runs"] if run["nodeId"] == "child")
    assert child_run["snapshot"]["references"][0]["sourceResultId"] == selected["resultId"]


def test_canvas_rejects_deleting_result_referenced_by_active_batch(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=document(nodes=[
        {"id": "root", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "root"},
        {"id": "child", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "child"},
    ], edges=[{"id": "edge", "source": "root", "target": "child", "sourceHandle": "image", "targetHandle": "image"}])).json()
    client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["root", "child"]})
    queue.run(); queue.run()
    payload = client.get(BASE).json()
    root_run = next(run for run in payload["runs"] if run["nodeId"] == "root")
    result = root_run["outputItems"][0]
    rejected = client.request("DELETE", BASE + f"/runs/{root_run['id']}/outputs/{result['resultId']}", json={"revision": saved["document"]["revision"]})
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "creative_canvas_result_batch_active"


def test_canvas_restart_drains_cancelled_queue_admission_gate(tmp_path):
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "cancelled-queued", "nodeId": "generate-1", "status": "cancelled",
        "localDisposition": "queued_cancelled", "cancellationRequested": True,
        "outputItems": [{"resultId": "a", "status": "cancelled", "cancellationRequested": True,
                         "localDisposition": "queued_cancelled", "providerDisposition": "not_submitted", "billingUnknown": False}],
    }])
    app = FastAPI()
    app.include_router(create_creative_canvas_router(
        tmp_path, lambda _: SimpleNamespace(id=PROJECT_ID), configurations=Configurations(),
        image_service=ImageService(), compute_queue=Queue(),
    ))
    client = TestClient(app)
    recovered = client.get(BASE).json()["runs"][0]
    assert recovered["localDisposition"] == "drained"
    assert recovered["outputItems"][0]["localDisposition"] == "queued_cancelled"
    saved = client.put(BASE, json=generation_document()).json()
    next_run = client.post(BASE + "/nodes/generate-1/runs", json={"revision": saved["document"]["revision"]})
    assert next_run.status_code == 200, next_run.text


def test_canvas_rejects_deleting_external_generated_input_frozen_by_active_batch(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "source-run", "nodeId": "source", "status": "completed", "outputAssetIds": ["asset-image"],
        "outputItems": [{"resultId": "source-result", "assetId": "asset-image", "mediaType": "image", "status": "installed", "sourceRunId": "source-run", "createdAt": "now"}],
        "inputAssetIds": [], "snapshot": {"inputAssetIds": [], "references": []},
    }])
    saved = client.put(BASE, json=document(nodes=[
        {"id": "source", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "source"},
        {"id": "selected", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "selected"},
    ], edges=[{"id": "edge", "source": "source", "target": "selected", "sourceHandle": "image", "targetHandle": "image"}])).json()
    selected = client.post(BASE + "/runs/source-run/outputs/source-result/select", json={"revision": saved["document"]["revision"]}).json()
    batch = client.post(BASE + "/batches", json={"revision": selected["document"]["revision"], "nodeIds": ["selected"]})
    assert batch.status_code == 200, batch.text
    rejected = client.request("DELETE", BASE + "/runs/source-run/outputs/source-result", json={"revision": selected["document"]["revision"]})
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "creative_canvas_result_batch_active"


def test_canvas_implicit_generated_source_uses_first_uncancelled_candidate(tmp_path):
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    state = PreproductionStore(tmp_path).load(PROJECT_ID)
    state["assets"].append({"id": "asset-second", "name": "second.png", "kind": "image", "role": "reference", "file": "asset-second.png"})
    (PreproductionStore(tmp_path).path(PROJECT_ID, "assets") / "asset-second.png").write_bytes(b"asset")
    state["revision"] += 1
    PreproductionStore(tmp_path).save(PROJECT_ID, state)
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "source-run", "nodeId": "source", "status": "completed", "outputAssetIds": ["asset-image", "asset-second"],
        "outputItems": [
            {"resultId": "first", "assetId": "asset-image", "mediaType": "image", "status": "installed", "sourceRunId": "source-run", "createdAt": "first", "cancellationRequested": False},
            {"resultId": "second", "assetId": "asset-second", "mediaType": "image", "status": "installed", "sourceRunId": "source-run", "createdAt": "second", "cancellationRequested": False},
            {"resultId": "late", "assetId": "asset-second", "mediaType": "image", "status": "installed", "sourceRunId": "source-run", "createdAt": "last", "cancellationRequested": True, "billingUnknown": True},
        ], "inputAssetIds": [], "snapshot": {"inputAssetIds": [], "references": []},
    }])
    saved = client.put(BASE, json=document(nodes=[
        {"id": "source", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "source"},
        {"id": "target", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "target"},
    ], edges=[{"id": "edge", "source": "source", "target": "target", "sourceHandle": "image", "targetHandle": "image"}])).json()
    queued = client.post(BASE + "/nodes/target/runs", json={"revision": saved["document"]["revision"]})
    assert queued.status_code == 200, queued.text
    target = next(run for run in queued.json()["runs"] if run["nodeId"] == "target")
    assert target["snapshot"]["references"][0]["sourceResultId"] == "first"
    assert target["snapshot"]["references"][0]["assetId"] == "asset-image"
    queue.run()


def test_canvas_implicit_generated_source_does_not_fall_back_past_deleted_latest_history(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    DocumentRunStore(tmp_path).save(PROJECT_ID, [
        {"id": "old", "nodeId": "source", "status": "completed", "outputAssetIds": ["asset-image"],
         "outputItems": [{"resultId": "old-result", "assetId": "asset-image", "mediaType": "image", "status": "installed"}], "snapshot": {}},
        {"id": "new", "nodeId": "source", "status": "completed", "outputAssetIds": ["asset-image"],
         "outputItems": [{"resultId": "deleted-result", "assetId": "asset-image", "mediaType": "image", "status": "deleted"}], "snapshot": {}},
    ])
    saved = client.put(BASE, json=document(nodes=[
        {"id": "source", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "source"},
        {"id": "target", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "target"},
    ], edges=[{"id": "edge", "source": "source", "target": "target", "sourceHandle": "image", "targetHandle": "image"}])).json()
    rejected = client.post(BASE + "/nodes/target/runs", json={"revision": saved["document"]["revision"]})
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "creative_canvas_reference_unavailable"


def test_canvas_batch_rejects_node_with_unknown_prior_result(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{"id": "unknown", "nodeId": "generate-1", "status": "unknown", "outputItems": [], "snapshot": {}}])
    saved = client.put(BASE, json=generation_document()).json()
    rejected = client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["generate-1"]})
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "creative_canvas_run_active"


def test_canvas_batch_blocks_step_when_unknown_result_appears_before_dispatch(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=generation_document()).json()
    created = client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["generate-1"]})
    assert created.status_code == 200, created.text
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{"id": "unknown", "nodeId": "generate-1", "status": "unknown", "outputItems": [], "snapshot": {}}])
    queue.run()
    batch = client.get(BASE).json()["batches"][0]
    assert batch["status"] == "blocked"
    assert batch["steps"][0]["error"] == "prior_result_unknown"
    assert service.snapshots == []


def test_canvas_batch_cancel_moves_each_count_candidate_before_provider_submission(tmp_path):
    class CancellingService(ImageService):
        client = None
        cancelled = False

        def generate(self, snapshot, input_images=None, input_image=None):
            if not self.cancelled:
                batch = self.client.get(BASE).json()["batches"][0]
                cancelled = self.client.post(BASE + f"/batches/{batch['id']}/cancel")
                assert cancelled.status_code == 200, cancelled.text
                run = cancelled.json()["runs"][0]
                states = {item["status"] for item in run["outputItems"]}
                assert "cancel_requested" in states
                assert "cancelled" in states
                self.cancelled = True
            return super().generate(snapshot, input_images=input_images, input_image=input_image)

    service = CancellingService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    service.client = client
    saved = client.put(BASE, json=document(nodes=[{
        "id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "main", "params": {"count": 2},
    }])).json()
    created = client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["generate-1"]})
    assert created.status_code == 200, created.text
    queue.run(); queue.run(); queue.run()
    payload = client.get(BASE).json()
    run = payload["runs"][0]
    assert payload["batches"][0]["status"] == "cancelled"
    assert run["status"] == "cancelled"
    assert [item["status"] for item in run["outputItems"]].count("cancelled") == 1
    assert [item["status"] for item in run["outputItems"]].count("installed") == 1
    assert len(service.snapshots) == 1


def test_canvas_delete_current_result_never_auto_selects_cancelled_late_candidate(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "source-run", "nodeId": "source", "status": "completed", "outputAssetIds": ["asset-image"],
        "outputItems": [
            {"resultId": "chosen", "assetId": "asset-image", "mediaType": "image", "status": "installed", "cancellationRequested": False},
            {"resultId": "late", "assetId": "asset-image", "mediaType": "image", "status": "installed", "cancellationRequested": True},
        ], "snapshot": {},
    }])
    saved = client.put(BASE, json=document(nodes=[{"id": "source", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "source"}])).json()
    selected = client.post(BASE + "/runs/source-run/outputs/chosen/select", json={"revision": saved["document"]["revision"]}).json()
    deleted = client.request("DELETE", BASE + "/runs/source-run/outputs/chosen", json={"revision": selected["document"]["revision"]})
    assert deleted.status_code == 200, deleted.text
    node = deleted.json()["document"]["nodes"][0]
    assert node.get("currentOutputRef") is None
    assert node.get("outputAssetId") is None


def test_canvas_rejects_deleting_implicit_external_source_frozen_by_active_batch(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService())
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "source-run", "nodeId": "source", "status": "completed", "outputAssetIds": ["asset-image"],
        "outputItems": [{"resultId": "source-result", "assetId": "asset-image", "mediaType": "image", "status": "installed", "sourceRunId": "source-run", "createdAt": "now"}],
        "inputAssetIds": [], "snapshot": {"inputAssetIds": [], "references": []},
    }])
    saved = client.put(BASE, json=document(nodes=[
        {"id": "source", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "source"},
        {"id": "selected", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "selected"},
    ], edges=[{"id": "edge", "source": "source", "target": "selected", "sourceHandle": "image", "targetHandle": "image"}])).json()
    created = client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["selected"]})
    assert created.status_code == 200, created.text
    rejected = client.request("DELETE", BASE + "/runs/source-run/outputs/source-result", json={"revision": saved["document"]["revision"]})
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "creative_canvas_result_batch_active"


def test_canvas_batch_cancel_queued_count_candidates_never_call_provider(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    saved = client.put(BASE, json=document(nodes=[{
        "id": "generate-1", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "main", "params": {"count": 2},
    }])).json()
    created = client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["generate-1"]}).json()
    queue.run()  # advance the batch and enqueue both candidates, but do not start either.
    cancelled = client.post(BASE + f"/batches/{created['batches'][0]['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    run = cancelled.json()["runs"][0]
    assert run["status"] == "cancelled"
    assert all(item["status"] == "cancelled" and item["providerDisposition"] == "not_submitted" for item in run["outputItems"])
    queue.run(); queue.run()
    assert service.snapshots == []


def test_canvas_batch_freezes_external_generated_result_at_submission(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    state = PreproductionStore(tmp_path).load(PROJECT_ID)
    state["assets"].append({"id": "asset-r2", "name": "r2.png", "kind": "image", "role": "reference", "file": "asset-r2.png"})
    (PreproductionStore(tmp_path).path(PROJECT_ID, "assets") / "asset-r2.png").write_bytes(b"asset")
    state["revision"] += 1
    PreproductionStore(tmp_path).save(PROJECT_ID, state)
    run_store = DocumentRunStore(tmp_path)
    run_store.save(PROJECT_ID, [{
        "id": "r1", "nodeId": "source", "status": "completed", "outputAssetIds": ["asset-image"],
        "outputItems": [{"resultId": "r1-result", "assetId": "asset-image", "mediaType": "image", "status": "installed"}], "snapshot": {},
    }])
    saved = client.put(BASE, json=document(nodes=[
        {"id": "source", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "source"},
        {"id": "target", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "target"},
    ], edges=[{"id": "edge", "source": "source", "target": "target", "sourceHandle": "image", "targetHandle": "image"}])).json()
    created = client.post(BASE + "/batches", json={"revision": saved["document"]["revision"], "nodeIds": ["target"]})
    assert created.status_code == 200, created.text
    batch = created.json()["batches"][0]
    assert batch["externalOutputRefs"] == [{"edgeId": "edge", "targetNodeId": "target", "sourceNodeId": "source", "runId": "r1", "resultId": "r1-result", "assetId": "asset-image"}]

    runs = run_store.load(PROJECT_ID)
    runs.append({"id": "r2", "nodeId": "source", "status": "completed", "outputAssetIds": ["asset-r2"],
                 "outputItems": [{"resultId": "r2-result", "assetId": "asset-r2", "mediaType": "image", "status": "installed"}], "snapshot": {}})
    run_store.save(PROJECT_ID, runs)
    queue.run()
    target = next(run for run in client.get(BASE).json()["runs"] if run["nodeId"] == "target")
    reference = target["snapshot"]["references"][0]
    assert reference["sourceRunId"] == "r1"
    assert reference["sourceResultId"] == "r1-result"
    assert reference["assetId"] == "asset-image"
    rejected = client.request("DELETE", BASE + "/runs/r1/outputs/r1-result", json={"revision": saved["document"]["revision"]})
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "creative_canvas_result_batch_active"
    assert "creative_canvas_batch_external_output" in {item["kind"] for item in creative_canvas_asset_references(tmp_path, PROJECT_ID, "asset-image")}


def test_canvas_batch_dispatches_explicitly_selected_cancelled_late_external_result(tmp_path):
    service = ImageService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=service)
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "source-run", "nodeId": "source", "status": "cancelled", "outputAssetIds": ["asset-image"],
        "outputItems": [{"resultId": "late", "assetId": "asset-image", "mediaType": "image", "status": "installed", "cancellationRequested": True}], "snapshot": {},
    }])
    saved = client.put(BASE, json=document(nodes=[
        {"id": "source", "kind": "generate", "position": {"x": 0, "y": 0}, "text": "source"},
        {"id": "target", "kind": "generate", "position": {"x": 300, "y": 0}, "text": "target"},
    ], edges=[{"id": "edge", "source": "source", "target": "target", "sourceHandle": "image", "targetHandle": "image"}])).json()
    selected = client.post(BASE + "/runs/source-run/outputs/late/select", json={"revision": saved["document"]["revision"]}).json()
    created = client.post(BASE + "/batches", json={"revision": selected["document"]["revision"], "nodeIds": ["target"]})
    assert created.status_code == 200, created.text
    queue.run(); queue.run()
    target = next(run for run in client.get(BASE).json()["runs"] if run["nodeId"] == "target")
    assert target["snapshot"]["references"][0]["sourceResultId"] == "late"
    assert service.snapshots


def test_canvas_text_run_persists_version_snapshot_and_does_not_overwrite_handwritten_text(tmp_path):
    class TextService:
        def generate(self, snapshot): return iter(["第一句", "第二句"])
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    saved = client.put(BASE, json=document(nodes=[{
        "id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写保留",
        "prompt": "写一段产品文案", "providerId": "test", "modelId": "text-1", "configurationVersion": "v1",
    }])).json()
    started = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"], "idempotencyKey": "text-1"})
    assert started.status_code == 200, started.text
    run = started.json()["textRuns"][0]
    assert run["snapshot"]["prompt"] == "写一段产品文案"
    assert run["versions"][0]["seq"] == 0
    assert started.json()["document"]["nodes"][0]["text"] == "手写保留"
    queue.run()
    finished = client.get(BASE).json()["textRuns"][0]
    assert finished["status"] == "completed"
    assert finished["versions"][0]["status"] == "installed"
    assert finished["versions"][0]["text"] == "第一句第二句"
    assert finished["versions"][0]["seq"] == 2


def test_canvas_text_run_freezes_ordered_text_and_image_references(tmp_path):
    class TextService:
        def generate(self, snapshot): return iter(["完成"])

    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    saved = client.put(BASE, json=document(nodes=[
        {"id": "source-text", "kind": "text", "position": {"x": 0, "y": 0}, "text": "冻结的手写正文"},
        {"id": "source-image", "kind": "media", "position": {"x": 0, "y": 120}, "assetId": "asset-image"},
        {"id": "target", "kind": "text", "position": {"x": 320, "y": 0}, "text": "旧正文",
         "prompt": "结合引用改写", "providerId": "test", "modelId": "text-1", "configurationVersion": "v1",
         "referenceOrder": ["edge-image", "edge-text"]},
    ], edges=[
        {"id": "edge-text", "source": "source-text", "target": "target", "sourceHandle": "text", "targetHandle": "in"},
        {"id": "edge-image", "source": "source-image", "target": "target", "sourceHandle": "image", "targetHandle": "in"},
    ])).json()

    response = client.post(BASE + "/nodes/target/text-runs", json={"revision": saved["document"]["revision"]})
    assert response.status_code == 200, response.text
    run = response.json()["textRuns"][0]
    assert [item["edgeId"] for item in run["snapshot"]["references"]] == ["edge-image", "edge-text"]
    assert run["snapshot"]["references"][0] == {
        "edgeId": "edge-image", "sourceNodeId": "source-image", "sourceHandle": "image",
        "order": 1, "dataType": "image", "assetId": "asset-image",
    }
    assert run["snapshot"]["references"][1]["text"] == "冻结的手写正文"
    assert run["snapshot"]["references"][1]["textVersionId"] is None
    assert run["versions"][0]["versionId"].startswith("text-version-")


def test_canvas_text_run_keeps_video_edge_but_rejects_unverified_transport(tmp_path):
    class TextService:
        def generate(self, snapshot): return iter(["不会执行"])

    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    saved = client.put(BASE, json=document(nodes=[
        {"id": "source-video", "kind": "media", "position": {"x": 0, "y": 0}, "assetId": "asset-video"},
        {"id": "target", "kind": "text", "position": {"x": 320, "y": 0}, "text": "旧正文",
         "prompt": "结合视频改写", "providerId": "test", "modelId": "text-1", "configurationVersion": "v1"},
    ], edges=[
        {"id": "edge-video", "source": "source-video", "target": "target", "sourceHandle": "video", "targetHandle": "in"},
    ])).json()
    response = client.post(BASE + "/nodes/target/text-runs", json={"revision": saved["document"]["revision"]})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "creative_canvas_text_media_unsupported"


def test_canvas_text_run_freezes_selected_generate_result(tmp_path):
    class TextService:
        def generate(self, snapshot): return iter(["完成"])

    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    DocumentRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "image-run", "canvasId": "default", "nodeId": "source", "status": "completed", "createdAt": "now",
        "outputAssetIds": ["asset-image"], "inputAssetIds": [], "snapshot": {},
        "outputItems": [{"resultId": "image-result", "assetId": "asset-image", "mediaType": "image", "status": "installed", "sourceRunId": "image-run", "createdAt": "now"}],
    }])
    saved = client.put(BASE, json=document(nodes=[
        {"id": "source", "kind": "generate", "position": {"x": 0, "y": 0}, "outputAssetId": "asset-image", "currentOutputRef": {"runId": "image-run", "resultId": "image-result"}},
        {"id": "target", "kind": "text", "position": {"x": 320, "y": 0}, "text": "旧正文",
         "prompt": "描述图片", "providerId": "test", "modelId": "text-1", "configurationVersion": "v1"},
    ], edges=[
        {"id": "edge-image", "source": "source", "target": "target", "sourceHandle": "image", "targetHandle": "in"},
    ])).json()
    response = client.post(BASE + "/nodes/target/text-runs", json={"revision": saved["document"]["revision"]})
    assert response.status_code == 200, response.text
    reference = response.json()["textRuns"][0]["snapshot"]["references"][0]
    assert (reference["assetId"], reference["sourceRunId"], reference["sourceResultId"]) == ("asset-image", "image-run", "image-result")


def test_canvas_text_run_keeps_independent_candidates_and_partial_success(tmp_path):
    class TextService:
        def generate(self, snapshot):
            if snapshot["candidateIndex"] == 1:
                raise RuntimeError("provider failed")
            return iter([f"候选{snapshot['candidateIndex']}"])

    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    saved = client.put(BASE, json=document(nodes=[{
        "id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写",
        "prompt": "写文案", "providerId": "test", "modelId": "text-1", "configurationVersion": "v1",
    }])).json()
    started = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"], "count": 3})
    assert started.status_code == 200, started.text
    versions = started.json()["textRuns"][0]["versions"]
    assert len({item["versionId"] for item in versions}) == 3
    queue.run(); queue.run(); queue.run()
    finished = client.get(BASE).json()["textRuns"][0]
    assert [item["status"] for item in finished["versions"]] == ["installed", "failed", "installed"]
    assert finished["status"] == "completed"


def test_canvas_text_run_queued_cancel_prevents_provider_and_is_idempotent(tmp_path):
    class TextService:
        def __init__(self): self.calls = 0
        def generate(self, snapshot): self.calls += 1; return iter(["不应生成"])

    service = TextService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=service)
    saved = client.put(BASE, json=document(nodes=[{
        "id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写",
        "prompt": "写文案", "providerId": "test", "modelId": "text-1", "configurationVersion": "v1",
    }])).json()
    started = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"], "count": 2})
    run_id = started.json()["textRuns"][0]["id"]
    first = client.post(BASE + f"/text-runs/{run_id}/cancel")
    second = client.post(BASE + f"/text-runs/{run_id}/cancel")
    assert first.status_code == second.status_code == 200
    queue.run(); queue.run()
    run = client.get(BASE).json()["textRuns"][0]
    assert run["status"] == "cancelled"
    assert all(item["status"] == "cancelled" for item in run["versions"])
    assert service.calls == 0


def test_canvas_text_version_installs_real_text_asset_idempotently(tmp_path):
    class TextService:
        def generate(self, snapshot): return iter(["可下载正文"])

    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    saved = client.put(BASE, json=document(nodes=[{
        "id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写",
        "prompt": "写文案", "providerId": "test", "modelId": "text-1", "configurationVersion": "v1",
    }])).json()
    started = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"]}).json()
    run_id = started["textRuns"][0]["id"]; version_id = started["textRuns"][0]["versions"][0]["versionId"]
    queue.run()
    first = client.post(BASE + f"/text-runs/{run_id}/versions/{version_id}/assets")
    second = client.post(BASE + f"/text-runs/{run_id}/versions/{version_id}/assets")
    assert first.status_code == second.status_code == 200
    version = second.json()["textRuns"][0]["versions"][0]
    asset = next(item for item in second.json()["assets"] if item["id"] == version["assetId"])
    assert (asset["kind"], asset["mimeType"], asset["bytes"]) == ("text", "text/plain", len("可下载正文".encode()))
    download = client.get(f"/api/projects/{PROJECT_ID}/preproduction/assets/{asset['id']}/file")
    assert download.status_code == 200
    assert download.content.decode() == "可下载正文"
    assert len([item for item in second.json()["assets"] if item["kind"] == "text"]) == 1


def test_canvas_text_model_validation_and_idempotency_are_server_enforced(tmp_path):
    class TextService:
        def validate(self, snapshot):
            if snapshot["configurationVersion"] != "valid": raise ValueError("stale")
        def generate(self, snapshot): return iter(["正文"])

    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    saved = client.put(BASE, json=document(nodes=[{
        "id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写", "prompt": "写文案",
        "providerId": "test", "modelId": "text-1", "configurationVersion": "stale",
    }])).json()
    rejected = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"], "idempotencyKey": "same"})
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "text_generation_model_unavailable"

    document_value = saved["document"]
    document_value["nodes"][0]["configurationVersion"] = "valid"
    accepted_document = client.put(BASE, json=document_value).json()
    first = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": accepted_document["document"]["revision"], "idempotencyKey": "same"})
    changed = accepted_document["document"]
    changed["nodes"][0]["prompt"] = "后续编辑"
    assert client.put(BASE, json=changed).status_code == 200
    second = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": accepted_document["document"]["revision"], "idempotencyKey": "same"})
    assert first.status_code == second.status_code == 200
    assert len(second.json()["textRuns"]) == 1


def test_canvas_text_version_selection_uses_revision_and_updates_only_projection(tmp_path):
    class TextService:
        def generate(self, snapshot): return iter(["生成正文"])

    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    saved = client.put(BASE, json=document(nodes=[
        {"id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写正文", "prompt": "写文案",
         "providerId": "test", "modelId": "text-1", "configurationVersion": "v1"},
        {"id": "generate-1", "kind": "generate", "position": {"x": 320, "y": 0}},
        {"id": "text-middle", "kind": "text", "position": {"x": 160, "y": 100}, "text": "中间正文"},
        {"id": "generate-indirect", "kind": "generate", "position": {"x": 640, "y": 100}},
    ], edges=[
        {"id": "edge-downstream", "source": "text-1", "target": "generate-1", "sourceHandle": "text", "targetHandle": "prompt"},
        {"id": "edge-middle", "source": "text-1", "target": "text-middle", "sourceHandle": "text", "targetHandle": "in"},
        {"id": "edge-indirect", "source": "text-middle", "target": "generate-indirect", "sourceHandle": "text", "targetHandle": "prompt"},
    ])).json()
    started = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"]}).json()
    run_id = started["textRuns"][0]["id"]; version_id = started["textRuns"][0]["versions"][0]["versionId"]
    queue.run()
    selected = client.post(BASE + f"/text-runs/{run_id}/versions/{version_id}/select", json={"revision": saved["document"]["revision"]})
    assert selected.status_code == 200, selected.text
    node = selected.json()["document"]["nodes"][0]
    assert (node["text"], node["currentTextVersionId"]) == ("生成正文", version_id)
    assert selected.json()["document"]["nodes"][1]["stale"] is True
    assert selected.json()["document"]["nodes"][3].get("stale") is True
    assert selected.json()["textRuns"][0]["versions"][0]["text"] == "生成正文"
    stale = client.post(BASE + f"/text-runs/{run_id}/versions/{version_id}/select", json={"revision": saved["document"]["revision"]})
    assert stale.status_code == 409


def test_canvas_text_running_cancel_keeps_late_text_without_auto_adoption(tmp_path):
    class TextService:
        client = None
        run_id = None

        def generate(self, snapshot):
            service = self

            class Stream:
                def __init__(self):
                    self.next_calls = 0
                    self.closed = False

                def __iter__(self):
                    return self

                def __next__(self):
                    self.next_calls += 1
                    if self.next_calls == 1:
                        return "前半段"
                    if self.next_calls == 2:
                        response = service.client.post(BASE + f"/text-runs/{service.run_id}/cancel")
                        assert response.status_code == 200
                        return "已到达段"
                    raise AssertionError("取消后不得继续消费文本流")

                def close(self):
                    self.closed = True

            self.stream = Stream()
            return self.stream

    service = TextService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=service)
    service.client = client
    saved = client.put(BASE, json=document(nodes=[{
        "id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写正文", "prompt": "写文案",
        "providerId": "test", "modelId": "text-1", "configurationVersion": "v1",
    }])).json()
    started = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"]}).json()
    service.run_id = started["textRuns"][0]["id"]
    queue.run()
    workspace = client.get(BASE).json(); run = workspace["textRuns"][0]; version = run["versions"][0]
    assert (run["status"], version["status"], version["cancellationRequested"], version["text"]) == ("unknown", "installed", True, "前半段已到达段")
    assert (service.stream.next_calls, service.stream.closed) == (2, True)
    assert (run["billingUnknown"], version["billingUnknown"]) == (True, True)
    assert workspace["document"]["nodes"][0]["text"] == "手写正文"


def test_canvas_text_cancel_close_error_keeps_billing_unknown(tmp_path):
    class TextService:
        client = None
        run_id = None

        def generate(self, snapshot, cancel_event=None):
            service = self

            class Stream:
                def __init__(self):
                    self.calls = 0
                    self.closed = False

                def __iter__(self):
                    return self

                def __next__(self):
                    self.calls += 1
                    if self.calls == 1:
                        return "前半段"
                    if self.calls == 2:
                        response = service.client.post(BASE + f"/text-runs/{service.run_id}/cancel")
                        assert response.status_code == 200
                        raise RuntimeError("local stream closed")
                    raise AssertionError("取消后不得继续消费流")

                def close(self):
                    self.closed = True

            self.stream = Stream()
            return self.stream

    service = TextService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=service)
    service.client = client
    saved = client.put(BASE, json=document(nodes=[{
        "id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写正文", "prompt": "写文案",
        "providerId": "test", "modelId": "text-1", "configurationVersion": "v1",
    }])).json()
    started = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"]}).json()
    service.run_id = started["textRuns"][0]["id"]
    queue.run()
    run = client.get(BASE).json()["textRuns"][0]
    version = run["versions"][0]
    assert (run["status"], version["status"], version["billingUnknown"]) == ("unknown", "unknown", True)
    assert run["billingUnknown"] is True
    assert service.stream.closed is True


def test_canvas_text_run_accepts_exactly_12000_characters(tmp_path):
    class TextService:
        def generate(self, snapshot):
            return iter(["甲" * 12_000])

    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    saved = client.put(BASE, json=document(nodes=[{
        "id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写", "prompt": "写文案",
        "providerId": "test", "modelId": "text-1", "configurationVersion": "v1",
    }])).json()
    started = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"]}).json()
    queue.run()
    run = client.get(BASE).json()["textRuns"][0]
    assert (run["status"], run["versions"][0]["status"], len(run["versions"][0]["text"])) == ("completed", "installed", 12_000)


def test_canvas_text_run_fails_at_limit_keeps_partial_text_and_closes_stream(tmp_path):
    class TextService:
        stream = None

        def generate(self, snapshot):
            class Stream:
                def __init__(self):
                    self.values = iter(["甲" * 11_999, "乙乙"])
                    self.closed = False

                def __iter__(self):
                    return self

                def __next__(self):
                    return next(self.values)

                def close(self):
                    self.closed = True

            self.stream = Stream()
            return self.stream

    service = TextService()
    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=service)
    saved = client.put(BASE, json=document(nodes=[{
        "id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写", "prompt": "写文案",
        "providerId": "test", "modelId": "text-1", "configurationVersion": "v1",
    }])).json()
    started = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"]}).json()
    run_id = started["textRuns"][0]["id"]
    version_id = started["textRuns"][0]["versions"][0]["versionId"]
    queue.run()
    run = client.get(BASE).json()["textRuns"][0]
    version = run["versions"][0]
    assert (run["status"], version["status"], version["error"], len(version["text"])) == ("failed", "failed", "text_output_too_large", 12_000)
    assert (run["billingUnknown"], version["billingUnknown"]) == (True, True)
    assert service.stream.closed is True
    assert client.post(BASE + f"/text-runs/{run_id}/versions/{version_id}/select", json={"revision": saved["document"]["revision"]}).status_code == 409
    installed = client.post(BASE + f"/text-runs/{run_id}/versions/{version_id}/assets")
    assert installed.status_code == 200, installed.text
    asset_id = installed.json()["textRuns"][0]["versions"][0]["assetId"]
    assert client.get(f"/api/projects/{PROJECT_ID}/preproduction/assets/{asset_id}/file").content.decode() == "甲" * 11_999 + "乙"
    retry = client.post(BASE + "/nodes/text-1/text-runs", json={
        "revision": saved["document"]["revision"], "idempotencyKey": "new-run-after-local-limit",
    })
    assert retry.status_code == 200, retry.text
    assert len(retry.json()["textRuns"]) == 2


def test_canvas_text_running_run_becomes_unknown_after_restart(tmp_path):
    client, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=object())
    DocumentTextRunStore(tmp_path).save(PROJECT_ID, [{
        "id": "text-run-restart", "canvasId": "default", "nodeId": "text-1", "status": "running", "createdAt": "now",
        "snapshot": {"prompt": "p", "providerId": "test", "modelId": "text-1", "configurationVersion": "v1", "references": [], "count": 1},
        "versions": [{"versionId": "text-version-restart", "runId": "text-run-restart", "status": "running", "text": "部分", "seq": 1, "createdAt": "now"}],
    }])
    restarted, _ = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=object())
    run = restarted.get(BASE).json()["textRuns"][0]
    assert run["status"] == "unknown"
    assert run["versions"][0]["status"] == "unknown"
    assert run["versions"][0]["text"] == "部分"
    assert run["billingUnknown"] is True


def test_canvas_text_partial_success_with_unknown_keeps_unknown_fence(tmp_path):
    class UnknownTextError(Exception):
        code = "provider_outcome_unknown"
        outcome_unknown = True

    class TextService:
        def generate(self, snapshot):
            if snapshot["candidateIndex"] == 1: raise UnknownTextError()
            return iter(["成功候选"])

    client, queue = setup(tmp_path, configurations=Configurations(), image_service=ImageService(), text_service=TextService())
    saved = client.put(BASE, json=document(nodes=[{
        "id": "text-1", "kind": "text", "position": {"x": 0, "y": 0}, "text": "手写", "prompt": "写文案",
        "providerId": "test", "modelId": "text-1", "configurationVersion": "v1",
    }])).json()
    started = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"], "count": 2})
    assert started.status_code == 200
    queue.run(); queue.run()
    run = client.get(BASE).json()["textRuns"][0]
    assert [item["status"] for item in run["versions"]] == ["installed", "unknown"]
    assert (run["status"], run["billingUnknown"]) == ("unknown", True)
    blocked = client.post(BASE + "/nodes/text-1/text-runs", json={"revision": saved["document"]["revision"]})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "creative_canvas_text_run_active"
