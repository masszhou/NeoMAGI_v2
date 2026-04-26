"""Renders a :class:`BranchSummaryMessage` — row 6."""

from __future__ import annotations

from cli.core.session_types import BranchSummaryMessage
from tui.component import Component
from tui.width import pad_to_width, wrap_to_width


class BranchSummaryComponent(Component):
    def __init__(self, message: BranchSummaryMessage) -> None:
        super().__init__()
        self.message: BranchSummaryMessage = message

    def render(self, width: int) -> list[str]:
        head = f"\x1b[33m▎ branch summary  (from {self.message.from_id})\x1b[0m"
        rows: list[str] = [pad_to_width(head, width)]
        for line in wrap_to_width(self.message.summary, max(1, width - 2)):
            rows.append(pad_to_width(f"  {line}", width))
        rows.append(pad_to_width("", width))
        return self.enforce_width(rows, width)


__all__ = ["BranchSummaryComponent"]
