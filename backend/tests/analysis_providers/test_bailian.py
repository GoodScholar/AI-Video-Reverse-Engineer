import httpx
import pytest

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
