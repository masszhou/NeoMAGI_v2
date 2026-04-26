"""``Component`` abstraction (ADR-0015 §影响 `src/tui/component.py`).

The substrate-level base class for any TUI element. Business code in
``src/cli/interactive/components/`` subclasses this; substrate widgets
(overlay, editor, status bar) too. Knows about width / focus / cursor /
request_render — knows nothing about agent / message / tool semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .width import visible_width


class ComponentOverflowError(RuntimeError):
    """Raised when ``Component.render(width)`` produces a line that exceeds
    the requested width and the component opted into fail-fast instead of
    silent truncation. ADR-0015 §验收 requires substrate to surface this."""


@dataclass(frozen=True)
class CursorPosition:
    """Absolute terminal cursor position. Substrate-internal — not protocol."""

    row: int
    col: int
    visible: bool = True


@dataclass(frozen=True)
class CursorMarker:
    """Component-local cursor marker; ``TUIApp`` translates to absolute
    coordinates before handing to ``Renderer.present``."""

    row: int
    col: int


class Focusable(Protocol):
    """Marker protocol — implemented by components that accept keyboard input.

    Components that only render (status bar, message bubbles) need not
    implement this; they will simply never receive ``handle_input``.
    """

    def handle_input(self, event: Any) -> None: ...


RequestRender = Callable[[], None]


class Component:
    """Substrate component base.

    Subclasses override :meth:`render`. Width-overflow handling is opt-in:
    set :attr:`fail_fast_on_overflow` to ``True`` to raise instead of letting
    a row through wider than ``width``. Default is ``False`` (silent
    truncation via :func:`tui.width.truncate_to_width`), which matches Pi's
    forgiving render path for streaming text.
    """

    fail_fast_on_overflow: bool = False

    def __init__(self) -> None:
        self._request_render: RequestRender | None = None
        self.focused: bool = False
        """Set by ``TUIApp.set_focus`` whenever this component is the
        keyboard-focus owner. Components that look the same in both
        states ignore it; overlays / pickers use it to make the focus
        shift visually obvious — e.g. inverse video on the selected
        row — so the user knows where their next keystroke will land."""

    def attach(self, request_render: RequestRender) -> None:
        """Called by ``TUIApp`` when the component joins the tree."""

        self._request_render = request_render

    def detach(self) -> None:
        self._request_render = None

    def request_render(self) -> None:
        """Schedule a redraw on the next event-loop tick.

        Components MUST NOT directly call ``Renderer.present``; the loop
        merges multiple requests into a single frame.
        """

        if self._request_render is not None:
            self._request_render()

    # ------------------------------------------------------------------ #
    # Subclass hooks                                                      #
    # ------------------------------------------------------------------ #

    def render(self, width: int) -> list[str]:
        """Return the lines that make up this component's frame fragment.

        Each line MUST occupy at most ``width`` terminal columns. Subclasses
        should call :meth:`enforce_width` on their output, or use
        :mod:`tui.width` helpers to be safe.
        """

        raise NotImplementedError

    def handle_input(self, event: Any) -> None:
        """Default: ignore input. Focusable subclasses override."""

    def cursor_marker(self) -> CursorMarker | None:
        """Local cursor position within :meth:`render` output, if any."""

        return None

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def enforce_width(self, lines: list[str], width: int) -> list[str]:
        """Validate or coerce each line to ``width`` columns.

        - If :attr:`fail_fast_on_overflow` is ``True`` and a line is too
          wide, raise :class:`ComponentOverflowError`.
        - Otherwise truncate via the width guard.
        """

        from .width import truncate_to_width

        out: list[str] = []
        for line in lines:
            if visible_width(line) > width:
                if self.fail_fast_on_overflow:
                    raise ComponentOverflowError(
                        f"{type(self).__name__}.render produced a line of "
                        f"{visible_width(line)} columns but width={width}"
                    )
                out.append(truncate_to_width(line, width))
            else:
                out.append(line)
        return out


__all__ = [
    "Component",
    "ComponentOverflowError",
    "CursorMarker",
    "CursorPosition",
    "Focusable",
    "RequestRender",
]
