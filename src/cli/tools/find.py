"""Python-native `find` tool."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from agent_core.runtime_types import AbortSignal, ToolUpdateCallback
from agent_core.types import AgentToolResult

from ._result import resolved_path_details, text_result
from .definitions import ToolDefinition, ToolExecutionContext, object_schema
from .truncate import DEFAULT_MAX_BYTES, format_size, truncate_head

DEFAULT_RESULT_LIMIT = 1000


def create_find_tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name="find",
        label="find",
        description=(
            "Find files by glob pattern under cwd. Hidden files are included. "
            f"Output is truncated to {DEFAULT_RESULT_LIMIT} results or {format_size(DEFAULT_MAX_BYTES)}."
        ),
        parameters=object_schema(
            {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "limit": {"type": "number", "minimum": 1},
            },
            required=["pattern"],
        ),
        execute=execute_find,
    )


async def execute_find(
    args: dict[str, Any],
    context: ToolExecutionContext,
    signal: AbortSignal | None,
    _on_update: ToolUpdateCallback | None,
) -> AgentToolResult:
    root = Path(context.policy_decision.resolved_paths.get("path", ""))
    logical_path = str(args.get("path") or ".")
    if not root.exists():
        return text_result(f"Path not found: {logical_path}", details=resolved_path_details(logical_path, root), is_error=True)

    pattern = str(args["pattern"])
    limit = int(args.get("limit") or DEFAULT_RESULT_LIMIT)
    matches: list[str] = []
    for path in _iter_paths(root):
        if signal is not None and signal.is_set():
            return text_result("Operation aborted", details=resolved_path_details(logical_path, root), is_error=True)
        rel = _display_path(path, root)
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern):
            matches.append(rel)
            if len(matches) >= limit:
                break
    if not matches:
        return text_result("No files found matching pattern", details=_details(root, logical_path, limit))

    truncation = truncate_head("\n".join(matches), max_lines=limit)
    text = truncation.content
    details = _details(root, logical_path, limit, truncation=truncation)
    if len(matches) >= limit:
        text += f"\n\n[{limit} results limit reached.]"
        details["resultLimitReached"] = limit
    return text_result(text, details=details)


def _iter_paths(root: Path):
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def _display_path(path: Path, root: Path) -> str:
    base = root if root.is_dir() else root.parent
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def _details(
    root: Path,
    logical_path: str,
    limit: int,
    *,
    truncation: Any | None = None,
) -> dict[str, Any]:
    details = {
        **resolved_path_details(logical_path, root),
        "resultLimit": limit,
    }
    if truncation is not None:
        details["truncation"] = truncation.to_details()
    return details


__all__ = ["create_find_tool_definition", "execute_find"]
