import asyncio
import base64
import json
import time
from pathlib import Path
from threading import Event, Timer

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.preproduction import PreproductionStore


class InMemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, account):
        return self.values.get(account)

    def set(self, account, value):
        self.values[account] = value

    def delete(self, account):
        self.values.pop(account, None)


def configurations(tmp_path, secrets):
    from app.text_generation import TextGenerationConfigurations, TextGenerationSettings

    return TextGenerationConfigurations(
        TextGenerationSettings(tmp_path / "text-generation.json"),
        secret_store=lambda: secrets,
    )


def sse(*parts, finish_reason="stop"):
    events = [f"data: {json.dumps({'choices': [{'delta': {'content': part}}]})}\n\n".encode() for part in parts]
    events.append(f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': finish_reason}]})}\n\n".encode())
    return b"".join(events + [b"data: [DONE]\n\n"])


def test_text_configuration_is_secret_free_and_requires_verified_text_before_catalog_is_configured(tmp_path):
    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(
        base_url="https://text.example.test/v1",
        models=[{"modelId": "model-a", "label": "Model A"}],
        api_key="TOPSECRET",
    )

    assert saved["credentialState"] == "configured"
    assert saved["modelCatalog"] == [{
        "providerId": "default", "providerLabel": "默认文本服务", "modelId": "model-a", "label": "Model A",
        "configured": False, "configurationVersion": saved["revision"], "textVerified": False,
        "imageVerified": False, "maxImageReferences": 0,
    }]
    assert "TOPSECRET" not in json.dumps(saved)

    verified = config.record_verification(saved["revision"], "model-a", mode="text")
    assert verified["credentialState"] == "configured"
    assert verified["modelCatalog"][0]["configured"] is True

    changed = config.save(
        base_url="https://text.example.test/v1",
        models=[{"modelId": "model-a"}, {"modelId": "model-b"}],
        expected_revision=saved["revision"],
    )
    assert changed["revision"] != saved["revision"]
    assert changed["credentialState"] == "configured"
    assert all(item["textVerified"] is False for item in changed["modelCatalog"])


def test_text_configuration_api_uses_cas_and_never_echoes_key(tmp_path):
    from app.text_generation_api import create_text_generation_router

    app = FastAPI()
    app.include_router(create_text_generation_router(configurations(tmp_path, InMemorySecrets())))
    client = TestClient(app)
    first = client.put("/api/text-generation/configuration", json={
        "baseUrl": "https://text.example.test/v1", "models": [{"modelId": "model-a"}], "apiKey": "TOPSECRET",
    })
    assert first.status_code == 200, first.text
    assert "TOPSECRET" not in first.text
    conflict = client.put("/api/text-generation/configuration", json={
        "baseUrl": "https://text.example.test/v1", "models": [{"modelId": "model-b"}], "revision": "stale",
    })
    assert conflict.status_code == 409
    invalid = client.put("/api/text-generation/configuration", json={
        "baseUrl": "https://text.example.test/v1", "models": [{"modelId": "model-a"}], "apiKey": "LEAKME", "extra": True,
    })
    assert invalid.status_code == 422
    assert "LEAKME" not in invalid.text


def test_connection_route_runs_the_sync_provider_call_off_the_event_loop(tmp_path):
    from app.text_generation_api import create_text_generation_router

    started = Event()
    release = Event()

    class SlowService:
        def test_connection(self, revision, model_id, *, mode):
            started.set()
            release.wait(1)
            return {"ok": True}

    app = FastAPI()
    app.include_router(create_text_generation_router(configurations(tmp_path, InMemorySecrets()), SlowService()))

    @app.get("/health")
    async def health():
        return {"ok": True}

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/api/text-generation/test-connection", json={
                "revision": "revision-1", "modelId": "model-a", "mode": "text",
            }))
            assert await asyncio.to_thread(started.wait, 1)
            timer = Timer(0.3, release.set)
            timer.start()
            try:
                before = time.monotonic()
                health_response = await client.get("/health")
                assert time.monotonic() - before < 0.2
                assert health_response.json() == {"ok": True}
            finally:
                release.set()
                timer.cancel()
            assert (await pending).json() == {"ok": True}

    asyncio.run(exercise())


