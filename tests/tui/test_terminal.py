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

from tui.terminal import TerminalSession, TerminalSize


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
