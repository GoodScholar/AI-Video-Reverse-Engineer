import json

import pytest
from fastapi.testclient import TestClient

from app.analysis_service_secrets import SecureStorageUnavailable
from app.analysis_settings import AnalysisSettings, validate_loopback_base_url
from app.main import create_app


class InMemoryCredentials:
    def __init__(self):
        self.values = {}

    def get(self, provider):
        return self.values.get(provider)

    def set(self, provider, secret):
        self.values[provider] = secret

    def delete(self, provider):
        self.values.pop(provider, None)


class UnavailableCredentials(InMemoryCredentials):
    def set(self, provider, secret):
        raise SecureStorageUnavailable("系统安全存储不可用。")


class FailingAfterInitialCredentialWrite(InMemoryCredentials):
    def __init__(self):
        super().__init__()
        self.fail_writes = False

    def set(self, provider, secret):
        if self.fail_writes:
            raise SecureStorageUnavailable("系统安全存储不可用。")
        super().set(provider, secret)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://localhost:1234/v1/", "http://localhost:1234/v1"),
        ("https://127.0.0.1", "https://127.0.0.1"),
        ("http://[::1]:8080/", "http://[::1]:8080"),
    ],
)
def test_validate_loopback_base_url_accepts_only_normalized_http_loopback_addresses(value, expected):
    assert validate_loopback_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "ftp://localhost:8080",
        "https://example.com",
        "http://localhost:8080?token=secret-value",
        "http://localhost:8080/#fragment",
        "http://user:password@localhost:8080",
        "http://localhost:bad-port",
    ],
)
def test_validate_loopback_base_url_rejects_remote_or_ambiguous_addresses(value):
    with pytest.raises(ValueError, match="回环主机"):
        validate_loopback_base_url(value)


def test_analysis_settings_persists_only_non_sensitive_provider_data_atomically(tmp_path):
    path = tmp_path / "analysis-providers.json"
    settings = AnalysisSettings(path)

    settings.save(
        provider="local_openai_compatible",
        model="vision-local",
        base_url="http://localhost:1234/v1/",
        selected_provider="local_openai_compatible",
    )

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted == {
        "providers": [{
            "provider": "local_openai_compatible",
            "model": "vision-local",
            "baseUrl": "http://localhost:1234/v1",
        }],
        "selectedProvider": "local_openai_compatible",
    }
    assert "apiKey" not in json.dumps(persisted)
    assert AnalysisSettings(path).get("local_openai_compatible").model == "vision-local"


def test_configuration_response_never_returns_or_persists_secret(tmp_path):
    credentials = InMemoryCredentials()
    settings_path = tmp_path / "analysis-providers.json"
    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=credentials,
        analysis_settings=AnalysisSettings(settings_path),
    ))

    response = client.put("/api/analysis-providers/bailian/configuration", json={
        "apiKey": "secret-value",
        "model": "qwen3.7-flash",
    })

    assert response.status_code == 200
    assert "secret-value" not in response.text
    assert response.json() == {
        "provider": "bailian",
        "model": "qwen3.7-flash",
        "baseUrl": None,
        "credentialState": "configured",
        "selectedProvider": "bailian",
    }
    assert "secret-value" not in settings_path.read_text(encoding="utf-8")
    assert credentials.values == {"bailian": "secret-value"}


def test_configuration_reports_secure_storage_unavailability_without_secret(tmp_path):
    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=UnavailableCredentials(),
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
    ))

    response = client.put("/api/analysis-providers/bailian/configuration", json={
        "apiKey": "secret-value",
        "model": "qwen3.7-flash",
    })

    assert response.status_code == 503
    assert response.json() == {"detail": {
        "code": "secure_storage_unavailable",
        "message": "系统安全存储不可用。",
    }}
    assert "secret-value" not in response.text


def test_failed_credential_write_preserves_the_previous_non_sensitive_configuration(tmp_path):
    credentials = FailingAfterInitialCredentialWrite()
    settings = AnalysisSettings(tmp_path / "analysis-providers.json")
    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=credentials,
        analysis_settings=settings,
    ))
    initial = client.put("/api/analysis-providers/bailian/configuration", json={
        "apiKey": "first-secret",
        "model": "first-model",
    })
    assert initial.status_code == 200
    credentials.fail_writes = True

    response = client.put("/api/analysis-providers/bailian/configuration", json={
        "apiKey": "replacement-secret",
        "model": "replacement-model",
    })

    assert response.status_code == 503
    assert settings.get("bailian").model == "first-model"
    assert credentials.get("bailian") == "first-secret"
    assert "replacement-secret" not in response.text


def test_listing_configurations_maps_settings_read_failures_to_a_stable_error(tmp_path):
    settings_path = tmp_path / "analysis-providers.json"
    settings_path.mkdir()
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            credential_store=InMemoryCredentials(),
            analysis_settings=AnalysisSettings(settings_path),
        ),
        raise_server_exceptions=False,
    )

    response = client.get("/api/analysis-providers")

    assert response.status_code == 503
    assert response.json() == {"detail": {
        "code": "analysis_settings_unavailable",
        "message": "分析供应商设置不可用。",
    }}