def test_connection_verifies_text_sse_then_image_order_with_two_random_references(tmp_path, monkeypatch):
    from app.text_generation import TextGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse("ok"))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse("red, blue"))

    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(service, "_probe_colors", lambda: ("red", "blue"))
    text_result = service.test_connection(saved["revision"], "model-a", mode="text")
    image_result = service.test_connection(saved["revision"], "model-a", mode="image")

    assert text_result["credentialState"] == "configured"
    assert image_result["modelCatalog"][0]["maxImageReferences"] == 2
    text_payload = json.loads(requests[0].content)
    image_payload = json.loads(requests[1].content)
    assert text_payload["stream"] is True and "response_format" not in text_payload
    content = image_payload["messages"][0]["content"]
    assert [part["type"] for part in content] == ["text", "image_url", "image_url"]
    assert "red" not in content[0]["text"].lower() and "blue" not in content[0]["text"].lower()
    assert all(part["image_url"]["url"].startswith("data:image/png;base64,") for part in content[1:])


def test_image_connection_probe_rejects_a_reply_that_ignores_images(tmp_path, monkeypatch):
    from app.text_generation import TextGenerationError, TextGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse("I cannot inspect images.")),
    )))
    monkeypatch.setattr(service, "_probe_colors", lambda: ("red", "blue"))

    with pytest.raises(TextGenerationError, match="image_probe_mismatch"):
        service.test_connection(saved["revision"], "model-a", mode="image")


def test_generation_reads_only_registered_image_assets_and_does_not_store_data_urls(tmp_path):
    from app.text_generation import TextGenerationError, TextGenerationService

    project_id = "project-001"
    store = PreproductionStore(tmp_path)
    state = store.load(project_id)
    state["assets"] = [{"id": "image-1", "name": "one.png", "kind": "image", "role": "reference", "file": "one.png"}]
    directory = store.path(project_id, "assets")
    directory.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), "red").save(directory / "one.png")
    store.save(project_id, state)

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    config.record_verification(saved["revision"], "model-a", mode="text")
    config.record_verification(saved["revision"], "model-a", mode="image", max_image_references=2)
    captured = []
    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: captured.append(request) or httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse("正文")),
    )))
    snapshot = {
        "projectId": project_id, "canvasId": "default", "runId": "run-1", "versionId": "version-1",
        "providerId": "default", "modelId": "model-a", "configurationVersion": saved["revision"],
        "prompt": "描述图片", "references": [{"dataType": "image", "assetId": "image-1", "order": 1}],
    }
    assert list(service.generate(snapshot)) == ["正文"]
    payload = json.loads(captured[0].content)
    assert payload["messages"][0]["content"][1] == {"type": "text", "text": "{{ref 1}}"}
    assert payload["messages"][0]["content"][2]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "base64" not in json.dumps(snapshot)

    snapshot["references"] = [{"dataType": "video", "assetId": "image-1", "order": 1}]
    with pytest.raises(TextGenerationError, match="unsupported_reference"):
        service.validate(snapshot)


