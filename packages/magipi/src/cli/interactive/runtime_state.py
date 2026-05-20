"""Small state and display helpers for ``InteractiveAgentRuntime``."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ai_provider.types import Usage, UsageCost


@dataclass(frozen=True, slots=True)
class RuntimeState:
    is_running: bool
    queued_steering: tuple[str, ...]
    queued_follow_up: tuple[str, ...]
    model_ref: str
    runtime_session_id: str
    provider_cache_affinity_id: str | None
    durable_session_id: str | None = None
    current_leaf_entry_id: str | None = None


def tool_text(result: Any) -> str:
    parts = []
    for block in result.content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            parts.append(str(block.text))
    return "\n".join(parts)


def display_name(value: str | None) -> str:
    return value or "(unnamed)"


def leaf_ref(value: str | None) -> str:
    if not value:
        return "none"
    return f"entry:{value[:8]}"


def now_ms() -> int:
    return int(time.time() * 1000)


def empty_usage() -> Usage:
    return Usage(
        input=0,
        output=0,
        cacheRead=0,
        cacheWrite=0,
        totalTokens=0,
        cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
    )


__all__ = [
    "RuntimeState",
    "display_name",
    "empty_usage",
    "leaf_ref",
    "now_ms",
    "tool_text",
]
