"""In-memory model registry keyed by provider and model id."""

from __future__ import annotations

from collections import defaultdict

from .types import Model, ModelCost

_models: dict[str, dict[str, Model]] = defaultdict(dict)


def _make_model(
    *,
    id: str,
    name: str,
    api: str,
    provider: str,
    base_url: str,
    reasoning: bool,
    input: list[str],
    cost: dict[str, float],
    context_window: int,
    max_tokens: int,
    headers: dict[str, str] | None = None,
    compat: dict[str, object] | None = None,
) -> Model:
    return Model(
        id=id,
        name=name,
        api=api,
        provider=provider,
        baseUrl=base_url,
        reasoning=reasoning,
        input=input,
        cost=ModelCost(
            input=cost["input"],
            output=cost["output"],
            cacheRead=cost["cacheRead"],
            cacheWrite=cost["cacheWrite"],
        ),
        contextWindow=context_window,
        maxTokens=max_tokens,
        headers=headers,
        compat=compat,
    )


BUILTIN_MODELS: tuple[Model, ...] = (
    _make_model(
        id="claude-haiku-4-5-20251001",
        name="Claude Haiku 4.5",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 1, "output": 5, "cacheRead": 0.1, "cacheWrite": 1.25},
        context_window=200000,
        max_tokens=64000,
    ),
    _make_model(
        id="gpt-4o-mini",
        name="GPT-4o mini",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
        reasoning=False,
        input=["text", "image"],
        cost={"input": 0.15, "output": 0.6, "cacheRead": 0.08, "cacheWrite": 0},
        context_window=128000,
        max_tokens=16384,
    ),
    _make_model(
        id="gpt-4o-mini-chat-completions",
        name="GPT-4o mini (Chat Completions)",
        api="openai-completions",
        provider="openai",
        base_url="https://api.openai.com/v1",
        reasoning=False,
        input=["text", "image"],
        cost={"input": 0.15, "output": 0.6, "cacheRead": 0.08, "cacheWrite": 0},
        context_window=128000,
        max_tokens=16384,
    ),
    _make_model(
        id="gpt-5.3-codex",
        name="GPT-5.3 Codex",
        api="openai-codex-responses",
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 1.75, "output": 14, "cacheRead": 0.175, "cacheWrite": 0},
        context_window=272000,
        max_tokens=128000,
    ),
    _make_model(
        id="glm-5",
        name="GLM-5",
        api="openai-completions",
        provider="opencode",
        base_url="https://opencode.ai/zen/v1",
        reasoning=True,
        input=["text"],
        cost={"input": 1, "output": 3.2, "cacheRead": 0.2, "cacheWrite": 0},
        context_window=204800,
        max_tokens=131072,
        compat={"sendSessionAffinityHeaders": True},
    ),
    _make_model(
        id="faux-1",
        name="Faux Model",
        api="faux",
        provider="faux",
        base_url="http://localhost:0",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=128000,
        max_tokens=16384,
    ),
)


def register_model(model: Model) -> None:
    if model.context_window <= 0:
        raise ValueError("model.contextWindow must be greater than 0")
    if model.max_tokens <= 0:
        raise ValueError("model.maxTokens must be greater than 0")
    _models[model.provider][model.id] = model


def get_model(provider: str, model_id: str) -> Model:
    try:
        return _models[provider][model_id]
    except KeyError as exc:
        raise KeyError(f"unknown model {provider}/{model_id}") from exc


def list_models(provider: str | None = None) -> list[Model]:
    if provider is not None:
        return list(_models.get(provider, {}).values())
    return [model for provider_models in _models.values() for model in provider_models.values()]


def resolve_model(model_ref: str) -> Model:
    provider, sep, model_id = model_ref.partition("/")
    if not sep or not provider or not model_id:
        raise ValueError("model reference must use explicit provider/model form")
    return get_model(provider, model_id)


def clear_models_for_tests() -> None:
    _models.clear()


for _model in BUILTIN_MODELS:
    register_model(_model)


__all__ = [
    "BUILTIN_MODELS",
    "clear_models_for_tests",
    "get_model",
    "list_models",
    "register_model",
    "resolve_model",
]
