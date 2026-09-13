import pytest

from app.provider_models import MODEL_CATALOG, models_for, model_is_allowed


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("bailian", "qwen3.7-flash"),
        ("openai", "gpt-5.6-luna"),
        ("doubao", "doubao-seed-2-0-lite-260428"),
        ("gemini", "gemini-2.5-flash"),
        ("grok", "grok-4.6"),
        ("claude", "claude-sonnet-5"),
    ],
)
def test_cloud_model_catalog_is_closed(provider, model):
    assert model_is_allowed(provider, model)
    assert not model_is_allowed(provider, "user-entered-model")


def test_local_openai_compatible_keeps_a_non_empty_custom_model_escape_hatch():
    assert models_for("local_openai_compatible") == ()
    assert model_is_allowed("local_openai_compatible", "vision-local")
    assert not model_is_allowed("local_openai_compatible", "   ")


def test_unknown_provider_has_no_catalog_entry():
    with pytest.raises(ValueError, match="不支持的分析供应商"):
        models_for("unknown")


def test_model_catalog_cannot_be_mutated_after_import():
    original = MODEL_CATALOG["openai"]
    try:
        with pytest.raises(TypeError):
            MODEL_CATALOG["openai"] = ()
    finally:
        if isinstance(MODEL_CATALOG, dict):
            MODEL_CATALOG["openai"] = original
