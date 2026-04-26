"""Renders a :class:`CustomMessage` (extension-supplied) — row 5."""

from __future__ import annotations

from cli.core.session_types import CustomMessage
from tui.component import Component
from tui.width import pad_to_width, wrap_to_width


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
    def __init__(self, message: CustomMessage) -> None:
        super().__init__()
        self.message: CustomMessage = message

    def render(self, width: int) -> list[str]:
        if not self.message.display:
            return []
        head = f"\x1b[36m▎ custom: {self.message.custom_type}\x1b[0m"
        rows: list[str] = [pad_to_width(head, width)]
        for line in wrap_to_width(_flatten(self.message), max(1, width - 2)):
            rows.append(pad_to_width(f"  {line}", width))
        rows.append(pad_to_width("", width))
        return self.enforce_width(rows, width)


__all__ = ["CustomMessageComponent"]
