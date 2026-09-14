import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

import app.analysis_providers.http_transport as provider_http
from app.analysis_input import ImageAnalysisInput
from app.analysis_prompt import build_analysis_prompt
from app.analysis_provider import BailianProviderConfig, ProviderRequest as LegacyProviderRequest
from app.analysis_providers import ProviderAnalysisError as LegacyProviderAnalysisError
from app.analysis_providers.base import ProviderAnalysisError, ProviderRequest
from app.analysis_providers.bailian import BailianAnalysisProvider
from app.analysis_providers.test_image import connection_test_image


def _valid_analysis():
    return {
        "observedFacts": {
            "staticVisual": {
                "subject": "人物", "scene": "室内", "composition": "居中", "viewpoint": "平视",
                "lighting": "柔光", "color": "暖色", "visualStyle": "纪实",
            },
            "temporal": None,
        },
        "generationSuggestions": {
            "subjectMotion": "缓慢移动", "environmentalMotion": "轻微变化", "cameraMotion": "稳定",
            "rhythm": "平缓", "suggestedDuration": 5.0, "audio": "环境音建议",
        },
    }


def _core_request():
    return ProviderRequest(
        analysisInput=ImageAnalysisInput(
            analysisProxyBytes=b"\xff\xd8\xff\xe0proxy\xff\xd9",
            width=16,
            height=16,
            aspectRatio=1.0,
        ),
        prompt=build_analysis_prompt("image"),
        model="qwen3.7-flash",
    )


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"error": "denied"}), "authentication_failed"),
        (httpx.Response(403, json={"error": "denied"}), "authentication_failed"),
        (httpx.Response(429, json={"error": "limited"}), "rate_limited"),
        (httpx.Response(500, json={"error": "unavailable"}), "provider_error"),
        (httpx.Response(200, content=b"not-json"), "invalid_analysis_response"),
    ],
)
def test_bailian_core_adapter_maps_http_and_parse_failures_without_sensitive_detail(response, code):
    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: response)),
        credential="secret-value",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_core_request())

    assert error.value.failure.code == code
    assert "secret-value" not in str(error.value)


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [(200, "invalid_analysis_response"), (401, "authentication_failed")],
)
def test_bailian_maps_deeply_nested_json_by_http_status(status_code, expected_code):
    deeply_nested_json = b"[" * 1_200 + b"0" + b"]" * 1_200
    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, content=deeply_nested_json),
        )),
        credential="test-credential",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_core_request())

    assert error.value.failure.code == expected_code


def test_bailian_rejects_invalid_utf8_even_when_replacement_would_form_json():
    response_bytes = b'{"choices":[{"message":{"content":"{}"}}],"diagnostic":"\xff"}'
    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=response_bytes),
        )),
        credential="test-credential",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_core_request())

    assert error.value.failure.code == "invalid_analysis_response"


def test_connection_probe_is_a_code_generated_fixed_rgb_png():
    probe = connection_test_image()

    assert probe.width == probe.height == 16
    assert probe.analysisProxyBytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert probe.aspectRatio == 1.0


def test_bailian_connection_test_sends_the_built_in_probe_not_user_input():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(_valid_analysis(), ensure_ascii=False),
        }}]})

    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        credential="test-credential",
    )

    result = provider.test_connection()

    body = captured[0].content.decode("utf-8")
    assert result.observedFacts.temporal is None
    assert "data:image/png;base64," in body
    assert "analysis-proxy" not in body


def test_bailian_connection_test_rejects_invalid_text_after_one_safe_repair_attempt():
    captured = []
    original_text = "这不是 JSON：Bearer test-credential data:image/png;base64,NOT_A_REAL_IMAGE"

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": original_text}}]})

    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-credential",
    )
    observed_requests = []
    original_analyze = provider.analyze

    def observe_request(request, *args, **kwargs):
        observed_requests.append(request)
        return original_analyze(request, *args, **kwargs)

    provider.analyze = observe_request

    with pytest.raises(ProviderAnalysisError) as error:
        provider.test_connection()

    assert error.value.failure.code == "invalid_analysis_response"
    assert [request.isRepair for request in observed_requests] == [False, True]
    assert len(captured) == 2
    repair = json.loads(captured[1].content)
    assert repair["response_format"] == {"type": "json_object"}
    assert [part["type"] for part in repair["messages"][0]["content"]] == ["text"]
    assert "data:image/png;base64," not in repair["messages"][0]["content"][0]["text"]
    assert original_text not in repair["messages"][0]["content"][0]["text"]
    assert "test-credential" not in repair["messages"][0]["content"][0]["text"]


