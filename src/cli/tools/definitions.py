"""Product-level coding tool definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from agent_core.runtime_types import AbortSignal, ToolUpdateCallback
from agent_core.types import AgentToolResult, ToolExecutionMode
from policy.types import PolicyDecision

ToolName = Literal["read", "bash", "edit", "write", "grep", "find", "ls"]


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    tool_call_id: str
    cwd: str
    policy_decision: PolicyDecision
    runtime_session_id: str | None = None
    run_id: str | None = None


ToolExecute = Callable[
    [dict[str, Any], ToolExecutionContext, AbortSignal | None, ToolUpdateCallback | None],
    AgentToolResult | Awaitable[AgentToolResult],
]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: ToolName
    label: str
    description: str
    parameters: dict[str, Any]
    execute: ToolExecute
    # May be called by both agent_core and the governed wrapper, so repairs must be idempotent.
    prepare_arguments: Callable[[Any], Any] | None = None
    execution_mode: ToolExecutionMode | None = None


def object_schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    additional_properties: bool = False,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional_properties,
    }


__all__ = [
    "ToolDefinition",
    "ToolExecutionContext",
    "ToolExecute",
    "ToolName",
    "object_schema",
]
