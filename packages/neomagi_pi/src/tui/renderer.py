"""Line-diff renderer (ADR-0015 §影响 `packages/neomagi_pi/src/tui/renderer.py`).

The substrate's ANSI line model is the single source of truth. Components
produce ``list[str]`` per frame; canvas rendering writes only the rows that
changed, while command rendering appends committed transcript rows and
rewrites a bounded live region. Frame rewrites are wrapped in a
synchronized-output envelope so terminals draw them atomically.

The public surface is mode-specific: :meth:`present` owns anchored canvas
frames, while :meth:`commit_lines`, :meth:`present_live`, and
:meth:`clear_live_region` own command-mode scrollback + live-region output.
Cursor positioning remains part of the same live/canvas frame call so
business code cannot split "frame" and "cursor" into races.
"""

from __future__ import annotations

import sys
from typing import TextIO

from .component import CursorPosition

# Synchronized output (DEC mode 2026): batch all writes between BEGIN and
# END into a single visible frame on terminals that understand it.
_SYNC_BEGIN = "\x1b[?2026h"
_SYNC_END = "\x1b[?2026l"
_RESET_SGR = "\x1b[0m"
_CURSOR_HIDE = "\x1b[?25l"
_CURSOR_SHOW = "\x1b[?25h"


def _move_cursor(row: int, col: int) -> str:
    return f"\x1b[{max(1, row)};{max(1, col)}H"


def _cursor_up(rows: int) -> str:
    if rows <= 0:
        return ""
    return f"\x1b[{rows}A"


def _cursor_col(col: int) -> str:
    return f"\x1b[{max(1, col)}G"


def _erase_line() -> str:
    return "\x1b[2K"


def _erase_below() -> str:
    return "\x1b[J"


