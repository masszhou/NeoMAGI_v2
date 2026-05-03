"""Renders a :class:`BashExecutionMessage` (architecture line 959–971 row 4).

``excludeFromContext`` is rendered as a visual hint so the user knows the
bash output won't be re-fed into the LLM context.
"""

from __future__ import annotations

from cli.core.session_types import BashExecutionMessage
from cli.interactive.tool_renderer_registry import single_line_summary
from tui.component import Component
from tui.width import wrap_to_width

from ..transcript import TOOL_ERROR, TOOL_OK, blank, child_rows, header_row, row


class BashExecutionComponent(Component):
    def __init__(self, message: BashExecutionMessage) -> None:
        super().__init__()
        self.message: BashExecutionMessage = message

    def render(self, width: int) -> list[str]:
        excluded = self.message.exclude_from_context
        source = "[!]" if excluded else "[user]"
        command = single_line_summary(self.message.command, limit=max(1, width - 14))
        label = f"Ran {source} {command}"
        if self.message.cancelled:
            label += " [cancelled]"
        elif self.message.exit_code not in (None, 0):
            label += " [error]"
        color = TOOL_ERROR if self.message.cancelled or self.message.exit_code not in (None, 0) else TOOL_OK
        rows: list[str] = [header_row(label, width, color=color)]
        output_lines = wrap_to_width(self.message.output, max(1, width - 4))
        rows.extend(child_rows(output_lines, width, max_lines=5, empty="(no output)"))
        status_bits: list[str] = []
        if self.message.exit_code is not None:
            status_bits.append(f"exit={self.message.exit_code}")
        if self.message.cancelled:
            status_bits.append("cancelled")
        if self.message.truncated:
            status_bits.append("truncated")
        if status_bits:
            rows.append(row(f"  \x1b[2m({', '.join(status_bits)})\x1b[0m", width))
        rows.append(blank(width))
        return self.enforce_width(rows, width)


__all__ = ["BashExecutionComponent"]
