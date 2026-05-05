from __future__ import annotations

from ai_provider.auth_storage import AUTH_PATH_ENV, save_api_key
from ai_provider.credentials import resolve_api_key
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Model, ModelCost


def test_credential_resolution_prefers_runtime_then_auth_storage_then_env(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    save_api_key("openai", "stored-key")
    model = _model("openai")

    assert resolve_api_key(model, StreamOptions(api_key="runtime-key")) == "runtime-key"
    assert resolve_api_key(model) == "stored-key"


def test_credential_resolution_uses_env_after_empty_auth_storage(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    assert resolve_api_key(_model("openai")) == "env-key"


def test_custom_provider_can_use_compat_env_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))
    monkeypatch.setenv("LOCAL_AI_KEY", "local-key")
    model = _model("local-ai", compat={"apiKeyEnv": "LOCAL_AI_KEY"})

    assert resolve_api_key(model) == "local-key"


def _model(provider: str, *, compat=None) -> Model:
    return Model(
        id="m",
        name="M",
        api="openai-responses",
        provider=provider,
        baseUrl="https://example.test/v1",
        reasoning=False,
        input=["text"],
        cost=ModelCost(input=0, output=0, cacheRead=0, cacheWrite=0),
        contextWindow=1024,
        maxTokens=256,
        compat=compat,
    )
