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
    __slots__ = ("_account_prefix", "_allowed_provider_ids", "_backend", "_service_name")

    def __init__(
        self,
        backend=None,
        *,
        service_name: str = _SERVICE_NAME,
        allowed_provider_ids=_ALLOWED_PROVIDER_IDS,
        account_prefix: str = "analysis-provider:",
    ):
        if backend is None:
            backend = self._default_backend()
        self._ensure_safe_backend(backend)
        self._service_name = service_name
        self._allowed_provider_ids = frozenset(allowed_provider_ids)
        self._account_prefix = account_prefix
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

    def _account_name(self, provider_id: str) -> str:
        if provider_id not in self._allowed_provider_ids:
            raise ValueError("不支持的分析供应商。")
        return f"{self._account_prefix}{provider_id}"

    def _call(self, method_name: str, provider_id: str, candidate=None):
        account_name = self._account_name(provider_id)
        method = getattr(self._backend, method_name)
        try:
            if method_name == "set_password":
                return method(self._service_name, account_name, candidate)
            return method(self._service_name, account_name)
        except Exception:
            raise SecureStorageUnavailable("系统安全存储不可用。") from None
