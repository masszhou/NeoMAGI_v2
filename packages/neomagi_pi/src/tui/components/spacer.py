"""Blank-row layout primitive."""

from __future__ import annotations

from tui.component import Component


class Spacer(Component):
    def __init__(self, rows: int = 1) -> None:
        super().__init__()
        self.rows = max(0, rows)

    def render(self, width: int) -> list[str]:
        return [" " * max(0, width) for _ in range(self.rows)]


__all__ = ["Spacer"]
