from .analysis_provider import (
    ProviderCatalog,
    ProviderConfigurationError,
    ProviderDefinition,
    ProviderModel,
)


# Verification evidence is intentionally limited to the Task 3 brief.  The
# remaining providers stay visible but model-free until Task 9 real smoke tests.
CATALOG = ProviderCatalog(
    version=1,
    providers={
        "bailian": ProviderDefinition(
            providerId="bailian",
            label="阿里云百炼",
            availability="verified",
            models=(
                ProviderModel(
                    modelId="qwen3.7-flash",
                    label="Qwen3.7 Flash",
                    recommended=True,
                    vision=True,
                    structuredOutput=True,
                    regions=("cn",),
                ),
            ),
        ),
        "openai": ProviderDefinition(
            providerId="openai",
            label="OpenAI",
            availability="unverified",
        ),
        "gemini": ProviderDefinition(
            providerId="gemini",
            label="Google Gemini",
            availability="unverified",
        ),
        "doubao": ProviderDefinition(
            providerId="doubao",
            label="火山方舟豆包",
            availability="unverified",
        ),
        "claude": ProviderDefinition(
            providerId="claude",
            label="Anthropic Claude",
            availability="unverified",
        ),
    },
)


__all__ = ["CATALOG", "ProviderCatalog", "ProviderConfigurationError"]
