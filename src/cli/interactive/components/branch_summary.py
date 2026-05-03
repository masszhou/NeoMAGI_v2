"""Renders a :class:`BranchSummaryMessage` — row 6."""

from __future__ import annotations

from cli.core.session_types import BranchSummaryMessage
from tui.component import Component
from tui.width import wrap_to_width

from ..transcript import BRANCH, blank, header_row, row


class BranchSummaryComponent(Component):
    def __init__(self, message: BranchSummaryMessage) -> None:
        super().__init__()
        self.message: BranchSummaryMessage = message

    def render(self, width: int) -> list[str]:
        rows: list[str] = [
            header_row(f"branch summary  (from {self.message.from_id})", width, color=BRANCH)
        ]
        for line in wrap_to_width(self.message.summary, max(1, width - 2)):
            rows.append(row(f"  {line}", width))
        rows.append(blank(width))
        return self.enforce_width(rows, width)


__all__ = ["BranchSummaryComponent"]
