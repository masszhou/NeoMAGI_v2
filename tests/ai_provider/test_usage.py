from __future__ import annotations

import json
from pathlib import Path

from ai_provider.model_registry import get_model
from ai_provider.usage import (
    normalize_anthropic_usage,
    normalize_openai_completions_usage,
    normalize_openai_responses_usage,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "pi_compat" / "usage_cache_normalization"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text())


def _assert_usage(actual, expected: dict) -> None:
    assert actual.input == expected["input"]
    assert actual.output == expected["output"]
    assert actual.cache_read == expected["cacheRead"]
    assert actual.cache_write == expected["cacheWrite"]
    assert actual.total_tokens == expected["totalTokens"]
    assert actual.cost.total == (
        actual.cost.input + actual.cost.output + actual.cost.cache_read + actual.cost.cache_write
    )


def test_anthropic_usage_fixture() -> None:
    row = _load("anthropic.json")
    model = get_model("anthropic", "claude-haiku-4-5-20251001")
    _assert_usage(normalize_anthropic_usage(row["raw"], model), row["expected"])


def test_openai_responses_usage_fixture() -> None:
    row = _load("openai_responses.json")
    model = get_model("openai", "gpt-4o-mini")
    _assert_usage(normalize_openai_responses_usage(row["raw"], model), row["expected"])


def test_openai_completions_usage_fixture() -> None:
    row = _load("openai_completions.json")
    model = get_model("openai", "gpt-4o-mini-chat-completions")
    _assert_usage(normalize_openai_completions_usage(row["raw"], model), row["expected"])


def test_openai_compatible_cache_write_fixture() -> None:
    row = _load("openai_compatible_cache_write.json")
    model = get_model("opencode", "glm-5")
    usage = normalize_openai_completions_usage(row["raw"], model)
    _assert_usage(usage, row["expected"])
    assert usage.cache_read == 60

