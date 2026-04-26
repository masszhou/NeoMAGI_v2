"""Overlay framework + generic widgets (Loader / Selector / Confirm / SettingsList).

Substrate-only. Business code (session selector, model selector, /quit
confirm, streaming loader) instantiates these from
``src/cli/interactive/`` — overlays here don't import any agent / message
types.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .component import Component, CursorMarker
from .keymap import Action, Keymap
from .stdin_buffer import KeyEvent
from .width import pad_to_width, truncate_to_width

Anchor = Literal["top-left", "bottom-left", "center"]


@dataclass(frozen=True)
class OverlayConfig:
    anchor: Anchor = "center"
    width: int | None = None
    height: int | None = None
    padding: int = 1


class Overlay(Component):
    """Base for floating overlays. Subclasses provide :meth:`render_body`."""

    def __init__(self, *, config: OverlayConfig | None = None) -> None:
        super().__init__()
        self.config: OverlayConfig = config or OverlayConfig()
        self.visible: bool = True
        self.on_close: Callable[[], None] | None = None

    def close(self) -> None:
        self.visible = False
        if self.on_close is not None:
            self.on_close()
        self.request_render()

    def render(self, width: int) -> list[str]:
        if not self.visible:
            return []
        body = self.render_body(width)
        return self.enforce_width(body, width)

    def render_body(self, width: int) -> list[str]:
        raise NotImplementedError


class Loader(Overlay):
    """Single-line spinner; updates via :meth:`tick` from the run loop."""

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str = "working") -> None:
        super().__init__()
        self.label: str = label
        self._frame: int = 0

    def tick(self) -> None:
        self._frame = (self._frame + 1) % len(self._FRAMES)
        self.request_render()

    def render_body(self, width: int) -> list[str]:
        spinner = self._FRAMES[self._frame]
        return [pad_to_width(f"{spinner} {self.label}", width)]


class CancellableLoader(Loader):
    def __init__(
        self,
        label: str = "working",
        *,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(label)
        self.on_cancel: Callable[[], None] | None = on_cancel

    def render_body(self, width: int) -> list[str]:
        spinner = self._FRAMES[self._frame]
        return [pad_to_width(f"{spinner} {self.label}  (Esc to cancel)", width)]

    def handle_input(self, event: Any) -> None:
        if isinstance(event, KeyEvent) and event.key == "Esc":
            if self.on_cancel is not None:
                self.on_cancel()
            self.close()


@dataclass(frozen=True)
class SelectorItem:
    label: str
    detail: str | None = None
    value: Any = None


class Selector(Overlay):
    """Up/Down + Enter list overlay. ``on_select`` receives the chosen item."""

    def __init__(
        self,
        title: str,
        items: Sequence[SelectorItem],
        *,
        on_select: Callable[[SelectorItem], None] | None = None,
        keymap: Keymap | None = None,
    ) -> None:
        super().__init__()
        self.title: str = title
        self.items: list[SelectorItem] = list(items)
        self.index: int = 0
        self.on_select: Callable[[SelectorItem], None] | None = on_select
        self._keymap: Keymap = keymap or Keymap()

    def render_body(self, width: int) -> list[str]:
        # Visual focus signal: bold cyan title bar + an `[active]` tag
        # when the picker has keyboard focus (Tab pressed); plain text
        # otherwise. The selected row uses inverse video while focused so
        # the cursor position is unmistakable on terminals where the
        # default cursor glyph is too subtle (manual §4.4 caught this on
        # macOS Terminal.app).
        if self.focused:
            head_text = f"▎ {self.title}  [active — arrows / Enter / Esc]"
            head = pad_to_width(f"\x1b[1;36m{head_text}\x1b[0m", width)
        else:
            head = pad_to_width(f"▎ {self.title}", width)
        rows: list[str] = [head]
        for i, item in enumerate(self.items):
            cursor = "▶ " if i == self.index else "  "
            text = item.label if item.detail is None else f"{item.label}  ─  {item.detail}"
            line = cursor + truncate_to_width(text, width - 2)
            line = pad_to_width(line, width)
            if i == self.index and self.focused:
                line = f"\x1b[7m{line}\x1b[0m"  # inverse video on focused row
            rows.append(line)
        return rows

    def cursor_marker(self) -> CursorMarker | None:
        return CursorMarker(row=1 + self.index, col=2)

    def handle_input(self, event: Any) -> None:
        if not isinstance(event, KeyEvent):
            return
        action = self._keymap.resolve(event)
        if action == Action.HISTORY_PREV or event.key == "Up":
            self.index = max(0, self.index - 1)
        elif action == Action.HISTORY_NEXT or event.key == "Down":
            self.index = min(len(self.items) - 1, self.index + 1)
        elif action == Action.SUBMIT:
            if self.items and self.on_select is not None:
                self.on_select(self.items[self.index])
            self.close()
            return
        elif event.key == "Esc":
            self.close()
            return
        self.request_render()


class Confirm(Overlay):
    """Yes/No prompt. Returns the choice via :paramref:`on_choose`."""

    def __init__(
        self,
        prompt: str,
        *,
        on_choose: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__()
        self.prompt: str = prompt
        self.choice: bool = False
        self.on_choose: Callable[[bool], None] | None = on_choose

    def render_body(self, width: int) -> list[str]:
        head = pad_to_width(f"▎ {self.prompt}", width)
        yes = "[Y]es" if self.choice else " Yes "
        no = " No  " if self.choice else "[N]o "
        line = f"  {yes}    {no}    Tab toggles, Enter confirms, Esc cancels"
        return [head, pad_to_width(line, width)]

    def handle_input(self, event: Any) -> None:
        if not isinstance(event, KeyEvent):
            return
        key = event.key
        if key in {"Tab", "Left", "Right", " "}:
            self.choice = not self.choice
        elif key in {"y", "Y"}:
            self.choice = True
        elif key in {"n", "N"}:
            self.choice = False
        elif key == "Enter":
            if self.on_choose is not None:
                self.on_choose(self.choice)
            self.close()
            return
        elif key == "Esc":
            if self.on_choose is not None:
                self.on_choose(False)
            self.close()
            return
        self.request_render()


@dataclass(frozen=True)
class SettingsRow:
    label: str
    value: str


class SettingsList(Overlay):
    """Scrollable label/value table — used by ``/hotkeys``, settings, etc."""

    def __init__(self, title: str, rows: Sequence[SettingsRow]) -> None:
        super().__init__()
        self.title: str = title
        self.rows: list[SettingsRow] = list(rows)
        self.scroll: int = 0

    def render_body(self, width: int) -> list[str]:
        head = pad_to_width(f"▎ {self.title}", width)
        out: list[str] = [head]
        label_width = max((len(r.label) for r in self.rows), default=0)
        for row in self.rows[self.scroll :]:
            text = f"  {row.label.ljust(label_width)}   {row.value}"
            out.append(pad_to_width(truncate_to_width(text, width), width))
        return out

    def handle_input(self, event: Any) -> None:
        if not isinstance(event, KeyEvent):
            return
        if event.key == "Esc":
            self.close()
            return
        if event.key == "Down":
            self.scroll = min(max(0, len(self.rows) - 1), self.scroll + 1)
        elif event.key == "Up":
            self.scroll = max(0, self.scroll - 1)
        self.request_render()


class FocusStack:
    """Tracks the active overlay so the run loop knows where input goes."""

    def __init__(self) -> None:
        self._stack: list[Component] = []

    def push(self, component: Component) -> None:
        self._stack.append(component)

    def pop(self) -> Component | None:
        return self._stack.pop() if self._stack else None

    @property
    def top(self) -> Component | None:
        return self._stack[-1] if self._stack else None

    def __len__(self) -> int:
        return len(self._stack)


_now = time.monotonic  # exposed for tests that want to freeze time

__all__ = [
    "Anchor",
    "CancellableLoader",
    "Confirm",
    "FocusStack",
    "Loader",
    "Overlay",
    "OverlayConfig",
    "Selector",
    "SelectorItem",
    "SettingsList",
    "SettingsRow",
]
