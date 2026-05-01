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


def test_builtin_models_include_daily_smoke_targets() -> None:
    gpt = resolve_model("openai/gpt-5.4")
    assert gpt.api == "openai-responses"
    assert gpt.reasoning is True
    assert gpt.context_window == 1_000_000
    assert gpt.max_tokens == 128_000
    assert gpt.cost.input == 2.5
    assert gpt.cost.cache_read == 0.25
    assert validate_thinking_level_for_model(gpt, "xhigh") == "xhigh"

    codex = resolve_model("openai-codex/gpt-5.3-codex")
    assert codex.api == "openai-codex-responses"

    sonnet = resolve_model("anthropic/claude-sonnet-4-6")
    assert sonnet.api == "anthropic-messages"
    assert sonnet.context_window == 1_000_000
    assert sonnet.max_tokens == 64_000
    assert sonnet.cost.input == 3

    opus = resolve_model("anthropic/claude-opus-4-7")
    assert opus.api == "anthropic-messages"
    assert opus.context_window == 1_000_000
    assert opus.max_tokens == 128_000
    assert opus.cost.output == 25
    assert validate_thinking_level_for_model(opus, "xhigh") == "xhigh"


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
