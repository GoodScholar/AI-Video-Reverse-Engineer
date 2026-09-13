import inspect
import warnings

import pytest
from pydantic import ValidationError

from app.analysis_models import StructuredAnalysis
from app.analysis_provider import (
    AnalysisProvider,
    BailianProviderConfig,
    ClaudeProviderConfig,
    DoubaoProviderConfig,
    GeminiProviderConfig,
    OpenAIProviderConfig,
    ProviderCatalog,
    ProviderConfigurationError,
    ProviderDefinition,
    ProviderFailure,
    ProviderModel,
    ProviderRequest,
    parse_provider_config,
)
from app.analysis_provider_catalog import CATALOG


def bailian_config(**overrides):
    return parse_provider_config({
        "providerId": "bailian",
        "modelId": "qwen3.7-flash",
        "region": "cn",
        "workspaceId": "workspace-001",
        **overrides,
    })


def valid_request_payload():
    return {
        "contactSheetBytes": b"\xff\xd8\xff\xe0contact-sheet\xff\xd9",
        "proxy": {
            "durationSeconds": 2.5,
            "motion": {"samples": [0.1, 0.2]},
        },
        "responseSchema": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
        },
    }


def verified_model(model_id="verified-model"):
    return ProviderModel(
        modelId=model_id,
        label="Verified model",
        recommended=True,
        vision=True,
        structuredOutput=True,
        regions=("cn",),
    )


def catalog_with_verified_doubao(endpoint_bindings):
    providers = dict(CATALOG.providers)
    providers["doubao"] = ProviderDefinition(
        providerId="doubao",
        label="火山方舟豆包",
        availability="verified",
        models=(verified_model(),),
        verifiedEndpointBindings=endpoint_bindings,
    )
    return ProviderCatalog(version=1, providers=providers)


def test_catalog_exposes_all_unified_provider_ids_and_verified_bailian_model():
    assert set(CATALOG.providers) == {
        "bailian", "local_openai_compatible", "openai", "doubao", "gemini", "grok", "claude",
    }

    bailian = CATALOG.providers["bailian"]
    assert bailian.availability == "verified"
    assert [model.modelId for model in bailian.models] == ["qwen3.7-flash"]
    assert bailian.models[0].recommended is True
    assert bailian.models[0].vision is True
    assert bailian.models[0].structuredOutput is True


@pytest.mark.parametrize(
    ("provider_id", "config"),
    [
        ("openai", {"providerId": "openai", "modelId": "not-verified"}),
        (
            "gemini",
            {
                "providerId": "gemini",
                "modelId": "not-verified",
                "projectId": "project-001",
                "location": "us-central1",
            },
        ),
        ("doubao", {"providerId": "doubao", "modelId": "not-verified", "endpointId": "ep-001"}),
        ("claude", {"providerId": "claude", "modelId": "not-verified"}),
    ],
)
def test_unverified_provider_is_visible_but_cannot_be_selected(provider_id, config):
    provider = CATALOG.providers[provider_id]

    assert provider.availability == "unverified"
    assert provider.models == ()
    with pytest.raises(ProviderConfigurationError):
        CATALOG.select_model(parse_provider_config(config))


def test_verified_recommended_model_can_be_selected_for_analysis():
    model = CATALOG.select_model(bailian_config())

    assert model.modelId == "qwen3.7-flash"
    assert model.vision is True
    assert model.structuredOutput is True


@pytest.mark.parametrize(
    "config",
    [
        {"providerId": "unknown", "modelId": "qwen3.7-flash"},
        {
            "providerId": "bailian",
            "modelId": "unknown-model",
            "region": "cn",
            "workspaceId": "workspace-001",
        },
    ],
)
def test_catalog_rejects_unknown_provider_or_model(config):
    with pytest.raises((ProviderConfigurationError, ValidationError)):
        CATALOG.select_model(parse_provider_config(config))


