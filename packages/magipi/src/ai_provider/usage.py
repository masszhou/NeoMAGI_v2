"""Provider-specific usage normalization and five-dimensional cost."""

from __future__ import annotations

from typing import Any

from .types import Model, Usage, UsageCost

PER_MILLION = 1_000_000


def calculate_cost(model: Model, usage: Usage) -> UsageCost:
    input_cost = model.cost.input * usage.input / PER_MILLION
    output_cost = model.cost.output * usage.output / PER_MILLION
    cache_read_cost = model.cost.cache_read * usage.cache_read / PER_MILLION
    cache_write_cost = model.cost.cache_write * usage.cache_write / PER_MILLION
    return UsageCost(
        input=input_cost,
        output=output_cost,
        cacheRead=cache_read_cost,
        cacheWrite=cache_write_cost,
        total=input_cost + output_cost + cache_read_cost + cache_write_cost,
    )


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _get(raw: Any, key: str, default: Any = None) -> Any:
    if isinstance(raw, dict):
        return raw.get(key, default)
    return getattr(raw, key, default)


def _has(raw: Any, key: str) -> bool:
    if raw is None:
        return False
    if isinstance(raw, dict):
        return key in raw
    fields_set = getattr(raw, "model_fields_set", None)
    if fields_set is not None:
        return key in fields_set
    return hasattr(raw, key)


def _details(raw: Any, key: str) -> Any:
    return _get(raw, key, {}) or {}


def _usage(model: Model, *, input: int, output: int, cache_read: int, cache_write: int) -> Usage:
    usage = Usage(
        input=max(input, 0),
        output=max(output, 0),
        cacheRead=max(cache_read, 0),
        cacheWrite=max(cache_write, 0),
        totalTokens=max(input, 0) + max(output, 0) + max(cache_read, 0) + max(cache_write, 0),
    )
    usage.cost = calculate_cost(model, usage)
    return usage


def normalize_anthropic_usage(raw: Any, model: Model) -> Usage:
    input_tokens = _as_int(_get(raw, "input_tokens"))
    output_tokens = _as_int(_get(raw, "output_tokens"))
    cache_read = _as_int(_get(raw, "cache_read_input_tokens"))
    cache_write = _as_int(_get(raw, "cache_creation_input_tokens"))
    return _usage(
        model,
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
    )


def merge_anthropic_usage(current: Usage | None, raw: Any, model: Model) -> Usage:
    """Merge Anthropic stream usage without zeroing absent delta fields."""

    usage = current.model_copy(deep=True) if current is not None else _usage(
        model,
        input=0,
        output=0,
        cache_read=0,
        cache_write=0,
    )
    if _has(raw, "input_tokens"):
        usage.input = max(_as_int(_get(raw, "input_tokens")), 0)
    if _has(raw, "output_tokens"):
        usage.output = max(_as_int(_get(raw, "output_tokens")), 0)
    if _has(raw, "cache_read_input_tokens"):
        usage.cache_read = max(_as_int(_get(raw, "cache_read_input_tokens")), 0)
    if _has(raw, "cache_creation_input_tokens"):
        usage.cache_write = max(_as_int(_get(raw, "cache_creation_input_tokens")), 0)
    usage.total_tokens = usage.input + usage.output + usage.cache_read + usage.cache_write
    usage.cost = calculate_cost(model, usage)
    return usage


def normalize_openai_responses_usage(raw: Any, model: Model) -> Usage:
    input_total = _as_int(_get(raw, "input_tokens"))
    output_tokens = _as_int(_get(raw, "output_tokens"))
    input_details = _details(raw, "input_tokens_details")
    cache_read = _as_int(_get(input_details, "cached_tokens"))
    return _usage(
        model,
        input=max(input_total - cache_read, 0),
        output=output_tokens,
        cache_read=cache_read,
        cache_write=0,
    )


def normalize_openai_completions_usage(raw: Any, model: Model) -> Usage:
    prompt_tokens = _as_int(_get(raw, "prompt_tokens"))
    output_tokens = _as_int(_get(raw, "completion_tokens"))
    prompt_details = _details(raw, "prompt_tokens_details")
    cached_tokens = _as_int(_get(prompt_details, "cached_tokens"))
    cache_write = _as_int(_get(prompt_details, "cache_write_tokens"))
    cache_read = max(cached_tokens - cache_write, 0)
    return _usage(
        model,
        input=max(prompt_tokens - cache_read - cache_write, 0),
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
    )


def normalize_faux_usage(raw: Any, model: Model) -> Usage:
    input_tokens = _as_int(_get(raw, "input"))
    output_tokens = _as_int(_get(raw, "output"))
    cache_read = _as_int(_get(raw, "cacheRead", _get(raw, "cache_read")))
    cache_write = _as_int(_get(raw, "cacheWrite", _get(raw, "cache_write")))
    return _usage(
        model,
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
    )


__all__ = [
    "PER_MILLION",
    "calculate_cost",
    "merge_anthropic_usage",
    "normalize_anthropic_usage",
    "normalize_faux_usage",
    "normalize_openai_completions_usage",
    "normalize_openai_responses_usage",
]
