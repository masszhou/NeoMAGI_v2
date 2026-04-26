"""W0 substrate ``TerminalSession`` tests (limited scope — most invariants
require a real PTY, which CI cannot reliably provide).

These tests run **without** an attached TTY and cover the safe paths:

- ``__enter__`` / ``__exit__`` are idempotent on a non-TTY backend.
- ``size()`` returns sane fallback values when ``os.get_terminal_size``
  raises.
- Resize handler isn't installed in non-POSIX / non-TTY contexts (the
  signal layer is gated by ``hasattr(signal, 'SIGWINCH')``).
"""

from __future__ import annotations

import io

from tui.terminal import CursorQueryResult, TerminalSession, TerminalSize


class _TTYIn:
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 123


class _TTYOut(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_enter_exit_is_safe_without_tty() -> None:
    out = io.StringIO()
    ts = TerminalSession(out_stream=out)
    ts.enter()
    ts.exit()
    ts.enter()
    ts.exit()


def test_size_returns_defaults_on_failure(monkeypatch) -> None:
    out = io.StringIO()
    ts = TerminalSession(out_stream=out)

    def _raise(*_args, **_kwargs):
        raise OSError("no tty")

    monkeypatch.setattr("os.get_terminal_size", _raise)
    size = ts.size()
    assert isinstance(size, TerminalSize)
    assert size.cols >= 20 and size.rows >= 5


def test_install_resize_handler_no_op_when_no_sigwinch(monkeypatch) -> None:
    """When SIGWINCH isn't available the call should silently no-op."""

    import signal

    monkeypatch.delattr(signal, "SIGWINCH", raising=False)
    out = io.StringIO()
    ts = TerminalSession(out_stream=out)
    # Must not raise.
    ts.install_resize_handler(lambda _c, _r: None)


def test_query_cursor_row_success_returns_row_and_leftover(monkeypatch) -> None:
    out = _TTYOut()
    ts = TerminalSession(in_stream=_TTYIn(), out_stream=out)
    reads = [b"before\x1b[5;9Rafter"]

    monkeypatch.setattr("select.select", lambda *_args: ([123], [], []))
    monkeypatch.setattr("os.read", lambda *_args: reads.pop(0))

    result = ts.query_cursor_row()
    assert result == CursorQueryResult(
        row=5,
        leftover=b"beforeafter",
        attempted=True,
        fallback_allowed=True,
    )
    assert out.getvalue() == "\x1b[6n"


def test_query_cursor_row_timeout_keeps_non_dsr_leftover(monkeypatch) -> None:
    out = _TTYOut()
    ts = TerminalSession(in_stream=_TTYIn(), out_stream=out)
    reads = [b"abc"]
    ready = [[123], []]

    monkeypatch.setattr("select.select", lambda *_args: (ready.pop(0), [], []))
    monkeypatch.setattr("os.read", lambda *_args: reads.pop(0))

    result = ts.query_cursor_row(timeout_ms=1)
    assert result.row is None
    assert result.leftover == b"abc"
    assert result.attempted is True
    assert result.fallback_allowed is True
    assert out.getvalue() == "\x1b[6n"


def test_query_cursor_row_non_tty_is_no_op() -> None:
    out = io.StringIO()
    ts = TerminalSession(out_stream=out)
    result = ts.query_cursor_row()
    assert result == CursorQueryResult(
        row=None,
        leftover=b"",
        attempted=False,
        fallback_allowed=False,
    )
    assert out.getvalue() == ""
