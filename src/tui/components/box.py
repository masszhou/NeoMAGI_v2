"""Box layout primitive."""

from __future__ import annotations

from collections.abc import Callable

from tui.component import Component, RequestRender
from tui.width import pad_to_width


class Box(Component):
    def __init__(
        self,
        child: Component,
        *,
        padding: int = 0,
        border: bool = False,
        border_style: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__()
        self.child = child
        self.padding = max(0, padding)
        self.border = border
        self.border_style = border_style

    def attach(self, request_render: RequestRender) -> None:
        super().attach(request_render)
        self.child.attach(request_render)

    def detach(self) -> None:
        super().detach()
        self.child.detach()

    def render(self, width: int) -> list[str]:
        if width <= 0:
            return [""]
        inner_width = max(0, width - (2 if self.border else 0) - self.padding * 2)
        rows = [" " * inner_width for _ in range(self.padding)]
        rows.extend(self.child.render(inner_width))
        rows.extend(" " * inner_width for _ in range(self.padding))
        rows = [self._pad_inner(row, inner_width) for row in rows]
        rows = [(" " * self.padding) + row + (" " * self.padding) for row in rows]
        if not self.border:
            return [pad_to_width(row, width) for row in rows]
        body = [self._style_border(f"│{row}│") for row in rows]
        top = self._style_border("┌" + "─" * (width - 2) + "┐")
        bottom = self._style_border("└" + "─" * (width - 2) + "┘")
        return [top, *body, bottom]

    def _pad_inner(self, text: str, width: int) -> str:
        return pad_to_width(text, width)

    def _style_border(self, text: str) -> str:
        if self.border_style is None:
            return text
        return self.border_style(text)


__all__ = ["Box"]