def test_generation_marks_each_mixed_reference_in_frozen_order(tmp_path):
    from app.text_generation import TextGenerationService

    project_id = "project-002"
    store = PreproductionStore(tmp_path)
    state = store.load(project_id)
    state["assets"] = [{"id": "image-1", "name": "one.png", "kind": "image", "role": "reference", "file": "one.png"}]
    directory = store.path(project_id, "assets")
    directory.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), "red").save(directory / "one.png")
    store.save(project_id, state)
    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    config.record_verification(saved["revision"], "model-a", mode="text")
    config.record_verification(saved["revision"], "model-a", mode="image")
    captured = []
    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: captured.append(request) or httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse("正文")),
    )))
    snapshot = {
        "projectId": project_id, "canvasId": "default", "runId": "run-1", "versionId": "version-1",
        "providerId": "default", "modelId": "model-a", "configurationVersion": saved["revision"], "prompt": "结合参考",
        "references": [{"dataType": "text", "text": "第一条"}, {"dataType": "image", "assetId": "image-1"}, {"dataType": "text", "text": "第三条"}],
    }
    assert list(service.generate(snapshot)) == ["正文"]
    content = json.loads(captured[0].content)["messages"][0]["content"]
    assert content[1:5] == [
        {"type": "text", "text": "{{ref 1}}\n第一条"},
        {"type": "text", "text": "{{ref 2}}"},
        content[3],
        {"type": "text", "text": "{{ref 3}}\n第三条"},
    ]
    assert content[3]["type"] == "image_url"


def test_generation_cancel_event_closes_stream_and_does_not_report_unfinished_sse_as_success(tmp_path):
    from app.text_generation import TextGenerationError, TextGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    config.record_verification(saved["revision"], "model-a", mode="text")
    closed = []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield "data: {\"choices\":[{\"delta\":{\"content\":\"前半段\"}}]}\n\n".encode()
            yield b"data: [DONE]\n\n"

        def close(self):
            closed.append(True)

    event = Event()
    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body()),
    )))
    snapshot = {"projectId": "p", "canvasId": "c", "runId": "r", "versionId": "v", "providerId": "default", "modelId": "model-a", "configurationVersion": saved["revision"], "prompt": "p", "references": []}
    stream = service.generate(snapshot, cancel_event=event)
    assert next(stream) == "前半段"
    event.set()
    with pytest.raises(TextGenerationError, match="cancelled") as error:
        next(stream)
    assert error.value.outcome_unknown is True
    assert closed


def test_generation_maps_read_error_after_partial_text_to_unknown_and_closes_stream(tmp_path):
    from app.text_generation import TextGenerationError, TextGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    config.record_verification(saved["revision"], "model-a", mode="text")
    closed = []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield "data: {\"choices\":[{\"delta\":{\"content\":\"保留段\"}}]}\n\n".encode()
            raise httpx.ReadError("socket closed")

        def close(self):
            closed.append(True)

    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body()),
    )))
    snapshot = {"projectId": "p", "canvasId": "c", "runId": "r", "versionId": "v", "providerId": "default", "modelId": "model-a", "configurationVersion": saved["revision"], "prompt": "p", "references": []}
    stream = service.generate(snapshot)
    assert next(stream) == "保留段"
    with pytest.raises(TextGenerationError, match="stream_read_failed") as error:
        next(stream)
    assert error.value.outcome_unknown is True
    assert closed


def test_generation_limits_unterminated_response_bytes_before_line_buffering(tmp_path):
    from app.text_generation import TextGenerationError, TextGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    config.record_verification(saved["revision"], "model-a", mode="text")
    closed = []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * (8 * 1024 * 1024 + 1)

        def close(self):
            closed.append(True)

    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body()),
    )))
    snapshot = {"projectId": "p", "canvasId": "c", "runId": "r", "versionId": "v", "providerId": "default", "modelId": "model-a", "configurationVersion": saved["revision"], "prompt": "p", "references": []}
    with pytest.raises(TextGenerationError, match="response_too_large") as error:
        next(service.generate(snapshot))
    assert error.value.outcome_unknown is True
    assert closed


