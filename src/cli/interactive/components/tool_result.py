"""Renders a :class:`ToolResultMessage` (architecture line 959–971 row 3).

Used when the agent loop emits a tool result message into the transcript
(separate from the live :class:`ToolExecutionComponent`, which represents
the in-progress execution itself).
"""

from __future__ import annotations

from typing import Any

from ai_provider.types import ToolResultMessage
from tui.component import Component
from tui.width import pad_to_width, truncate_to_width, wrap_to_width


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
        flag = "✗" if self.message.is_error else "✓"
        head = pad_to_width(
            f"\x1b[36m▎ tool result {flag} {self.message.tool_name}"
            f" ({self.message.tool_call_id})\x1b[0m",
            width,
        )
        rows: list[str] = [head]
        read_rows = self._read_rows(width)
        if read_rows is not None:
            rows.extend(read_rows)
            rows.append(pad_to_width("", width))
            return self.enforce_width(rows, width)
        rows.extend(self._generic_body_rows(width))
        rows.append(pad_to_width("", width))
        return self.enforce_width(rows, width)

    def _generic_body_rows(self, width: int) -> list[str]:
        rows: list[str] = []
        body = _summarise_blocks(self.message)
        for line in wrap_to_width(body, max(1, width - 2)):
            rows.append(pad_to_width(f"  {truncate_to_width(line, width - 2)}", width))
        return rows

    def _read_rows(self, width: int) -> list[str] | None:
        if self.message.tool_name != "read" or self.message.is_error or width < 50:
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
        gutter_width = max(len(str(line_end)), len(str(line_start)))
        rows = [pad_to_width(f"  {path}:{line_start}-{line_end}", width)]
        for index, line in enumerate(file_lines, start=line_start):
            prefix = f"  {index:>{gutter_width}} | "
            available = max(1, width - len(prefix))
            wrapped = wrap_to_width(line, available) or [""]
            for wrapped_index, wrapped_line in enumerate(wrapped):
                current_prefix = prefix if wrapped_index == 0 else " " * len(prefix)
                rows.append(
                    pad_to_width(
                        current_prefix
                        + truncate_to_width(wrapped_line, max(1, width - len(current_prefix))),
                        width,
                    )
                )
        for line in notice_lines:
            for wrapped_line in wrap_to_width(line, max(1, width - 2)) or [""]:
                rows.append(pad_to_width(f"  {truncate_to_width(wrapped_line, width - 2)}", width))
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


__all__ = ["ToolResultComponent"]
