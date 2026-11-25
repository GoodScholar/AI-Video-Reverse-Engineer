from pathlib import Path

import httpx
from PIL import Image

from app.project_assets import ProjectAssets


def _install_frame(root: Path):
    source = root / "frame.png"
    Image.new("RGB", (16, 16), "navy").save(source)
    return ProjectAssets(root).install_run_result(
        "project", source=source, run_id="source-run", common_run_id="source-common",
        result_id="frame-result", domain_kind="test", domain_run_id="source-run",
        kind="image", name="frame.png", input_snapshot={"source": "test"}, media={"width": 16, "height": 16},
    )


def test_unconfigured_comfy_adapter_is_optional_and_not_a_video_catalog_entry(tmp_path):
    from app.comfyui_adapter import ComfyUIAdapter, ComfyUIConfigurationStore

    settings = ComfyUIConfigurationStore(tmp_path)
    adapter = ComfyUIAdapter(tmp_path, settings)

    assert settings.status()["state"] == "unconfigured"
    assert adapter.catalog() == []
    assert adapter.capability_catalog("comfyui", "wan22-i2v") is None


def test_application_exposes_unconfigured_comfyui_as_optional_without_registering_a_video_model(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(data_dir=tmp_path)) as client:
        status = client.get("/api/comfyui-adapter/status")
        assert status.status_code == 200
        assert status.json()["state"] == "unconfigured"
        project = client.post("/api/projects", json={"name": "可选 ComfyUI"}).json()
        assert client.get(f"/api/projects/{project['id']}/video-generation/models").json() == {"models": []}


def test_verified_comfy_adapter_maps_only_the_fixed_workflow_input_and_uses_history_results(tmp_path):
    from app.comfyui_adapter import ComfyUIAdapter, ComfyUIConfigurationStore

    frame = _install_frame(tmp_path)
    settings = ComfyUIConfigurationStore(tmp_path)
    settings.save("http://127.0.0.1:8188")
    settings.record_verified_workflow({
        "workflowId": "wan22_i2v", "workflowVersion": "aaac56dd5cc5497533d92cbe50edc35ea660e587",
        "configurationVersion": "1", "comfyVersion": "test", "promptId": "prompt-verified",
        "historyStatus": "success", "resultAssetId": "asset-verified", "verifiedAt": "2026-10-02T00:00:00+00:00",
    })
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/upload/image":
            return httpx.Response(200, json={"name": "frame.png", "subfolder": "", "type": "input"})
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        if request.url.path == "/history/prompt-1":
            return httpx.Response(200, json={"prompt-1": {"status": {"completed": True, "status_str": "success"}, "outputs": {"9": {"gifs": [{"filename": "result.mp4", "subfolder": "", "type": "output"}]}}}})
        raise AssertionError(request.url.path)

    adapter = ComfyUIAdapter(tmp_path, settings, client_factory=lambda _: __import__("app.comfyui_client", fromlist=["ComfyUIClient"]).ComfyUIClient("http://127.0.0.1:8188", client=httpx.Client(transport=httpx.MockTransport(handler))))
    run = {"_projectId": "project", "configurationVersion": "1", "input": {"prompt": "商品旋转", "videoParams": {"profileId": "comfyui-wan22-i2v", "mode": "image-to-video", "references": [{"assetId": frame["id"], "assetVersionId": frame["assetVersionId"], "referenceType": "first-frame"}], "durationSeconds": 5, "aspectRatio": "16:9", "resolution": "1280x720", "audio": False, "resultCount": 1}}}

    submitted = adapter.submit(run)
    assert submitted["remoteTaskId"] == "prompt-1"
    event = adapter.poll({**run, "remoteTaskId": "prompt-1"})
    assert event["status"] == "succeeded"
    assert event["results"][0]["name"] == "result.mp4"
    assert calls == ["/upload/image", "/prompt", "/history/prompt-1"]
