"""Line-diff renderer (ADR-0015 §影响 `src/tui/renderer.py`).

The substrate's ANSI line model is the single source of truth. Components
produce ``list[str]`` per frame; ``Renderer.present`` writes only the rows
that changed, wrapped in a synchronized-output envelope so terminals draw
the frame atomically.

Public surface is intentionally one method: :meth:`present`. Cursor
positioning is part of the same call so business code can never split
"frame" and "cursor" into races.
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

    def present(
        self,
        frame: list[str],
        cursor: CursorPosition | None = None,
    ) -> None:
        """Render ``frame`` and place the hardware cursor.

        Single-entry contract (ADR-0015 §影响 `src/tui/renderer.py`):
        business code MUST go through this method; raw escape writes
        forbidden anywhere outside the substrate.
        """

        out = self._out
        previous = self._previous
        chunks: list[str] = [_SYNC_BEGIN]

        if not previous:
            self._last_changed_rows = self._append_full_frame(chunks, frame)
        else:
            self._last_changed_rows = self._append_diff_frame(chunks, previous, frame)

        self._append_cursor(chunks, cursor)
        chunks.append(_SYNC_END)

        try:
            out.write("".join(chunks))
            out.flush()
        except (OSError, ValueError):
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


__all__ = ["Renderer"]