def test_bailian_connection_test_returns_a_repaired_structured_result_after_one_text_only_retry():
    captured = []
    responses = iter(["{}", json.dumps(_valid_analysis(), ensure_ascii=False)])

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": next(responses)}}]})

    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), credential="test-credential",
    )
    observed_requests = []
    original_analyze = provider.analyze

    def observe_request(request, *args, **kwargs):
        observed_requests.append(request)
        return original_analyze(request, *args, **kwargs)

    provider.analyze = observe_request

    result = provider.test_connection()

    assert result.observedFacts.temporal is None
    assert [request.isRepair for request in observed_requests] == [False, True]
    assert len(captured) == 2
    first = json.loads(captured[0].content)
    repair = json.loads(captured[1].content)
    assert [part["type"] for part in first["messages"][0]["content"]] == [
        "text", "text", "image_url",
    ]
    assert repair["response_format"] == {"type": "json_object"}
    assert [part["type"] for part in repair["messages"][0]["content"]] == ["text"]


def test_legacy_bailian_contract_has_an_explicit_compatibility_entrypoint():
    provider = BailianAnalysisProvider(httpx.Client())
    legacy_request = LegacyProviderRequest(
        contactSheetBytes=b"\xff\xd8\xff\xe0legacy\xff\xd9",
        proxy={"durationSeconds": 1.0},
        responseSchema={"type": "object"},
    )
    config = BailianProviderConfig(
        providerId="bailian",
        modelId="qwen3.7-flash",
        region="cn",
        workspaceId="workspace-test",
    )

    with pytest.raises(LegacyProviderAnalysisError) as error:
        provider.analyze_legacy(legacy_request, config, "")

    assert error.value.failure.code == "provider_unconfigured"


def test_core_bailian_request_rejects_legacy_arguments_instead_of_ignoring_them():
    provider = BailianAnalysisProvider(httpx.Client(), credential="test-credential")

    with pytest.raises(TypeError, match="旧配置"):
        provider.analyze(_core_request(), BailianProviderConfig(
            providerId="bailian",
            modelId="qwen3.7-flash",
            region="cn",
            workspaceId="workspace-test",
        ))
    with pytest.raises(TypeError, match="旧配置"):
        provider.analyze(_core_request(), credential="another-credential")


def test_bailian_307_redirect_to_remote_is_not_followed_or_retried():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(307, headers={"location": "https://example.com/steal"})
        pytest.fail("不得跟随重定向向远端再次发送请求")

    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        credential="test-credential",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_core_request())

    assert error.value.failure.code == "provider_error"
    assert len(requests) == 1


def test_bailian_rejects_an_unlisted_core_model_without_calling_the_network():
    def handler(request):
        pytest.fail("未列入白名单的模型不得发起请求")

    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        credential="test-credential",
    )
    request = _core_request().model_copy(update={"model": "unlisted-model"})

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(request)

    assert error.value.failure.code == "unsupported_model_capability"


def test_bailian_rejects_oversized_provider_response_before_exposing_raw_text():
    payload = {"choices": [{"message": {"content": "x" * 300_000}}]}
    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))),
        credential="test-credential",
    )

    with pytest.raises(ProviderAnalysisError) as error:
        provider.analyze(_core_request())

    assert error.value.failure.code == "invalid_analysis_response"


def test_shared_transport_returns_one_bounded_result_for_success_and_http_error_statuses():
    def handler(request):
        if request.url.path == "/success":
            return httpx.Response(200, headers={"x-request-id": "request-success"}, json={"ok": True})
        return httpx.Response(418, headers={"x-request-id": "request-error"}, json={"error": "teapot"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        success = provider_http.post_provider_json(
            client, "https://provider.invalid/success", headers={}, payload={},
        )
        error = provider_http.post_provider_json(
            client, "https://provider.invalid/error", headers={}, payload={},
        )

    assert success.status_code == 200
    assert success.json_body == {"ok": True}
    assert success.body_text == '{"ok":true}'
    assert success.request_id == "request-success"
    assert error.status_code == 418
    assert error.json_body == {"error": "teapot"}
    assert error.body_text == '{"error":"teapot"}'
    assert error.request_id == "request-error"


class _StreamingResponse:
    status_code = 200
    headers = {}

    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_bytes(self):
        yield from self._chunks


class _StreamingClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


class _LoopbackProviderServer:
    def __init__(self, handler):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever)

    @property
    def url(self):
        return "http://127.0.0.1:%d/chat/completions" % self._server.server_port

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1.0)
        assert not self._thread.is_alive()


def test_bailian_stops_streaming_response_as_soon_as_byte_limit_is_exceeded():
    def chunks():
        yield b"{" + b"x" * 256_000
        pytest.fail("超过响应上限后不得读取后续流块")

    response = _StreamingResponse(chunks())
    client = _StreamingClient(response)

    with pytest.raises(provider_http.ProviderHTTPTransportError) as error:
        provider_http.post_provider_json(client, "https://provider.invalid", headers={}, payload={})

    assert error.value.failure_code == "invalid_analysis_response"
    assert response.closed is True
    assert len(client.calls) == 1


