"""Renders a :class:`ToolResultMessage` (architecture line 959–971 row 3).

Used when the agent loop emits a tool result message into the transcript
(separate from the live :class:`ToolExecutionComponent`, which represents
the in-progress execution itself).
"""

from __future__ import annotations

from typing import Any

from ai_provider.types import ToolResultMessage
from tui.component import Component
from tui.width import truncate_to_width, wrap_to_width

from ..transcript import TOOL_ERROR, TOOL_OK, blank, child_rows, header_row, row

_READ_GUTTER_MIN_WIDTH = 50


def _summarise_blocks(message: ToolResultMessage) -> str:
    parts: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)  # type: ignore[union-attr]
        else:
            parts.append("[image]")
    return "\n".join(parts)


class ToolResultComponent(Component):
    def __init__(self, message: ToolResultMessage) -> None:
        super().__init__()
        self.message: ToolResultMessage = message

    def render(self, width: int) -> list[str]:
        status = " [error]" if self.message.is_error else ""
        rows: list[str] = [
            header_row(
                _tool_label(self.message) + status,
                width,
                color=TOOL_ERROR if self.message.is_error else TOOL_OK,
            )
        ]
        read_rows = self._read_rows(width)
        if read_rows is not None:
            rows.extend(read_rows)
            rows.append(blank(width))
            return self.enforce_width(rows, width)
        rows.extend(self._generic_body_rows(width))
        rows.append(blank(width))
        return self.enforce_width(rows, width)

    def _generic_body_rows(self, width: int) -> list[str]:
        body = _summarise_blocks(self.message)
        return child_rows(wrap_to_width(body, max(1, width - 4)), width, max_lines=4)

    def _read_rows(self, width: int) -> list[str] | None:
        if self.message.tool_name != "read" or self.message.is_error:
            return None
        metadata = _read_metadata(self.message.details)
        if metadata is None:
            return None
        line_start, line_end, output_lines, path = metadata
        if output_lines <= 0:
            return None

        body = _summarise_blocks(self.message)
        lines = body.split("\n")
        file_lines = lines[:output_lines]
        notice_lines = lines[output_lines:]
        if width < _READ_GUTTER_MIN_WIDTH:
            rows = child_rows(file_lines, width, max_lines=1)
        else:
            rows = _read_gutter_rows(
                file_lines,
                width,
                line_start=line_start,
                line_end=line_end,
            )
        for line in notice_lines:
            for wrapped_line in wrap_to_width(line, max(1, width - 2)) or [""]:
                rows.append(
                    row(f"  {truncate_to_width(wrapped_line, width - 2)}", width)
                )
        return rows


def _read_metadata(details: Any) -> tuple[int, int, int, str] | None:
    if not isinstance(details, dict):
        return None
    line_start = _int_detail(details, "lineStart")
    line_end = _int_detail(details, "lineEnd")
    output_lines = _int_detail(details, "outputLines")
    if line_start is None or line_end is None or output_lines is None:
        return None
    path = details.get("path")
    return line_start, line_end, output_lines, str(path or "?")


def _int_detail(details: dict[str, Any], key: str) -> int | None:
    value = details.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _read_gutter_rows(
    file_lines: list[str],
    width: int,
    *,
    line_start: int,
    line_end: int,
) -> list[str]:
    rows: list[str] = []
    gutter_width = max(len(str(line_end)), len(str(line_start)))
    for index, line in enumerate(file_lines, start=line_start):
        prefix = f"  {index:>{gutter_width}} | "
        available = max(1, width - len(prefix))
        wrapped = wrap_to_width(line, available) or [""]
        for wrapped_index, wrapped_line in enumerate(wrapped):
            current_prefix = prefix if wrapped_index == 0 else " " * len(prefix)
            rows.append(
                row(
                    current_prefix
                    + truncate_to_width(
                        wrapped_line,
                        max(1, width - len(current_prefix)),
                    ),
                    width,
                )
            )
    return rows


def _tool_label(message: ToolResultMessage) -> str:
    details = message.details if isinstance(message.details, dict) else {}
    path = details.get("path")
    if message.tool_name == "read":
        suffix = ""
        start = details.get("lineStart")
        end = details.get("lineEnd")
        if isinstance(start, int) and isinstance(end, int):
            suffix = f":{start}-{end}"
        return f"Read {path or message.tool_name}{suffix}"
    if message.tool_name == "write":
        return f"Wrote {path or message.tool_name}"
    if message.tool_name == "edit":
        return f"Edited {path or message.tool_name}"
    if message.tool_name == "bash":
        command = details.get("command") or details.get("args", {}).get("command")
        return f"Ran {command or message.tool_name}"
    return f"tool result {message.tool_name} ({message.tool_call_id})"


__all__ = ["ToolResultComponent"]
