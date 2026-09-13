import json

import httpx
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


class GetFailingCredentials(InMemoryCredentials):
    def get(self, provider):
        raise SecureStorageUnavailable("系统安全存储不可用。")


class RecordingCredentials(InMemoryCredentials):
    def __init__(self):
        super().__init__()
        self.operations = []

    def get(self, provider):
        self.operations.append(("get", provider))
        return super().get(provider)

    def set(self, provider, secret):
        self.operations.append(("set", provider))
        super().set(provider, secret)

    def delete(self, provider):
        self.operations.append(("delete", provider))
        super().delete(provider)


class RollbackFailingCredentials(RecordingCredentials):
    def set(self, provider, secret):
        self.operations.append(("set", provider))
        if secret == "first-secret":
            raise SecureStorageUnavailable("系统安全存储不可用。")
        self.values[provider] = secret

    def delete(self, provider):
        self.operations.append(("delete", provider))
        raise SecureStorageUnavailable("系统安全存储不可用。")


class SingleReadCredentials(RecordingCredentials):
    def get(self, provider):
        if any(operation == "get" for operation, _ in self.operations):
            raise SecureStorageUnavailable("系统安全存储不可用。")
        return super().get(provider)


class CommitFailingSettings(AnalysisSettings):
    def commit(self, settings):
        raise OSError("配置文件写入失败")


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


@pytest.mark.parametrize(
    "value",
    [
        "http://[::1]evil/v1",
        "http://[::1]evil:1234/v1",
        "http://localhost\n.evil:1234/v1",
        "http://localhost:1234/v1?",
        "http://localhost:1234/v1#",
        "http://localhost:1234/v1\n",
    ],
)
def test_validate_loopback_base_url_rejects_control_characters_and_malformed_authorities(value):
    with pytest.raises(ValueError, match="回环主机"):
        validate_loopback_base_url(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://localhost:1234/v1/", "http://localhost:1234/v1"),
        ("https://127.0.0.1", "https://127.0.0.1"),
    ],
)
def test_normalized_loopback_base_url_is_accepted_by_httpx(value, expected):
    normalized = validate_loopback_base_url(value)

    assert normalized == expected
    assert str(httpx.URL(normalized)) == expected


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


def test_configuration_api_lists_catalog_providers_and_rejects_unknown_cloud_model(tmp_path):
    settings_path = tmp_path / "analysis-providers.json"
    settings_path.write_text(json.dumps({
        "providers": [{
            "provider": "bailian",
            "model": "qwen3.7-flash",
        }],
        "selectedProvider": "bailian",
    }), encoding="utf-8")
    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=InMemoryCredentials(),
        analysis_settings=AnalysisSettings(settings_path),
    ))

    listed = client.get("/api/analysis-providers")
    invalid = client.put("/api/analysis-providers/openai/configuration", json={
        "apiKey": "secret-value",
        "model": "user-entered-model",
    })
    valid = client.put("/api/analysis-providers/openai/configuration", json={
        "apiKey": "secret-value",
        "model": "gpt-5.6-luna",
    })

    configurations = listed.json()
    assert [item["provider"] for item in configurations] == [
        "bailian", "local_openai_compatible", "openai", "doubao", "gemini", "grok", "claude",
    ]
    assert configurations[0]["model"] == "qwen3.7-flash"
    assert configurations[0]["selectedProvider"] == "bailian"
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "invalid_analysis_model"
    assert valid.status_code == 200
    assert valid.json() == {
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "baseUrl": None,
        "credentialState": "configured",
        "selectedProvider": "openai",
    }


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
    initial = client.put("/api/analysis-providers/local_openai_compatible/configuration", json={
        "apiKey": "first-secret",
        "model": "vision-local-first",
        "baseUrl": "http://localhost:1234/v1",
    })
    assert initial.status_code == 200
    credentials.fail_writes = True

    response = client.put("/api/analysis-providers/local_openai_compatible/configuration", json={
        "apiKey": "replacement-secret",
        "model": "vision-local-replacement",
        "baseUrl": "http://localhost:1234/v1",
    })

    assert response.status_code == 503
    assert settings.get("local_openai_compatible").model == "vision-local-first"
    assert credentials.get("local_openai_compatible") == "first-secret"
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


