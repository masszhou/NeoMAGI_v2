"""W1 lifecycle tests.

Heavy stty-level checks need a real PTY, which CI doesn't have. These
tests cover the abstraction layer:

- ``lifecycle()`` calls ``enter`` / ``exit`` even on exception.
- ``atexit`` callback runs only once (idempotency).
- SIGINT inside the loop flips ``app.exit``.
"""

from __future__ import annotations

import io
import signal
import time

import pytest

from tui.app import TUIApp
from tui.lifecycle import lifecycle
from tui.renderer import Renderer
from tui.terminal import TerminalSession, TerminalSize


def _make_app() -> TUIApp:
    out = io.StringIO()
    return TUIApp(
        terminal=TerminalSession(out_stream=out),
        renderer=Renderer(out_stream=out),
        out_stream=out,
    )


def test_lifecycle_runs_exit_on_normal_path() -> None:
    app = _make_app()
    with lifecycle(app):
        app.exit()
    assert app.terminal.is_active is False


def test_lifecycle_runs_exit_on_exception() -> None:
    app = _make_app()
    with pytest.raises(RuntimeError):
        with lifecycle(app):
            raise RuntimeError("boom")
    assert app.terminal.is_active is False


def test_lifecycle_idempotent_cleanup() -> None:
    app = _make_app()
    counter = {"calls": 0}

    def cb() -> None:
        counter["calls"] += 1

    with lifecycle(app, on_exit=cb):
        app.exit()
    assert counter["calls"] == 1


def test_sigint_inside_lifecycle_calls_app_exit() -> None:
    """SIGINT delivered while the lifecycle is active must flip ``_running``
    to False — that's how the run loop tears down. We start with
    ``_running=True`` so a False reading after the signal proves the
    lifecycle handler actually ran (rather than just observing the
    constructor default)."""

    if not hasattr(signal, "SIGINT"):
        pytest.skip("SIGINT not available")

    app = _make_app()
    with lifecycle(app):
        app._running = True  # noqa: SLF001 — simulate an active run loop
        signal.raise_signal(signal.SIGINT)
        # Python invokes signal handlers at the next bytecode checkpoint.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and app._running:  # noqa: SLF001
            time.sleep(0.01)
        assert app._running is False, (  # noqa: SLF001
            "SIGINT did not propagate through the lifecycle handler"
        )


class _ExitCursorTerminal:
    def __init__(self, rows: int = 10, is_tty: bool = True) -> None:
        self.rows = rows
        self.is_tty = is_tty
        self.writes: list[str] = []
        self.entered = False

    def enter(self) -> None:
        self.entered = True

    def exit(self) -> None:
        self.entered = False

    def size(self) -> TerminalSize:
        return TerminalSize(cols=20, rows=self.rows)

    def write(self, data: str) -> None:
        self.writes.append(data)

    @property
    def is_active(self) -> bool:
        return self.entered


def _make_exit_cursor_app(
    rows: int = 10,
    *,
    is_tty: bool = True,
) -> tuple[TUIApp, _ExitCursorTerminal]:
    out = io.StringIO()
    terminal = _ExitCursorTerminal(rows=rows, is_tty=is_tty)
    app = TUIApp(
        terminal=terminal,  # type: ignore[arg-type]
        renderer=Renderer(out_stream=out),
        out_stream=out,
    )
    return app, terminal


def test_lifecycle_does_not_move_cursor_without_presented_frame() -> None:
    app, terminal = _make_exit_cursor_app()
    with lifecycle(app):
        app.exit()
    assert terminal.writes == []


def test_lifecycle_does_not_move_cursor_for_non_tty_terminal() -> None:
    app, terminal = _make_exit_cursor_app(is_tty=False)
    app.renderer.present(["a"])
    with lifecycle(app):
        app.exit()
    assert terminal.writes == []


def test_lifecycle_moves_cursor_to_line_after_tui_when_space_remains() -> None:
    app, terminal = _make_exit_cursor_app(rows=10)
    app.renderer.set_anchor(3)
    app.renderer.present(["a", "b"])
    with lifecycle(app):
        app.exit()
    assert "\x1b[5;1H\x1b[2K" in terminal.writes


def test_lifecycle_scrolls_when_tui_ended_at_screen_bottom() -> None:
    app, terminal = _make_exit_cursor_app(rows=10)
    app.renderer.set_anchor(9)
    app.renderer.present(["a", "b"])
    with lifecycle(app):
        app.exit()
    assert "\x1b[10;1H\r\n" in terminal.writes
