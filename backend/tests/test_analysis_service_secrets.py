import json

import pytest

from app.analysis_service_secrets import ProviderSecretStore, SecureStorageUnavailable


class FakeKeyring:
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


FakeKeyring.__module__ = "keyring.backends.macOS"


def backend_with_module(module_name):
    return type("Backend", (FakeKeyring,), {"__module__": module_name})()


@pytest.fixture
def fake_keyring():
    return FakeKeyring()


def test_secret_names_are_scoped_by_provider(fake_keyring):
    store = ProviderSecretStore(fake_keyring)

    store.set("openai", "secret-value")

    assert fake_keyring.saved == (
        "ai-video-reverse-engineer", "analysis-provider:openai", "secret-value",
    )


def test_get_configured_and_delete_use_the_provider_scoped_secret(fake_keyring):
    store = ProviderSecretStore(fake_keyring)

    assert store.get("gemini") is None
    assert store.configured("gemini") is False

    store.set("gemini", "first-secret")
    store.set("gemini", "replacement-secret")

    assert store.get("gemini") == "replacement-secret"
    assert store.configured("gemini") is True

    store.delete("gemini")

    assert fake_keyring.deleted == (
        "ai-video-reverse-engineer", "analysis-provider:gemini",
    )
    assert store.get("gemini") is None
    assert store.configured("gemini") is False


@pytest.mark.parametrize(
    "provider_id",
    ["grok", "azure-openai", "", "OpenAI", "openai ", None],
)
def test_unknown_provider_ids_are_rejected_before_accessing_keyring(fake_keyring, provider_id):
    store = ProviderSecretStore(fake_keyring)

    with pytest.raises(ValueError, match="不支持的分析供应商"):
        store.configured(provider_id)

    assert fake_keyring.saved is None


@pytest.mark.parametrize(
    "module_name",
    [
        "keyring.backends.fail",
        "keyring.backends.null",
        "keyrings.alt.file",
        "keyring.backends.file",
        "custom.in_memory_backend",
    ],
)
def test_fail_null_plaintext_and_unknown_backends_are_rejected(module_name):
    with pytest.raises(SecureStorageUnavailable):
        ProviderSecretStore(backend_with_module(module_name))


@pytest.mark.parametrize(
    "module_name",
    [
        "keyring.backends.macOS",
        "keyring.backends.Windows",
        "keyring.backends.SecretService",
        "keyring.backends.libsecret",
    ],
)
def test_recognized_operating_system_backends_are_accepted(module_name):
    store = ProviderSecretStore(backend_with_module(module_name))

    store.set("claude", "secret-value")

    assert store.configured("claude") is True


@pytest.mark.parametrize("operation", ["get", "set", "delete", "configured"])
def test_backend_errors_are_wrapped_without_exposing_secret_content(fake_keyring, operation):
    candidate = "secret-that-must-not-escape"
    fake_keyring.error = RuntimeError(f"backend rejected {candidate}")
    store = ProviderSecretStore(fake_keyring)

    with pytest.raises(SecureStorageUnavailable) as error:
        getattr(store, operation)("bailian", candidate) if operation == "set" else getattr(store, operation)("bailian")

    assert candidate not in str(error.value)


def test_store_serialization_never_contains_a_secret(fake_keyring):
    store = ProviderSecretStore(fake_keyring)
    store.set("doubao", "secret-that-must-not-serialize")

    serialized = json.dumps(store.__getstate__())

    assert "secret-that-must-not-serialize" not in serialized
