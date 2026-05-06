"""Shared durable tool execution record type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


__all__ = ["ToolExecutionRecord"]
