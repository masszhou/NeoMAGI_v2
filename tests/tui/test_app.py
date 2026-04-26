"""TUIApp substrate scheduling and anchor tests."""

from __future__ import annotations

import io

from tui.app import TUIApp
from tui.renderer import Renderer
from tui.stdin_buffer import KeyEvent
from tui.terminal import CursorQueryResult, TerminalSize


class _FakeTerminal:
    def __init__(self, result: CursorQueryResult) -> None:
        self.result = result
        self.writes: list[str] = []
        self.query_calls = 0

    def query_cursor_row(self):
        self.query_calls += 1
        return self.result

    def write(self, data: str) -> None:
        self.writes.append(data)

    def size(self) -> TerminalSize:
        return TerminalSize(cols=20, rows=10)

    def install_resize_handler(self, _callback):
        return None

    @property
    def is_active(self) -> bool:
        return False


def _make_app(result: CursorQueryResult) -> tuple[TUIApp, Renderer, _FakeTerminal]:
    out = io.StringIO()
    renderer = Renderer(out_stream=out)
    terminal = _FakeTerminal(result)
    app = TUIApp(
        terminal=terminal,  # type: ignore[arg-type]
        renderer=renderer,
        out_stream=out,
        anchor_reserved_height=8,
    )
    app._cols = 20  # noqa: SLF001
    app._rows = 10  # noqa: SLF001
    return app, renderer, terminal


def test_prepare_anchor_uses_dsr_row_and_replays_leftover() -> None:
    app, renderer, terminal = _make_app(
        CursorQueryResult(row=3, leftover=b"x", attempted=True, fallback_allowed=True)
    )
    app._prepare_anchor(TerminalSize(cols=20, rows=10))  # noqa: SLF001
    assert renderer.anchor_row == 3
    assert app._anchor_row == 3  # noqa: SLF001
    assert terminal.writes == []
    events = app._stdin.drain()  # noqa: SLF001
    assert events == [KeyEvent("x", raw="x")]


def test_prepare_anchor_scrolls_when_dsr_row_is_too_low() -> None:
    app, renderer, terminal = _make_app(
        CursorQueryResult(row=9, leftover=b"", attempted=True, fallback_allowed=True)
    )
    app._prepare_anchor(TerminalSize(cols=20, rows=10))  # noqa: SLF001
    assert renderer.anchor_row == 3
    assert terminal.writes == ["\n" * 6]


def test_prepare_anchor_timeout_uses_bottom_reserved_fallback() -> None:
    app, renderer, terminal = _make_app(
        CursorQueryResult(row=None, leftover=b"", attempted=True, fallback_allowed=True)
    )
    app._prepare_anchor(TerminalSize(cols=20, rows=10))  # noqa: SLF001
    assert renderer.anchor_row == 3
    assert terminal.writes == ["\n" * 10]


def test_prepare_anchor_non_tty_keeps_default_without_writes() -> None:
    app, renderer, terminal = _make_app(
        CursorQueryResult(
            row=None, leftover=b"", attempted=False, fallback_allowed=False
        )
    )
    app._prepare_anchor(TerminalSize(cols=20, rows=10))  # noqa: SLF001
    assert renderer.anchor_row == 1
    assert terminal.writes == []


def test_resize_marks_anchor_dirty_without_querying() -> None:
    app, _, terminal = _make_app(
        CursorQueryResult(row=5, leftover=b"", attempted=True, fallback_allowed=True)
    )
    app.simulate_resize(40, 12)
    assert app._anchor_dirty is True  # noqa: SLF001
    assert terminal.query_calls == 0


def test_draw_after_resize_requeries_anchor_when_dirty() -> None:
    app, renderer, terminal = _make_app(
        CursorQueryResult(row=4, leftover=b"", attempted=True, fallback_allowed=True)
    )
    app.simulate_resize(40, 12)
    app._draw()  # noqa: SLF001
    assert terminal.query_calls == 1
    assert renderer.anchor_row == 4
    assert app._anchor_dirty is False  # noqa: SLF001


def test_schedule_callback_runs_due_callback_before_render(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    app, _, _ = _make_app(
        CursorQueryResult(
            row=None, leftover=b"", attempted=False, fallback_allowed=False
        )
    )
    calls: list[str] = []
    app._render_requested = False  # noqa: SLF001
    app.schedule_callback(1.0, lambda: calls.append("due"))
    now[0] = 1.0
    app._check_wakeups()  # noqa: SLF001
    assert calls == ["due"]
    assert app._render_requested is True  # noqa: SLF001


def test_schedule_wake_keeps_existing_redraw_semantics(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    app, _, _ = _make_app(
        CursorQueryResult(
            row=None, leftover=b"", attempted=False, fallback_allowed=False
        )
    )
    app._render_requested = False  # noqa: SLF001
    app.schedule_wake(2.0)
    now[0] = 2.0
    app._check_wakeups()  # noqa: SLF001
    assert app._render_requested is True  # noqa: SLF001
