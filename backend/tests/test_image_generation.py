import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


class InMemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, account):
        return self.values.get(account)

    def set(self, account, value):
        self.values[account] = value


class UnavailableSecrets(InMemorySecrets):
    def get(self, account):
        from app.analysis_service_secrets import SecureStorageUnavailable
        raise SecureStorageUnavailable("系统安全存储不可用。")

    def set(self, account, value):
        from app.analysis_service_secrets import SecureStorageUnavailable
        raise SecureStorageUnavailable("系统安全存储不可用。")


def png_bytes(color="orange"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(stream, format="PNG")
    return stream.getvalue()


def image_bytes(image_format, color="orange"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(stream, format=image_format)
    return stream.getvalue()


def configurations(tmp_path, secrets=None):
    from app.image_generation import ImageGenerationConfigurations, ImageGenerationSettings

    return ImageGenerationConfigurations(
        ImageGenerationSettings(tmp_path / "image-generation.json"),
        secret_store=lambda: secrets or InMemorySecrets(),
    )


def test_configuration_persists_only_non_secret_fields_and_exposes_secret_state(tmp_path):
    secrets = InMemorySecrets()
    service = configurations(tmp_path, secrets)

    saved = service.save(
        base_url="https://images.example.test/v1/",
        model="gpt-image-1",
        api_key="secret-that-must-not-be-written",
    )

    assert {key: saved[key] for key in ("baseUrl", "provider", "model", "models", "protocolCapabilities", "credentialState", "revision")} == {
        "baseUrl": "https://images.example.test/v1",
        "provider": "openai-compatible",
        "model": "gpt-image-1",
        "models": ["gpt-image-1"],
        "protocolCapabilities": {"referenceImages": "single", "sizeMode": "provider-defined"},
        "credentialState": "configured",
        "revision": saved["revision"],
    }
    assert saved["configVersion"] == saved["revision"]
    assert saved["modelCatalog"] == [{
        "providerId": "default", "modelId": "gpt-image-1", "mediaType": "image", "label": "gpt-image-1",
        "configured": True, "capabilitySource": "unknown", "capabilityVersion": saved["revision"], "capabilities": {},
    }]
    assert secrets.values == {"image-generation": "secret-that-must-not-be-written"}
    persisted = (tmp_path / "image-generation.json").read_text()
    assert "secret-that-must-not-be-written" not in persisted
    assert json.loads(persisted)["baseUrl"] == "https://images.example.test/v1"


def test_multi_provider_configuration_catalog_keeps_explicit_capabilities_and_secret_accounts(tmp_path):
    secrets = InMemorySecrets()
    service = configurations(tmp_path, secrets)

    saved = service.save(providers=[
        {"providerId": "ark-main", "protocol": "volcengine-ark", "baseUrl": "https://ark.example.test/api/v3", "defaultModelId": "seedream",
         "models": [{"modelId": "seedream", "label": "Seedream", "capabilities": {"imageSizes": ["1024x1024"], "imageRatios": ["1:1"], "maxReferences": 2}}], "apiKey": "ark-secret"},
        {"providerId": "openai-main", "protocol": "openai-compatible", "baseUrl": "https://images.example.test/v1", "defaultModelId": "gpt-image-1",
         "models": [{"modelId": "gpt-image-1"}], "apiKey": "openai-secret"},
    ], active_provider_id="ark-main")

    assert saved["activeProviderId"] == "ark-main"
    assert saved["credentialState"] == "configured"
    catalog = {item["modelId"]: item for item in saved["modelCatalog"]}
    assert catalog["seedream"]["capabilitySource"] == "user-configured"
    assert catalog["seedream"]["capabilities"]["maxReferences"] == 2
    assert catalog["gpt-image-1"]["capabilitySource"] == "unknown"
    assert secrets.values == {"ark-main": "ark-secret", "openai-main": "openai-secret"}
    configured, credential = service.configured_snapshot({
        "baseUrl": "https://images.example.test/v1", "provider": "openai-compatible", "providerId": "openai-main",
        "model": "gpt-image-1", "revision": saved["revision"],
    })
    assert configured["provider"] == "openai-compatible"
    assert credential == "openai-secret"
    secrets.values.pop("openai-main")
    refreshed_catalog = {item["modelId"]: item for item in service.get()["modelCatalog"]}
    assert refreshed_catalog["gpt-image-1"]["configured"] is False

def test_configuration_reader_treats_unavailable_keyring_as_unconfigured(tmp_path):
    from app.image_generation import ImageGenerationConfigurations, ImageGenerationSettings

    settings = ImageGenerationSettings(tmp_path / "image-generation.json")
    settings.save(base_url="https://images.example.test/v1", model="gpt-image-1")
    service = ImageGenerationConfigurations(settings, secret_store=lambda: UnavailableSecrets())

    assert service.get()["credentialState"] == "unconfigured"


def test_legacy_configuration_defaults_to_the_openai_compatible_protocol(tmp_path):
    from app.image_generation import ImageGenerationSettings

    path = tmp_path / "image-generation.json"
    path.write_text(json.dumps({
        "baseUrl": "https://images.example.test/v1", "model": "gpt-image-1", "revision": "legacy-revision",
    }), encoding="utf-8")

    restored = ImageGenerationSettings(path).get()
    assert {key: restored[key] for key in ("baseUrl", "provider", "model", "models", "revision")} == {
        "baseUrl": "https://images.example.test/v1", "provider": "openai-compatible",
        "model": "gpt-image-1", "models": ["gpt-image-1"], "revision": "legacy-revision",
    }
    assert restored["activeProviderId"] == "default"


@pytest.mark.parametrize(
    "value",
    [
        "http://images.example.test/v1",
        "https://user:pass@images.example.test/v1",
        "https://images.example.test/v1?next=https://elsewhere.test",
        "https://images.example.test/v1#fragment",
        "https://images.example.test/v1\nX-Header: injected",
    ],
)
def test_configuration_rejects_unsafe_cloud_endpoint(value, tmp_path):
    service = configurations(tmp_path)

    with pytest.raises(ValueError, match="图片生成服务地址"):
        service.save(base_url=value, model="gpt-image-1", api_key="test-key")


def test_configuration_allows_http_only_for_loopback_development(tmp_path):
    service = configurations(tmp_path)

    assert service.save(
        base_url="http://127.0.0.1:8080/v1/", model="gpt-image-1", api_key="test-key",
    )["baseUrl"] == "http://127.0.0.1:8080/v1"


def test_configuration_rejects_endpoint_change_without_replacing_credential(tmp_path):
    service = configurations(tmp_path)
    saved = service.save(base_url="https://first.example.test/v1", model="gpt-image-1", api_key="old-key")

    with pytest.raises(ValueError, match="更换图片生成服务地址"):
        service.save(
            base_url="https://second.example.test/v1", model="gpt-image-1",
            expected_revision=saved["revision"],
        )


def test_configuration_requires_current_revision_after_the_first_save(tmp_path):
    from app.image_generation import ImageGenerationError

    service = configurations(tmp_path)
    first = service.save(base_url="https://images.example.test/v1", model="gpt-image-1", api_key="first-key")

    with pytest.raises(ImageGenerationError, match="configuration_changed"):
        service.save(base_url="https://images.example.test/v1", model="gpt-image-1", api_key="second-key")
    with pytest.raises(ImageGenerationError, match="configuration_changed"):
        service.save(
            base_url="https://images.example.test/v1", model="gpt-image-1",
            api_key="second-key", expected_revision="stale-revision",
        )

    updated = service.save(
        base_url="https://images.example.test/v1", model="gpt-image-1",
        api_key="second-key", expected_revision=first["revision"],
    )
    assert updated["revision"] != first["revision"]


def test_first_write_uses_none_as_the_expected_empty_revision_and_rejects_a_second_write(tmp_path):
    from app.image_generation import ImageGenerationError

    service = configurations(tmp_path)
    service.save(
        base_url="https://images.example.test/v1", model="gpt-image-1", api_key="first-key",
        expected_revision=None,
    )

    with pytest.raises(ImageGenerationError, match="configuration_changed"):
        service.save(
            base_url="https://images.example.test/v1", model="gpt-image-1", api_key="second-key",
            expected_revision=None,
        )


def test_configuration_api_never_returns_api_key_and_reports_safe_write_failure(tmp_path):
    from app.image_generation_api import create_image_generation_router

    secrets = UnavailableSecrets()
    app = FastAPI()
    app.include_router(create_image_generation_router(configurations(tmp_path, secrets)))
    client = TestClient(app)

    assert client.get("/api/image-generation/configuration").json()["credentialState"] == "unconfigured"
    response = client.put("/api/image-generation/configuration", json={
        "baseUrl": "https://images.example.test/v1", "model": "gpt-image-1", "apiKey": "not-returned",
    })

    assert response.status_code == 503
    assert "not-returned" not in response.text


def test_configuration_api_updates_and_returns_only_public_configuration(tmp_path):
    from app.image_generation_api import create_image_generation_router

    secrets = InMemorySecrets()
    app = FastAPI()
    app.include_router(create_image_generation_router(configurations(tmp_path, secrets)))
    client = TestClient(app)

    response = client.put("/api/image-generation/configuration", json={
        "baseUrl": "https://images.example.test/v1/", "provider": "volcengine-ark",
        "model": "ep-20260930-seedream", "models": ["ep-20260930-seedream", "ep-20260930-seedream-edit"],
        "apiKey": "private-key",
    })

    assert response.status_code == 200
    assert response.json()["credentialState"] == "configured"
    assert response.json()["baseUrl"] == "https://images.example.test/v1"
    assert response.json()["provider"] == "volcengine-ark"
    assert response.json()["models"] == ["ep-20260930-seedream", "ep-20260930-seedream-edit"]
    assert "apiKey" not in response.json()
    assert "private-key" not in response.text


@pytest.mark.parametrize("revision", [pytest.param("omitted", id="omitted"), None, "stale-revision"])
def test_configuration_api_rejects_an_update_without_the_current_revision(tmp_path, revision):
    from app.image_generation_api import create_image_generation_router

    app = FastAPI()
    app.include_router(create_image_generation_router(configurations(tmp_path, InMemorySecrets())))
    client = TestClient(app)
    first = client.put("/api/image-generation/configuration", json={
        "baseUrl": "https://images.example.test/v1", "model": "gpt-image-1", "apiKey": "first-key",
    })
    assert first.status_code == 200
    replacement = {
        "baseUrl": "https://images.example.test/v1", "model": "gpt-image-1", "apiKey": "second-key",
    }
    if revision != "omitted":
        replacement["revision"] = revision

    response = client.put("/api/image-generation/configuration", json=replacement)

    assert response.status_code == 409


def test_configuration_api_accepts_the_current_revision(tmp_path):
    from app.image_generation_api import create_image_generation_router

    app = FastAPI()
    app.include_router(create_image_generation_router(configurations(tmp_path, InMemorySecrets())))
    client = TestClient(app)
    first = client.put("/api/image-generation/configuration", json={
        "baseUrl": "https://images.example.test/v1", "model": "gpt-image-1", "apiKey": "first-key",
    }).json()

    response = client.put("/api/image-generation/configuration", json={
        "baseUrl": "https://images.example.test/v1", "model": "gpt-image-1", "apiKey": "second-key",
        "revision": first["revision"],
    })

    assert response.status_code == 200
    assert response.json()["revision"] != first["revision"]


def test_generation_uses_snapshot_and_returns_validated_png_without_remote_url_download(tmp_path):
    from app.image_generation import ImageGenerationService

    secrets = InMemorySecrets()
    config = configurations(tmp_path, secrets)
    saved = config.save(base_url="https://images.example.test/v1", model="gpt-image-1", api_key="test-key")
    captured = []
    result = png_bytes()

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(result).decode("ascii"),
            "url": "https://untrusted.example.test/never-fetch-this.png",
        }]})

    service = ImageGenerationService(
        config, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    output = service.generate({
        "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": "gpt-image-1", "revision": saved["revision"],
        "prompt": "一只橙色猫", "size": "1024x1024",
    })

    assert Image.open(io.BytesIO(output)).format == "PNG"
    assert captured[0].url == httpx.URL("https://images.example.test/v1/images/generations")
    assert json.loads(captured[0].content) == {"model": "gpt-image-1", "prompt": "一只橙色猫", "n": 1, "size": "1024x1024"}
    assert captured[0].headers["authorization"] == "Bearer test-key"


@pytest.mark.parametrize(
    ("suffix", "image_format", "mime_type"),
    [("jpg", "JPEG", "image/jpeg"), ("webp", "WEBP", "image/webp")],
)
def test_generation_uses_edits_multipart_with_the_actual_input_image_mime(tmp_path, suffix, image_format, mime_type):
    from app.image_generation import ImageGenerationService

    secrets = InMemorySecrets()
    config = configurations(tmp_path, secrets)
    saved = config.save(base_url="https://images.example.test/v1", model="gpt-image-1", api_key="test-key")
    result = png_bytes()
    captured = []
    service = ImageGenerationService(config, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: captured.append(request) or httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(result).decode("ascii"),
        }]}),
    )))
    source = tmp_path / f"source.{suffix}"
    source.write_bytes(image_bytes(image_format, "blue"))

    service.generate({
        "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": "gpt-image-1", "revision": saved["revision"],
        "prompt": "改成蓝色", "size": "1024x1024",
    }, input_image=source)

    assert captured[0].url == httpx.URL("https://images.example.test/v1/images/edits")
    assert captured[0].headers["content-type"].startswith("multipart/form-data;")
    assert b'name="image"' in captured[0].content
    assert b'name="model"' in captured[0].content
    assert f"Content-Type: {mime_type}".encode() in captured[0].content


