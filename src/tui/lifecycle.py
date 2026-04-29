"""Process-level lifecycle around :class:`TerminalSession` + :class:`TUIApp`.

``TerminalSession`` already handles raw mode / bracketed paste / SIGWINCH
within a context manager. This module layers on:

- ``atexit`` registration so an interpreter shutdown still restores cooked
  mode.
- ``SIGINT`` / ``SIGTERM`` handlers that exit the loop cleanly.
- An exception trailer that prints a short traceback to stderr **after**
  restoring the terminal, so the message isn't lost in raw-mode garbage.

Critical: SIGWINCH stays the property of ``TerminalSession`` (single owner
per ADR-0015 §影响 `src/tui/terminal.py`); this module never touches it.
"""

from __future__ import annotations

import atexit
import signal
import sys
import traceback
from collections.abc import Callable
from contextlib import contextmanager
from types import FrameType
from typing import Iterator

from .app import TUIApp


@contextmanager
def lifecycle(
    app: TUIApp,
    *,
    on_exit: Callable[[], None] | None = None,
) -> Iterator[TUIApp]:
    """Wrap an app run with terminal entry/exit + signal handlers.

    Usage::

        app = TUIApp(...)
        with lifecycle(app):
            app.run()
    """

    app.terminal.enter()
    cleanup = _make_cleanup(app, on_exit)
    atexit.register(cleanup)
    old_int, old_term = _install_exit_signal_handlers(app)

    try:
        yield app
    except SystemExit:
        cleanup()
        raise
    except BaseException as exc:
        cleanup()
        # Print AFTER terminal restoration so the traceback is readable.
        print(
            f"\nNeoMAGI TUI crashed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        raise
    else:
        cleanup()
    finally:
        _restore_exit_signal_handlers(old_int, old_term)


__all__ = ["lifecycle"]


def _make_cleanup(
    app: TUIApp,
    on_exit: Callable[[], None] | None,
) -> Callable[[], None]:
    cleanup_called = {"done": False}

    def cleanup() -> None:
        if cleanup_called["done"]:
            return
        cleanup_called["done"] = True
        try:
            if on_exit is not None:
                on_exit()
        except Exception:
            pass
        _place_cursor_after_frame(app)
        app.terminal.exit()

    return cleanup


def _install_exit_signal_handlers(app: TUIApp) -> tuple[object, object]:
    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)

    def _on_signal(_sig: int, _frame: FrameType | None) -> None:
        app.exit()

    try:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
    except (ValueError, OSError):
        pass
    return old_int, old_term


def _restore_exit_signal_handlers(old_int: object, old_term: object) -> None:
    try:
        signal.signal(signal.SIGINT, old_int)  # type: ignore[arg-type]
        signal.signal(signal.SIGTERM, old_term)  # type: ignore[arg-type]
    except (ValueError, OSError, TypeError):
        pass


def _place_cursor_after_frame(app: TUIApp) -> None:
    if getattr(app.terminal, "is_tty", True) is False:
        return
    if getattr(app, "render_mode", "canvas") == "command":
        app.clear_live_region()
        app.terminal.write("\r\n")
        return
    bottom = app.renderer.last_bottom_row()
    if bottom is None:
        return
    rows = app.terminal.size().rows
    if bottom < rows:
        app.terminal.write(f"\x1b[{bottom + 1};1H\x1b[2K")
    else:
        app.terminal.write(f"\x1b[{rows};1H\r\n")
