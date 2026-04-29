"""TUIApp substrate scheduling and anchor tests."""

from __future__ import annotations

import io
import threading
import time

from tui.app import TUIApp
from tui.component import CursorPosition
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


def _make_app(
    result: CursorQueryResult,
    *,
    anchor_reserved_height: int | None = 8,
    render_mode: str = "canvas",
) -> tuple[TUIApp, Renderer, _FakeTerminal]:
    out = io.StringIO()
    renderer = Renderer(out_stream=out)
    terminal = _FakeTerminal(result)
    app = TUIApp(
        terminal=terminal,  # type: ignore[arg-type]
        renderer=renderer,
        out_stream=out,
        anchor_reserved_height=anchor_reserved_height,
        render_mode=render_mode,  # type: ignore[arg-type]
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


def test_prepare_anchor_default_reserves_full_terminal_height() -> None:
    app, renderer, terminal = _make_app(
        CursorQueryResult(row=9, leftover=b"", attempted=True, fallback_allowed=True),
        anchor_reserved_height=None,
    )
    app._prepare_anchor(TerminalSize(cols=20, rows=10))  # noqa: SLF001
    assert renderer.anchor_row == 1
    assert terminal.writes == ["\n" * 8]


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


def test_command_draw_does_not_prepare_full_screen_anchor() -> None:
    app, _, terminal = _make_app(
        CursorQueryResult(row=9, leftover=b"", attempted=True, fallback_allowed=True),
        render_mode="command",
    )
    app._draw()  # noqa: SLF001
    assert terminal.query_calls == 0
    assert terminal.writes == []


def test_command_resize_clears_old_live_region_on_next_step() -> None:
    out = io.StringIO()
    renderer = Renderer(out_stream=out)
    terminal = _FakeTerminal(
        CursorQueryResult(row=9, leftover=b"", attempted=True, fallback_allowed=True)
    )
    app = TUIApp(
        terminal=terminal,  # type: ignore[arg-type]
        renderer=renderer,
        out_stream=out,
        render_mode="command",
    )
    renderer.present_live(
        ["stale live row", "stale footer"],
        cursor=CursorPosition(row=1, col=1),
    )
    out.truncate(0)
    out.seek(0)

    app.simulate_resize(40, 12)
    app.step()

    text = out.getvalue()
    assert "\x1b[J" in text
    assert "stale live row" not in text


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


def test_schedule_callback_is_thread_safe_under_concurrent_wakes() -> None:
    app, _, _ = _make_app(
        CursorQueryResult(
            row=None, leftover=b"", attempted=False, fallback_allowed=False
        )
    )
    calls: list[int] = []
    total = 200

    def schedule_range(start: int, stop: int) -> None:
        for item in range(start, stop):
            app.schedule_callback(
                time.monotonic() - 1.0,
                lambda item=item: calls.append(item),
            )

    threads = [
        threading.Thread(target=schedule_range, args=(0, total // 2)),
        threading.Thread(target=schedule_range, args=(total // 2, total)),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        app._check_wakeups()  # noqa: SLF001
        if all(not thread.is_alive() for thread in threads):
            with app._wake_lock:  # noqa: SLF001
                if not app._wake_callbacks:  # noqa: SLF001
                    break
        time.sleep(0)

    for thread in threads:
        thread.join(timeout=1.0)
    app._check_wakeups()  # noqa: SLF001
    assert sorted(calls) == list(range(total))
