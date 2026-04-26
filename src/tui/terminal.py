"""Terminal lifecycle (ADR-0015 §影响 `src/tui/terminal.py`).

Owns raw mode, bracketed paste, cursor visibility, alt screen, SIGWINCH and
keyboard-protocol negotiation. Business code never touches ``termios`` /
``signal.SIGWINCH`` / raw escape sequences directly — that's a hard rule
from ADR-0015.
"""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType, TracebackType
from typing import IO, TextIO

# Local imports kept minimal so this module is importable in tests that don't
# instantiate the session.

# Escape strings centralised here; the rest of the substrate is forbidden
# from writing these directly.
_BRACKETED_PASTE_ON = "\x1b[?2004h"
_BRACKETED_PASTE_OFF = "\x1b[?2004l"
_CURSOR_HIDE = "\x1b[?25l"
_CURSOR_SHOW = "\x1b[?25h"
_ALT_SCREEN_ON = "\x1b[?1049h"
_ALT_SCREEN_OFF = "\x1b[?1049l"
# xterm modifyOtherKeys=2: encode shift/ctrl on more keys.
_MODIFY_OTHER_KEYS_ON = "\x1b[>4;2m"
_MODIFY_OTHER_KEYS_OFF = "\x1b[>4;0m"
# Kitty keyboard protocol level 1.
_KITTY_KEYS_ON = "\x1b[>1u"
_KITTY_KEYS_OFF = "\x1b[<u"


@dataclass(frozen=True)
class TerminalSize:
    cols: int
    rows: int


class TerminalSession:
    """Context manager that puts the terminal into TUI mode and restores it.

    Use as ``with TerminalSession() as ts: ...``. The exit path runs even if
    the body raises; combined with ``lifecycle.py``'s ``atexit`` + signal
    handlers this gives the four-way restoration guarantee from ADR-0015
    §验收.
    """

    def __init__(
        self,
        *,
        in_stream: IO[bytes] | None = None,
        out_stream: TextIO | None = None,
        use_alt_screen: bool = False,
        hide_cursor: bool = True,
    ) -> None:
        self._in_stream: IO[bytes] | None = in_stream
        self._out_stream: TextIO = out_stream if out_stream is not None else sys.stdout
        self._use_alt_screen = use_alt_screen
        self._hide_cursor = hide_cursor

        self._fd: int | None = None
        self._old_termios: list | None = None  # type: ignore[type-arg]
        self._old_winch: object = None
        self._resize_handler: Callable[[int, int], None] | None = None
        self._entered: bool = False
        self.keyboard_protocol_level: int = 0
        """0 = none (plain ESC sequences only); 1 = modifyOtherKeys=2;
        2 = Kitty level 1. Best-effort — set after enter()."""

    # ------------------------------------------------------------------ #
    # Context manager                                                     #
    # ------------------------------------------------------------------ #

    def __enter__(self) -> TerminalSession:
        self.enter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.exit()

    def enter(self) -> None:
        if self._entered:
            return
        self._entered = True

        if not self._is_tty():
            # Tests / piped runs — skip terminal mode mutation but still
            # flag entered so exit() is a no-op symmetric.
            return

        import termios
        import tty

        fd = sys.stdin.fileno()
        self._fd = fd
        self._old_termios = termios.tcgetattr(fd)
        tty.setraw(fd)

        out = self._out_stream
        if self._use_alt_screen:
            out.write(_ALT_SCREEN_ON)
        if self._hide_cursor:
            out.write(_CURSOR_HIDE)
        out.write(_BRACKETED_PASTE_ON)
        # Best-effort keyboard protocol negotiation; terminals that don't
        # understand will silently ignore.
        out.write(_MODIFY_OTHER_KEYS_ON)
        out.write(_KITTY_KEYS_ON)
        out.flush()
        # We don't have a true DA response loop here; assume level 1 if the
        # caller didn't disable it. M1 acceptance is "best-effort".
        self.keyboard_protocol_level = 1

    def exit(self) -> None:
        if not self._entered:
            return
        self._entered = False

        if self._fd is None:
            return

        out = self._out_stream
        try:
            out.write(_KITTY_KEYS_OFF)
            out.write(_MODIFY_OTHER_KEYS_OFF)
            out.write(_BRACKETED_PASTE_OFF)
            if self._hide_cursor:
                out.write(_CURSOR_SHOW)
            if self._use_alt_screen:
                out.write(_ALT_SCREEN_OFF)
            out.write("\x1b[0m")
            out.flush()
        except (OSError, ValueError):
            # Stream may already be closed during interpreter shutdown.
            pass

        if self._old_termios is not None and self._fd is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_termios)
            except (OSError, ValueError, ImportError):
                pass

        if self._resize_handler is not None and self._old_winch is not None:
            try:
                signal.signal(signal.SIGWINCH, self._old_winch)  # type: ignore[arg-type]
            except (ValueError, OSError, AttributeError):
                pass
            self._resize_handler = None

        self._fd = None
        self._old_termios = None
        self._old_winch = None

    # ------------------------------------------------------------------ #
    # Resize: SIGWINCH single owner                                       #
    # ------------------------------------------------------------------ #

    def install_resize_handler(self, callback: Callable[[int, int], None]) -> None:
        """Register the **single** SIGWINCH handler for this session.

        ``lifecycle.py`` and any other layer must go through this rather
        than calling ``signal.signal(signal.SIGWINCH, ...)`` directly.
        Call from a thread that may install signal handlers (typically the
        main thread).
        """

        if not hasattr(signal, "SIGWINCH"):
            return  # Windows / non-POSIX — silently no-op.

        self._resize_handler = callback
        try:
            self._old_winch = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, self._on_sigwinch)
        except (ValueError, OSError):
            # Not main thread — caller (lifecycle) handles fallback.
            pass

    def _on_sigwinch(self, _signum: int, _frame: FrameType | None) -> None:
        if self._resize_handler is None:
            return
        size = self.size()
        try:
            self._resize_handler(size.cols, size.rows)
        except Exception:
            # Signal handlers must not raise into the interpreter.
            pass

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def size(self) -> TerminalSize:
        try:
            ts = os.get_terminal_size()
            return TerminalSize(cols=max(20, ts.columns), rows=max(5, ts.lines))
        except OSError:
            return TerminalSize(cols=100, rows=30)

    def write(self, data: str) -> None:
        """Write to the output stream. Lifecycle code uses this; business
        components write through ``Renderer.present`` instead."""

        try:
            self._out_stream.write(data)
            self._out_stream.flush()
        except (OSError, ValueError):
            pass

    @property
    def is_active(self) -> bool:
        return self._entered

    def _is_tty(self) -> bool:
        try:
            return sys.stdin.isatty() and self._out_stream.isatty()
        except (AttributeError, ValueError):
            return False


__all__ = [
    "TerminalSession",
    "TerminalSize",
]
