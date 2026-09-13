from .analysis_service_secrets import ProviderSecretStore


ANALYSIS_PROVIDER_IDS = frozenset({
    "bailian",
    "local_openai_compatible",
})


class CredentialStore(ProviderSecretStore):
    """Stores analysis-provider credentials in the system keyring only."""

    def __init__(self, backend=None):
        super().__init__(
            backend,
            service_name="ai-video-reverse-engineer.analysis-provider",
            allowed_provider_ids=ANALYSIS_PROVIDER_IDS,
            account_prefix="",
        )
