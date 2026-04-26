"""Single-line truncated text primitive."""

from __future__ import annotations

from collections.abc import Callable

from tui.component import Component
from tui.width import truncate_to_width


class TruncatedText(Component):
    def __init__(
        self,
        content: str,
        *,
        ellipsis: str = "…",
        style: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__()
        self.content = content
        self.ellipsis = ellipsis
        self.style = style

    def render(self, width: int) -> list[str]:
        content = self.content.replace("\n", " ")
        line = truncate_to_width(content, width, ellipsis=self.ellipsis)
        if self.style is not None:
            line = self.style(line)
        return [line]


__all__ = ["TruncatedText"]
