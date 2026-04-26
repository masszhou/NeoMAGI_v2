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


def main() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.stderr.write("diag_keys: needs an interactive terminal.\n")
        raise SystemExit(2)

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    log_fp = LOG_PATH.open("w")

    def emit(line: str) -> None:
        sys.stdout.write(line + "\r\n")
        sys.stdout.flush()
        log_fp.write(line + "\n")
        log_fp.flush()

    try:
        # Same negotiation the TUI does — that's what we're diagnosing.
        sys.stdout.write("\x1b[?2004h")  # bracketed paste on
        sys.stdout.write("\x1b[>4;2m")   # modifyOtherKeys=2
        sys.stdout.write("\x1b[>1u")     # Kitty keyboard level 1
        sys.stdout.flush()
        tty.setraw(fd)

        emit(
            f"diag_keys: capturing for {DURATION_SECONDS:.0f}s. "
            "Press q to stop early. Try: a, b, Ctrl+C, Ctrl+L, Up, Esc, Enter."
        )
        emit(f"log file: {LOG_PATH}")

        deadline = time.monotonic() + DURATION_SECONDS
        # Defensive: re-arm SIGINT to a no-op so even if raw mode failed to
        # clear ISIG (which is what would cause "Ctrl+C kills the probe"),
        # we don't lose the diagnostic itself. The bytes still appear in
        # stdin if raw mode took.
        signal.signal(signal.SIGINT, lambda *_: None)

        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError as exc:
                emit(f"read error: {exc}")
                break
            if not chunk:
                emit("eof on stdin")
                break
            emit(f"got {len(chunk)} byte(s): {chunk!r}")
            if b"q" in chunk:
                emit("'q' seen, stopping early")
                break
    finally:
        sys.stdout.write("\x1b[<u")     # Kitty keys off
        sys.stdout.write("\x1b[>4;0m")  # modifyOtherKeys off
        sys.stdout.write("\x1b[?2004l") # bracketed paste off
        sys.stdout.write("\x1b[0m")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        log_fp.close()
        sys.stdout.write(f"\ndiag_keys: done. log saved to {LOG_PATH}\n")


if __name__ == "__main__":
    main()
