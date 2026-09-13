import pytest

from app.analysis_service_secrets import SecureStorageUnavailable
from app.credential_store import CredentialStore


class InMemorySystemKeyring:
    def __init__(self):
        self.values = {}
        self.saved = None
        self.deleted = None
        self.error = None

    def get_password(self, service_name, username):
        if self.error:
            raise self.error
        return self.values.get((service_name, username))

    def set_password(self, service_name, username, password):
        if self.error:
            raise self.error
        self.values[(service_name, username)] = password
        self.saved = (service_name, username, password)

    def delete_password(self, service_name, username):
        if self.error:
            raise self.error
        self.values.pop((service_name, username), None)
        self.deleted = (service_name, username)


InMemorySystemKeyring.__module__ = "keyring.backends.macOS"


def test_credential_store_scopes_system_keyring_entries_to_analysis_provider_service():
    backend = InMemorySystemKeyring()
    store = CredentialStore(backend=backend)

    store.set("bailian", "secret-value")

    assert backend.saved == (
        "ai-video-reverse-engineer.analysis-provider", "bailian", "secret-value",
    )
    assert store.get("bailian") == "secret-value"

    store.delete("bailian")

    assert backend.deleted == (
        "ai-video-reverse-engineer.analysis-provider", "bailian",
    )
    assert store.get("bailian") is None


@pytest.mark.parametrize("provider", ["", "bailian ", None])
def test_credential_store_rejects_unavailable_or_unknown_provider_before_secret_access(provider):
    store = CredentialStore(backend=InMemorySystemKeyring())

    with pytest.raises(ValueError, match="不支持的分析供应商"):
        store.get(provider)


@pytest.mark.parametrize("provider", ["bailian", "local_openai_compatible", "openai", "doubao", "gemini", "grok", "claude"])
def test_credential_store_accepts_every_catalog_provider(provider):
    backend = InMemorySystemKeyring()
    store = CredentialStore(backend=backend)

    store.set(provider, "secret-value")

    assert store.get(provider) == "secret-value"


def test_credential_store_converts_system_store_errors_without_exposing_secret():
    backend = InMemorySystemKeyring()
    backend.error = RuntimeError("failed to store secret-value")
    store = CredentialStore(backend=backend)

    with pytest.raises(SecureStorageUnavailable) as error:
        store.set("local_openai_compatible", "secret-value")

    assert "secret-value" not in str(error.value)
