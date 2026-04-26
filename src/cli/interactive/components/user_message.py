"""Renders a :class:`UserMessage` (architecture line 959–971 row 1)."""

from __future__ import annotations

from ai_provider.types import TextContent, UserContent, UserMessage
from tui.component import Component
from tui.width import pad_to_width, wrap_to_width


def _flatten_content(content: str | list[UserContent]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        else:
            parts.append(f"[image: {getattr(block, 'mime_type', 'image')}]")
    return "\n".join(parts)


class UserMessageComponent(Component):
    """One bubble per user turn."""

    def __init__(self, message: UserMessage) -> None:
        super().__init__()
        self.message: UserMessage = message

    def render(self, width: int) -> list[str]:
        body = _flatten_content(self.message.content)
        wrapped = wrap_to_width(body, max(1, width - 4))
        if not wrapped:
            wrapped = [""]
        rows = [pad_to_width("\x1b[1m\x1b[34m▎ user\x1b[0m", width)]
        for line in wrapped:
            rows.append(pad_to_width(f"  {line}", width))
        rows.append(pad_to_width("", width))
        return self.enforce_width(rows, width)


__all__ = ["UserMessageComponent"]
