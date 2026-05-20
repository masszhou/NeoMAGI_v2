"""Editor / TUI key bindings (architecture line 972–982).

The keymap maps :class:`KeyEvent` (already parsed by ``stdin_buffer``) to
logical actions; nothing here touches escape sequences directly. Core
actions cannot be re-bound by extensions (M8) — they live in
:data:`CORE_KEYS` and are checked by extension registration code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .stdin_buffer import KeyEvent


class Action(str, Enum):
    SUBMIT = "submit"
    QUEUE_NEWLINE = "queue_newline"
    QUEUE_FOLLOWUP = "queue_followup"
    ABORT = "abort"
    DOUBLE_ESCAPE = "double_escape"
    SLASH_TRIGGER = "slash_trigger"
    AT_TRIGGER = "at_trigger"
    BANG_TRIGGER = "bang_trigger"
    DOUBLE_BANG_TRIGGER = "double_bang_trigger"
    PASTE_IMAGE = "paste_image"
    AUTOCOMPLETE = "autocomplete"
    HISTORY_PREV = "history_prev"
    HISTORY_NEXT = "history_next"
    MODEL_CYCLE = "model_cycle"
    CURSOR_LEFT = "cursor_left"
    CURSOR_RIGHT = "cursor_right"
    CURSOR_HOME = "cursor_home"
    CURSOR_END = "cursor_end"
    SCROLL_PAGE_UP = "scroll_page_up"
    SCROLL_PAGE_DOWN = "scroll_page_down"
    BACKSPACE = "backspace"
    DELETE = "delete"
    CLEAR_SCREEN = "clear_screen"
    QUIT = "quit"
    INSERT = "insert"


@dataclass(frozen=True)
class KeyBinding:
    key: str
    action: Action
    core: bool = False


CORE_KEYS: frozenset[str] = frozenset(
    {
        "Esc",
        "Esc Esc",
        "Enter",
        "Shift+Enter",
        "Ctrl+Enter",
        "Alt+Enter",
        "Ctrl+C",
        "Ctrl+L",
        "Ctrl+P",
        "PageUp",
        "PageDown",
        "Tab",
        "/",
        "@",
        "!",
    }
)


def default_bindings() -> list[KeyBinding]:
    return [
        KeyBinding("Enter", Action.SUBMIT, core=True),
        KeyBinding("Shift+Enter", Action.QUEUE_NEWLINE, core=True),
        KeyBinding("Ctrl+Enter", Action.QUEUE_FOLLOWUP, core=True),
        KeyBinding("Alt+Enter", Action.QUEUE_FOLLOWUP, core=True),
        KeyBinding("Esc", Action.ABORT, core=True),
        KeyBinding("Esc Esc", Action.DOUBLE_ESCAPE, core=True),
        KeyBinding("Tab", Action.AUTOCOMPLETE, core=True),
        KeyBinding("Ctrl+C", Action.QUIT, core=True),
        KeyBinding("Ctrl+L", Action.CLEAR_SCREEN, core=True),
        KeyBinding("Ctrl+P", Action.MODEL_CYCLE, core=True),
        KeyBinding("Up", Action.HISTORY_PREV),
        KeyBinding("Down", Action.HISTORY_NEXT),
        KeyBinding("Left", Action.CURSOR_LEFT),
        KeyBinding("Right", Action.CURSOR_RIGHT),
        KeyBinding("Home", Action.CURSOR_HOME),
        KeyBinding("End", Action.CURSOR_END),
        KeyBinding("PageUp", Action.SCROLL_PAGE_UP, core=True),
        KeyBinding("PageDown", Action.SCROLL_PAGE_DOWN, core=True),
        KeyBinding("Ctrl+A", Action.CURSOR_HOME),
        KeyBinding("Ctrl+E", Action.CURSOR_END),
        KeyBinding("Backspace", Action.BACKSPACE),
        KeyBinding("Delete", Action.DELETE),
        KeyBinding("/", Action.SLASH_TRIGGER, core=True),
        KeyBinding("@", Action.AT_TRIGGER, core=True),
        KeyBinding("!", Action.BANG_TRIGGER, core=True),
    ]


class Keymap:
    """Resolve :class:`KeyEvent` to :class:`Action` + degraded fallbacks.

    ``keyboard_protocol_level`` (passed in from
    ``TerminalSession.keyboard_protocol_level``) lets us pick the right
    fallback when modified Enter keys can't be distinguished from plain
    ``Enter``.
    """

    def __init__(self, *, keyboard_protocol_level: int = 1) -> None:
        self.keyboard_protocol_level = keyboard_protocol_level
        self._bindings: dict[str, Action] = {b.key: b.action for b in default_bindings()}
        self._core_keys: frozenset[str] = CORE_KEYS

    def resolve(self, event: KeyEvent) -> Action:
        """Map a :class:`KeyEvent` to the action the editor should execute.

        Falls through to :data:`Action.INSERT` for printable single-char
        keys and the (substrate already filtered) bracketed-paste payload.
        """

        action = self._bindings.get(event.key)
        if action is not None:
            return action
        # Printable single-char fallback: insert as text.
        if len(event.key) == 1 and event.key.isprintable():
            return Action.INSERT
        return Action.INSERT  # Unknown — silently swallow rather than crash.

    def assert_core_unrebindable(self, key: str) -> None:
        """Used by M8 extension registration to refuse rebinding core keys."""

        if key in self._core_keys:
            raise ValueError(f"key {key!r} is core and cannot be rebound by extensions")


__all__ = ["Action", "CORE_KEYS", "KeyBinding", "Keymap", "default_bindings"]
