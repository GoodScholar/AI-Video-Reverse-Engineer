import importlib.util
from pathlib import Path

import pytest


def _script():
    path = Path(__file__).parents[1] / "scripts" / "smoke_analysis_provider.py"
    spec = importlib.util.spec_from_file_location("smoke_analysis_provider", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_script_uses_only_the_local_connection_test_api_and_prints_a_redacted_summary(monkeypatch, capsys):
    script = _script()
    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self, amount=-1):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def open_url(request, timeout):
        calls.append((request.full_url, request.method, request.data, timeout))
        if request.method == "GET":
            return Response(b'[{"provider":"openai","model":"gpt-5.6-luna","configurationRevision":"revision","catalogVersion":"catalog"}]')
        return Response(b'{"provider":"openai","model":"gpt-5.6-luna","status":"connected","verificationState":"available","configurationRevision":"revision","catalogVersion":"catalog"}')

    monkeypatch.setattr(script, "_open_connection", open_url)

    assert script.main(["--provider", "openai", "--base-url", "http://127.0.0.1:8000"]) == 0
    assert calls == [
        ("http://127.0.0.1:8000/api/analysis-providers", "GET", None, 130),
        ("http://127.0.0.1:8000/api/analysis-providers/openai/test-connection", "POST", b"{}", 130),
    ]
    assert capsys.readouterr().out == "openai gpt-5.6-luna connected\n"


def test_smoke_script_returns_nonzero_for_unconfigured_without_printing_response_body(monkeypatch, capsys):
    script = _script()

    class Error(script.error.HTTPError):
        def read(self):
            return b'{"detail":{"message":"secret-value"}}'

    def open_url(*_, **__):
        raise Error("http://127.0.0.1:8000", 409, "Conflict", {}, None)

    monkeypatch.setattr(script, "_open_connection", open_url)

    assert script.main(["--provider", "openai"]) == 1
    output = capsys.readouterr().out
    assert "openai connection test failed (HTTP 409)" in output
    assert "secret-value" not in output


@pytest.mark.parametrize("base_url", ["https://example.com", "ftp://127.0.0.1:8000"])
def test_smoke_script_rejects_non_loopback_base_url(base_url, capsys):
    script = _script()

    assert script.main(["--provider", "openai", "--base-url", base_url]) == 1
    assert capsys.readouterr().out == "openai connection test failed\n"


def test_smoke_script_accepts_chatanywhere_as_a_catalog_provider():
    assert _script().parse_args(["--provider", "chatanywhere"]).provider == "chatanywhere"


def test_smoke_script_rejects_redirects_and_connected_but_unverified(monkeypatch, capsys):
    script = _script()

    def redirected(*_, **__):
        raise script.error.HTTPError("http://127.0.0.1:8000", 302, "Found", {}, None)

    monkeypatch.setattr(script, "_open_connection", redirected)
    assert script.main(["--provider", "openai"]) == 1
    assert capsys.readouterr().out == "openai connection test failed (HTTP 302)\n"

    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self, amount=-1):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def unverified_response(call, timeout):
        calls.append(call.method)
        if call.method == "GET":
            return Response(b'[{"provider":"openai","model":"gpt-5.6-luna","configurationRevision":"revision","catalogVersion":"catalog"}]')
        return Response(b'{"provider":"openai","model":"gpt-5.6-luna","status":"connected","verificationState":"unverified","configurationRevision":"revision","catalogVersion":"catalog"}')

    monkeypatch.setattr(script, "_open_connection", unverified_response)
    assert script.main(["--provider", "openai"]) == 1
    assert calls == ["GET", "POST"]
    assert capsys.readouterr().out == "openai connection test failed\n"


def test_smoke_script_rejects_a_connection_result_for_an_expired_snapshot(monkeypatch, capsys):
    script = _script()

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self, amount=-1):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def open_url(call, timeout):
        if call.method == "GET":
            return Response(b'[{"provider":"openai","model":"gpt-5.6-luna","configurationRevision":"old-revision","catalogVersion":"catalog"}]')
        return Response(b'{"provider":"openai","model":"gpt-5.6-luna","status":"connected","verificationState":"available","configurationRevision":"new-revision","catalogVersion":"catalog"}')

    monkeypatch.setattr(script, "_open_connection", open_url)

    assert script.main(["--provider", "openai"]) == 1
    assert capsys.readouterr().out == "openai connection test failed\n"


def test_smoke_script_rejects_a_connection_result_with_a_different_catalog_version(monkeypatch, capsys):
    script = _script()

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self, amount=-1):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def open_url(call, timeout):
        if call.method == "GET":
            return Response(b'[{"provider":"openai","model":"gpt-5.6-luna","configurationRevision":"revision","catalogVersion":"old-catalog"}]')
        return Response(b'{"provider":"openai","model":"gpt-5.6-luna","status":"connected","verificationState":"available","configurationRevision":"revision","catalogVersion":"new-catalog"}')

    monkeypatch.setattr(script, "_open_connection", open_url)

    assert script.main(["--provider", "openai"]) == 1
    assert capsys.readouterr().out == "openai connection test failed\n"


def test_smoke_script_rejects_a_response_larger_than_64_kib(monkeypatch, capsys):
    script = _script()
    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self, amount=-1):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def open_url(call, timeout):
        calls.append(call.method)
        if call.method == "GET":
            return Response(b'[{"provider":"openai","model":"gpt-5.6-luna","configurationRevision":"revision","catalogVersion":"catalog"}]')
        return Response((
            b'{"provider":"openai","model":"gpt-5.6-luna","status":"connected",'
            b'"verificationState":"available","configurationRevision":"revision",'
            b'"catalogVersion":"catalog","padding":"' + b"x" * script._MAX_RESPONSE_BYTES + b'"}'
        ))

    monkeypatch.setattr(script, "_open_connection", open_url)

    assert script.main(["--provider", "openai"]) == 1
    assert calls == ["GET", "POST"]
    assert capsys.readouterr().out == "openai connection test failed\n"


def test_smoke_script_builds_an_opener_with_the_no_redirect_handler(monkeypatch):
    script = _script()
    calls = []

    class Opener:
        def open(self, call, timeout):
            calls.append((call, timeout))
            return object()

    def build_opener(*handlers):
        assert len(handlers) == 1
        assert isinstance(handlers[0], script._NoRedirect)
        return Opener()

    monkeypatch.setattr(script.request, "build_opener", build_opener)
    call = script.request.Request("http://127.0.0.1:8000/api/analysis-providers")

    assert script._open_connection(call, timeout=7) is not None
    assert calls == [(call, 7)]
