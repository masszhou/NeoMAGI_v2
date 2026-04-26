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
        self._cursor_visible: bool = True

    def reset(self) -> None:
        """Drop the previous-frame snapshot.

        Called on resize / explicit redraw so the next ``present`` writes
        the full frame from scratch.
        """

        self._previous = []
        self._last_changed_rows = 0

    @property
    def last_changed_rows(self) -> int:
        return self._last_changed_rows

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
            chunks.append(_move_cursor(1, 1))
            chunks.append(_erase_below())
            for row, line in enumerate(frame, start=1):
                chunks.append(_move_cursor(row, 1))
                chunks.append(_erase_line())
                chunks.append(line)
                chunks.append(_RESET_SGR)
            self._last_changed_rows = len(frame)
        else:
            max_len = max(len(previous), len(frame))
            changed = 0
            for i in range(max_len):
                old = previous[i] if i < len(previous) else None
                new = frame[i] if i < len(frame) else ""
                if i >= len(frame):
                    # Frame shrank — clear the orphaned row.
                    chunks.append(_move_cursor(i + 1, 1))
                    chunks.append(_erase_line())
                    changed += 1
                    continue
                if old == new:
                    continue
                chunks.append(_move_cursor(i + 1, 1))
                chunks.append(_erase_line())
                chunks.append(new)
                chunks.append(_RESET_SGR)
                changed += 1
            self._last_changed_rows = changed

        # Cursor handling — single source of truth for visibility too.
        if cursor is None or not cursor.visible:
            if self._cursor_visible:
                chunks.append(_CURSOR_HIDE)
                self._cursor_visible = False
        else:
            chunks.append(_move_cursor(cursor.row, cursor.col))
            if not self._cursor_visible:
                chunks.append(_CURSOR_SHOW)
                self._cursor_visible = True

        chunks.append(_SYNC_END)

        try:
            out.write("".join(chunks))
            out.flush()
        except (OSError, ValueError):
            # Output may close during shutdown; swallow to keep teardown clean.
            pass

        # Snapshot AFTER write so a mid-write exception leaves us in a
        # known-consistent state (next call will full-redraw via the
        # ``not previous`` branch).
        self._previous = list(frame)


__all__ = ["Renderer"]
