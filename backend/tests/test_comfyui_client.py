import json

import httpx
import pytest


def _client(handler):
    from app.comfyui_client import ComfyUIClient

    return ComfyUIClient(
        "http://127.0.0.1:8188", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _workflow():
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
    }


def test_base_url_accepts_only_private_or_loopback_http_addresses():
    from app.comfyui_client import ComfyUIClient

    for address in ("http://localhost:8188", "https://192.168.1.5:8188", "http://[::1]:8188"):
        ComfyUIClient(address)
    for address in (
        "http://example.com:8188", "http://127.0.0.1:8188/?redirect=x",
        "http://user@127.0.0.1:8188", "ftp://127.0.0.1:8188",
    ):
        with pytest.raises(ValueError):
            ComfyUIClient(address)


def test_check_reports_connection_version_nodes_and_models_separately():
    from app.comfyui_client import ComfyUIClient

    def handler(request):
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.3.45"}})
        assert request.url.path == "/object_info"
        return httpx.Response(200, json={
            "LoadImage": {"input": {"required": {}}},
            "UNETLoader": {"input": {"required": {"unet_name": [["model.safetensors"]]}}},
        })

    report = _client(handler).check(_workflow())

    assert report == {
        "connected": True,
        "version": "0.3.45",
        "missingNodes": [],
        "missingModels": [],
        "ready": True,
        "message": "本地 ComfyUI 环境完整。",
    }


def test_check_keeps_missing_nodes_and_models_observable():
    def handler(request):
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.3.45"}})
        return httpx.Response(200, json={"LoadImage": {"input": {"required": {}}}})

    report = _client(handler).check(_workflow())

    assert report["connected"] is True
    assert report["missingNodes"] == ["UNETLoader"]
    assert report["missingModels"] == ["model.safetensors"]
    assert report["ready"] is False


def test_submit_returns_comfy_prompt_id_and_rejects_redirects():
    from app.comfyui_client import ComfyUIClientError

    def accepted(request):
        assert request.url.path == "/prompt"
        assert json.loads(request.content) == {"prompt": _workflow()}
        return httpx.Response(200, json={"prompt_id": "prompt-123", "number": 1})

    assert _client(accepted).submit(_workflow()) == "prompt-123"

    with pytest.raises(ComfyUIClientError):
        _client(lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1/"})).submit(_workflow())


def test_submit_marks_network_or_server_failures_unknown_but_not_explicit_rejection():
    from app.comfyui_client import ComfyUIClientError

    with pytest.raises(ComfyUIClientError) as rejected:
        _client(lambda request: httpx.Response(400, json={"error": "sensitive upstream detail"})).submit(_workflow())
    assert rejected.value.outcome_unknown is False
    assert "sensitive" not in str(rejected.value)

    with pytest.raises(ComfyUIClientError) as uncertain:
        _client(lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline"))).submit(_workflow())
    assert uncertain.value.outcome_unknown is True


def test_poll_reports_queue_running_completed_and_safe_outputs():
    def queued(request):
        if request.url.path == "/history/prompt-123":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"queue_pending": [[1, "prompt-123"]], "queue_running": []})

    assert _client(queued).poll("prompt-123") == {
        "status": "queued", "outputs": [], "error": None,
    }

    def completed(request):
        if request.url.path == "/history/prompt-123":
            return httpx.Response(200, json={"prompt-123": {
                "status": {"completed": True, "status_str": "success", "messages": []},
                "outputs": {"15": {"gifs": [{"filename": "result.mp4", "subfolder": "video/Wan", "type": "output"}]}},
            }})
        return httpx.Response(200, json={"queue_pending": [], "queue_running": []})

    assert _client(completed).poll("prompt-123") == {
        "status": "completed",
        "outputs": [{"filename": "result.mp4", "subfolder": "video/Wan", "type": "output"}],
        "error": None,
    }


def test_poll_returns_unknown_when_a_known_prompt_is_absent_from_history_and_queue():
    client = _client(lambda request: httpx.Response(200, json={}))

    assert client.poll("prompt-123") == {
        "status": "unknown", "outputs": [], "error": "ComfyUI 尚未报告此执行状态。",
    }


def test_poll_does_not_reflect_raw_comfy_execution_error():
    def handler(request):
        if request.url.path == "/history/prompt-123":
            return httpx.Response(200, json={"prompt-123": {
                "status": {"completed": True, "status_str": "error", "messages": [
                    ["execution_error", {"exception_message": "secret path /private/key"}],
                ]},
                "outputs": {},
            }})
        return httpx.Response(200, json={"queue_pending": [], "queue_running": []})

    result = _client(handler).poll("prompt-123")

    assert result == {"status": "failed", "outputs": [], "error": "ComfyUI 工作流执行失败。"}


def test_fetch_output_and_upload_reject_traversal_and_keep_comfy_names_safe(tmp_path):
    from app.comfyui_client import ComfyUIClientError

    source = tmp_path / "depth.mp4"
    source.write_bytes(b"control video")

    def handler(request):
        if request.url.path == "/upload/image":
            return httpx.Response(200, json={"name": "depth.mp4", "subfolder": "controls", "type": "input"})
        assert request.url.path == "/view"
        assert dict(request.url.params) == {"filename": "result.mp4", "subfolder": "video/Wan", "type": "output"}
        return httpx.Response(200, content=b"video")

    client = _client(handler)
    assert client.upload_image(source, "depth.mp4") == "controls/depth.mp4"
    assert client.fetch_output({"filename": "result.mp4", "subfolder": "video/Wan", "type": "output"}) == b"video"
    with pytest.raises(ComfyUIClientError):
        client.fetch_output({"filename": "../secret", "subfolder": "", "type": "output"})
    with pytest.raises(ComfyUIClientError):
        client.fetch_output({"filename": "result.mp4", "subfolder": "video//Wan", "type": "output"})


def test_fetch_output_rejects_oversized_content_before_buffering_it():
    from app.comfyui_client import ComfyUIClientError

    client = _client(lambda request: httpx.Response(200, headers={"content-length": "536870913"}))

    with pytest.raises(ComfyUIClientError, match="超过大小限制"):
        client.fetch_output({"filename": "result.mp4", "subfolder": "", "type": "output"})


def test_fetch_output_allows_a_video_larger_than_the_json_response_limit():
    payload = b"v" * 2_000_001
    client = _client(lambda request: httpx.Response(200, content=payload))

    assert client.fetch_output({"filename": "result.mp4", "subfolder": "", "type": "output"}) == payload
