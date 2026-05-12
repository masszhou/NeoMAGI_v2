"""Exact replacement `edit` tool."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from agent_core.runtime_types import AbortSignal, ToolUpdateCallback
from agent_core.types import AgentToolResult

from ._result import resolved_path_details, text_result
from .definitions import ToolDefinition, ToolExecutionContext, object_schema
from .mutation_queue import with_file_mutation_queue


def create_edit_tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name="edit",
        label="edit",
        description=(
            "Edit one file using exact text replacements. Each edits[].oldText "
            "must match exactly once in the original file."
        ),
        parameters=object_schema(
            {
                "path": {"type": "string"},
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "items": object_schema(
                        {
                            "oldText": {"type": "string"},
                            "newText": {"type": "string"},
                        },
                        required=["oldText", "newText"],
                    ),
                },
            },
            required=["path", "edits"],
        ),
        execute=execute_edit,
        prepare_arguments=prepare_edit_arguments,
        execution_mode="sequential",
    )


def prepare_edit_arguments(input_value: Any) -> Any:
    if not isinstance(input_value, dict):
        return input_value
    args = dict(input_value)
    edits = args.get("edits")
    if isinstance(edits, str):
        import json

        try:
            parsed = json.loads(edits)
            if isinstance(parsed, list):
                args["edits"] = parsed
        except json.JSONDecodeError:
            pass
    if isinstance(args.get("oldText"), str) and isinstance(args.get("newText"), str):
        existing = args.get("edits") if isinstance(args.get("edits"), list) else []
        args["edits"] = [*existing, {"oldText": args.pop("oldText"), "newText": args.pop("newText")}]
    return args


async def execute_edit(
    args: dict[str, Any],
    context: ToolExecutionContext,
    signal: AbortSignal | None,
    _on_update: ToolUpdateCallback | None,
) -> AgentToolResult:
    resolved = Path(context.policy_decision.resolved_paths.get("path", ""))
    logical_path = str(args.get("path") or ".")
    return await with_file_mutation_queue(
        resolved,
        lambda: _apply_edit(args, resolved, logical_path, signal),
    )


async def _apply_edit(
    args: dict[str, Any],
    resolved: Path,
    logical_path: str,
    signal: AbortSignal | None,
) -> AgentToolResult:
    if signal is not None and signal.is_set():
        return text_result("Operation aborted", details=resolved_path_details(logical_path, resolved), is_error=True)
    try:
        original = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return text_result(str(exc), details=resolved_path_details(logical_path, resolved), is_error=True)
    except UnicodeDecodeError:
        return text_result("Unsupported binary file.", details=resolved_path_details(logical_path, resolved), is_error=True)

    edits = args["edits"]
    error = _validate_edits(original, edits)
    if error:
        return text_result(error, details=resolved_path_details(logical_path, resolved), is_error=True)

    updated = original
    for edit in edits:
        updated = updated.replace(edit["oldText"], edit["newText"], 1)
    if signal is not None and signal.is_set():
        return text_result("Operation aborted", details=resolved_path_details(logical_path, resolved), is_error=True)
    resolved.write_text(updated, encoding="utf-8")
    diff = _unified_diff(logical_path, original, updated)
    first_changed = _first_changed_line(original, updated)
    details = {
        **resolved_path_details(logical_path, resolved),
        "unifiedDiff": diff,
        "firstChangedLine": first_changed,
    }
    return text_result(f"Successfully replaced {len(edits)} block(s) in {logical_path}.", details=details)


def _validate_edits(original: str, edits: Any) -> str | None:
    if not isinstance(edits, list) or not edits:
        return "edits must contain at least one replacement"
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return f"edit {index} must be an object"
        old = edit.get("oldText")
        new = edit.get("newText")
        if not isinstance(old, str) or not isinstance(new, str):
            return f"edit {index} must include oldText and newText strings"
        count = original.count(old)
        if count == 0:
            return f"oldText for edit {index} was not found"
        if count > 1:
            return f"oldText for edit {index} is ambiguous ({count} matches)"
    return None


def _unified_diff(path: str, original: str, updated: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
    )


def _first_changed_line(original: str, updated: str) -> int | None:
    old_lines = original.splitlines()
    new_lines = updated.splitlines()
    for index, (old, new) in enumerate(zip(old_lines, new_lines, strict=False), start=1):
        if old != new:
            return index
    if len(old_lines) != len(new_lines):
        return min(len(old_lines), len(new_lines)) + 1
    return None


__all__ = ["create_edit_tool_definition", "execute_edit", "prepare_edit_arguments"]
