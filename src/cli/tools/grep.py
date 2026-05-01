"""Python-native `grep` tool."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from agent_core.runtime_types import AbortSignal, ToolUpdateCallback
from agent_core.types import AgentToolResult

from ._result import resolved_path_details, text_result
from .definitions import ToolDefinition, ToolExecutionContext, object_schema
from .truncate import DEFAULT_MAX_BYTES, GREP_MAX_LINE_LENGTH, format_size, truncate_head, truncate_line

DEFAULT_MATCH_LIMIT = 100


def create_grep_tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name="grep",
        label="grep",
        description=(
            "Search UTF-8 file contents under cwd. Hidden files are included. "
            f"Output is truncated to {DEFAULT_MATCH_LIMIT} matches or {format_size(DEFAULT_MAX_BYTES)}."
        ),
        parameters=object_schema(
            {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
                "ignoreCase": {"type": "boolean"},
                "literal": {"type": "boolean"},
                "context": {"type": "number", "minimum": 0},
                "limit": {"type": "number", "minimum": 1},
            },
            required=["pattern"],
        ),
        execute=execute_grep,
    )


async def execute_grep(
    args: dict[str, Any],
    context: ToolExecutionContext,
    signal: AbortSignal | None,
    _on_update: ToolUpdateCallback | None,
) -> AgentToolResult:
    root = Path(context.policy_decision.resolved_paths.get("path", ""))
    logical_path = str(args.get("path") or ".")
    if not root.exists():
        return text_result(f"Path not found: {logical_path}", details=resolved_path_details(logical_path, root), is_error=True)

    try:
        matcher = _matcher(str(args["pattern"]), bool(args.get("literal")), bool(args.get("ignoreCase")))
    except re.error as exc:
        return text_result(f"Invalid regex: {exc}", details=resolved_path_details(logical_path, root), is_error=True)

    limit = int(args.get("limit") or DEFAULT_MATCH_LIMIT)
    context_lines = max(0, int(args.get("context") or 0))
    glob = args.get("glob")
    output: list[str] = []
    match_count = 0
    line_truncated = False
    for file_path in _iter_files(root):
        if signal is not None and signal.is_set():
            return text_result("Operation aborted", details=resolved_path_details(logical_path, root), is_error=True)
        rel = _display_path(file_path, root)
        if isinstance(glob, str) and glob and not _matches_glob(rel, glob):
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").split("\n")
        except UnicodeDecodeError:
            continue
        except OSError:
            continue
        for index, line in enumerate(lines, start=1):
            if not matcher(line):
                continue
            match_count += 1
            output.extend(_format_match_block(rel, lines, index, context_lines))
            line_truncated = line_truncated or any("[truncated]" in item for item in output[-(context_lines * 2 + 1) :])
            if match_count >= limit:
                return _grep_result(output, args, root, logical_path, limit, line_truncated, match_limit=limit)

    if not output:
        return text_result("No matches found", details=_grep_details(args, root, logical_path, limit, line_truncated))
    return _grep_result(output, args, root, logical_path, limit, line_truncated)


def _matcher(pattern: str, literal: bool, ignore_case: bool):
    if literal:
        needle = pattern.casefold() if ignore_case else pattern

        def match_literal(line: str) -> bool:
            haystack = line.casefold() if ignore_case else line
            return needle in haystack

        return match_literal
    flags = re.IGNORECASE if ignore_case else 0
    regex = re.compile(pattern, flags)
    return lambda line: regex.search(line) is not None


def _iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def _display_path(file_path: Path, root: Path) -> str:
    base = root if root.is_dir() else root.parent
    try:
        return file_path.relative_to(base).as_posix()
    except ValueError:
        return file_path.name


def _matches_glob(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern)


def _format_match_block(rel: str, lines: list[str], line_number: int, context_lines: int) -> list[str]:
    start = max(1, line_number - context_lines)
    end = min(len(lines), line_number + context_lines)
    rows = []
    for current in range(start, end + 1):
        text, _was_truncated = truncate_line(lines[current - 1], GREP_MAX_LINE_LENGTH)
        sep = ":" if current == line_number else "-"
        rows.append(f"{rel}{sep}{current}{sep} {text}")
    return rows


def _grep_result(
    output: list[str],
    args: dict[str, Any],
    root: Path,
    logical_path: str,
    limit: int,
    line_truncated: bool,
    *,
    match_limit: int | None = None,
) -> AgentToolResult:
    truncation = truncate_head("\n".join(output), max_lines=10_000)
    text = truncation.content
    if match_limit is not None:
        text += f"\n\n[{match_limit} matches limit reached.]"
    details = _grep_details(args, root, logical_path, limit, line_truncated, truncation=truncation, match_limit=match_limit)
    return text_result(text, details=details)


def _grep_details(
    args: dict[str, Any],
    root: Path,
    logical_path: str,
    limit: int,
    line_truncated: bool,
    *,
    truncation: Any | None = None,
    match_limit: int | None = None,
) -> dict[str, Any]:
    details = {
        **resolved_path_details(logical_path, root),
        "matchLimit": limit,
        "lineTruncation": {"maxChars": GREP_MAX_LINE_LENGTH, "truncated": line_truncated},
    }
    if truncation is not None:
        details["truncation"] = truncation.to_details()
    if match_limit is not None:
        details["matchLimitReached"] = match_limit
    return details


__all__ = ["create_grep_tool_definition", "execute_grep"]
