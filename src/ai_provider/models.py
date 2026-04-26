"""Model helpers and typed compatibility options."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .types import Model, ThinkingLevel, Usage, UsageCost
from .usage import calculate_cost


@dataclass(frozen=True, slots=True)
class OpenAICompletionsCompat:
    supports_store: bool | None = None
    supports_developer_role: bool | None = None
    supports_reasoning_effort: bool | None = None
    reasoning_effort_map: dict[ThinkingLevel, str] = field(default_factory=dict)
    supports_usage_in_streaming: bool | None = None
    max_tokens_field: Literal["max_completion_tokens", "max_tokens"] | None = None
    requires_tool_result_name: bool | None = None
    supports_strict_mode: bool | None = None
    send_session_affinity_headers: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def parse_openai_completions_compat(raw: dict[str, Any] | None) -> OpenAICompletionsCompat:
    raw = dict(raw or {})
    known = {
        "supportsStore",
        "supportsDeveloperRole",
        "supportsReasoningEffort",
        "reasoningEffortMap",
        "supportsUsageInStreaming",
        "maxTokensField",
        "requiresToolResultName",
        "supportsStrictMode",
        "sendSessionAffinityHeaders",
    }
    return OpenAICompletionsCompat(
        supports_store=raw.get("supportsStore"),
        supports_developer_role=raw.get("supportsDeveloperRole"),
        supports_reasoning_effort=raw.get("supportsReasoningEffort"),
        reasoning_effort_map=dict(raw.get("reasoningEffortMap") or {}),
        supports_usage_in_streaming=raw.get("supportsUsageInStreaming"),
        max_tokens_field=raw.get("maxTokensField"),
        requires_tool_result_name=raw.get("requiresToolResultName"),
        supports_strict_mode=raw.get("supportsStrictMode"),
        send_session_affinity_headers=raw.get("sendSessionAffinityHeaders"),
        extra={key: value for key, value in raw.items() if key not in known},
    )


def supports_xhigh(model: Model) -> bool:
    return any(
        marker in model.id
        for marker in (
            "gpt-5.2",
            "gpt-5.3",
            "gpt-5.4",
            "opus-4-6",
            "opus-4.6",
            "opus-4-7",
            "opus-4.7",
        )
    )


def supports_reasoning(model: Model) -> bool:
    return model.reasoning


__all__ = [
    "OpenAICompletionsCompat",
    "Usage",
    "UsageCost",
    "calculate_cost",
    "parse_openai_completions_compat",
    "supports_reasoning",
    "supports_xhigh",
]