def test_unknown_provider_is_rejected_before_any_credential_store_access(tmp_path):
    credentials = RecordingCredentials()
    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=credentials,
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
    ))

    response = client.put("/api/analysis-providers/unknown/configuration", json={
        "apiKey": "secret-value",
        "model": "unknown-model",
    })

    assert response.status_code == 400
    assert response.json() == {"detail": {
        "code": "invalid_analysis_provider_configuration",
        "message": "分析供应商配置无效。",
    }}
    assert credentials.operations == []
    assert "secret-value" not in response.text


def test_credential_read_failure_without_an_api_key_preserves_existing_configuration(tmp_path):
    settings = AnalysisSettings(tmp_path / "analysis-providers.json")
    settings.save(
        provider="local_openai_compatible",
        model="vision-local-first",
        base_url="http://localhost:1234/v1",
        selected_provider="local_openai_compatible",
    )
    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=GetFailingCredentials(),
        analysis_settings=settings,
    ))

    response = client.put("/api/analysis-providers/local_openai_compatible/configuration", json={
        "model": "vision-local-replacement",
        "baseUrl": "http://localhost:1234/v1",
    })

    assert response.status_code == 503
    assert settings.get("local_openai_compatible").model == "vision-local-first"


def test_invalid_local_url_never_attempts_a_credential_rollback(tmp_path):
    credentials = RollbackFailingCredentials()
    credentials.values["local_openai_compatible"] = "first-secret"
    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=credentials,
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
    ))

    response = client.put("/api/analysis-providers/local_openai_compatible/configuration", json={
        "apiKey": "replacement-secret",
        "model": "vision-local",
        "baseUrl": "http://[::1]evil/v1",
    })

    assert response.status_code == 400
    assert credentials.values == {"local_openai_compatible": "first-secret"}
    assert credentials.operations == []


def test_successful_configuration_uses_the_credential_read_done_before_commit(tmp_path):
    credentials = SingleReadCredentials()
    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=credentials,
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
    ))

    response = client.put("/api/analysis-providers/bailian/configuration", json={
        "apiKey": "secret-value",
        "model": "qwen3.7-flash",
    })

    assert response.status_code == 200
    assert response.json()["credentialState"] == "configured"
    assert credentials.operations == [("get", "bailian"), ("set", "bailian")]


def test_default_credential_store_initialization_failure_is_reported_safely(tmp_path, monkeypatch):
    def unavailable_store():
        raise SecureStorageUnavailable("系统安全存储不可用。")

    monkeypatch.setattr("app.main.CredentialStore", unavailable_store)
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )

    response = client.put("/api/analysis-providers/bailian/configuration", json={
        "model": "qwen3.7-flash",
    })

    assert response.status_code == 503
    assert response.json() == {"detail": {
        "code": "secure_storage_unavailable",
        "message": "系统安全存储不可用。",
    }}


@pytest.mark.parametrize("has_previous_credential", [False, True])
def test_failed_settings_commit_and_failed_credential_rollback_returns_safe_storage_error(
    tmp_path,
    caplog,
    has_previous_credential,
):
    credentials = RollbackFailingCredentials()
    if has_previous_credential:
        credentials.values["bailian"] = "first-secret"
    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=credentials,
        analysis_settings=CommitFailingSettings(tmp_path / "analysis-providers.json"),
    ))

    response = client.put("/api/analysis-providers/bailian/configuration", json={
        "apiKey": "replacement-secret",
        "model": "qwen3.7-flash",
    })

    assert response.status_code == 503
    assert response.json() == {"detail": {
        "code": "secure_storage_unavailable",
        "message": "系统安全存储不可用。",
    }}
    assert "replacement-secret" not in caplog.text
    assert str(tmp_path) not in caplog.text