@pytest.mark.parametrize(
    "config",
    [
        {
            "providerId": "bailian",
            "modelId": "qwen3.7-flash",
            "region": "cn",
            "workspaceId": "workspace-001",
            "baseUrl": "https://untrusted.example/v1",
        },
        {"providerId": "openai", "modelId": "not-verified", "endpointId": "ep-001"},
    ],
)
def test_provider_configs_reject_arbitrary_or_wrong_provider_fields(config):
    with pytest.raises(ValidationError):
        parse_provider_config(config)


def test_bailian_config_requires_region_and_workspace():
    with pytest.raises(ValidationError):
        parse_provider_config({"providerId": "bailian", "modelId": "qwen3.7-flash"})


def test_gemini_config_requires_project_and_location():
    with pytest.raises(ValidationError):
        parse_provider_config({"providerId": "gemini", "modelId": "not-verified", "projectId": "project-001"})


def test_provider_config_parser_returns_the_closed_discriminated_types():
    assert isinstance(bailian_config(), BailianProviderConfig)
    assert isinstance(parse_provider_config({"providerId": "openai", "modelId": "not-verified"}), OpenAIProviderConfig)
    assert isinstance(parse_provider_config({
        "providerId": "gemini",
        "modelId": "not-verified",
        "projectId": "project-001",
        "location": "us-central1",
    }), GeminiProviderConfig)
    assert isinstance(parse_provider_config({
        "providerId": "doubao", "modelId": "not-verified", "endpointId": "ep-001",
    }), DoubaoProviderConfig)
    assert isinstance(parse_provider_config({"providerId": "claude", "modelId": "not-verified"}), ClaudeProviderConfig)


def test_catalog_rejects_an_unverified_doubao_endpoint_binding():
    with pytest.raises(ProviderConfigurationError, match="推理接入点尚未验证"):
        CATALOG.select_model(parse_provider_config({
            "providerId": "doubao",
            "modelId": "not-verified",
            "endpointId": "ep-not-verified",
        }))


def test_verified_doubao_requires_a_matching_endpoint_binding_at_selection():
    catalog = catalog_with_verified_doubao({"ep-verified": "verified-model"})
    missing_endpoint = DoubaoProviderConfig.model_construct(
        providerId="doubao", modelId="verified-model",
    )

    with pytest.raises(ProviderConfigurationError, match="推理接入点"):
        catalog.select_model(missing_endpoint)
    assert catalog.select_model(parse_provider_config({
        "providerId": "doubao",
        "modelId": "verified-model",
        "endpointId": "ep-verified",
    })).modelId == "verified-model"


def test_provider_request_accepts_only_non_empty_jpeg_bytes_and_json_payloads():
    request = ProviderRequest.model_validate(valid_request_payload())

    assert request.contactSheetBytes == b"\xff\xd8\xff\xe0contact-sheet\xff\xd9"
    assert request.proxy["motion"]["samples"] == (0.1, 0.2)
    with pytest.raises(TypeError):
        request.proxy["other"] = "value"


