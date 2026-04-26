"""Plain wrapped text primitive."""

from __future__ import annotations

from collections.abc import Callable

from tui.component import Component
from tui.width import wrap_to_width


class Text(Component):
    def __init__(
        self,
        content: str,
        *,
        style: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__()
        self.content = content
        self.style = style

    def render(self, width: int) -> list[str]:
        lines = wrap_to_width(self.content, width)
        if self.style is None:
            return lines
        return [self.style(line) for line in lines]


__all__ = ["Text"]
