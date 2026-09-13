from .analysis_provider import ProviderCatalog, ProviderConfigurationError, ProviderDefinition, ProviderModel
from .provider_models import PROVIDER_IDS, provider_for


def _compatibility_provider(provider_id: str) -> ProviderDefinition:
    source = provider_for(provider_id)
    models = ()
    if source.compatibilityAvailability == "verified":
        models = tuple(
            ProviderModel(
                modelId=model.id,
                label=model.label,
                recommended=index == 0,
                vision=True,
                structuredOutput=True,
                regions=source.compatibilityRegions,
            )
            for index, model in enumerate(source.models)
        )
    return ProviderDefinition(
        providerId=source.id,
        label=source.label,
        availability=source.compatibilityAvailability,
        models=models,
    )


CATALOG = ProviderCatalog(
    version=1,
    providers={provider_id: _compatibility_provider(provider_id) for provider_id in PROVIDER_IDS},
)


__all__ = ["CATALOG", "ProviderCatalog", "ProviderConfigurationError"]