def test_generation_rejects_a_stale_snapshot_before_sending_a_billable_request(tmp_path):
    from app.image_generation import ImageGenerationError, ImageGenerationService

    secrets = InMemorySecrets()
    config = configurations(tmp_path, secrets)
    first = config.save(base_url="https://images.example.test/v1", model="gpt-image-1", api_key="first-key")
    config.save(
        base_url="https://images.example.test/v1", model="gpt-image-1", api_key="second-key",
        expected_revision=first["revision"],
    )
    service = ImageGenerationService(config, client_factory=lambda: pytest.fail("不得发送请求"))

    with pytest.raises(ImageGenerationError, match="configuration_changed") as error:
        service.generate({
            "baseUrl": first["baseUrl"], "provider": first["provider"], "model": "gpt-image-1", "revision": first["revision"],
            "prompt": "一朵花", "size": "1024x1024",
        })

    assert error.value.outcome_unknown is False


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"error": "not shown"}),
    httpx.Response(200, json={"data": [{"b64_json": "not base64"}]}),
])
def test_generation_marks_transport_or_ambiguous_success_as_unknown_without_exposing_response_body(tmp_path, response):
    from app.image_generation import ImageGenerationError, ImageGenerationService

    secrets = InMemorySecrets()
    config = configurations(tmp_path, secrets)
    saved = config.save(base_url="https://images.example.test/v1", model="gpt-image-1", api_key="test-key")
    service = ImageGenerationService(config, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: response,
    )))

    with pytest.raises(ImageGenerationError) as error:
        service.generate({
            "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": "gpt-image-1", "revision": saved["revision"],
            "prompt": "一朵花", "size": "1024x1024",
        })

    assert error.value.outcome_unknown is True
    assert "not shown" not in str(error.value)