class Renderer:
    """Owns the previous-frame snapshot used for diff rendering."""

    def __init__(self, *, out_stream: TextIO | None = None) -> None:
        self._out: TextIO = out_stream if out_stream is not None else sys.stdout
        self._previous: list[str] = []
        self._last_changed_rows: int = 0
        self._last_presented_frame_height: int | None = None
        self._anchor_row: int = 1
        self._cursor_visible: bool = False
        self._command_live_height: int = 0
        self._command_cursor_row: int = 1

    def reset(self) -> None:
        """Drop the previous-frame snapshot.

        Called on resize / explicit redraw so the next ``present`` writes
        the full frame from scratch.
        """

        self._previous = []
        self._last_changed_rows = 0
        self._last_presented_frame_height = None

    @property
    def last_changed_rows(self) -> int:
        return self._last_changed_rows

    @property
    def anchor_row(self) -> int:
        return self._anchor_row

    def set_anchor(self, row: int) -> None:
        self._anchor_row = max(1, row)

    def last_bottom_row(self) -> int | None:
        if self._last_presented_frame_height is None:
            return None
        if self._last_presented_frame_height <= 0:
            return None
        return self._anchor_row + self._last_presented_frame_height - 1

    def commit_lines(self, lines: list[str]) -> None:
        """Append completed transcript rows to terminal scrollback.

        Command render mode keeps committed transcript outside the live
        diff region. Before appending new transcript rows, clear the current
        editor/status/streaming tail and then write each row as normal
        CRLF-terminated terminal output.
        """

        chunks: list[str] = []
        self._append_clear_command_live(chunks)
        for line in lines:
            self._append_command_line(chunks, line)
            chunks.append("\r\n")
        if not chunks:
            return
        if self._write("".join(chunks)):
            self._command_live_height = 0
            self._command_cursor_row = 1
            self._last_presented_frame_height = None

    def present_live(
        self,
        frame: list[str],
        cursor: CursorPosition | None = None,
    ) -> None:
        """Render a bounded command-mode live region.

        This path never addresses the terminal by absolute screen row and
        never clears content above the previous live region.
        """

        body: list[str] = []
        self._append_clear_command_live(body)
        for index, line in enumerate(frame):
            if index:
                body.append("\r\n")
            self._append_command_line(body, line)

        if cursor is not None and cursor.visible and frame:
            row = min(max(1, cursor.row), len(frame))
            body.append(_cursor_up(len(frame) - row))
            body.append(_cursor_col(cursor.col))
            if not self._cursor_visible:
                body.append(_CURSOR_SHOW)
                self._cursor_visible = True
            cursor_row = row
        else:
            self._append_cursor_hide(body)
            cursor_row = max(1, len(frame))

        if not body:
            return
        chunks = [_SYNC_BEGIN, *body, _SYNC_END]
        if self._write("".join(chunks)):
            self._command_live_height = len(frame)
            self._command_cursor_row = cursor_row
            self._last_presented_frame_height = len(frame)

    def clear_live_region(self) -> None:
        """Erase the current command-mode live region, if any."""

        chunks: list[str] = []
        self._append_clear_command_live(chunks)
        if chunks and self._write("".join(chunks)):
            self._command_live_height = 0
            self._command_cursor_row = 1
            self._last_presented_frame_height = None

    def present(
        self,
        frame: list[str],
        cursor: CursorPosition | None = None,
    ) -> None:
        """Render ``frame`` and place the hardware cursor.

        Single-entry contract (ADR-0015 §影响 `packages/neomagi_pi/src/tui/renderer.py`):
        business code MUST go through this method; raw escape writes
        forbidden anywhere outside the substrate.
        """

        previous = self._previous
        chunks: list[str] = [_SYNC_BEGIN]

        if not previous:
            self._last_changed_rows = self._append_full_frame(chunks, frame)
        else:
            self._last_changed_rows = self._append_diff_frame(chunks, previous, frame)

        self._append_cursor(chunks, cursor)
        chunks.append(_SYNC_END)

        if not self._write("".join(chunks)):
            # Output may close during shutdown; swallow to keep teardown clean.
            return

        # Snapshot AFTER write so a mid-write exception leaves us in a
        # known-consistent state (next call will full-redraw via the
        # ``not previous`` branch).
        self._previous = list(frame)
        self._last_presented_frame_height = len(frame)

    def _move_cursor(self, row: int, col: int) -> str:
        return _move_cursor(self._anchor_row + max(1, row) - 1, col)

    def _append_full_frame(self, chunks: list[str], frame: list[str]) -> int:
        chunks.append(self._move_cursor(1, 1))
        chunks.append(_erase_below())
        for row, line in enumerate(frame, start=1):
            chunks.extend([self._move_cursor(row, 1), _erase_line(), line, _RESET_SGR])
        return len(frame)

    def _append_diff_frame(
        self,
        chunks: list[str],
        previous: list[str],
        frame: list[str],
    ) -> int:
        changed = 0
        for i in range(max(len(previous), len(frame))):
            old = previous[i] if i < len(previous) else None
            new = frame[i] if i < len(frame) else ""
            if i >= len(frame):
                chunks.extend([self._move_cursor(i + 1, 1), _erase_line()])
                changed += 1
            elif old != new:
                chunks.extend(
                    [self._move_cursor(i + 1, 1), _erase_line(), new, _RESET_SGR]
                )
                changed += 1
        return changed

    def _append_cursor(
        self,
        chunks: list[str],
        cursor: CursorPosition | None,
    ) -> None:
        if cursor is None or not cursor.visible:
            self._append_cursor_hide(chunks)
            return
        chunks.append(self._move_cursor(cursor.row, cursor.col))
        if not self._cursor_visible:
            chunks.append(_CURSOR_SHOW)
            self._cursor_visible = True

    def _append_cursor_hide(self, chunks: list[str]) -> None:
        if self._cursor_visible:
            chunks.append(_CURSOR_HIDE)
            self._cursor_visible = False

    def _append_command_line(self, chunks: list[str], line: str) -> None:
        chunks.append(line.rstrip())
        chunks.append(_RESET_SGR)

    def _append_clear_command_live(self, chunks: list[str]) -> None:
        if self._command_live_height <= 0:
            return
        chunks.append(_cursor_up(self._command_cursor_row - 1))
        chunks.append("\r")
        chunks.append(_erase_below())

    def _write(self, data: str) -> bool:
        try:
            self._out.write(data)
            self._out.flush()
            return True
        except (OSError, ValueError):
            return False


__all__ = ["Renderer"]
