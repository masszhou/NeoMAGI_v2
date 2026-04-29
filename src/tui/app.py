"""Generic ``TUIApp`` substrate runtime.

Composes :class:`TerminalSession`, :class:`StdinBuffer`, :class:`Renderer`
and a focus stack of :class:`Component` instances. Knows nothing about
agent / message / tool semantics — those live in
``src/cli/interactive/app.py`` (per ADR-0015 + plan acceptance §9).
"""

from __future__ import annotations

import os
import select
import sys
import threading
import time
from collections.abc import Callable
from typing import TextIO

from .component import Component, CursorMarker, CursorPosition
from .renderer import Renderer
from .stdin_buffer import (
    Event,
    KeyEvent,
    PasteEvent,
    ResizeEvent,
    StdinBuffer,
)
from .terminal import TerminalSession
from .terminal import TerminalSize

InputHook = Callable[[Event], bool]
"""Pre-dispatch hook. Return ``True`` to indicate the event was handled
and shouldn't be forwarded to the focused component."""


class TUIApp:
    """Substrate event loop.

    Public surface (intentionally minimal — agent-aware wrappers belong in
    ``cli.interactive``):

    - :meth:`attach_root` / :meth:`attach_overlay`
    - :meth:`set_focus`
    - :meth:`request_render`
    - :meth:`inject_input` / :meth:`simulate_resize`
    - :meth:`run` / :meth:`exit`
    - :meth:`add_input_hook` (used by ``InteractiveController`` for global
      keys like Ctrl+C / double Esc)

    Forbidden: importing ``agent_core`` / ``cli.core`` / ``ai_provider``.
    Plan §完成标准 #9 makes this a hard rule.
    """

    def __init__(
        self,
        *,
        terminal: TerminalSession | None = None,
        renderer: Renderer | None = None,
        stdin_buffer: StdinBuffer | None = None,
        out_stream: TextIO | None = None,
        tick_interval: float = 0.012,
        anchor_reserved_height: int | None = None,
    ) -> None:
        self._out: TextIO = out_stream if out_stream is not None else sys.stdout
        self._terminal: TerminalSession = terminal or TerminalSession(
            out_stream=self._out
        )
        self._renderer: Renderer = renderer or Renderer(out_stream=self._out)
        self._stdin: StdinBuffer = stdin_buffer or StdinBuffer()
        self._tick_interval: float = tick_interval

        self._root: Component | None = None
        self._overlays: list[Component] = []
        self._focus: Component | None = None
        self._input_hooks: list[InputHook] = []

        self._render_requested: bool = True
        self._running: bool = False
        self._cols: int = 100
        self._rows: int = 30
        self._anchor_row: int = 1
        self._anchor_dirty: bool = True
        self._anchor_reserved_height: int | None = (
            None if anchor_reserved_height is None else max(1, anchor_reserved_height)
        )
        self._injected: list[Event] = []
        self._focus_offset_provider: Callable[[Component, int], int | None] | None = (
            None
        )
        """Optional resolver for focused components nested inside a composite
        root. If the substrate's default walk (root + overlays) doesn't find
        the focused component, this provider gets a chance to translate the
        focus into a row offset so the cursor positions correctly. Set by
        the interactive controller, which composes status / messages /
        editor under a single root node."""
        self._wake_callbacks: list[tuple[float, Callable[[], None]]] = []
        """Sorted-ish list of callbacks due at monotonic timestamps. Used by
        transient components for passive expiry and by spinner-like widgets
        for frame advancement without direct terminal writes."""
        self._wake_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Component tree                                                      #
    # ------------------------------------------------------------------ #

    def attach_root(self, component: Component) -> None:
        if self._root is not None:
            self._root.detach()
        self._root = component
        component.attach(self.request_render)
        self.request_render()

    def attach_overlay(self, component: Component) -> None:
        component.attach(self.request_render)
        self._overlays.append(component)
        self.request_render()

    def detach_overlay(self, component: Component) -> None:
        if component in self._overlays:
            self._overlays.remove(component)
            component.detach()
            self.request_render()

    def set_focus(self, component: Component | None) -> None:
        # Maintain a `focused` flag on each Component so render paths can
        # show a clear visual indicator (inverse video, focus glyph, etc.)
        # — manual §4.4 reported the prior cursor-only signal was too
        # subtle on macOS Terminal.app to tell whether Tab actually moved
        # focus into the picker.
        if self._focus is not None and self._focus is not component:
            self._focus.focused = False
        self._focus = component
        if component is not None:
            component.focused = True
        self.request_render()

    def set_focus_offset_provider(
        self, provider: Callable[[Component, int], int | None] | None
    ) -> None:
        self._focus_offset_provider = provider

    @property
    def focused(self) -> Component | None:
        return self._focus

    @property
    def renderer(self) -> Renderer:
        return self._renderer

    @property
    def terminal(self) -> TerminalSession:
        return self._terminal

    # ------------------------------------------------------------------ #
    # Render scheduling                                                   #
    # ------------------------------------------------------------------ #

    def request_render(self) -> None:
        self._render_requested = True

    def schedule_wake(self, when: float) -> None:
        """Register a monotonic timestamp at which the loop must redraw,
        even with no input pending. Transient components call this to
        guarantee TTL-style expiry shows up on screen."""

        self.schedule_callback(when, lambda: None)

    def schedule_callback(self, when: float, fn: Callable[[], None]) -> None:
        """Run ``fn`` once the event loop reaches ``when``, then redraw."""

        with self._wake_lock:
            self._wake_callbacks.append((when, fn))

    def _consume_render_request(self) -> bool:
        if not self._render_requested:
            return False
        self._render_requested = False
        return True

    def _check_wakeups(self) -> None:
        """Promote any scheduled wake-up that has come due into a render
        request, then drop it from the queue."""

        import time as _time

        now = _time.monotonic()
        with self._wake_lock:
            if not self._wake_callbacks:
                return
            due = [item for item in self._wake_callbacks if item[0] <= now]
            if due:
                self._wake_callbacks = [
                    item for item in self._wake_callbacks if item[0] > now
                ]
        if not due:
            return
        for _, callback in due:
            try:
                callback()
            except Exception:
                continue
        self._render_requested = True

    # ------------------------------------------------------------------ #
    # Input                                                               #
    # ------------------------------------------------------------------ #

    def add_input_hook(self, hook: InputHook) -> None:
        self._input_hooks.append(hook)

    def inject_input(self, event: Event) -> None:
        """Synchronous input injection — used by playback harness and tests.

        The event is processed at the next loop tick (or immediately if the
        loop is currently waiting for input).
        """

        self._injected.append(event)

    def simulate_resize(self, cols: int, rows: int) -> None:
        """The single public resize entry — `lifecycle.py`'s SIGWINCH handler
        and the playback harness funnel here. Avoids a second ``on_resize``
        override path."""

        self._cols = max(20, cols)
        self._rows = max(5, rows)
        self._renderer.reset()
        self._anchor_dirty = True
        self._injected.append(ResizeEvent(cols=self._cols, rows=self._rows))
        self.request_render()

    # ------------------------------------------------------------------ #
    # Run loop                                                            #
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        size = self._terminal.size()
        self._cols = size.cols
        self._rows = size.rows
        self._running = True
        self._terminal.install_resize_handler(self.simulate_resize)
        try:
            while self._running:
                events = self._collect_input_events()
                for event in events:
                    self._dispatch(event)

                self._check_wakeups()
                if self._consume_render_request():
                    self._draw()

                if not events:
                    time.sleep(self._tick_interval)
        finally:
            self._running = False

    def step(self) -> None:
        """Process pending injected events + one render. Test-friendly."""

        for event in self._drain_injected():
            self._dispatch(event)
        self._check_wakeups()
        if self._consume_render_request():
            self._draw()

    def exit(self) -> None:
        self._running = False

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    def _collect_input_events(self) -> list[Event]:
        events: list[Event] = self._drain_injected()
        if self._terminal.is_active and sys.stdin.isatty():
            fd = sys.stdin.fileno()
            try:
                while select.select([fd], [], [], 0)[0]:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break
                    self._stdin.feed(chunk)
            except OSError:
                pass
            events.extend(self._stdin.drain())
        return events

    def _drain_injected(self) -> list[Event]:
        out, self._injected = self._injected, []
        return out

    def _dispatch(self, event: Event) -> None:
        for hook in self._input_hooks:
            try:
                if hook(event):
                    return
            except Exception:
                # Hooks must not crash the loop; log via stderr in dev.
                continue
        if isinstance(event, ResizeEvent):
            return  # Already handled by simulate_resize → renderer.reset.
        if self._focus is not None and isinstance(event, KeyEvent | PasteEvent):
            self._focus.handle_input(event)
            self.request_render()

    def _draw(self) -> None:
        if self._anchor_dirty:
            self._prepare_anchor(TerminalSize(cols=self._cols, rows=self._rows))
        frame = self._compose_frame()
        cursor = self._compose_cursor(frame)
        self._renderer.present(frame, cursor=cursor)

    def _prepare_anchor(self, size: TerminalSize) -> None:
        result = self._terminal.query_cursor_row()
        if result.leftover:
            self._stdin.feed(result.leftover)

        rows = max(1, size.rows)
        anchor = 1
        if result.row is not None:
            anchor = min(max(1, result.row), rows)
            available = rows - anchor + 1
            reserved = self._reserved_anchor_height(rows)
            if available < reserved and result.fallback_allowed:
                self._terminal.write("\n" * (reserved - available))
                anchor = self._bottom_reserved_anchor(rows)
        elif result.fallback_allowed:
            self._terminal.write("\n" * rows)
            anchor = self._bottom_reserved_anchor(rows)

        self._anchor_row = anchor
        self._renderer.set_anchor(anchor)
        self._anchor_dirty = False

    def _bottom_reserved_anchor(self, rows: int) -> int:
        reserved = self._reserved_anchor_height(rows)
        return max(1, rows - reserved + 1)

    def _reserved_anchor_height(self, rows: int) -> int:
        rows = max(1, rows)
        if self._anchor_reserved_height is None:
            return rows
        return min(self._anchor_reserved_height, rows)

    def _compose_frame(self) -> list[str]:
        lines: list[str] = []
        available_rows = max(1, self._rows - self._anchor_row + 1)
        # Render overlays first so we know how much vertical space they
        # claim — the root then gets the *remaining* height to do its
        # height-aware composition. Without this the message column
        # overflows the screen and the editor (which root pins at the
        # bottom of its output) gets clipped off, exactly the §4.9
        # "history doesn't accumulate" complaint.
        overlay_lines: list[str] = []
        for overlay in self._overlays:
            overlay_lines.extend(overlay.render(self._cols))
        if self._root is not None:
            available = max(1, available_rows - len(overlay_lines))
            render_with_height = getattr(self._root, "render_with_height", None)
            if callable(render_with_height):
                lines.extend(render_with_height(self._cols, available))
            else:
                lines.extend(self._root.render(self._cols))
        lines.extend(overlay_lines)
        # Final fail-safe: a non-height-aware root or a tall overlay
        # could still overflow. Clip from the *top* so the editor and
        # newest messages stay visible (they sit at the bottom of root).
        if len(lines) > available_rows:
            lines = lines[-available_rows:]
        else:
            lines.extend([""] * (available_rows - len(lines)))
        return lines

    def _compose_cursor(self, frame: list[str]) -> CursorPosition | None:
        if self._focus is None:
            return None
        marker = self._focus.cursor_marker()
        if marker is None or not isinstance(marker, CursorMarker):
            return None
        # Locate the focused component's first row in the composed frame.
        offset = self._focused_row_offset()
        if offset is None:
            return None
        return CursorPosition(
            row=offset + marker.row + 1,
            col=marker.col + 1,
            visible=True,
        )

    def _focused_row_offset(self) -> int | None:
        # Direct match on the root or any overlay first — that's the cheap
        # path the substrate-only tests exercise.
        offset = 0
        if self._root is not None:
            if self._focus is self._root:
                return 0
            # Nested focus providers may depend on cached height-aware render
            # state. Consult them before a plain root.render() call can
            # overwrite that cache.
            if self._focus is not None and self._focus_offset_provider is not None:
                provided = self._focus_offset_provider(self._focus, self._cols)
                if provided is not None:
                    return provided
            offset += len(self._root.render(self._cols))
        for overlay in self._overlays:
            if self._focus is overlay:
                return offset
            offset += len(overlay.render(self._cols))
        # Nested focus (e.g. interactive controller's editor inside a
        # composite root) — defer to the provider the controller installs.
        if self._focus is not None and self._focus_offset_provider is not None:
            return self._focus_offset_provider(self._focus, self._cols)
        return None


__all__ = ["TUIApp", "InputHook"]


# ---------------------------------------------------------------------- #
# Static guard: this module must NOT import agent_core / cli.core /      #
# ai_provider types. Enforced at test-time too (see                      #
# tests/cli/interactive/test_event_router.py).                            #
# ---------------------------------------------------------------------- #
_FORBIDDEN_IMPORTS = {"agent_core", "cli.core", "ai_provider"}
