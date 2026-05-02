"""Conservative token accounting for context governance."""

from __future__ import annotations

import json
from typing import Any

from ai_provider.types import Usage


def calculate_context_tokens(usage: Usage | None) -> int:
    if usage is None:
        return 0
    return (
        max(0, int(usage.input))
        + max(0, int(usage.output))
        + max(0, int(usage.cache_read))
        + max(0, int(usage.cache_write))
    )


def estimate_text_tokens(text: str) -> int:
    return max(1, (len(text) + 2) // 3) if text else 0


def estimate_value_tokens(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return estimate_text_tokens(value)
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, exclude_none=True)
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        text = str(value)
    return estimate_text_tokens(text)


def estimate_messages_tokens(messages: list[Any]) -> int:
    return sum(estimate_value_tokens(message) for message in messages)


def estimate_entry_tokens(entry: Any) -> int:
    payload = getattr(entry, "payload", entry)
    return estimate_value_tokens(payload)


__all__ = [
    "calculate_context_tokens",
    "estimate_entry_tokens",
    "estimate_messages_tokens",
    "estimate_text_tokens",
    "estimate_value_tokens",
]
