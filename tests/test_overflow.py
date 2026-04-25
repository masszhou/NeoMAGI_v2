"""Tests for ``ai_provider.overflow``.

Each provider sample message is taken from the inline comments / regex sources
of `packages/ai/src/utils/overflow.ts` (pi-mono ``97a38bf6``); plus a few
synthetic-but-realistic strings for the generic fallbacks. The test pins:

- 16 distinct provider families return ``True``.
- 3 throttling / rate-limit messages return ``False`` even when they overlap a
  generic overflow pattern (``too many tokens``).
- silent overflow detection works when ``contextWindow`` is supplied.
"""

from __future__ import annotations

import pytest

from ai_provider.overflow import is_context_overflow
from ai_provider.types import AssistantMessage, Usage, UsageCost

ZERO_USAGE = Usage(
    input=0,
    output=0,
    cacheRead=0,
    cacheWrite=0,
    totalTokens=0,
    cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
)


def _error_msg(text: str) -> AssistantMessage:
    """Build a minimal AssistantMessage carrying an error message."""

    return AssistantMessage(
        role="assistant",
        content=[],
        api="anthropic-messages",
        provider="anthropic",
        model="test-model",
        usage=ZERO_USAGE,
        stopReason="error",
        errorMessage=text,
        timestamp=0,
    )


PROVIDER_SAMPLES: list[tuple[str, str]] = [
    ("anthropic-token", "prompt is too long: 213462 tokens > 200000 maximum"),
    ("anthropic-413", '413 {"error":{"type":"request_too_large","message":"Request exceeds the maximum size"}}'),
    ("bedrock", "Validation error: input is too long for requested model"),
    ("openai", "Your input exceeds the context window of this model"),
    ("google", "The input token count (1196265) exceeds the maximum number of tokens allowed (1048575)"),
    ("xai", "This model's maximum prompt length is 131072 but the request contains 537812 tokens"),
    ("groq", "Please reduce the length of the messages or completion"),
    ("openrouter", "This endpoint's maximum context length is 8192 tokens. However, you requested about 12000 tokens"),
    ("copilot", "prompt token count of 200000 exceeds the limit of 128000"),
    ("llama-cpp", "the request exceeds the available context size, try increasing it"),
    ("lm-studio", "tokens to keep from the initial prompt is greater than the context length"),
    ("minimax", "invalid params, context window exceeds limit"),
    ("kimi", "Your request exceeded model token limit: 200000 (requested: 250000)"),
    ("mistral", "Prompt contains 32000 tokens, this is too large for model with 16384 maximum context length"),
    ("zai-explicit", "model_context_window_exceeded"),
    ("ollama", "prompt too long; exceeded max context length by 4096 tokens"),
    ("cerebras", "400 status code (no body)"),
]


@pytest.mark.parametrize("provider,sample", PROVIDER_SAMPLES, ids=[s[0] for s in PROVIDER_SAMPLES])
def test_overflow_pattern_hits_for_each_provider(provider: str, sample: str) -> None:
    assert is_context_overflow(_error_msg(sample)) is True, (
        f"Provider {provider!r} sample should be classified as overflow: {sample!r}"
    )


# Pi treats these as transient capacity errors, not overflow, even though some
# also match the generic ``too many tokens`` catch-all. Without the exclusion,
# Pi's retry loop would compact forever on rate limits.
#
# Note: Pi's ``^(Throttling error|Service unavailable):`` pattern matches the
# *formatted* prefix produced by ``formatBedrockError``, not the raw AWS
# ``ThrottlingException:``. Pi normalizes the prefix before checking.
NON_OVERFLOW_SAMPLES = [
    ("bedrock-throttle", "Throttling error: Too many tokens, please wait before trying again."),
    ("bedrock-unavailable", "Service unavailable: too many tokens reported"),
    ("generic-rate-limit", "Rate limit reached for this model"),
    ("http-429", "HTTP 429: too many requests"),
]


@pytest.mark.parametrize("name,sample", NON_OVERFLOW_SAMPLES, ids=[s[0] for s in NON_OVERFLOW_SAMPLES])
def test_non_overflow_excluded(name: str, sample: str) -> None:
    assert is_context_overflow(_error_msg(sample)) is False


def test_silent_overflow_via_context_window() -> None:
    msg = AssistantMessage(
        role="assistant",
        content=[],
        api="zai",
        provider="zai",
        model="glm-4.6",
        usage=Usage(
            input=200000,
            output=0,
            cacheRead=10000,
            cacheWrite=0,
            totalTokens=210000,
            cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
        ),
        stopReason="stop",
        timestamp=0,
    )
    assert is_context_overflow(msg, context_window=128_000) is True
    assert is_context_overflow(msg, context_window=300_000) is False
    # Without ``context_window``, silent overflow is not detected.
    assert is_context_overflow(msg) is False


def test_no_error_message_is_not_overflow() -> None:
    msg = AssistantMessage(
        role="assistant",
        content=[],
        api="anthropic-messages",
        provider="anthropic",
        model="claude-4",
        usage=ZERO_USAGE,
        stopReason="stop",
        timestamp=0,
    )
    assert is_context_overflow(msg) is False
