"""Usage normalization + cost calculation (M0 boundary).

Architecture: ``ai_provider`` Protocol § Model and Provider — usage normalization
must keep Pi semantics so cross-provider handoff is byte-stable.

Pi rules (architecture line 257):

- ``input`` excludes ``cacheRead`` and ``cacheWrite``.
- ``totalTokens = input + output + cacheRead + cacheWrite``.
- Many OpenAI-compatible / proxy providers report ``prompt_tokens_details.cached_tokens``
  while *also* including those tokens in ``prompt_tokens`` — the adapter must
  subtract cached tokens before adding ``cacheRead`` / ``cacheWrite``.

M0 only ships the cost calculator and the per-provider normalization
*hook surface*. Concrete provider adapters land in M2; the round-trip fixture
``usage_cache_normalization`` (W4) is what each M2 adapter must satisfy.
"""

from __future__ import annotations

from typing import Any

from .types import Model, Usage, UsageCost

PER_MILLION = 1_000_000


def calculate_cost(model: Model, usage: Usage) -> UsageCost:
    """Compute a five-dimensional ``UsageCost`` from raw token counts.

    Costs in :class:`Model.cost` are quoted in dollars per million tokens, so
    the conversion factor is :data:`PER_MILLION`.
    """

    input_cost = model.cost.input * usage.input / PER_MILLION
    output_cost = model.cost.output * usage.output / PER_MILLION
    cache_read_cost = model.cost.cache_read * usage.cache_read / PER_MILLION
    cache_write_cost = model.cost.cache_write * usage.cache_write / PER_MILLION
    total = input_cost + output_cost + cache_read_cost + cache_write_cost

    return UsageCost(
        input=input_cost,
        output=output_cost,
        cacheRead=cache_read_cost,
        cacheWrite=cache_write_cost,
        total=total,
    )


def normalize_provider_usage(raw: dict[str, Any], provider: str) -> Usage:
    """Provider-aware usage normalization (placeholder).

    M0 only resolves the "avoid double-counting cacheRead" guarantee for the
    common case (``prompt_tokens`` already includes ``cached_tokens``). Each
    provider adapter in M2 will replace this with provider-specific extraction.

    The current default treats ``raw`` as the OpenAI-compatible shape:

    - ``prompt_tokens`` may include ``cached_tokens`` (subtract before assigning).
    - ``completion_tokens`` → ``output``.
    - ``cached_tokens`` → ``cacheRead``.
    - ``cache_creation_input_tokens`` → ``cacheWrite`` (Anthropic-flavored shim;
      ignored if absent).

    Anything outside these keys is left as ``0``; M2 fills the per-provider gaps.
    """

    prompt_tokens = int(raw.get("prompt_tokens", 0) or 0)
    completion_tokens = int(raw.get("completion_tokens", 0) or 0)
    details = raw.get("prompt_tokens_details") or {}
    cached_tokens = int(details.get("cached_tokens", 0) or 0)
    cache_write_tokens = int(raw.get("cache_creation_input_tokens", 0) or 0)

    input_tokens = max(prompt_tokens - cached_tokens, 0)
    total_tokens = input_tokens + completion_tokens + cached_tokens + cache_write_tokens

    return Usage(
        input=input_tokens,
        output=completion_tokens,
        cacheRead=cached_tokens,
        cacheWrite=cache_write_tokens,
        totalTokens=total_tokens,
    )


__all__ = [
    "PER_MILLION",
    "calculate_cost",
    "normalize_provider_usage",
]
