import base64
import json

import httpx
import pytest

from app.analysis_models import StructuredAnalysis
from app.analysis_provider import (
    BailianProviderConfig,
    ClaudeProviderConfig,
    DoubaoProviderConfig,
    GeminiProviderConfig,
    OpenAIProviderConfig,
    ProviderRequest,
)
from app.analysis_providers import ProviderAnalysisError
from app.analysis_providers.bailian import BailianAnalysisProvider
from app.analysis_providers.claude import ClaudeAnalysisProvider
from app.analysis_providers.doubao import DoubaoAnalysisProvider
from app.analysis_providers.gemini import GeminiAnalysisProvider
from app.analysis_providers.openai import OpenAIAnalysisProvider


def valid_analysis_payload():
    def entry(entry_id):
        return {
            "id": entry_id,
            "kind": "observation",
            "summary": "主体从画面左侧向右侧移动。",
            "timeRange": {"startSeconds": 0.0, "endSeconds": 1.0},
            "confidence": "high",
            "confidenceReason": "联系表中的主体位置连续变化。",
        }

    def check(entry_id, criterion):
        return {
            **entry(entry_id),
            "criterion": criterion,
            "status": "passed",
            "evidence": "分析代理中的连续视觉证据。",
        }

    return {
        "version": 1,
        "status": "in_scope",
        "referenceVideoDurationSeconds": 2.5,
        "basicFacts": [entry("fact-001")],
        "suitability": [
            check("check-single-shot", "single_shot"),
            check("check-motion-range", "motion_range"),
            check("check-primary-subject-count", "primary_subject_count"),
            check("check-complex-interaction", "complex_interaction"),
        ],
        "subject": [entry("subject-001")],
        "scene": [entry("scene-001")],
        "action": [entry("action-001")],
        "camera": [entry("camera-001")],
        "lighting": [entry("lighting-001")],
    }


def analysis_request():
    return ProviderRequest(
        contactSheetBytes=b"\xff\xd8\xff\xe0contact-sheet\xff\xd9",
        proxy={"durationSeconds": 2.5, "motion": {"samples": [0.1, 0.2]}},
        responseSchema=StructuredAnalysis.model_json_schema(),
    )


def success_response(provider_id, payload):
    encoded = json.dumps(payload, ensure_ascii=False)
    if provider_id == "bailian":
        return {"choices": [{"message": {"content": encoded}}]}
    if provider_id in {"openai", "doubao"}:
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": encoded}]}]}
    if provider_id == "gemini":
        return {"candidates": [{"content": {"parts": [{"text": encoded}]}}]}
    return {"content": [{"type": "text", "text": encoded}]}


def configured_provider(provider_id, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=0.1)
    if provider_id == "bailian":
        return BailianAnalysisProvider(client), BailianProviderConfig(
            providerId="bailian", modelId="qwen3.7-flash", region="cn", workspaceId="workspace-test",
        ), client
    if provider_id == "openai":
        return OpenAIAnalysisProvider(client), OpenAIProviderConfig(
            providerId="openai", modelId="openai-test-model",
        ), client
    if provider_id == "gemini":
        return GeminiAnalysisProvider(client), GeminiProviderConfig(
            providerId="gemini", modelId="gemini-test-model", projectId="project-test", location="us-central1",
        ), client
    if provider_id == "doubao":
        return DoubaoAnalysisProvider(client, {"ep-test": "doubao-test-model"}), DoubaoProviderConfig(
            providerId="doubao", modelId="doubao-test-model", endpointId="ep-test",
        ), client
    return ClaudeAnalysisProvider(client), ClaudeProviderConfig(
        providerId="claude", modelId="claude-test-model",
    ), client


def analyze_legacy(provider_id, provider, request, config, credential):
    if provider_id in {"openai", "gemini", "claude"}:
        return provider.analyze_legacy(request, config, credential)
    return provider.analyze(request, config, credential)


@pytest.mark.parametrize("provider_id", ["bailian", "openai", "gemini", "doubao", "claude"])
def test_each_provider_sends_only_analysis_proxy_contact_sheet_and_fixed_contract(provider_id):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=success_response(provider_id, valid_analysis_payload()))

    provider, config, client = configured_provider(provider_id, handler)
    try:
        result = analyze_legacy(provider_id, provider, analysis_request(), config, "secret-value")
    finally:
        client.close()

    assert result.model_dump() == valid_analysis_payload()
    assert len(captured) == 1
    body = captured[0].content.decode("utf-8")
    for forbidden in ("reference-video.mp4", "/Users/", "project-alpha", "secret-value"):
        assert forbidden not in body
    assert base64.b64encode(analysis_request().contactSheetBytes).decode("ascii") in body