@pytest.mark.parametrize(
    "contact_sheet",
    ["not-bytes", b"", b"not-a-jpeg", b"\xff\xd8unfinished"],
)
def test_provider_request_rejects_non_jpeg_or_coerced_contact_sheet_bytes(contact_sheet):
    payload = valid_request_payload()
    payload["contactSheetBytes"] = contact_sheet

    with pytest.raises(ValidationError):
        ProviderRequest.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("proxy", {"nested": object()}),
        ("responseSchema", {"binary": b"not-json"}),
        ("proxy", {"apiKey": "secret-value"}),
        ("proxy", {"Authorization": "Bearer secret-value"}),
        ("proxy", {"videoPath": "/Users/someone/input.mp4"}),
        ("responseSchema", {"example": "C:\\Users\\someone\\input.mp4"}),
        ("proxy", {"AUTH": "sk-live-opaque-value"}),
        ("proxy", {"access_token": "opaque-value"}),
        ("proxy", {"client-secret": "opaque-value"}),
        ("proxy", {"nested": {"authentication": "opaque-value"}}),
        ("proxy", {"nested": {"private_key": "opaque-value"}}),
        ("proxy", {"nested": {"secretKey": "opaque-value"}}),
        ("proxy", {"description": "Bearer opaque-value"}),
        ("proxy", {"description": "sk-live-opaque-value"}),
        ("proxy", {"description": "AKIAIOSFODNN7EXAMPLE"}),
        ("proxy", {"source": "local=/Users/someone/private-video.mp4"}),
        ("proxy", {"source": "/tmp"}),
        ("proxy", {"source": "working directory is /home"}),
        ("responseSchema", {"example": "note: C:\\Users\\someone\\input.mp4"}),
    ],
)
def test_provider_request_rejects_non_json_sensitive_or_local_path_content(field, value):
    payload = valid_request_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        ProviderRequest.model_validate(payload)


@pytest.mark.parametrize(
    "local_path",
    [
        "/root/project/input.json",
        "/opt/data.json",
        "/tmp",
        "C:\\",
        "C:/",
    ],
)
def test_provider_request_rejects_any_posix_or_windows_absolute_path(local_path):
    payload = valid_request_payload()
    payload["proxy"] = {"source": local_path}

    with pytest.raises(ValidationError):
        ProviderRequest.model_validate(payload)


@pytest.mark.parametrize(
    "description",
    [
        "https://example.com/tmp/schema.json",
        "https://host/root/project.json",
        "token: a lexical unit in the prompt",
        "password = a word shown on screen",
        "16/9",
    ],
)
def test_provider_request_preserves_urls_and_ordinary_descriptive_text(description):
    payload = valid_request_payload()
    payload["proxy"] = {"description": description}

    request = ProviderRequest.model_validate(payload)

    assert request.proxy["description"] == description


def test_provider_request_preserves_legitimate_descriptive_text():
    payload = valid_request_payload()
    payload["proxy"] = {"description": "主体在明亮广场中从左向右行走，password token count 是普通说明。"}
    payload["responseSchema"] = {"$schema": "https://json-schema.org/draft/2020-12/schema"}

    request = ProviderRequest.model_validate(payload)
    assert request.proxy["description"] == "主体在明亮广场中从左向右行走，password token count 是普通说明。"
    assert request.responseSchema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.parametrize(
    "fragment",
    [
        "#/$defs/item",
        "#/properties/name",
        "#/$defs/tmp",
        "#/properties/name%20with%20spaces",
    ],
)
def test_provider_request_preserves_local_json_schema_fragment_references(fragment):
    payload = valid_request_payload()
    payload["responseSchema"] = {"$ref": fragment}

    request = ProviderRequest.model_validate(payload)

    assert request.responseSchema["$ref"] == fragment


@pytest.mark.parametrize(
    "fragment",
    [
        "#/properties/name /tmp/file",
        r"#/properties/C:\tmp\file",
    ],
)
def test_provider_request_rejects_json_pointer_fragments_with_raw_path_characters(fragment):
    payload = valid_request_payload()
    payload["responseSchema"] = {"$ref": fragment}

    with pytest.raises(ValidationError):
        ProviderRequest.model_validate(payload)


def test_provider_request_preserves_nested_local_json_schema_references():
    payload = valid_request_payload()
    payload["responseSchema"] = {
        "$defs": {
            "item": {
                "type": "object",
                "properties": {"name": {"$ref": "#/properties/name"}},
            },
        },
        "properties": {"result": {"$ref": "#/$defs/item"}},
    }

    request = ProviderRequest.model_validate(payload)

    assert request.responseSchema["$defs"]["item"]["properties"]["name"]["$ref"] == "#/properties/name"
    assert request.responseSchema["properties"]["result"]["$ref"] == "#/$defs/item"


