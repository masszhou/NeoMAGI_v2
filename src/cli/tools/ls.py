"""Python-native `ls` tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core.runtime_types import AbortSignal, ToolUpdateCallback
from agent_core.types import AgentToolResult

from ._result import resolved_path_details, text_result
from .definitions import ToolDefinition, ToolExecutionContext, object_schema
from .truncate import truncate_head

DEFAULT_ENTRY_LIMIT = 200


def create_ls_tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name="ls",
        label="ls",
        description="List directory entries under cwd. Hidden files are included.",
        parameters=object_schema(
            {
                "path": {"type": "string"},
                "limit": {"type": "number", "minimum": 1},
            },
        ),
        execute=execute_ls,
    )


async def execute_ls(
    args: dict[str, Any],
    context: ToolExecutionContext,
    _signal: AbortSignal | None,
    _on_update: ToolUpdateCallback | None,
) -> AgentToolResult:
    root = Path(context.policy_decision.resolved_paths.get("path", ""))
    logical_path = str(args.get("path") or ".")
    limit = int(args.get("limit") or DEFAULT_ENTRY_LIMIT)
    if not root.exists():
        return text_result(f"Path not found: {logical_path}", details=_details(root, logical_path, limit), is_error=True)
    if root.is_file():
        return text_result(root.name, details=_details(root, logical_path, limit))

    entries = []
    for child in sorted(root.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower(), item.name)):
        marker = "/" if child.is_dir() else ""
        entries.append(f"{child.name}{marker}")
        if len(entries) >= limit:
            break
    truncation = truncate_head("\n".join(entries), max_lines=limit)
    text = truncation.content or "(empty directory)"
    details = _details(root, logical_path, limit, truncation=truncation)
    if len(entries) >= limit:
        text += f"\n\n[{limit} entries limit reached.]"
        details["entryLimitReached"] = limit
    return text_result(text, details=details)


def _details(
    root: Path,
    logical_path: str,
    limit: int,
    *,
    truncation: Any | None = None,
) -> dict[str, Any]:
    details = {
        **resolved_path_details(logical_path, root),
        "entryLimit": limit,
    }
    if truncation is not None:
        details["truncation"] = truncation.to_details()
    return details


__all__ = ["create_ls_tool_definition", "execute_ls"]