@pytest.mark.parametrize(
    ("provider_id", "expected_url_fragment", "assert_shape"),
    [
        ("bailian", "dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", lambda body: body["response_format"]["type"] == "json_schema" and body["messages"][1]["content"][0]["type"] == "image_url"),
        ("openai", "api.openai.com/v1/responses", lambda body: body["text"]["format"]["type"] == "json_schema" and body["input"][0]["content"][0]["type"] == "input_image"),
        ("gemini", "generativelanguage.googleapis.com/v1beta/models/gemini-test-model:generateContent", lambda body: body["generationConfig"]["responseMimeType"] == "application/json" and body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/jpeg"),
        ("doubao", "ark.cn-beijing.volces.com/api/v3/responses", lambda body: body["text"]["format"]["type"] == "json_schema" and body["input"][0]["content"][0]["type"] == "input_image"),
        ("claude", "api.anthropic.com/v1/messages", lambda body: body["output_config"]["format"]["type"] == "json_schema" and body["messages"][0]["content"][0]["source"]["type"] == "base64"),
    ],
)
def test_each_provider_uses_its_native_official_image_and_structured_output_shape(provider_id, expected_url_fragment, assert_shape):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=success_response(provider_id, valid_analysis_payload()))

    provider, config, client = configured_provider(provider_id, handler)
    try:
        analyze_legacy(provider_id, provider, analysis_request(), config, "secret-value")
    finally:
        client.close()

    request = captured[0]
    assert expected_url_fragment in str(request.url)
    assert assert_shape(json.loads(request.content))
    if provider_id == "claude":
        assert request.headers["x-api-key"] == "secret-value"
    elif provider_id == "gemini":
        assert request.headers["x-goog-api-key"] == "secret-value"
    else:
        assert request.headers["authorization"] == "Bearer secret-value"


@pytest.mark.parametrize("provider_id", ["bailian", "openai", "gemini", "doubao", "claude"])
@pytest.mark.parametrize(
    ("response_factory", "expected_code"),
    [
        (lambda: httpx.Response(401, json={"error": {"message": "denied"}}), "authentication_failed"),
        (lambda: httpx.Response(403, json={"error": {"message": "denied"}}), "authentication_failed"),
        (lambda: httpx.Response(429, json={"error": {"message": "limited"}}), "rate_limited"),
        (lambda: httpx.Response(400, json={"error": {"code": "unsupported_model"}}), "unsupported_model_capability"),
        (lambda: httpx.Response(400, json={"error": {"message": "content blocked"}}), "content_rejected"),
        (lambda: httpx.Response(500, json={"error": {"message": "unavailable"}}), "provider_error"),
        (lambda: httpx.Response(200, content=b"not-json"), "invalid_response"),
        (lambda: httpx.Response(200, json=success_response("openai", {"unexpected": True})), "invalid_response"),
    ],
)
def test_each_provider_maps_http_and_invalid_response_failures_to_stable_codes(provider_id, response_factory, expected_code):
    def handler(request):
        return response_factory()

    provider, config, client = configured_provider(provider_id, handler)
    try:
        with pytest.raises(ProviderAnalysisError) as error:
            analyze_legacy(provider_id, provider, analysis_request(), config, "secret-value")
    finally:
        client.close()

    assert error.value.failure.code == expected_code
    assert "secret-value" not in str(error.value)


@pytest.mark.parametrize("provider_id", ["bailian", "openai", "gemini", "doubao", "claude"])
@pytest.mark.parametrize(
    ("exception", "expected_code"),
    [
        (httpx.ConnectTimeout("timed out"), "timeout"),
        (httpx.ConnectError("offline"), "network_error"),
    ],
)
def test_each_provider_maps_transport_failures_without_retries(provider_id, exception, expected_code):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        raise exception

    provider, config, client = configured_provider(provider_id, handler)
    try:
        with pytest.raises(ProviderAnalysisError) as error:
            analyze_legacy(provider_id, provider, analysis_request(), config, "secret-value")
    finally:
        client.close()

    assert error.value.failure.code == expected_code
    assert attempts == 1


def test_configuration_validation_rejects_provider_mismatch_missing_credential_and_unverified_doubao_endpoint():
    never_called = lambda request: pytest.fail("配置无效时不得调用 HTTP")
    openai, _, openai_client = configured_provider("openai", never_called)
    doubao, _, doubao_client = configured_provider("doubao", never_called)
    try:
        with pytest.raises(ProviderAnalysisError) as mismatch:
            openai.validate_configuration(
                BailianProviderConfig(providerId="bailian", modelId="qwen3.7-flash", region="cn", workspaceId="workspace-test"),
                "secret-value",
            )
        with pytest.raises(ProviderAnalysisError) as missing_credential:
            openai.validate_configuration(OpenAIProviderConfig(providerId="openai", modelId="openai-test-model"), "")
        with pytest.raises(ProviderAnalysisError) as unverified_endpoint:
            doubao.validate_configuration(
                DoubaoProviderConfig(providerId="doubao", modelId="different-model", endpointId="ep-test"),
                "secret-value",
            )
    finally:
        openai_client.close()
        doubao_client.close()

    assert mismatch.value.failure.code == "provider_unconfigured"
    assert missing_credential.value.failure.code == "provider_unconfigured"
    assert unverified_endpoint.value.failure.code == "unsupported_model_capability"


def test_transport_failure_does_not_retain_sensitive_provider_exception_text():
    def handler(request):
        raise httpx.ConnectError("secret-value must not escape")

    provider, config, client = configured_provider("openai", handler)
    try:
        with pytest.raises(ProviderAnalysisError) as error:
            analyze_legacy("openai", provider, analysis_request(), config, "secret-value")
    finally:
        client.close()

    assert error.value.failure.code == "network_error"
    assert error.value.__cause__ is None