def test_provider_failure_uses_fixed_safe_mapping_without_serializing_request_ids():
    failure = ProviderFailure.for_code("rate_limited")

    assert failure.model_dump() == {
        "code": "rate_limited",
        "message": "分析服务暂时限流，请稍后重试。",
        "retryable": True,
    }
    assert "request" not in "".join(failure.model_dump()).lower()
    with pytest.raises(TypeError):
        ProviderFailure.for_code("rate_limited", provider_request_id="req_12345678")
    with pytest.raises(ValidationError):
        ProviderFailure.model_validate({"code": "rate_limited", "providerRequestId": "req_12345678"})
    with pytest.raises(ValidationError):
        ProviderFailure(
            code="rate_limited",
            message="Authorization: Bearer secret-value",
            retryable=True,
        )


@pytest.mark.parametrize(
    "code",
    [
        "provider_unconfigured",
        "authentication_failed",
        "rate_limited",
        "network_error",
        "timeout",
        "unsupported_model_capability",
        "content_rejected",
        "invalid_response",
        "provider_error",
        "analysis_interrupted",
    ],
)
def test_provider_failure_accepts_each_stable_analysis_error_category(code):
    assert ProviderFailure.for_code(code).code == code


def test_provider_failure_rejects_unknown_error_code_and_credentials_in_serialized_input():
    with pytest.raises(ValidationError):
        ProviderFailure.for_code("unknown_failure")
    with pytest.raises(ValidationError):
        ProviderFailure.model_validate({
            "code": "rate_limited",
            "credential": "secret-value",
        })


def test_catalog_and_nested_value_objects_cannot_be_reinitialized_or_mutated_after_construction():
    selection_before = CATALOG.select_model(bailian_config()).modelId
    model = CATALOG.providers["bailian"].models[0]
    with pytest.raises(AttributeError):
        CATALOG.providers["bailian"].models.append(verified_model("invented-model"))
    with pytest.raises(TypeError):
        CATALOG.providers["invented"] = CATALOG.providers["bailian"]
    with pytest.raises(TypeError):
        model.__init__(
            modelId="invented-model",
            label="Invented model",
            recommended=True,
            vision=True,
            structuredOutput=True,
            regions=("cn",),
        )
    with pytest.raises(AttributeError):
        model.modelId = "invented-model"

    assert CATALOG.select_model(bailian_config()).modelId == selection_before == "qwen3.7-flash"


def test_catalog_selection_does_not_emit_mapping_serialization_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert CATALOG.select_model(bailian_config()).modelId == "qwen3.7-flash"


@pytest.mark.parametrize(
    "definition",
    [
        lambda: ProviderDefinition(
            providerId="bailian", label="Bailian", availability="verified", models=(),
        ),
        lambda: ProviderDefinition(
            providerId="bailian",
            label="Bailian",
            availability="verified",
            models=(verified_model("duplicate"), verified_model("duplicate")),
        ),
        lambda: ProviderDefinition(
            providerId="doubao",
            label="Doubao",
            availability="verified",
            models=(verified_model(),),
            verifiedEndpointBindings={"ep-001": "missing-model"},
        ),
    ],
)
def test_catalog_definition_rejects_invalid_verified_model_or_endpoint_invariants(definition):
    with pytest.raises(ValueError):
        definition()


def test_catalog_constructor_rejects_invalid_provider_mapping():
    with pytest.raises(ValueError):
        ProviderCatalog(version=1, providers={"bailian": CATALOG.providers["bailian"]})


def test_analysis_provider_protocol_requires_validated_structured_analysis_result():
    class LocalProvider:
        provider_id = "bailian"

        def validate_configuration(self, config, credential):
            return None

        def analyze(self, request, config, credential) -> StructuredAnalysis:
            raise NotImplementedError

    assert isinstance(LocalProvider(), AnalysisProvider)
    assert inspect.signature(AnalysisProvider.analyze).return_annotation is StructuredAnalysis
