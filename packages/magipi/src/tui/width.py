"""ANSI-aware terminal width primitives (ADR-0015 §影响 `packages/magipi/src/tui/width.py`).

`Component.render(width)` 输出必须经此模块校验；业务代码不得用 `len()` /
`textwrap` 当列宽真相。CJK / emoji / combining mark / ANSI SGR / tab 全部
归一到这里。
"""

from __future__ import annotations

import re

from wcwidth import wcswidth, wcwidth

# Match a single ANSI escape sequence (CSI / SGR / OSC / APC / ...).
_ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|P[^\x1b]*\x1b\\"  # DCS
    r"|_[^\x1b]*\x1b\\"  # APC
    r"|[@-Z\\-_]"  # 7-bit single-shift / single character escapes
    r")"
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences. Preserves printable + control chars."""

    return _ANSI_RE.sub("", text)


def _char_columns(ch: str) -> int:
    width = wcwidth(ch)
    if width < 0:
        return 0
    return width


def visible_width(text: str) -> int:
    """Number of terminal columns ``text`` will occupy.

    - Skips ANSI escape sequences.
    - Uses ``wcwidth`` to handle CJK / emoji / combining marks correctly.
    - Treats tab as advancing to the next 8-column stop (terminal default).
    """

    visible = strip_ansi(text)
    if not visible:
        return 0
    if "\t" not in visible:
        width = wcswidth(visible)
        return max(0, width)
    columns = 0
    for ch in visible:
        if ch == "\t":
            columns += 8 - (columns % 8)
        else:
            columns += _char_columns(ch)
    return columns


def slice_by_columns(text: str, start: int, end: int) -> str:
    """Return the slice of ``text`` whose visible columns fall in ``[start, end)``.

    ANSI SGR sequences are dropped — callers that need to preserve color must
    re-apply it after slicing. This is consistent with how line-diff renderers
    operate on a normalised plain-text frame.
    """

    if end <= start:
        return ""
    out: list[str] = []
    visible = strip_ansi(text)
    column = 0
    for ch in visible:
        if ch == "\t":
            advance = 8 - (column % 8)
        else:
            advance = _char_columns(ch)
        next_column = column + advance
        if next_column <= start:
            column = next_column
            continue
        if column >= end:
            break
        if column >= start and next_column <= end:
            out.append(ch)
        column = next_column
    return "".join(out)


def truncate_to_width(text: str, width: int, ellipsis: str = "…") -> str:
    """Truncate ``text`` to ``width`` columns, appending ``ellipsis`` if cut.

    ANSI escape sequences are removed; combining marks attached to the cut-off
    character are dropped too (the simplest safe behavior).
    """

    if width <= 0:
        return ""
    plain = strip_ansi(text)
    if visible_width(plain) <= width:
        return plain
    ellipsis_width = visible_width(ellipsis)
    budget = max(0, width - ellipsis_width)
    head = slice_by_columns(plain, 0, budget)
    return head + ellipsis


def pad_to_width(text: str, width: int) -> str:
    """Right-pad ``text`` with spaces so it occupies exactly ``width`` columns.

    If ``text`` already exceeds ``width`` it is truncated.
    """

    if width <= 0:
        return ""
    current = visible_width(text)
    if current > width:
        return truncate_to_width(text, width)
    return text + " " * (width - current)


def wrap_to_width(text: str, width: int) -> list[str]:
    """ANSI-aware wrap: split ``text`` into lines that each fit ``width`` columns.

    - Preserves explicit ``\\n`` as hard breaks.
    - Greedy column accounting; does not split escape sequences.
    - Empty input returns ``[""]`` so callers can still render one blank line.
    """

    if width <= 0:
        return [""]
    if text == "":
        return [""]
    lines: list[str] = []
    for hard_line in text.split("\n"):
        plain = strip_ansi(hard_line)
        if not plain:
            lines.append("")
            continue
        column = 0
        buffer: list[str] = []
        for ch in plain:
            if ch == "\t":
                advance = 8 - (column % 8)
            else:
                advance = _char_columns(ch)
            if column + advance > width and buffer:
                lines.append("".join(buffer))
                buffer = []
                column = 0
            if ch == "\t":
                advance = 8 - (column % 8)
            buffer.append(ch)
            column += advance
        lines.append("".join(buffer))
    return lines


__all__ = [
    "pad_to_width",
    "slice_by_columns",
    "strip_ansi",
    "truncate_to_width",
    "visible_width",
    "wrap_to_width",
]
