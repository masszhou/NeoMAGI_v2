"""Python-native `read` tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core.runtime_types import AbortSignal, ToolUpdateCallback
from agent_core.types import AgentToolResult

from ._result import resolved_path_details, text_result
from .definitions import ToolDefinition, ToolExecutionContext, object_schema
from .safe_file_ops import safe_read_bytes
from .truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, format_size, truncate_head


def create_read_tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name="read",
        label="read",
        description=(
            "Read a UTF-8 text file under the current working directory. "
            f"Output is truncated to {DEFAULT_MAX_LINES} lines or {format_size(DEFAULT_MAX_BYTES)}."
        ),
        parameters=object_schema(
            {
                "path": {"type": "string", "description": "Path to the file to read"},
                "offset": {"type": "number", "description": "1-indexed line offset", "minimum": 1},
                "limit": {"type": "number", "description": "Maximum number of lines", "minimum": 1},
            },
            required=["path"],
        ),
        execute=execute_read,
    )


async def execute_read(
    args: dict[str, Any],
    context: ToolExecutionContext,
    _signal: AbortSignal | None,
    _on_update: ToolUpdateCallback | None,
) -> AgentToolResult:
    resolved = Path(context.policy_decision.resolved_paths.get("path", ""))
    logical_path = str(args.get("path") or ".")
    try:
        resolved, data = safe_read_bytes(context.cwd, logical_path)
    except OSError as exc:
        return text_result(str(exc), details=resolved_path_details(logical_path, resolved), is_error=True)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return text_result(
            "Unsupported binary file: read only supports UTF-8 text in M5.",
            details=resolved_path_details(logical_path, resolved),
            is_error=True,
        )

    lines = text.split("\n")
    offset = int(args.get("offset") or 1)
    if offset < 1:
        offset = 1
    if offset > len(lines):
        return text_result(
            f"Offset {offset} is beyond end of file ({len(lines)} lines total).",
            details=resolved_path_details(logical_path, resolved),
            is_error=True,
        )
    limit = int(args["limit"]) if args.get("limit") is not None else None
    selected = lines[offset - 1 : offset - 1 + limit] if limit is not None else lines[offset - 1 :]
    selected_text = "\n".join(selected)
    truncation = truncate_head(selected_text)
    output = _with_read_notice(truncation.content, truncation, offset, len(lines), limit)
    details = {
        **resolved_path_details(logical_path, resolved),
        "lineStart": offset,
        "lineEnd": offset + max(truncation.output_lines, 1) - 1,
        "totalLines": len(lines),
        "outputLines": truncation.output_lines,
        "truncation": truncation.to_details(),
    }
    return text_result(output, details=details)


def _with_read_notice(
    output: str,
    truncation: Any,
    offset: int,
    total_lines: int,
    limit: int | None,
) -> str:
    if truncation.first_line_exceeds_limit:
        return f"[Line {offset} exceeds {format_size(truncation.max_bytes)} limit.]"
    if truncation.truncated:
        end = offset + truncation.output_lines - 1
        return f"{output}\n\n[Showing lines {offset}-{end} of {total_lines}. Use offset={end + 1} to continue.]"
    if limit is not None and offset - 1 + limit < total_lines:
        next_offset = offset + limit
        remaining = total_lines - next_offset + 1
        return f"{output}\n\n[{remaining} more lines in file. Use offset={next_offset} to continue.]"
    return output


__all__ = ["create_read_tool_definition", "execute_read"]
