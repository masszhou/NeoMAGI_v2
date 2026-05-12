"""Apply product settings and extension provider registrations to model APIs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ai_provider.api_registry import register_api, unregister_apis_by_prefix
from ai_provider.model_registry import (
    EXTENSION_SOURCE,
    SETTINGS_SOURCE,
    get_model,
    list_models,
    register_model,
    unregister_models_by_source,
)
from ai_provider.types import Model, ModelCost
from cli.core.settings import ProductSettings, ProviderOverride
from cli.extensions.runtime import LoadedExtension

_EXTENSION_API_PREFIX = "extension:"


def apply_settings_models(settings: ProductSettings) -> None:
    unregister_models_by_source(f"{SETTINGS_SOURCE}:global")
    unregister_models_by_source(f"{SETTINGS_SOURCE}:project")
    # ProductSettings is already the effective global->project merge. Keep the
    # source label project-scoped because it is the final settings layer.
    _apply_provider_overrides(
        settings.providers,
        source=f"{SETTINGS_SOURCE}:project",
        owner="settings",
    )


def apply_extension_providers(extensions: Iterable[LoadedExtension]) -> list[str]:
    unregister_models_by_source(EXTENSION_SOURCE)
    _unregister_extension_apis()
    diagnostics: list[str] = []
    for extension in extensions:
        for provider_id, registration in extension.providers.items():
            try:
                _apply_extension_provider(
                    provider_id,
                    registration.config,
                    owner=registration.owner,
                )
            except Exception as exc:
                diagnostics.append(
                    f"extension {registration.owner} provider {provider_id!r} ignored: {exc}"
                )
    return diagnostics


def _apply_provider_overrides(
    providers: dict[str, ProviderOverride],
    *,
    source: str,
    owner: str | None,
) -> None:
    for provider_id, override in providers.items():
        models = override.models
        if models:
            for model_override in models:
                register_model(
                    _model_from_override(provider_id, override, model_override),
                    source=source,
                    owner=owner,
                )
            continue
        if override.base_url or override.api or override.headers or override.compat:
            for model in list_models(provider_id):
                register_model(
                    _model_with_provider_override(model, override),
                    source=source,
                    owner=owner,
                )


def _apply_extension_provider(provider_id: str, config: Any, *, owner: str) -> None:
    api_name = config.api
    if callable(config.stream_simple):
        api_name = api_name or f"{_EXTENSION_API_PREFIX}{owner}:{provider_id}"
        register_api(api_name, config.stream_simple, config.stream_simple)
    elif api_name is None:
        api_name = _existing_provider_api(provider_id) or "openai-responses"

    if config.models:
        for model_config in config.models:
            model = _model_from_extension(provider_id, config, model_config, api_name)
            register_model(model, source=EXTENSION_SOURCE, owner=owner)
        return

    if config.base_url or config.headers:
        for model in list_models(provider_id):
            register_model(
                model.model_copy(
                    update={
                        "base_url": config.base_url or model.base_url,
                        "headers": _merged_headers(model.headers, config.headers),
                    }
                ),
                source=EXTENSION_SOURCE,
                owner=owner,
            )


def _model_from_override(provider_id: str, provider: ProviderOverride, model_override: Any) -> Model:
    base_model = _maybe_get_model(provider_id, model_override.id)
    api = model_override.api or provider.api or (base_model.api if base_model else None)
    base_url = (
        model_override.base_url
        or provider.base_url
        or (base_model.base_url if base_model else None)
    )
    cost = model_override.cost or (
        base_model.cost.model_dump(by_alias=True) if base_model else None
    )
    if not api:
        raise ValueError(f"settings model {provider_id}/{model_override.id} requires api")
    if not base_url:
        raise ValueError(f"settings model {provider_id}/{model_override.id} requires baseUrl")
    if not cost or not {"input", "output", "cacheRead", "cacheWrite"} <= set(cost):
        raise ValueError(
            f"settings model {provider_id}/{model_override.id} requires input/output/cacheRead/cacheWrite cost"
        )
    context_window = model_override.context_window or (
        base_model.context_window if base_model else None
    )
    max_tokens = model_override.max_tokens or (base_model.max_tokens if base_model else None)
    if not context_window:
        raise ValueError(f"settings model {provider_id}/{model_override.id} requires contextWindow")
    if not max_tokens:
        raise ValueError(f"settings model {provider_id}/{model_override.id} requires maxTokens")
    return Model(
        id=model_override.model or model_override.id,
        name=model_override.name or (base_model.name if base_model else model_override.id),
        api=api,
        provider=provider_id,
        baseUrl=base_url,
        reasoning=(
            model_override.reasoning
            if model_override.reasoning is not None
            else (base_model.reasoning if base_model else False)
        ),
        input=model_override.input or (base_model.input if base_model else ["text"]),
        cost=ModelCost(
            input=cost["input"],
            output=cost["output"],
            cacheRead=cost["cacheRead"],
            cacheWrite=cost["cacheWrite"],
        ),
        contextWindow=context_window,
        maxTokens=max_tokens,
        headers=_merged_headers(provider.headers, model_override.headers),
        compat=_merged_compat(provider.compat, model_override.compat),
    )


def _model_with_provider_override(model: Model, provider: ProviderOverride) -> Model:
    return model.model_copy(
        update={
            "api": provider.api or model.api,
            "base_url": provider.base_url or model.base_url,
            "headers": _merged_headers(model.headers, provider.headers),
            "compat": _merged_compat(model.compat, provider.compat),
        }
    )


def _model_from_extension(provider_id: str, config: Any, model_config: Any, api_name: str) -> Model:
    cost = dict(model_config.cost)
    if not {"input", "output"} <= set(cost):
        raise ValueError(f"extension model {provider_id}/{model_config.id} requires input/output cost")
    return Model(
        id=model_config.id,
        name=model_config.name,
        api=model_config.api or api_name,
        provider=provider_id,
        baseUrl=config.base_url or "http://localhost:0",
        reasoning=model_config.reasoning,
        input=model_config.input,
        cost=ModelCost(
            input=cost["input"],
            output=cost["output"],
            cacheRead=cost.get("cacheRead", 0),
            cacheWrite=cost.get("cacheWrite", 0),
        ),
        contextWindow=model_config.context_window,
        maxTokens=model_config.max_tokens,
        headers=_merged_headers(config.headers, model_config.headers),
    )


def _existing_provider_api(provider_id: str) -> str | None:
    models = list_models(provider_id)
    return models[0].api if models else None


def _maybe_get_model(provider_id: str, model_id: str) -> Model | None:
    try:
        return get_model(provider_id, model_id)
    except KeyError:
        return None


def _merged_headers(*values: dict[str, str] | None) -> dict[str, str] | None:
    merged: dict[str, str] = {}
    for value in values:
        if value:
            merged.update(value)
    return merged or None


def _merged_compat(*values: dict[str, Any] | None) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    for value in values:
        if value:
            merged.update(value)
    return merged or None


def _unregister_extension_apis() -> None:
    unregister_apis_by_prefix(_EXTENSION_API_PREFIX)


__all__ = ["apply_extension_providers", "apply_settings_models"]
