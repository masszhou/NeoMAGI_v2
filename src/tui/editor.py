"""Multi-line prompt editor.

Substrate-only — knows about :class:`KeyEvent` / :class:`PasteEvent`,
column widths, and a small :class:`Action` table. **Does not** dispatch to
agent / playback; the upstream :class:`InteractiveController` decides what
SUBMIT means in mock vs. real mode.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .component import Component, CursorMarker
from .keymap import Action, Keymap
from .stdin_buffer import KeyEvent, PasteEvent
from .width import pad_to_width, visible_width, wrap_to_width

PROMPT = "> "


class EditorState(str, Enum):
    IDLE = "idle"
    STREAMING = "streaming"
    ABORTING = "aborting"


@dataclass
class EditorBuffer:
    """Mutable text buffer with a single cursor position.

    Newlines are preserved; the renderer wraps each logical line via
    :func:`tui.width.wrap_to_width` so CJK / emoji land in the right
    column.
    """

    text: str = ""
    cursor: int = 0
    history: list[str] = field(default_factory=list)
    history_index: int | None = None

    def insert(self, snippet: str) -> None:
        self.text = self.text[: self.cursor] + snippet + self.text[self.cursor :]
        self.cursor += len(snippet)
        self.history_index = None

    def backspace(self) -> None:
        if self.cursor > 0:
            self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
            self.cursor -= 1
            self.history_index = None

    def delete(self) -> None:
        if self.cursor < len(self.text):
            self.text = self.text[: self.cursor] + self.text[self.cursor + 1 :]
            self.history_index = None

    def move_left(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def move_right(self) -> None:
        self.cursor = min(len(self.text), self.cursor + 1)

    def move_home(self) -> None:
        nl = self.text.rfind("\n", 0, self.cursor)
        self.cursor = nl + 1 if nl != -1 else 0

    def move_end(self) -> None:
        nl = self.text.find("\n", self.cursor)
        self.cursor = nl if nl != -1 else len(self.text)

    def history_prev(self) -> None:
        if not self.history:
            return
        if self.history_index is None:
            self.history_index = len(self.history) - 1
        else:
            self.history_index = max(0, self.history_index - 1)
        self.text = self.history[self.history_index]
        self.cursor = len(self.text)

    def history_next(self) -> None:
        if self.history_index is None:
            return
        self.history_index += 1
        if self.history_index >= len(self.history):
            self.history_index = None
            self.text = ""
        else:
            self.text = self.history[self.history_index]
        self.cursor = len(self.text)

    def take(self) -> str:
        text = self.text
        if text.strip():
            self.history.append(text)
        self.text = ""
        self.cursor = 0
        self.history_index = None
        return text


@dataclass(frozen=True)
class EditorSubmission:
    """What ``SUBMIT`` produced. The interactive layer routes this to either
    a queued steering message (streaming) or a fresh user prompt (idle)."""

    text: str
    state_at_submit: EditorState


SubmitHandler = Callable[[EditorSubmission], None]
ActionHandler = Callable[[Action], None]
BufferChangeHandler = Callable[[str], None]


class Editor(Component):
    """Focusable component: editor + status footer + cursor marker."""

    fail_fast_on_overflow = False

    def __init__(
        self,
        keymap: Keymap | None = None,
        *,
        on_submit: SubmitHandler | None = None,
        on_action: ActionHandler | None = None,
        on_buffer_change: BufferChangeHandler | None = None,
    ) -> None:
        super().__init__()
        self._keymap: Keymap = keymap or Keymap()
        self._buffer: EditorBuffer = EditorBuffer()
        self._on_submit: SubmitHandler | None = on_submit
        self._on_action: ActionHandler | None = on_action
        self._on_buffer_change: BufferChangeHandler | None = on_buffer_change
        self._state: EditorState = EditorState.IDLE
        self._footer: str = "ready"
        self._last_body_width: int = 80
        self._last_seen_text: str = ""

    # ------------------------------------------------------------------ #
    # Public knobs                                                        #
    # ------------------------------------------------------------------ #

    @property
    def buffer(self) -> EditorBuffer:
        return self._buffer

    @property
    def state(self) -> EditorState:
        return self._state

    def set_state(self, state: EditorState) -> None:
        self._state = state
        self.request_render()

    def set_footer(self, text: str) -> None:
        self._footer = text
        self.request_render()

    def set_on_submit(self, handler: SubmitHandler) -> None:
        self._on_submit = handler

    def set_on_action(self, handler: ActionHandler) -> None:
        self._on_action = handler

    def set_on_buffer_change(self, handler: BufferChangeHandler) -> None:
        self._on_buffer_change = handler

    # ------------------------------------------------------------------ #
    # Component contract                                                  #
    # ------------------------------------------------------------------ #

    def render(self, width: int) -> list[str]:
        body_width = max(1, width - len(PROMPT))
        self._last_body_width = body_width
        text = self._buffer.text or ""
        lines = wrap_to_width(text if text else "", body_width)
        if not lines:
            lines = [""]
        rendered = [PROMPT + lines[0]] + ["  " + ln for ln in lines[1:]]
        # Pad each line to full width so the diff renderer sees stable
        # row widths and old characters get cleared on shrink.
        rendered = [pad_to_width(ln, width) for ln in rendered]
        rendered.append(pad_to_width(self._render_footer(width), width))
        return self.enforce_width(rendered, width)

    def _render_footer(self, width: int) -> str:
        state_label = {
            EditorState.IDLE: "idle",
            EditorState.STREAMING: "streaming",
            EditorState.ABORTING: "aborting",
        }[self._state]
        text = f"[{state_label}] {self._footer}"
        return text[:width]

    def cursor_marker(self) -> CursorMarker | None:
        """Where the cursor sits in this component's local frame."""

        body_width = max(1, self._last_body_width)
        before = self._buffer.text[: self._buffer.cursor]
        nl = before.rfind("\n")
        if nl == -1:
            row = 0
            col_chars = before
        else:
            # Sum lines split by '\n' in the wrapped output. We approximate
            # by counting how many wrap chunks a single logical line yields.
            row = before.count("\n")
            col_chars = before[nl + 1 :]
        # Account for word-wrap within the current logical line.
        wrapped_above = 0
        for logical_line in self._buffer.text.split("\n")[:row]:
            wrapped = wrap_to_width(logical_line, body_width)
            wrapped_above += len(wrapped) if wrapped else 1
        wrapped = wrap_to_width(col_chars, body_width)
        if not wrapped:
            wrap_row = 0
            col_in_wrap = ""
        else:
            wrap_row = len(wrapped) - 1
            col_in_wrap = wrapped[-1]
        prompt_offset = len(PROMPT) if (row == 0 and wrap_row == 0) else 2
        return CursorMarker(
            row=wrapped_above + wrap_row,
            col=prompt_offset + visible_width(col_in_wrap),
        )

    def handle_input(self, event: Any) -> None:
        if isinstance(event, PasteEvent):
            self._buffer.insert(event.text)
            self.request_render()
            self._notify_buffer_change()
            return
        if not isinstance(event, KeyEvent):
            return
        action = self._keymap.resolve(event)
        if action == Action.INSERT:
            if len(event.key) == 1 and event.key.isprintable():
                self._buffer.insert(event.key)
            elif event.key == " ":
                self._buffer.insert(" ")
            self.request_render()
            self._notify_buffer_change()
            return
        # Trigger actions: insert the printable key first so the user can
        # actually type ``/quit`` / ``@path`` / ``!cmd`` (the keystroke is
        # only an autocomplete *trigger* — Pi keeps the character in the
        # buffer). The controller-level autocomplete UI then layers on top
        # via ``on_action``. PASTE_IMAGE / AUTOCOMPLETE are *not* printable
        # keys so they pass through directly.
        if action in (Action.SLASH_TRIGGER, Action.AT_TRIGGER, Action.BANG_TRIGGER):
            if len(event.key) == 1 and event.key.isprintable():
                self._buffer.insert(event.key)
            self.request_render()
            self._notify_buffer_change()
            if self._on_action is not None:
                self._on_action(action)
            return
        self._dispatch_action(action, event)
        self._notify_buffer_change()

    def _notify_buffer_change(self) -> None:
        if self._on_buffer_change is None:
            return
        text = self._buffer.text
        if text == self._last_seen_text:
            return
        self._last_seen_text = text
        self._on_buffer_change(text)

    def _dispatch_action(self, action: Action, event: KeyEvent) -> None:
        if action == Action.SUBMIT:
            text = self._buffer.take()
            if self._on_submit is not None:
                self._on_submit(EditorSubmission(text=text, state_at_submit=self._state))
            self.request_render()
            return
        if action == Action.QUEUE_NEWLINE:
            self._buffer.insert("\n")
            self.request_render()
            return
        if action == Action.BACKSPACE:
            self._buffer.backspace()
            self.request_render()
            return
        if action == Action.DELETE:
            self._buffer.delete()
            self.request_render()
            return
        if action == Action.CURSOR_LEFT:
            self._buffer.move_left()
            self.request_render()
            return
        if action == Action.CURSOR_RIGHT:
            self._buffer.move_right()
            self.request_render()
            return
        if action == Action.CURSOR_HOME:
            self._buffer.move_home()
            self.request_render()
            return
        if action == Action.CURSOR_END:
            self._buffer.move_end()
            self.request_render()
            return
        if action == Action.HISTORY_PREV:
            self._buffer.history_prev()
            self.request_render()
            return
        if action == Action.HISTORY_NEXT:
            self._buffer.history_next()
            self.request_render()
            return
        # Anything else (autocomplete trigger, abort, slash trigger, paste
        # image, etc.) bubbles up to the controller.
        if self._on_action is not None:
            self._on_action(action)


__all__ = [
    "Editor",
    "EditorBuffer",
    "EditorState",
    "EditorSubmission",
    "PROMPT",
]
