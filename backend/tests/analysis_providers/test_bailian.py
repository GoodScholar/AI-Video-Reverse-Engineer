import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

import app.analysis_providers.openai_compatible_chat as openai_chat
from app.analysis_input import ImageAnalysisInput
from app.analysis_prompt import build_analysis_prompt
from app.analysis_provider import BailianProviderConfig, ProviderRequest as LegacyProviderRequest
from app.analysis_providers import ProviderAnalysisError as LegacyProviderAnalysisError
from app.analysis_providers.base import ProviderAnalysisError, ProviderRequest
from app.analysis_providers.bailian import BailianAnalysisProvider
from app.analysis_providers.test_image import connection_test_image


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


def test_connection_probe_is_a_code_generated_fixed_rgb_png():
    probe = connection_test_image()

    assert probe.width == probe.height == 16
    assert probe.analysisProxyBytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert probe.aspectRatio == 1.0


def test_bailian_connection_test_sends_the_built_in_probe_not_user_input():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = BailianAnalysisProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        credential="test-credential",
    )

    provider.test_connection()

    body = captured[0].content.decode("utf-8")
    assert "data:image/png;base64," in body
    assert "analysis-proxy" not in body


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

    with pytest.raises(ProviderAnalysisError) as error:
        openai_chat.post_chat_completion(client, "https://provider.invalid", "test-credential", _core_request())

    assert error.value.failure.code == "invalid_analysis_response"
    assert response.closed is True
    assert len(client.calls) == 1


def test_bailian_streaming_response_enforces_one_total_deadline(monkeypatch):
    response = _StreamingResponse([b'{"choices":[{"message":{"content":"{}"}}]}'])
    client = _StreamingClient(response)
    ticks = iter([0.0, 0.0, 121.0])
    monkeypatch.setattr(openai_chat.time, "monotonic", lambda: next(ticks))

    with pytest.raises(ProviderAnalysisError) as error:
        openai_chat.post_chat_completion(client, "https://provider.invalid", "test-credential", _core_request())

    assert error.value.failure.code == "timeout"
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

    monkeypatch.setattr(openai_chat, "TOTAL_PROVIDER_DEADLINE_SECONDS", 0.1)
    with _LoopbackProviderServer(DelayedFirstByteHandler) as server, openai_chat.new_cancellable_client(timeout=1.0) as client:
        started = time.monotonic()
        with pytest.raises(ProviderAnalysisError) as error:
            openai_chat.post_chat_completion(client, server.url, "test-credential", _core_request())
        elapsed = time.monotonic() - started

    assert error.value.failure.code == "timeout"
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

    monkeypatch.setattr(openai_chat, "TOTAL_PROVIDER_DEADLINE_SECONDS", 0.1)
    with _LoopbackProviderServer(SlowDripHandler) as server, openai_chat.new_cancellable_client(timeout=1.0) as client:
        started = time.monotonic()
        with pytest.raises(ProviderAnalysisError) as error:
            openai_chat.post_chat_completion(client, server.url, "test-credential", _core_request())
        elapsed = time.monotonic() - started

    assert error.value.failure.code == "timeout"
    assert elapsed < 0.25


def test_bailian_total_deadline_interrupts_when_only_partial_response_headers_arrive(monkeypatch):
    class PartialHeadersHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n")
            self.wfile.flush()
            time.sleep(0.4)

        def log_message(self, *args):
            pass

    monkeypatch.setattr(openai_chat, "TOTAL_PROVIDER_DEADLINE_SECONDS", 0.1)
    with _LoopbackProviderServer(PartialHeadersHandler) as server, openai_chat.new_cancellable_client(timeout=1.0) as client:
        started = time.monotonic()
        with pytest.raises(ProviderAnalysisError) as error:
            openai_chat.post_chat_completion(client, server.url, "test-credential", _core_request())
        elapsed = time.monotonic() - started

    assert error.value.failure.code == "timeout"
    assert elapsed < 0.25
