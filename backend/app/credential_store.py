from .analysis_service_secrets import ProviderSecretStore
from .provider_models import PROVIDER_IDS


ANALYSIS_PROVIDER_IDS = frozenset(PROVIDER_IDS)


class CredentialStore(ProviderSecretStore):
    """Stores analysis-provider credentials in the system keyring only."""

    def __init__(self, backend=None):
        super().__init__(
            backend,
            service_name="ai-video-reverse-engineer.analysis-provider",
            allowed_provider_ids=ANALYSIS_PROVIDER_IDS,
            account_prefix="",
        )
