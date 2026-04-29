from __future__ import annotations

import asyncio

from ai_provider.api_registry import get_api, stream
from ai_provider.model_registry import (
    get_model,
    list_models,
    register_model,
    resolve_model,
    validate_thinking_level_for_model,
)
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, Model, ModelCost, UserMessage


def test_builtin_models_cover_m2_api_families() -> None:
    api_families = {model.api for model in list_models()}
    assert {
        "anthropic-messages",
        "openai-codex-responses",
        "openai-responses",
        "openai-completions",
        "faux",
    } <= api_families


def test_get_api_registers_builtin_api_families() -> None:
    assert get_api("openai-responses").api_name == "openai-responses"
    assert get_api("openai-codex-responses").api_name == "openai-codex-responses"
    assert get_api("openai-completions").api_name == "openai-completions"


def test_resolve_model_requires_explicit_provider_model() -> None:
    assert resolve_model("openai/gpt-4o-mini").id == "gpt-4o-mini"


def test_validate_thinking_level_uses_model_capabilities() -> None:
    assert validate_thinking_level_for_model(get_model("faux", "faux-1"), "low") == "low"
    try:
        validate_thinking_level_for_model(get_model("openai", "gpt-4o-mini"), "low")
    except ValueError as exc:
        assert "does not support thinking" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("non-reasoning model should reject thinking levels")


def test_register_model_rejects_missing_windows() -> None:
    model = Model(
        id="bad",
        name="bad",
        api="faux",
        provider="faux",
        baseUrl="http://localhost:0",
        reasoning=False,
        input=["text"],
        cost=ModelCost(input=0, output=0, cacheRead=0, cacheWrite=0),
        contextWindow=0,
        maxTokens=1,
    )

    try:
        register_model(model)
    except ValueError as exc:
        assert "contextWindow" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("register_model should reject contextWindow=0")


def test_registry_stream_dispatches_by_api_family() -> None:
    async def run() -> None:
        model = get_model("faux", "faux-1")
        context = Context(messages=[UserMessage(content="hi", timestamp=1)])
        result = await stream(model, context, StreamOptions(metadata={"response": "ok"})).result()
        assert result.api == "faux"
        assert result.content[0].text == "ok"

    asyncio.run(run())
