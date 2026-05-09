from __future__ import annotations

import asyncio

import pytest

from ai_provider.api_registry import get_api, stream
from ai_provider.model_registry import (
    canonical_model_ref,
    get_model,
    legacy_model_ref,
    list_models,
    parse_model_ref,
    provider_auth_info,
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


def test_canonical_three_segment_refs_resolve_builtin_models() -> None:
    gpt = resolve_model("openai/api/gpt-5.4")
    assert gpt.provider == "openai"
    assert gpt.id == "gpt-5.4"

    codex = resolve_model("openai/oauth/gpt-5.3-codex")
    assert codex.provider == "openai-codex"
    assert codex.id == "gpt-5.3-codex"

    sonnet = resolve_model("anthropic/api/claude-sonnet-4-6")
    assert sonnet.provider == "anthropic"

    faux = resolve_model("faux/local/faux-1")
    assert faux.provider == "faux"


def test_legacy_two_segment_refs_still_resolve() -> None:
    assert resolve_model("openai/gpt-5.4").id == "gpt-5.4"
    assert resolve_model("openai-codex/gpt-5.3-codex").provider == "openai-codex"
    assert resolve_model("anthropic/claude-sonnet-4-6").provider == "anthropic"
    assert resolve_model("faux/faux-1").provider == "faux"


def test_canonical_model_ref_uses_vendor_auth_channel() -> None:
    assert canonical_model_ref(get_model("openai", "gpt-5.4")) == "openai/api/gpt-5.4"
    assert (
        canonical_model_ref(get_model("openai-codex", "gpt-5.3-codex"))
        == "openai/oauth/gpt-5.3-codex"
    )
    assert (
        canonical_model_ref(get_model("anthropic", "claude-sonnet-4-6"))
        == "anthropic/api/claude-sonnet-4-6"
    )
    assert canonical_model_ref(get_model("faux", "faux-1")) == "faux/local/faux-1"


def test_legacy_model_ref_renders_internal_provider() -> None:
    assert legacy_model_ref(get_model("openai-codex", "gpt-5.3-codex")) == (
        "openai-codex/gpt-5.3-codex"
    )
    # Round-trip from canonical to legacy via parse_model_ref.
    parsed = parse_model_ref("openai/oauth/gpt-5.3-codex")
    assert parsed.legacy == "openai-codex/gpt-5.3-codex"


def test_parse_model_ref_rejects_unknown_auth_channel() -> None:
    with pytest.raises(ValueError) as exc_info:
        parse_model_ref("openai/keychain/gpt-5.4")
    assert "auth-channel" in str(exc_info.value)
    assert "vendor/auth-channel/model" in str(exc_info.value)


def test_parse_model_ref_rejects_case_variants() -> None:
    # Strict lowercase allowlist: API/OAuth/Local must fail-fast.
    with pytest.raises(ValueError):
        parse_model_ref("openai/API/gpt-5.4")
    with pytest.raises(ValueError):
        parse_model_ref("openai/OAuth/gpt-5.3-codex")


def test_parse_model_ref_rejects_unknown_vendor_channel_combo() -> None:
    # vendor=openai with auth=local is not in the built-in table.
    with pytest.raises(ValueError) as exc_info:
        parse_model_ref("openai/local/gpt-5.4")
    assert "no internal provider" in str(exc_info.value)
    # vendor=anthropic with auth=oauth is not in the built-in table.
    with pytest.raises(ValueError):
        parse_model_ref("anthropic/oauth/claude-sonnet-4-6")


def test_resolve_model_unknown_ref_message_mentions_three_segment_format() -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_model("")
    assert "vendor/auth-channel/model" in str(exc_info.value)


def test_provider_auth_info_defaults_custom_provider_to_api() -> None:
    assert provider_auth_info("local-openai") == ("local-openai", "api")


def _register_custom_model(provider: str, model_id: str) -> None:
    register_model(
        Model(
            id=model_id,
            name=model_id,
            api="openai-responses",
            provider=provider,
            baseUrl="http://127.0.0.1:0",
            reasoning=False,
            input=["text"],
            cost=ModelCost(input=0, output=0, cacheRead=0, cacheWrite=0),
            contextWindow=8,
            maxTokens=4,
        )
    )


def test_legacy_two_segment_ref_supports_slash_in_model_id() -> None:
    """Custom OpenAI-compatible providers often use ``org/model`` ids."""

    from ai_provider.model_registry import unregister_models_by_source

    _register_custom_model("local-openai", "org/llama-3.1-8b")
    try:
        model = resolve_model("local-openai/org/llama-3.1-8b")
        assert model.provider == "local-openai"
        assert model.id == "org/llama-3.1-8b"
    finally:
        unregister_models_by_source("runtime")


def test_canonical_three_segment_ref_supports_slash_in_model_id() -> None:
    """Canonical refs join everything after the auth-channel into model_id."""

    from ai_provider.model_registry import unregister_models_by_source

    _register_custom_model("local-openai", "org/llama-3.1-8b")
    try:
        ref = parse_model_ref("local-openai/api/org/llama-3.1-8b")
        assert ref.vendor == "local-openai"
        assert ref.auth_channel == "api"
        assert ref.model_id == "org/llama-3.1-8b"
        assert ref.provider == "local-openai"
        assert resolve_model("local-openai/api/org/llama-3.1-8b").id == "org/llama-3.1-8b"
    finally:
        unregister_models_by_source("runtime")


def test_builtin_internal_provider_id_is_not_a_legal_canonical_vendor() -> None:
    """Built-in provider ids must never appear as the vendor segment.

    ``openai-codex`` is the *internal* credential boundary, not a user-facing
    vendor — accepting ``openai-codex/api/...`` would silently re-canonicalize
    to the OAuth lane and defeat the auth-channel guarantee.
    """

    with pytest.raises(ValueError) as exc_info:
        parse_model_ref("openai-codex/api/gpt-5.3-codex")
    assert "no internal provider" in str(exc_info.value)

    with pytest.raises(ValueError):
        parse_model_ref("openai-codex/oauth/gpt-5.3-codex")

    with pytest.raises(ValueError):
        parse_model_ref("faux/api/faux-1")


def test_registry_stream_dispatches_by_api_family() -> None:
    async def run() -> None:
        model = get_model("faux", "faux-1")
        context = Context(messages=[UserMessage(content="hi", timestamp=1)])
        result = await stream(model, context, StreamOptions(metadata={"response": "ok"})).result()
        assert result.api == "faux"
        assert result.content[0].text == "ok"

    asyncio.run(run())