def test_bailian_streaming_response_enforces_one_total_deadline(monkeypatch):
    response = _StreamingResponse([b'{"choices":[{"message":{"content":"{}"}}]}'])
    client = _StreamingClient(response)
    ticks = iter([0.0, 0.0, 121.0])
    monkeypatch.setattr(provider_http.time, "monotonic", lambda: next(ticks))

    with pytest.raises(provider_http.ProviderHTTPTransportError) as error:
        provider_http.post_provider_json(client, "https://provider.invalid", headers={}, payload={})

    assert error.value.failure_code == "timeout"
    assert response.closed is True


def test_bailian_total_deadline_interrupts_a_real_loopback_server_before_first_response_byte(monkeypatch):
    class DelayedFirstByteHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            time.sleep(0.4)
            try:
                self.wfile.write(b'{"choices":[{"message":{"content":"{}"}}]}')
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    monkeypatch.setattr(provider_http, "TOTAL_PROVIDER_DEADLINE_SECONDS", 0.1)
    with _LoopbackProviderServer(DelayedFirstByteHandler) as server, provider_http.new_provider_http_client(timeout=1.0) as client:
        started = time.monotonic()
        with pytest.raises(provider_http.ProviderHTTPTransportError) as error:
            provider_http.post_provider_json(client, server.url, headers={}, payload={})
        elapsed = time.monotonic() - started

    assert error.value.failure_code == "timeout"
    assert elapsed < 0.25


def test_bailian_total_deadline_interrupts_a_real_slow_drip_stream(monkeypatch):
    class SlowDripHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                for chunk in (b'{"choices":', b'[{"message":', b'{"content":"{}"}}]}'):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(0.06)
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    monkeypatch.setattr(provider_http, "TOTAL_PROVIDER_DEADLINE_SECONDS", 0.1)
    with _LoopbackProviderServer(SlowDripHandler) as server, provider_http.new_provider_http_client(timeout=1.0) as client:
        started = time.monotonic()
        with pytest.raises(provider_http.ProviderHTTPTransportError) as error:
            provider_http.post_provider_json(client, server.url, headers={}, payload={})
        elapsed = time.monotonic() - started

    assert error.value.failure_code == "timeout"
    assert elapsed < 0.25


def test_bailian_total_deadline_interrupts_when_only_partial_response_headers_arrive(monkeypatch):
    class PartialHeadersHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n")
            self.wfile.flush()
            time.sleep(0.4)

        def log_message(self, *args):
            pass

    monkeypatch.setattr(provider_http, "TOTAL_PROVIDER_DEADLINE_SECONDS", 0.1)
    with _LoopbackProviderServer(PartialHeadersHandler) as server, provider_http.new_provider_http_client(timeout=1.0) as client:
        started = time.monotonic()
        with pytest.raises(provider_http.ProviderHTTPTransportError) as error:
            provider_http.post_provider_json(client, server.url, headers={}, payload={})
        elapsed = time.monotonic() - started

    assert error.value.failure_code == "timeout"
    assert elapsed < 0.25


def test_bailian_total_deadline_cancels_tls_handshake_after_connect_budget_is_consumed(monkeypatch):
    client_hello_received = threading.Event()

    class ClientHelloOnlyHandler(BaseHTTPRequestHandler):
        def handle(self):
            self.connection.recv(16 * 1024)
            client_hello_received.set()
            time.sleep(0.4)

        def log_message(self, *args):
            pass

    original_connect_tcp = provider_http._CancellableNetworkBackend.connect_tcp

    def delayed_connect_tcp(self, *args, **kwargs):
        stream = original_connect_tcp(self, *args, **kwargs)
        time.sleep(0.15)
        return stream

    monkeypatch.setattr(provider_http._CancellableNetworkBackend, "connect_tcp", delayed_connect_tcp)
    monkeypatch.setattr(provider_http, "TOTAL_PROVIDER_DEADLINE_SECONDS", 0.2)
    with _LoopbackProviderServer(ClientHelloOnlyHandler) as server, provider_http.new_provider_http_client(timeout=1.0) as client:
        started = time.monotonic()
        with pytest.raises(provider_http.ProviderHTTPTransportError) as error:
            provider_http.post_provider_json(
                client, "https://127.0.0.1:%d/chat/completions" % server._server.server_port,
                headers={}, payload={},
            )
        elapsed = time.monotonic() - started

    assert client_hello_received.is_set()
    assert error.value.failure_code == "timeout"
    assert elapsed < 0.3