def test_generation_marks_a_confirmed_4xx_as_failed(tmp_path):
    from app.image_generation import ImageGenerationError, ImageGenerationService

    secrets = InMemorySecrets()
    config = configurations(tmp_path, secrets)
    saved = config.save(base_url="https://images.example.test/v1", model="gpt-image-1", api_key="test-key")
    service = ImageGenerationService(config, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={"error": "not shown"}),
    )))

    with pytest.raises(ImageGenerationError, match="request_failed") as error:
        service.generate({
            "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": "gpt-image-1", "revision": saved["revision"],
            "prompt": "一朵花", "size": "1024x1024",
        })

    assert error.value.outcome_unknown is False


def test_configuration_keeps_provider_when_an_existing_save_omits_optional_protocol_fields(tmp_path):
    secrets = InMemorySecrets()
    config = configurations(tmp_path, secrets)
    first = config.save(
        base_url="https://ark.example.test/api/v3",
        provider="volcengine-ark",
        model="ep-20260930-seedream",
        models=["ep-20260930-seedream", "ep-20260930-seedream-edit"],
        api_key="test-key",
    )

    saved = config.save(
        base_url="https://ark.example.test/api/v3",
        model="ep-20260930-seedream",
        expected_revision=first["revision"],
    )

    assert saved["provider"] == "volcengine-ark"
    assert saved["models"] == ["ep-20260930-seedream", "ep-20260930-seedream-edit"]
    assert saved["protocolCapabilities"] == {
        "referenceImages": "multiple", "sizeMode": "custom-pixels", "sizePattern": "<width>x<height>",
    }


