"""Standalone raw-mode key probe.

Puts the controlling terminal into the same raw mode + keyboard-protocol
negotiation the M1 TUI uses, then prints every byte chunk that arrives
on stdin until either 8 seconds elapse or you press 'q'. Use this to
diagnose "Ctrl+C does nothing" reports — the output tells you exactly
what byte sequence the terminal delivered for each keystroke, which
either confirms the StdinBuffer is at fault or tells us the terminal
is doing something we didn't anticipate.

Run:
    uv run python scripts/diag_keys.py

Then quickly press: a, b, Ctrl+C, Ctrl+L, arrow-up, Esc, Enter, q.
Each keystroke prints one ``repr(bytes)`` line. After 8 s (or 'q'),
the script restores cooked mode and exits.

Output goes to stdout AND to /tmp/neomagi-diag-keys.log so you can
copy / paste it back even if the terminal swallows escape codes.
"""

from __future__ import annotations

import os
import select
import signal
import sys
import termios
import time
import tty
from pathlib import Path

LOG_PATH = Path("/tmp/neomagi-diag-keys.log")
DURATION_SECONDS = 8.0


def _emit(log_fp, line: str) -> None:
    sys.stdout.write(line + "\r\n")
    sys.stdout.flush()
    log_fp.write(line + "\n")
    log_fp.flush()


def _capture_loop(fd: int, log_fp) -> None:
    deadline = time.monotonic() + DURATION_SECONDS
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError as exc:
            _emit(log_fp, f"read error: {exc}")
            return
        if not chunk:
            _emit(log_fp, "eof on stdin")
            return
        _emit(log_fp, f"got {len(chunk)} byte(s): {chunk!r}")
        if b"q" in chunk:
            _emit(log_fp, "'q' seen, stopping early")
            return


def main() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.stderr.write("diag_keys: needs an interactive terminal.\n")
        raise SystemExit(2)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    log_fp = LOG_PATH.open("w")
    try:
        # Same negotiation the TUI does — that's what we're diagnosing.
        sys.stdout.write("\x1b[?2004h\x1b[>4;2m\x1b[>1u")
        sys.stdout.flush()
        tty.setraw(fd)
        _emit(log_fp, f"diag_keys: capturing for {DURATION_SECONDS:.0f}s.")
        _emit(log_fp, f"log file: {LOG_PATH}")
        # Defensive: even if raw mode failed to clear ISIG (which would let
        # Ctrl+C kill the probe before we logged it), swallow SIGINT so the
        # capture itself survives.
        signal.signal(signal.SIGINT, lambda *_: None)
        _capture_loop(fd, log_fp)
    finally:
        sys.stdout.write("\x1b[<u\x1b[>4;0m\x1b[?2004l\x1b[0m")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        log_fp.close()
        sys.stdout.write(f"\ndiag_keys: done. log saved to {LOG_PATH}\n")


if __name__ == "__main__":
    main()
