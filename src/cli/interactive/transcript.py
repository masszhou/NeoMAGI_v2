"""Shared transcript presentation helpers for interactive components."""

from __future__ import annotations

from collections.abc import Iterable

from tui.width import pad_to_width, truncate_to_width, wrap_to_width

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
USER = "\x1b[34m"
ASSISTANT = "\x1b[33m"
TOOL_OK = "\x1b[32m"
TOOL_ERROR = "\x1b[31m"
SUMMARY = "\x1b[36m"
BRANCH = "\x1b[32m"
SYSTEM = "\x1b[2m"

MARKER = "⏺"
CHILD = "└"
TRUNCATED = "⋮"


def header_row(label: str, width: int, *, color: str = SYSTEM) -> str:
    return pad_to_width(f"{BOLD}{color}{MARKER} {label}{RESET}", width)


def body_rows(text: str, width: int, *, indent: str = "  ") -> list[str]:
    rows: list[str] = []
    for line in wrap_to_width(text, max(1, width - len(indent))):
        rows.append(pad_to_width(f"{indent}{line}", width))
    return rows


def child_rows(
    lines: Iterable[str],
    width: int,
    *,
    max_lines: int = 4,
    empty: str | None = None,
) -> list[str]:
    values = [line for line in _split_lines(lines) if line.strip()]
    if not values:
        if empty is None:
            return []
        values = [empty]

    rendered: list[str] = []
    clipped = values[:max_lines]
    for index, line in enumerate(clipped):
        prefix = f"  {CHILD} " if index == 0 else "    "
        rendered.append(row(prefix + truncate_to_width(line, max(1, width - len(prefix))), width))
    if len(values) > len(clipped):
        rendered.append(row(f"    {TRUNCATED}", width))
    return rendered


def row(text: str, width: int) -> str:
    return pad_to_width(text, width)


def blank(width: int) -> str:
    return pad_to_width("", width)


def _split_lines(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in lines:
        out.extend(str(value).splitlines() or [""])
    return out


__all__ = [
    "ASSISTANT",
    "BRANCH",
    "CHILD",
    "DIM",
    "MARKER",
    "RESET",
    "SUMMARY",
    "SYSTEM",
    "TOOL_ERROR",
    "TOOL_OK",
    "TRUNCATED",
    "USER",
    "blank",
    "body_rows",
    "child_rows",
    "header_row",
    "row",
]
