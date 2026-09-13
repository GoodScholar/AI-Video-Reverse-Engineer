from typing import Optional


_ALLOWED_PROVIDER_IDS = frozenset({
    "bailian", "openai", "gemini", "doubao", "claude",
})
_ALLOWED_BACKEND_MODULES = frozenset({
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.libsecret",
})
_SERVICE_NAME = "ai-video-reverse-engineer"


class SecureStorageUnavailable(RuntimeError):
    """The system credential store cannot be used safely."""


class ProviderSecretStore:
    __slots__ = ("_backend",)

    def __init__(self, backend=None):
        if backend is None:
            backend = self._default_backend()
        self._ensure_safe_backend(backend)
        self._backend = backend

    def get(self, provider_id: str) -> Optional[str]:
        return self._call("get_password", provider_id)

    def set(self, provider_id: str, candidate: str) -> None:
        self._call("set_password", provider_id, candidate)

    def delete(self, provider_id: str) -> None:
        self._call("delete_password", provider_id)

    def configured(self, provider_id: str) -> bool:
        return self.get(provider_id) is not None

    def __getstate__(self):
        return {}

    @staticmethod
    def _default_backend():
        try:
            import keyring

            return keyring.get_keyring()
        except Exception:
            raise SecureStorageUnavailable("系统安全存储不可用。") from None

    @staticmethod
    def _ensure_safe_backend(backend) -> None:
        if type(backend).__module__ not in _ALLOWED_BACKEND_MODULES:
            raise SecureStorageUnavailable("系统安全存储不可用。")

    @staticmethod
    def _account_name(provider_id: str) -> str:
        if provider_id not in _ALLOWED_PROVIDER_IDS:
            raise ValueError("不支持的分析供应商。")
        return f"analysis-provider:{provider_id}"

    def _call(self, method_name: str, provider_id: str, candidate=None):
        account_name = self._account_name(provider_id)
        method = getattr(self._backend, method_name)
        try:
            if method_name == "set_password":
                return method(_SERVICE_NAME, account_name, candidate)
            return method(_SERVICE_NAME, account_name)
        except Exception:
            raise SecureStorageUnavailable("系统安全存储不可用。") from None
