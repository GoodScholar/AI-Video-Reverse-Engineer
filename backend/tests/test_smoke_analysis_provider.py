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
        status = 200

        def read(self):
            return b'{"provider":"openai","model":"gpt-5.6-luna","status":"connected"}'

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def open_url(request, timeout):
        calls.append((request.full_url, request.method, request.data, timeout))
        return Response()

    monkeypatch.setattr(script.request, "urlopen", open_url)

    assert script.main(["--provider", "openai", "--base-url", "http://127.0.0.1:8000"]) == 0
    assert calls == [("http://127.0.0.1:8000/api/analysis-providers/openai/test-connection", "POST", b"{}", 130)]
    assert capsys.readouterr().out == "openai gpt-5.6-luna connected\n"


def test_smoke_script_returns_nonzero_for_unconfigured_without_printing_response_body(monkeypatch, capsys):
    script = _script()

    class Error(script.error.HTTPError):
        def read(self):
            return b'{"detail":{"message":"secret-value"}}'

    def open_url(*_, **__):
        raise Error("http://127.0.0.1:8000", 409, "Conflict", {}, None)

    monkeypatch.setattr(script.request, "urlopen", open_url)

    assert script.main(["--provider", "openai"]) == 1
    output = capsys.readouterr().out
    assert "openai connection test failed (HTTP 409)" in output
    assert "secret-value" not in output
