"""Renders a :class:`BashExecutionMessage` (architecture line 959–971 row 4).

``excludeFromContext`` is rendered as a visual hint so the user knows the
bash output won't be re-fed into the LLM context.
"""

from __future__ import annotations

from cli.core.session_types import BashExecutionMessage
from tui.component import Component
from tui.width import pad_to_width, wrap_to_width


class BashExecutionComponent(Component):
    def __init__(self, message: BashExecutionMessage) -> None:
        super().__init__()
        self.message: BashExecutionMessage = message

    def render(self, width: int) -> list[str]:
        excluded = self.message.exclude_from_context
        excl_tag = " [no-context]" if excluded else ""
        head = f"\x1b[35m▎ bash{excl_tag}\x1b[0m  $ {self.message.command}"
        rows: list[str] = [pad_to_width(head, width)]
        for line in wrap_to_width(self.message.output, max(1, width - 4)):
            rows.append(pad_to_width(f"  \x1b[2m{line}\x1b[0m", width))
        status_bits: list[str] = []
        if self.message.exit_code is not None:
            status_bits.append(f"exit={self.message.exit_code}")
        if self.message.cancelled:
            status_bits.append("cancelled")
        if self.message.truncated:
            status_bits.append("truncated")
        if status_bits:
            rows.append(pad_to_width(f"  \x1b[2m({', '.join(status_bits)})\x1b[0m", width))
        rows.append(pad_to_width("", width))
        return self.enforce_width(rows, width)


__all__ = ["BashExecutionComponent"]
