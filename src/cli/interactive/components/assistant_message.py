"""Streaming :class:`AssistantMessage` renderer (architecture line 959–971 row 2).

Consumes :class:`AssistantMessageEvent` frames either via the embedded
``MessageUpdateEvent`` path or directly from the bare top-level stream
path. Accumulates partial text / thinking / toolCall content; the final
``done`` / ``error`` frame flips the component into its terminal state
(complete / aborted / errored) so abort-during-stream keeps the partial
text visible per plan §完成标准 #7.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_provider.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    AssistantMessageEvent,
    StreamDone,
    StreamError,
    StreamTextDelta,
    StreamTextEnd,
    StreamTextStart,
    StreamThinkingDelta,
    StreamThinkingEnd,
    StreamThinkingStart,
    StreamToolCallDelta,
    StreamToolCallEnd,
    StreamToolCallStart,
)
from tui.component import Component
from tui.markdown import render_markdown
from tui.width import pad_to_width, wrap_to_width


@dataclass
class _ContentSlot:
    kind: str  # "text" | "thinking" | "toolcall"
    text: str = ""
    tool_name: str | None = None
    tool_call_id: str | None = None


class AssistantMessageComponent(Component):
    """Live-updating assistant turn."""

    def __init__(self, message: AssistantMessage | None = None) -> None:
        super().__init__()
        self.message: AssistantMessage | None = message
        self._slots: list[_ContentSlot] = []
        self.completed: bool = False
        self.aborted: bool = False
        self.error_text: str | None = None
        self.stop_reason: str | None = None
        if message is not None:
            self._load_message(message)

    def apply(self, event: AssistantMessageEvent) -> None:
        """Fold one stream frame into the local content slots."""

        if isinstance(event, StreamTextStart | StreamTextDelta | StreamTextEnd):
            self._apply_text_event(event)
            return
        if isinstance(event, StreamThinkingStart | StreamThinkingDelta | StreamThinkingEnd):
            self._apply_thinking_event(event)
            return
        if isinstance(event, StreamToolCallStart | StreamToolCallDelta | StreamToolCallEnd):
            self._apply_tool_event(event)
            return
        if isinstance(event, StreamDone | StreamError):
            self._apply_terminal_event(event)
            return

    def mark_aborted(self) -> None:
        """Flip terminal state when the controller receives a control-plane
        abort that didn't manifest as a stream error frame."""

        self.aborted = True
        self.completed = True
        self.request_render()

    def complete(self, message: AssistantMessage) -> None:
        self.message = message
        self._load_message(message)
        self.request_render()

    def _ensure_slot(self, index: int, kind: str) -> None:
        while len(self._slots) <= index:
            self._slots.append(_ContentSlot(kind=kind))

    def _apply_text_event(self, event: StreamTextStart | StreamTextDelta | StreamTextEnd) -> None:
        self._ensure_slot(event.content_index, "text")
        if isinstance(event, StreamTextDelta):
            self._slots[event.content_index].text += event.delta
        elif isinstance(event, StreamTextEnd):
            self._slots[event.content_index].text = event.content

    def _apply_thinking_event(
        self,
        event: StreamThinkingStart | StreamThinkingDelta | StreamThinkingEnd,
    ) -> None:
        self._ensure_slot(event.content_index, "thinking")
        if isinstance(event, StreamThinkingDelta):
            self._slots[event.content_index].text += event.delta
        elif isinstance(event, StreamThinkingEnd):
            self._slots[event.content_index].text = event.content

    def _apply_tool_event(
        self,
        event: StreamToolCallStart | StreamToolCallDelta | StreamToolCallEnd,
    ) -> None:
        self._ensure_slot(event.content_index, "toolcall")
        if isinstance(event, StreamToolCallDelta):
            self._slots[event.content_index].text += event.delta
        elif isinstance(event, StreamToolCallEnd):
            self._slots[event.content_index].tool_name = event.tool_call.name
            self._slots[event.content_index].tool_call_id = event.tool_call.id
            self._slots[event.content_index].text = (
                event.tool_call.arguments
                if isinstance(event.tool_call.arguments, str)
                else str(event.tool_call.arguments)
            )

    def _apply_terminal_event(self, event: StreamDone | StreamError) -> None:
        if isinstance(event, StreamDone):
            self._load_message(event.message)
            return

        self.message = event.error
        self.error_text = event.error.error_message
        if event.reason == "aborted":
            self.aborted = True
        else:
            self.stop_reason = "error"

    def _load_message(self, message: AssistantMessage) -> None:
        self._slots = []
        self.completed = True
        self.aborted = message.stop_reason == "aborted"
        self.error_text = message.error_message
        self.stop_reason = message.stop_reason
        for block in message.content:
            if isinstance(block, TextContent):
                self._slots.append(_ContentSlot(kind="text", text=block.text))
            elif isinstance(block, ThinkingContent):
                self._slots.append(_ContentSlot(kind="thinking", text=block.thinking))
            else:
                self._slots.append(
                    _ContentSlot(
                        kind="toolcall",
                        text=str(block.arguments),
                        tool_name=block.name,
                        tool_call_id=block.id,
                    )
                )

    # ------------------------------------------------------------------ #
    # Component contract                                                  #
    # ------------------------------------------------------------------ #

    def render(self, width: int) -> list[str]:
        rows: list[str] = [pad_to_width("\x1b[1m\x1b[32m▎ assistant\x1b[0m", width)]

        for slot in self._slots:
            if slot.kind == "text":
                for line in render_markdown(slot.text or "", max(1, width - 2)):
                    rows.append(pad_to_width(f"  {line}", width))
            elif slot.kind == "thinking":
                rows.append(pad_to_width("  \x1b[2m▸ thinking\x1b[0m", width))
                for line in wrap_to_width(slot.text or "", max(1, width - 4)):
                    rows.append(pad_to_width(f"    \x1b[2m{line}\x1b[0m", width))
            else:  # toolcall
                tool = slot.tool_name or "?"
                rows.append(
                    pad_to_width(
                        f"  \x1b[36m⚙ tool: {tool}\x1b[0m  args={slot.text[:120]}",
                        width,
                    )
                )

        if self.completed and self.aborted:
            rows.append(pad_to_width("  \x1b[33m[aborted — partial output kept]\x1b[0m", width))
        elif self.error_text:
            rows.append(pad_to_width(f"  \x1b[31m[error: {self.error_text}]\x1b[0m", width))
        elif self.completed and self.stop_reason and self.stop_reason != "stop":
            rows.append(pad_to_width(f"  \x1b[2m[stopReason={self.stop_reason}]\x1b[0m", width))

        rows.append(pad_to_width("", width))
        return self.enforce_width(rows, width)


__all__ = ["AssistantMessageComponent"]