def test_configuration_changes_revision_when_model_whitelist_changes(tmp_path):
    config = configurations(tmp_path, InMemorySecrets())
    first = config.save(base_url="https://images.example.test/v1", model="gpt-image-1", api_key="test-key")

    saved = config.save(
        base_url="https://images.example.test/v1",
        model="gpt-image-1",
        models=["gpt-image-1", "gpt-image-1-mini"],
        expected_revision=first["revision"],
    )

    assert saved["models"] == ["gpt-image-1", "gpt-image-1-mini"]
    assert saved["revision"] != first["revision"]


def test_configuration_provider_migration_requires_a_new_credential_and_exposes_no_secret(tmp_path):
    secrets = InMemorySecrets()
    config = configurations(tmp_path, secrets)
    first = config.save(base_url="https://images.example.test/v1", model="gpt-image-1", api_key="old-key")

    with pytest.raises(ValueError, match="服务地址"):
        config.save(
            base_url="https://images.example.test/v1", provider="volcengine-ark", model="ep-20260930-seedream",
            models=["ep-20260930-seedream"],
            expected_revision=first["revision"],
        )
    saved = config.save(
        base_url="https://images.example.test/v1", provider="volcengine-ark", model="ep-20260930-seedream",
        models=["ep-20260930-seedream"],
        api_key="new-key", expected_revision=first["revision"],
    )

    assert saved["provider"] == "volcengine-ark"
    assert "new-key" not in json.dumps(saved)
    assert secrets.values == {"image-generation": "new-key"}


