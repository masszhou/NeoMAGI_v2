from __future__ import annotations

from cli.core.model_settings import apply_settings_models
from cli.core.settings import ProductSettings
from ai_provider.model_registry import (
    get_model_entry,
    resolve_model,
    unregister_models_by_source,
)


def test_settings_custom_openai_compatible_model_is_source_tracked() -> None:
    settings = ProductSettings.model_validate(
        {
            "providers": {
                "local-openai": {
                    "api": "openai-responses",
                    "baseUrl": "http://127.0.0.1:11434/v1",
                    "models": [
                        {
                            "id": "local-1",
                            "name": "Local 1",
                            "reasoning": False,
                            "input": ["text"],
                            "cost": {
                                "input": 0,
                                "output": 0,
                                "cacheRead": 0,
                                "cacheWrite": 0,
                            },
                            "contextWindow": 8192,
                            "maxTokens": 2048,
                        }
                    ],
                }
            }
        }
    )
    try:
        apply_settings_models(settings)

        model = resolve_model("local-openai/local-1")
        entry = get_model_entry("local-openai", "local-1")

        assert model.base_url == "http://127.0.0.1:11434/v1"
        assert model.context_window == 8192
        assert entry is not None
        assert entry.source == "settings:project"
    finally:
        unregister_models_by_source("settings:project")


def test_settings_provider_override_is_reversible_for_builtin() -> None:
    original = resolve_model("openai/gpt-4o-mini")
    settings = ProductSettings.model_validate(
        {"providers": {"openai": {"baseUrl": "https://proxy.example/v1"}}}
    )

    apply_settings_models(settings)
    overridden = resolve_model("openai/gpt-4o-mini")
    unregister_models_by_source("settings:project")
    restored = resolve_model("openai/gpt-4o-mini")

    assert overridden.base_url == "https://proxy.example/v1"
    assert restored.base_url == original.base_url
