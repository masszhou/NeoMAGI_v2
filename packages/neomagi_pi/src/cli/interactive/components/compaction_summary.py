"""Renders a :class:`CompactionSummaryMessage` — row 7."""

from __future__ import annotations

from cli.core.session_types import CompactionSummaryMessage
from tui.component import Component
from tui.width import wrap_to_width

from ..transcript import SUMMARY, blank, header_row, row


class CompactionSummaryComponent(Component):
    def __init__(self, message: CompactionSummaryMessage) -> None:
        super().__init__()
        self.message: CompactionSummaryMessage = message

    def render(self, width: int) -> list[str]:
        rows: list[str] = [
            header_row(
                f"compaction summary  (tokensBefore={self.message.tokens_before})",
                width,
                color=SUMMARY,
            )
        ]
        for line in wrap_to_width(self.message.summary, max(1, width - 2)):
            rows.append(row(f"  {line}", width))
        rows.append(blank(width))
        return self.enforce_width(rows, width)


__all__ = ["CompactionSummaryComponent"]