def test_generation_posts_all_ark_reference_images_as_data_urls_and_requests_base64(tmp_path):
    from app.image_generation import ImageGenerationService

    secrets = InMemorySecrets()
    config = configurations(tmp_path, secrets)
    saved = config.save(
        base_url="https://ark.example.test/api/v3",
        provider="volcengine-ark",
        model="ep-20260930-seedream",
        models=["ep-20260930-seedream", "ep-20260930-seedream-edit"],
        api_key="test-key",
    )
    sources = [tmp_path / "one.png", tmp_path / "two.png"]
    sources[0].write_bytes(png_bytes("blue"))
    sources[1].write_bytes(png_bytes("green"))
    captured = []
    result = png_bytes()
    service = ImageGenerationService(config, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: captured.append(request) or httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(result).decode("ascii"),
        }]}),
    )))

    service.generate({
        "baseUrl": saved["baseUrl"], "provider": "volcengine-ark", "model": "ep-20260930-seedream-edit",
        "revision": saved["revision"], "prompt": "合并两张参考图", "size": "1777x1234",
    }, input_images=sources)

    assert captured[0].url == httpx.URL("https://ark.example.test/api/v3/images/generations")
    assert captured[0].headers["content-type"] == "application/json"
    payload = json.loads(captured[0].content)
    assert payload["model"] == "ep-20260930-seedream-edit"
    assert payload["prompt"] == "合并两张参考图"
    assert payload["size"] == "1777x1234"
    assert payload["response_format"] == "b64_json"
    assert isinstance(payload["image"], list) and len(payload["image"]) == 2
    assert all(value.startswith("data:image/png;base64,") for value in payload["image"])
    assert captured[0].headers["authorization"] == "Bearer test-key"


def test_ark_text_to_image_requests_base64_without_reference_image(tmp_path):
    from app.image_generation import ImageGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(
        base_url="https://ark.example.test/api/v3", provider="volcengine-ark",
        model="ep-20260930-seedream", api_key="test-key",
    )
    captured = []
    service = ImageGenerationService(config, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: captured.append(request) or httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(png_bytes()).decode("ascii"),
        }]}),
    )))

    service.generate({
        "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": saved["model"],
        "revision": saved["revision"], "prompt": "一朵花", "size": "10000x1536",
    })

    assert json.loads(captured[0].content) == {
        "model": "ep-20260930-seedream", "prompt": "一朵花", "size": "10000x1536", "response_format": "b64_json",
    }


def test_generation_rejects_unconfigured_snapshot_model_before_request(tmp_path):
    from app.image_generation import ImageGenerationError, ImageGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(
        base_url="https://ark.example.test/api/v3", provider="volcengine-ark",
        model="ep-20260930-seedream", models=["ep-20260930-seedream"], api_key="test-key",
    )
    service = ImageGenerationService(config, client_factory=lambda: pytest.fail("不得发送请求"))

    with pytest.raises(ImageGenerationError, match="configuration_changed"):
        service.generate({
            "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": "arbitrary-model",
            "revision": saved["revision"], "prompt": "一朵花", "size": "1024x1024",
        })


