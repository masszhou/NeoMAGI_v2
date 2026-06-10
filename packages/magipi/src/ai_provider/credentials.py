"""Runtime credential resolution boundary for provider adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .auth_storage import resolve_stored_credential
from .oauth_github_copilot import (
    GITHUB_COPILOT_PROVIDER_ID,
    github_copilot_base_url,
    normalize_domain,
)
from .runtime_types import StreamOptions
from .types import Model


@dataclass(frozen=True, slots=True)
class ResolvedAuth:
    """A single credential resolution: api key plus the base url to call.

    For most providers ``base_url`` is just ``model.base_url``. GitHub Copilot
    derives it from the resolved token's ``proxy-ep`` so the chat host matches
    the (individual / business / enterprise) account the token was minted for.
    """

    api_key: str
    base_url: str


def get_env_api_key(provider: str) -> str | None:
    if provider == "openai":
        return os.environ.get("OPENAI_API_KEY")
    if provider == "openai-codex":
        return os.environ.get("OPENAI_CODEX_OAUTH_TOKEN")
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY")
    normalized = provider.upper().replace("-", "_")
    return os.environ.get(f"NEOMAGI_{normalized}_API_KEY")


def resolve_provider_auth(model: Model, options: StreamOptions | None = None) -> ResolvedAuth:
    """Resolve api key and base url in a single pass.

    The credential is resolved exactly once (``resolve_stored_api_key`` may
    refresh + persist an expired OAuth token as a side effect); the base url is
    then derived from that same resolved token, never by re-reading storage.
    """

    api_key, extra = _resolve_api_key_and_extra(model, options)
    base_url = _resolve_base_url(model, api_key, extra)
    return ResolvedAuth(api_key=api_key, base_url=base_url)


def resolve_api_key(model: Model, options: StreamOptions | None = None) -> str:
    return _resolve_api_key_and_extra(model, options)[0]


def _resolve_api_key_and_extra(
    model: Model, options: StreamOptions | None = None
) -> tuple[str, dict[str, Any]]:
    if options and options.api_key:
        return options.api_key, {}
    stored = resolve_stored_credential(model.provider)
    if stored is not None and stored.api_key:
        return stored.api_key, stored.extra
    api_key = get_env_api_key(model.provider)
    if api_key:
        return api_key, {}
    fallback = _custom_provider_fallback(model)
    if fallback:
        return fallback, {}
    raise RuntimeError(
        f"missing API key for provider {model.provider!r}; pass StreamOptions.api_key "
        "or set the provider environment variable/login credential"
    )


def _resolve_base_url(model: Model, api_key: str, extra: dict[str, Any]) -> str:
    if model.provider != GITHUB_COPILOT_PROVIDER_ID:
        return model.base_url
    enterprise = extra.get("enterpriseUrl") if extra else None
    enterprise_domain = (
        normalize_domain(enterprise) if isinstance(enterprise, str) and enterprise else None
    )
    return github_copilot_base_url(api_key, enterprise_domain)


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
    "ResolvedAuth",
    "get_env_api_key",
    "resolve_api_key",
    "resolve_provider_auth",
]
