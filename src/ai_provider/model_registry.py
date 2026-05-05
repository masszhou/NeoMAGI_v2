"""Source-aware in-memory model registry keyed by provider and model id."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import supports_xhigh
from .types import Model, ModelCost, ThinkingLevel

BUILTIN_SOURCE = "builtin"
SETTINGS_SOURCE = "settings"
EXTENSION_SOURCE = "extension"
RUNTIME_SOURCE = "runtime"

SOURCE_PRIORITIES: dict[str, int] = {
    BUILTIN_SOURCE: 0,
    f"{SETTINGS_SOURCE}:global": 10,
    f"{SETTINGS_SOURCE}:project": 20,
    EXTENSION_SOURCE: 30,
    RUNTIME_SOURCE: 40,
}


@dataclass(frozen=True, slots=True)
class ModelRegistryEntry:
    model: Model
    source: str
    owner: str | None = None
    priority: int = 0


_models: dict[tuple[str, str], list[ModelRegistryEntry]] = defaultdict(list)


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
        id="claude-opus-4-7",
        name="Claude Opus 4.7",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 5, "output": 25, "cacheRead": 0.5, "cacheWrite": 6.25},
        context_window=1_000_000,
        max_tokens=128_000,
    ),
    _make_model(
        id="claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 3, "output": 15, "cacheRead": 0.3, "cacheWrite": 3.75},
        context_window=1_000_000,
        max_tokens=64_000,
    ),
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
        id="gpt-5.4",
        name="GPT-5.4",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 2.5, "output": 15, "cacheRead": 0.25, "cacheWrite": 0},
        context_window=1_000_000,
        max_tokens=128_000,
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


def register_model(
    model: Model,
    *,
    source: str = RUNTIME_SOURCE,
    owner: str | None = None,
    priority: int | None = None,
) -> None:
    if model.context_window <= 0:
        raise ValueError("model.contextWindow must be greater than 0")
    if model.max_tokens <= 0:
        raise ValueError("model.maxTokens must be greater than 0")
    key = (model.provider, model.id)
    resolved_priority = SOURCE_PRIORITIES.get(source, 100 if priority is None else priority)
    if priority is not None:
        resolved_priority = priority
    _models[key] = [
        entry
        for entry in _models[key]
        if not (entry.source == source and entry.owner == owner)
    ]
    _models[key].append(
        ModelRegistryEntry(
            model=model,
            source=source,
            owner=owner,
            priority=resolved_priority,
        )
    )


def get_model(provider: str, model_id: str) -> Model:
    entry = get_model_entry(provider, model_id)
    if entry is None:
        raise KeyError(f"unknown model {provider}/{model_id}")
    return entry.model


def get_model_entry(provider: str, model_id: str) -> ModelRegistryEntry | None:
    entries = _models.get((provider, model_id), [])
    if not entries:
        return None
    return max(enumerate(entries), key=lambda item: (item[1].priority, item[0]))[1]


def list_models(provider: str | None = None) -> list[Model]:
    return [entry.model for entry in list_model_entries(provider)]


def list_model_entries(provider: str | None = None) -> list[ModelRegistryEntry]:
    entries: list[ModelRegistryEntry] = []
    for (entry_provider, _model_id), layers in _models.items():
        if provider is not None and entry_provider != provider:
            continue
        if layers:
            entries.append(
                max(
                    enumerate(layers),
                    key=lambda item: (item[1].priority, item[0]),
                )[1]
            )
    return sorted(entries, key=lambda entry: (entry.model.provider, entry.model.id))


def resolve_model(model_ref: str) -> Model:
    provider, sep, model_id = model_ref.partition("/")
    if not sep or not provider or not model_id:
        raise ValueError("model reference must use explicit provider/model form")
    return get_model(provider, model_id)


def validate_thinking_level_for_model(model: Model, level: ThinkingLevel) -> ThinkingLevel:
    if level == "off":
        return level
    if not model.reasoning:
        raise ValueError(f"model {model.provider}/{model.id} does not support thinking")
    if level == "xhigh" and not supports_xhigh(model):
        raise ValueError(f"model {model.provider}/{model.id} does not support thinking level xhigh")
    return level


def clear_models_for_tests() -> None:
    _models.clear()


def unregister_models_by_source(
    source: str,
    *,
    owner: str | None = None,
    provider: str | None = None,
) -> None:
    for key in list(_models):
        key_provider, _model_id = key
        if provider is not None and key_provider != provider:
            continue
        _models[key] = [
            entry
            for entry in _models[key]
            if not (entry.source == source and (owner is None or entry.owner == owner))
        ]
        if not _models[key]:
            del _models[key]


for _model in BUILTIN_MODELS:
    register_model(_model, source=BUILTIN_SOURCE, priority=0)


__all__ = [
    "BUILTIN_MODELS",
    "BUILTIN_SOURCE",
    "EXTENSION_SOURCE",
    "ModelRegistryEntry",
    "RUNTIME_SOURCE",
    "SETTINGS_SOURCE",
    "clear_models_for_tests",
    "get_model",
    "get_model_entry",
    "list_model_entries",
    "list_models",
    "register_model",
    "resolve_model",
    "unregister_models_by_source",
    "validate_thinking_level_for_model",
]
