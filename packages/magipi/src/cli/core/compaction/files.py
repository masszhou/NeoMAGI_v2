"""Read/modified-file extraction from durable tool executions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from storage.session_repository import ToolExecutionRecord


@dataclass(frozen=True, slots=True)
class FileContext:
    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)

    def details(self) -> dict[str, list[str]]:
        return {
            "readFiles": list(self.read_files),
            "modifiedFiles": list(self.modified_files),
        }


def extract_file_context(records: list[ToolExecutionRecord]) -> FileContext:
    read_files: list[str] = []
    modified_files: list[str] = []
    for record in records:
        args = record.args if isinstance(record.args, dict) else {}
        details = record.result_details if isinstance(record.result_details, dict) else {}
        if _excluded_from_context(args, details):
            continue
        tool = record.tool_name
        if tool == "read":
            _append_unique(read_files, _read_path(args, details))
        elif tool in {"edit", "write"}:
            _append_unique(modified_files, _path(args, details))
    return FileContext(read_files=read_files, modified_files=modified_files)


def _read_path(args: dict[str, Any], details: dict[str, Any]) -> str | None:
    path = _path(args, details)
    if not path:
        return None
    start = details.get("lineStart") or args.get("offset")
    end = details.get("lineEnd")
    if start is not None and end is not None:
        return f"{path}:{start}-{end}"
    if start is not None:
        return f"{path}:{start}"
    return path


def _path(args: dict[str, Any], details: dict[str, Any]) -> str | None:
    for source in (args, details):
        for key in ("path", "logicalPath", "file", "filePath"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _append_unique(values: list[str], value: str | None) -> None:
    if value and value not in values:
        values.append(value)


def _excluded_from_context(args: dict[str, Any], details: dict[str, Any]) -> bool:
    return bool(
        args.get("excludeFromContext")
        or args.get("exclude_from_context")
        or details.get("excludeFromContext")
        or details.get("exclude_from_context")
    )


__all__ = ["FileContext", "extract_file_context"]
