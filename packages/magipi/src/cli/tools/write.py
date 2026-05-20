"""Complete-file `write` tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core.runtime_types import AbortSignal, ToolUpdateCallback
from agent_core.types import AgentToolResult

from ._result import resolved_path_details, text_result
from .definitions import ToolDefinition, ToolExecutionContext, object_schema
from .mutation_queue import with_file_mutation_queue
from .safe_file_ops import logical_mutation_key, safe_atomic_write_text


def create_write_tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name="write",
        label="write",
        description="Write complete UTF-8 content to one file under cwd.",
        parameters=object_schema(
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            required=["path", "content"],
        ),
        execute=execute_write,
        execution_mode="sequential",
    )


async def execute_write(
    args: dict[str, Any],
    context: ToolExecutionContext,
    signal: AbortSignal | None,
    _on_update: ToolUpdateCallback | None,
) -> AgentToolResult:
    resolved = Path(context.policy_decision.resolved_paths.get("path", ""))
    logical_path = str(args.get("path") or ".")
    return await with_file_mutation_queue(
        logical_mutation_key(context.cwd, logical_path),
        lambda: _write_file(args, context.cwd, resolved, logical_path, signal),
    )


async def _write_file(
    args: dict[str, Any],
    cwd: str,
    resolved: Path,
    logical_path: str,
    signal: AbortSignal | None,
) -> AgentToolResult:
    if signal is not None and signal.is_set():
        return text_result("Operation aborted", details=resolved_path_details(logical_path, resolved), is_error=True)
    try:
        resolved = safe_atomic_write_text(cwd, logical_path, str(args["content"]))
    except Exception as exc:
        return text_result(str(exc), details=resolved_path_details(logical_path, resolved), is_error=True)
    details = resolved_path_details(logical_path, resolved)
    return text_result(f"Successfully wrote {len(str(args['content']))} bytes to {logical_path}.", details=details)


__all__ = ["create_write_tool_definition", "execute_write"]
