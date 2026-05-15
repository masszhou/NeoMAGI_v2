"""Shared durable tool execution record type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .session_utils import duration_from_details as _duration_from_details


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    args: Any
    result_content: Any = None
    result_details: Any = None
    is_error: bool | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    truncation: Any = None
    policy_decision: Any = None
    sandbox: Any = None
    runtime_session_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class _ToolExecutionEndRequest:
    session_id: str
    tool_call_id: str
    tool_name: str
    result_content: Any
    result_details: Any
    is_error: bool
    duration_ms: int | None = None

    @property
    def details(self) -> dict[str, Any]:
        return self.result_details if isinstance(self.result_details, dict) else {}

    @property
    def resolved_duration_ms(self) -> int | None:
        return self.duration_ms or _duration_from_details(self.details)


@dataclass(frozen=True, slots=True)
class _ToolExecutionBase:
    id: str
    args: Any
    started_at: str
    runtime_session_id: str | None
    run_id: str | None


__all__ = ["ToolExecutionRecord"]
