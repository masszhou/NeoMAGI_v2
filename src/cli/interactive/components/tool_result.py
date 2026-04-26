"""Renders a :class:`ToolResultMessage` (architecture line 959–971 row 3).

Used when the agent loop emits a tool result message into the transcript
(separate from the live :class:`ToolExecutionComponent`, which represents
the in-progress execution itself).
"""

from __future__ import annotations

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
        body = _summarise_blocks(self.message)
        for line in wrap_to_width(body, max(1, width - 2)):
            rows.append(pad_to_width(f"  {truncate_to_width(line, width - 2)}", width))
        rows.append(pad_to_width("", width))
        return self.enforce_width(rows, width)


__all__ = ["ToolResultComponent"]
