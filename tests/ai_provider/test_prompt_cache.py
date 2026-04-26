from __future__ import annotations

from ai_provider.prompt_cache import (
    cache_enabled,
    resolve_cache_retention,
    sanitize_cache_affinity_id,
)


def test_cache_retention_defaults_to_short(monkeypatch) -> None:
    monkeypatch.delenv("PI_CACHE_RETENTION", raising=False)
    assert resolve_cache_retention(None) == "short"


def test_cache_retention_env_can_promote_default(monkeypatch) -> None:
    monkeypatch.setenv("PI_CACHE_RETENTION", "long")
    assert resolve_cache_retention(None) == "long"
    assert resolve_cache_retention("none") == "none"


def test_cache_enabled_only_false_for_none() -> None:
    assert cache_enabled("short") is True
    assert cache_enabled("long") is True
    assert cache_enabled("none") is False


def test_sanitize_cache_affinity_id_rejects_unsafe_values() -> None:
    assert sanitize_cache_affinity_id("session-1") == "session-1"
    assert sanitize_cache_affinity_id("") is None
    assert sanitize_cache_affinity_id("bad value with spaces") is None
    assert sanitize_cache_affinity_id("x" * 257) is None