def test_openai_compatible_rejects_multiple_reference_images_without_sending_request(tmp_path):
    from app.image_generation import ImageGenerationError, ImageGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(base_url="https://images.example.test/v1", model="gpt-image-1", api_key="test-key")
    sources = [tmp_path / "one.png", tmp_path / "two.png"]
    for source in sources:
        source.write_bytes(png_bytes())
    service = ImageGenerationService(config, client_factory=lambda: pytest.fail("不得发送请求"))

    with pytest.raises(ImageGenerationError, match="multiple_input_images_unsupported"):
        service.generate({
            "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": saved["model"],
            "revision": saved["revision"], "prompt": "一朵花", "size": "1024x1024",
        }, input_images=sources)


def test_ark_reference_count_budget_blocks_request_before_base64_conversion(tmp_path):
    from app.image_generation import ImageGenerationError, ImageGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(
        base_url="https://ark.example.test/api/v3", provider="volcengine-ark",
        model="ep-20260930-seedream", api_key="test-key",
    )
    sources = []
    for index in range(33):
        source = tmp_path / "reference-{}.png".format(index)
        source.write_bytes(png_bytes())
        sources.append(source)
    service = ImageGenerationService(config, client_factory=lambda: pytest.fail("不得发送请求"))

    with pytest.raises(ImageGenerationError, match="input_budget_exceeded") as error:
        service.generate({
            "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": saved["model"],
            "revision": saved["revision"], "prompt": "合并参考图", "size": "1024x1024",
        }, input_images=sources)

    assert error.value.outcome_unknown is False


def test_ark_total_input_byte_budget_blocks_request_before_base64_conversion(tmp_path, monkeypatch):
    import app.image_generation as image_generation
    from app.image_generation import ImageGenerationError, ImageGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(
        base_url="https://ark.example.test/api/v3", provider="volcengine-ark",
        model="ep-20260930-seedream", api_key="test-key",
    )
    sources = [tmp_path / "one.png", tmp_path / "two.png"]
    for source in sources:
        source.write_bytes(png_bytes())
    monkeypatch.setattr(image_generation, "_MAX_TOTAL_INPUT_BYTES", sum(source.stat().st_size for source in sources) - 1)
    service = ImageGenerationService(config, client_factory=lambda: pytest.fail("不得发送请求"))

    with pytest.raises(ImageGenerationError, match="input_budget_exceeded"):
        service.generate({
            "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": saved["model"],
            "revision": saved["revision"], "prompt": "合并参考图", "size": "1024x1024",
        }, input_images=sources)


def test_ark_reference_budget_boundaries_send_all_valid_images(tmp_path, monkeypatch):
    import app.image_generation as image_generation
    from app.image_generation import ImageGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(
        base_url="https://ark.example.test/api/v3", provider="volcengine-ark",
        model="ep-20260930-seedream", api_key="test-key",
    )
    source = tmp_path / "reference.png"
    source.write_bytes(png_bytes())
    sources = [source] * 32
    monkeypatch.setattr(image_generation, "_MAX_TOTAL_INPUT_BYTES", source.stat().st_size * len(sources))
    captured = []
    service = ImageGenerationService(config, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: captured.append(request) or httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(png_bytes()).decode("ascii"),
        }]}),
    )))

    service.generate({
        "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": saved["model"],
        "revision": saved["revision"], "prompt": "合并参考图", "size": "1024x1024",
    }, input_images=sources)

    assert len(json.loads(captured[0].content)["image"]) == 32


def test_missing_reference_image_is_invalid_input_not_an_unknown_billable_outcome(tmp_path):
    from app.image_generation import ImageGenerationError, ImageGenerationService

    config = configurations(tmp_path, InMemorySecrets())
    saved = config.save(
        base_url="https://ark.example.test/api/v3", provider="volcengine-ark",
        model="ep-20260930-seedream", api_key="test-key",
    )
    service = ImageGenerationService(config, client_factory=lambda: pytest.fail("不得发送请求"))

    with pytest.raises(ImageGenerationError, match="invalid_input") as error:
        service.generate({
            "baseUrl": saved["baseUrl"], "provider": saved["provider"], "model": saved["model"],
            "revision": saved["revision"], "prompt": "合并参考图", "size": "1024x1024",
        }, input_images=[tmp_path / "missing.png"])

    assert error.value.outcome_unknown is False


