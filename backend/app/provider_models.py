from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field


ProviderId = Literal[
    "bailian",
    "local_openai_compatible",
    "openai",
    "doubao",
    "gemini",
    "grok",
    "claude",
]


class ProviderModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)


MODEL_CATALOG: Mapping[ProviderId, tuple[ProviderModel, ...]] = MappingProxyType({
    "bailian": (ProviderModel(id="qwen3.7-flash", label="Qwen 3.7 Flash"),),
    "local_openai_compatible": (),
    "openai": (ProviderModel(id="gpt-5.6-luna", label="GPT-5.6 Luna"),),
    "doubao": (ProviderModel(id="doubao-seed-2-0-lite-260428", label="Doubao Seed 2.0 Lite"),),
    "gemini": (ProviderModel(id="gemini-2.5-flash", label="Gemini 2.5 Flash"),),
    "grok": (ProviderModel(id="grok-4.6", label="Grok 4.6"),),
    "claude": (ProviderModel(id="claude-sonnet-5", label="Claude Sonnet 5"),),
})


def models_for(provider: ProviderId) -> tuple[ProviderModel, ...]:
    try:
        return MODEL_CATALOG[provider]
    except KeyError:
        raise ValueError("不支持的分析供应商。") from None


def model_is_allowed(provider: ProviderId, model: str) -> bool:
    if provider == "local_openai_compatible":
        return bool(model.strip())
    return any(candidate.id == model for candidate in models_for(provider))


PROVIDER_IDS = tuple(MODEL_CATALOG)


__all__ = ["MODEL_CATALOG", "PROVIDER_IDS", "ProviderId", "ProviderModel", "model_is_allowed", "models_for"]
