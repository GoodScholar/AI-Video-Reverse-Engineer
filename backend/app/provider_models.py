from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field


CATALOG_VERSION = "2026-09-14.2"


ProviderId = Literal[
    "bailian",
    "local_openai_compatible",
    "openai",
    "doubao",
    "gemini",
    "grok",
    "claude",
    "chatanywhere",
]


class ProviderModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class ProviderCatalogEntry(BaseModel):
    """The only source for provider identity, labels, and fixed models."""

    model_config = ConfigDict(frozen=True)

    id: ProviderId
    label: str = Field(min_length=1)
    models: tuple[ProviderModel, ...]
    compatibilityAvailability: Literal["verified", "unverified"]
    compatibilityRegions: tuple[str, ...] = Field(min_length=1)


PROVIDER_CATALOG: Mapping[ProviderId, ProviderCatalogEntry] = MappingProxyType({
    "bailian": ProviderCatalogEntry(
        id="bailian", label="阿里云百炼", models=(ProviderModel(id="qwen3.7-flash", label="Qwen 3.7 Flash"),),
        compatibilityAvailability="verified", compatibilityRegions=("cn",),
    ),
    "local_openai_compatible": ProviderCatalogEntry(
        id="local_openai_compatible", label="本地 OpenAI 兼容服务", models=(),
        compatibilityAvailability="unverified", compatibilityRegions=("local",),
    ),
    "openai": ProviderCatalogEntry(
        id="openai", label="OpenAI", models=(ProviderModel(id="gpt-5.6-luna", label="GPT-5.6 Luna"),),
        compatibilityAvailability="unverified", compatibilityRegions=("global",),
    ),
    "doubao": ProviderCatalogEntry(
        id="doubao", label="火山方舟豆包", models=(ProviderModel(id="doubao-seed-2-0-lite-260428", label="Doubao Seed 2.0 Lite"),),
        compatibilityAvailability="unverified", compatibilityRegions=("cn",),
    ),
    "gemini": ProviderCatalogEntry(
        id="gemini", label="Google Gemini", models=(ProviderModel(id="gemini-2.5-flash", label="Gemini 2.5 Flash"),),
        compatibilityAvailability="unverified", compatibilityRegions=("global",),
    ),
    "grok": ProviderCatalogEntry(
        id="grok", label="xAI Grok", models=(ProviderModel(id="grok-4.6", label="Grok 4.6"),),
        compatibilityAvailability="unverified", compatibilityRegions=("global",),
    ),
    "claude": ProviderCatalogEntry(
        id="claude", label="Anthropic Claude", models=(ProviderModel(id="claude-sonnet-5", label="Claude Sonnet 5"),),
        compatibilityAvailability="unverified", compatibilityRegions=("global",),
    ),
    "chatanywhere": ProviderCatalogEntry(
        id="chatanywhere", label="ChatAnywhere", models=(ProviderModel(id="gpt-4o-mini", label="GPT-4o mini"),),
        compatibilityAvailability="unverified", compatibilityRegions=("global",),
    ),
})

MODEL_CATALOG: Mapping[ProviderId, tuple[ProviderModel, ...]] = MappingProxyType({
    provider_id: definition.models for provider_id, definition in PROVIDER_CATALOG.items()
})


def models_for(provider: ProviderId) -> tuple[ProviderModel, ...]:
    try:
        return MODEL_CATALOG[provider]
    except KeyError:
        raise ValueError("不支持的分析供应商。") from None


def provider_for(provider: ProviderId) -> ProviderCatalogEntry:
    try:
        return PROVIDER_CATALOG[provider]
    except KeyError:
        raise ValueError("不支持的分析供应商。") from None


def model_is_allowed(provider: ProviderId, model: str) -> bool:
    if provider == "local_openai_compatible":
        return bool(model.strip())
    return any(candidate.id == model for candidate in models_for(provider))


PROVIDER_IDS = tuple(PROVIDER_CATALOG)


__all__ = [
    "CATALOG_VERSION", "MODEL_CATALOG", "PROVIDER_CATALOG", "PROVIDER_IDS", "ProviderCatalogEntry", "ProviderId", "ProviderModel",
    "model_is_allowed", "models_for", "provider_for",
]
