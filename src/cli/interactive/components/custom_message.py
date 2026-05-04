"""Renders a :class:`CustomMessage` (extension-supplied) — row 5."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cli.core.session_types import CustomMessage
from tui.component import Component
from tui.width import wrap_to_width

from ..transcript import SYSTEM, blank, header_row, row


def _flatten(message: CustomMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    parts: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)  # type: ignore[union-attr]
        else:
            parts.append("[image]")
    return "\n".join(parts)


class CustomMessageComponent(Component):
    def __init__(
        self,
        message: CustomMessage,
        *,
        renderer: Callable[[CustomMessage, dict[str, Any]], Any] | None = None,
        on_renderer_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        super().__init__()
        self.message: CustomMessage = message
        self._renderer = renderer
        self._on_renderer_error = on_renderer_error

    def render(self, width: int) -> list[str]:
        if not self.message.display:
            return []
        rendered = self._render_extension(width)
        if rendered is not None:
            return self.enforce_width(rendered, width)
        return self._render_generic(width)

    def _render_extension(self, width: int) -> list[str] | None:
        if self._renderer is None:
            return None
        try:
            rendered = self._renderer(self.message, {"width": width})
            if isinstance(rendered, str):
                return [row(line, width) for line in rendered.splitlines() or [""]]
            if isinstance(rendered, list) and all(isinstance(item, str) for item in rendered):
                return [row(item, width) for item in rendered]
            raise TypeError(f"unsupported custom renderer result: {type(rendered).__name__}")
        except Exception as exc:
            if self._on_renderer_error is not None:
                self._on_renderer_error(self.message.custom_type, exc)
            return None

    def _render_generic(self, width: int) -> list[str]:
        rows: list[str] = [header_row(f"custom: {self.message.custom_type}", width, color=SYSTEM)]
        for line in wrap_to_width(_flatten(self.message), max(1, width - 2)):
            rows.append(row(f"  {line}", width))
        rows.append(blank(width))
        return self.enforce_width(rows, width)


__all__ = ["CustomMessageComponent"]
