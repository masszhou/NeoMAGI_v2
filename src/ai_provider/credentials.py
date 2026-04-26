"""Runtime credential resolution boundary for provider adapters."""

from __future__ import annotations

import os

from .runtime_types import StreamOptions
from .types import Model


def get_env_api_key(provider: str) -> str | None:
    if provider == "openai":
        return os.environ.get("OPENAI_API_KEY")
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY")
    return None


def resolve_api_key(model: Model, options: StreamOptions | None = None) -> str:
    if options and options.api_key:
        return options.api_key
    api_key = get_env_api_key(model.provider)
    if api_key:
        return api_key
    raise RuntimeError(
        f"missing API key for provider {model.provider!r}; pass StreamOptions.api_key "
        "or set the provider environment variable"
    )


__all__ = [
    "get_env_api_key",
    "resolve_api_key",
]