def test_generation_decodes_chinese_and_crlf_across_byte_chunks(tmp_path):
    from app.text_generation import TextGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    config.record_verification(saved["revision"], "model-a", mode="text")
    payload = (
        b'data: {"choices":[{"delta":{"content":"' + "中文".encode("utf-8")
        + b'"}}]}\r\n\r\n'
        + b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\r\n\r\n'
        + b"data: [DONE]\r\n\r\n"
    )
    split_at = payload.index("中".encode("utf-8")) + 1

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield payload[:split_at]
            yield payload[split_at:]

        def close(self):
            pass

    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body()),
    )))
    snapshot = {"projectId": "p", "canvasId": "c", "runId": "r", "versionId": "v", "providerId": "default", "modelId": "model-a", "configurationVersion": saved["revision"], "prompt": "p", "references": []}
    assert list(service.generate(snapshot)) == ["中文"]


def test_generation_does_not_send_when_cancelled_before_response_headers(tmp_path):
    from app.text_generation import TextGenerationError, TextGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    config.record_verification(saved["revision"], "model-a", mode="text")
    event = Event()
    event.set()

    class Client:
        def build_request(self, *args, **kwargs):
            return httpx.Request(*args, **kwargs)

        def send(self, *_args, **_kwargs):
            pytest.fail("已取消的请求不得发送")

        def close(self):
            pass

    service = TextGenerationService(config, tmp_path, client_factory=Client)
    snapshot = {"projectId": "p", "canvasId": "c", "runId": "r", "versionId": "v", "providerId": "default", "modelId": "model-a", "configurationVersion": saved["revision"], "prompt": "p", "references": []}
    with pytest.raises(TextGenerationError, match="cancelled") as error:
        service.generate(snapshot, cancel_event=event)
    assert error.value.outcome_unknown is True


def test_stream_reports_over_limit_after_yielding_the_bounded_final_delta(tmp_path):
    from app.text_generation import TextGenerationError, TextGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    config.record_verification(saved["revision"], "model-a", mode="text")
    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse("甲" * 12_001)),
    )))
    snapshot = {"projectId": "p", "canvasId": "c", "runId": "r", "versionId": "v", "providerId": "default", "modelId": "model-a", "configurationVersion": saved["revision"], "prompt": "p", "references": []}
    stream = service.generate(snapshot)
    assert len(next(stream)) == 12_000
    with pytest.raises(TextGenerationError, match="text_output_too_large") as error:
        next(stream)
    assert error.value.outcome_unknown is False
    assert error.value.billing_unknown is True


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_generation_does_not_treat_non_stop_sse_finish_as_installed(tmp_path, finish_reason):
    from app.text_generation import TextGenerationError, TextGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    config.record_verification(saved["revision"], "model-a", mode="text")
    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse("保留段", finish_reason=finish_reason)),
    )))
    snapshot = {"projectId": "p", "canvasId": "c", "runId": "r", "versionId": "v", "providerId": "default", "modelId": "model-a", "configurationVersion": saved["revision"], "prompt": "p", "references": []}
    stream = service.generate(snapshot)
    assert next(stream) == "保留段"
    with pytest.raises(TextGenerationError, match="stream_not_completed") as error:
        next(stream)
    assert error.value.outcome_unknown is False


def test_generation_requires_a_stop_finish_reason_before_done(tmp_path):
    from app.text_generation import TextGenerationError, TextGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://text.example.test/v1", models=[{"modelId": "model-a"}], api_key="key")
    config.record_verification(saved["revision"], "model-a", mode="text")
    service = TextGenerationService(config, tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse("保留段", finish_reason=None)),
    )))
    snapshot = {"projectId": "p", "canvasId": "c", "runId": "r", "versionId": "v", "providerId": "default", "modelId": "model-a", "configurationVersion": saved["revision"], "prompt": "p", "references": []}
    stream = service.generate(snapshot)
    assert next(stream) == "保留段"
    with pytest.raises(TextGenerationError, match="stream_not_completed") as error:
        next(stream)
    assert error.value.outcome_unknown is True


def test_main_registers_text_configuration_router(tmp_path):
    from app.main import create_app

    response = TestClient(create_app(tmp_path)).get("/api/text-generation/configuration")
    assert response.status_code == 200
    assert response.json()["modelCatalog"] == []
