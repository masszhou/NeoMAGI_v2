"""Runtime credential resolution boundary for provider adapters."""

from __future__ import annotations

import os

from .auth_storage import resolve_stored_api_key
from .runtime_types import StreamOptions
from .types import Model


def get_env_api_key(provider: str) -> str | None:
    if provider == "openai":
        return os.environ.get("OPENAI_API_KEY")
    if provider == "openai-codex":
        return os.environ.get("OPENAI_CODEX_OAUTH_TOKEN")
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY")
    normalized = provider.upper().replace("-", "_")
    return os.environ.get(f"NEOMAGI_{normalized}_API_KEY")


def resolve_api_key(model: Model, options: StreamOptions | None = None) -> str:
    if options and options.api_key:
        return options.api_key
    stored_api_key = resolve_stored_api_key(model.provider)
    if stored_api_key:
        return stored_api_key
    api_key = get_env_api_key(model.provider)
    if api_key:
        return api_key
    fallback = _custom_provider_fallback(model)
    if fallback:
        return fallback
    raise RuntimeError(
        f"missing API key for provider {model.provider!r}; pass StreamOptions.api_key "
        "or set the provider environment variable/login credential"
    )


def _custom_provider_fallback(model: Model) -> str | None:
    compat = model.compat if isinstance(model.compat, dict) else {}
    candidates = compat.get("apiKeyEnv") or compat.get("api_key_env")
    if isinstance(candidates, str):
        candidates = [candidates]
    if isinstance(candidates, list):
        for name in candidates:
            if isinstance(name, str) and os.environ.get(name):
                return os.environ[name]
    return None


__all__ = [
    "get_env_api_key",
    "resolve_api_key",
]