def test_configuration_api_accepts_multi_provider_entries_without_returning_keys(tmp_path):
    from app.image_generation_api import create_image_generation_router

    secrets = InMemorySecrets()
    app = FastAPI()
    app.include_router(create_image_generation_router(configurations(tmp_path, secrets)))
    client = TestClient(app)
    response = client.put("/api/image-generation/configuration", json={
        "providers": [
            {"providerId": "ark-main", "protocol": "volcengine-ark", "baseUrl": "https://ark.example.test/api/v3", "defaultModelId": "seedream",
             "models": [{"modelId": "seedream", "capabilities": {"imageSizes": ["1024x1024"], "maxReferences": 2}}], "apiKey": "ark-secret"},
            {"providerId": "openai-main", "protocol": "openai-compatible", "baseUrl": "https://images.example.test/v1", "defaultModelId": "gpt-image-1",
             "models": [{"modelId": "gpt-image-1"}], "apiKey": "openai-secret"},
        ], "activeProviderId": "ark-main",
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["activeProviderId"] == "ark-main"
    assert body["configVersion"] == body["revision"]
    assert "apiKey" not in json.dumps(body)
    assert {item["providerId"] for item in body["providers"]} == {"ark-main", "openai-main"}



def test_multi_provider_rejects_legacy_credential_account_alias(tmp_path):
    service = configurations(tmp_path, InMemorySecrets())
    with pytest.raises(ValueError, match="供应商标识"):
        service.save(providers=[
            {"providerId": "default", "protocol": "openai-compatible", "baseUrl": "https://images.example.test/v1", "defaultModelId": "gpt-image-1", "models": [{"modelId": "gpt-image-1"}], "apiKey": "default-key"},
            {"providerId": "image-generation", "protocol": "volcengine-ark", "baseUrl": "https://ark.example.test/api/v3", "defaultModelId": "seedream", "models": [{"modelId": "seedream"}], "apiKey": "second-key"},
        ], active_provider_id="default")


def test_configuration_api_validation_failure_never_echoes_nested_provider_key(tmp_path):
    from app.image_generation_api import create_image_generation_router

    app = FastAPI()
    app.include_router(create_image_generation_router(configurations(tmp_path, InMemorySecrets())))
    response = TestClient(app).put("/api/image-generation/configuration", json={
        "baseUrl": "https://images.example.test/v1", "model": "gpt-image-1",
        "providers": [{
            "providerId": "openai-main", "protocol": "openai-compatible", "baseUrl": "https://images.example.test/v1",
            "defaultModelId": "gpt-image-1", "models": [{"modelId": "gpt-image-1"}], "apiKey": "TOPSECRET",
        }],
    })

    assert response.status_code == 422
    assert "TOPSECRET" not in response.text
    assert "apiKey" not in response.text


def test_configuration_api_nested_validation_failure_never_echoes_provider_key(tmp_path):
    from app.image_generation_api import create_image_generation_router

    app = FastAPI()
    app.include_router(create_image_generation_router(configurations(tmp_path, InMemorySecrets())))
    response = TestClient(app).put("/api/image-generation/configuration", json={
        "providers": [{
            "providerId": "openai-main", "protocol": "openai-compatible", "baseUrl": "https://images.example.test/v1",
            "models": [{"modelId": "gpt-image-1"}], "apiKey": "NESTEDTOPSECRET",
        }],
    })

    assert response.status_code == 422
    assert "NESTEDTOPSECRET" not in response.text
    assert "apiKey" not in response.text


def test_multi_provider_rejects_catalog_image_size_outside_canvas_safety_limits(tmp_path):
    service = configurations(tmp_path, InMemorySecrets())

    with pytest.raises(ValueError, match="能力"):
        service.save(providers=[{
            "providerId": "openai-main", "protocol": "openai-compatible", "baseUrl": "https://images.example.test/v1",
            "defaultModelId": "gpt-image-1", "models": [{"modelId": "gpt-image-1", "capabilities": {"imageSizes": ["16384x16384"]}}],
            "apiKey": "secret",
        }], active_provider_id="openai-main")
