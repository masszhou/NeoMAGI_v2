"""Small helpers for built-in tool results."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core.types import AgentToolResult


def text_result(text: str, *, details: dict[str, Any] | None = None, is_error: bool = False) -> AgentToolResult:
    return AgentToolResult(
        content=[{"type": "text", "text": text}],
        details=details or {},
        isError=is_error,
    )


def resolved_path_details(logical_path: str | None, resolved: Path) -> dict[str, Any]:
    return {
        "path": logical_path or ".",
        "resolvedPath": str(resolved),
    }


__all__ = ["resolved_path_details", "text_result"]
