"""Shared truncation helpers for coding tool output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_LENGTH = 500


@dataclass(frozen=True, slots=True)
class TruncationResult:
    content: str
    truncated: bool
    truncated_by: Literal["lines", "bytes"] | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool
    first_line_exceeds_limit: bool
    max_lines: int
    max_bytes: int

    def to_details(self) -> dict[str, object]:
        return {
            "content": self.content,
            "truncated": self.truncated,
            "truncatedBy": self.truncated_by,
            "totalLines": self.total_lines,
            "totalBytes": self.total_bytes,
            "outputLines": self.output_lines,
            "outputBytes": self.output_bytes,
            "lastLinePartial": self.last_line_partial,
            "firstLineExceedsLimit": self.first_line_exceeds_limit,
            "maxLines": self.max_lines,
            "maxBytes": self.max_bytes,
        }


def truncate_head(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    lines = content.split("\n")
    total = _totals(content, lines)
    if total[0] <= max_lines and total[1] <= max_bytes:
        return _result(content, False, None, total, len(lines), total[1], False, False, max_lines, max_bytes)
    if _byte_len(lines[0]) > max_bytes:
        return _result("", True, "bytes", total, 0, 0, False, True, max_lines, max_bytes)

    out: list[str] = []
    out_bytes = 0
    truncated_by: Literal["lines", "bytes"] = "lines"
    for index, line in enumerate(lines[:max_lines]):
        line_bytes = _byte_len(line) + (1 if index else 0)
        if out_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        out.append(line)
        out_bytes += line_bytes
    text = "\n".join(out)
    return _result(text, True, truncated_by, total, len(out), _byte_len(text), False, False, max_lines, max_bytes)


def truncate_tail(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    lines = content.split("\n")
    total = _totals(content, lines)
    if total[0] <= max_lines and total[1] <= max_bytes:
        return _result(content, False, None, total, len(lines), total[1], False, False, max_lines, max_bytes)

    out: list[str] = []
    out_bytes = 0
    truncated_by: Literal["lines", "bytes"] = "lines"
    last_line_partial = False
    for line in reversed(lines):
        if len(out) >= max_lines:
            truncated_by = "lines"
            break
        line_bytes = _byte_len(line) + (1 if out else 0)
        if out_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not out:
                partial = _tail_bytes(line, max_bytes)
                out.insert(0, partial)
                out_bytes = _byte_len(partial)
                last_line_partial = True
            break
        out.insert(0, line)
        out_bytes += line_bytes
    text = "\n".join(out)
    return _result(
        text,
        True,
        truncated_by,
        total,
        len(out),
        _byte_len(text),
        last_line_partial,
        False,
        max_lines,
        max_bytes,
    )


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> tuple[str, bool]:
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes / (1024 * 1024):.1f}MB"


def _result(
    content: str,
    truncated: bool,
    truncated_by: Literal["lines", "bytes"] | None,
    totals: tuple[int, int],
    output_lines: int,
    output_bytes: int,
    last_line_partial: bool,
    first_line_exceeds_limit: bool,
    max_lines: int,
    max_bytes: int,
) -> TruncationResult:
    return TruncationResult(
        content=content,
        truncated=truncated,
        truncated_by=truncated_by,
        total_lines=totals[0],
        total_bytes=totals[1],
        output_lines=output_lines,
        output_bytes=output_bytes,
        last_line_partial=last_line_partial,
        first_line_exceeds_limit=first_line_exceeds_limit,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def _totals(content: str, lines: list[str]) -> tuple[int, int]:
    return len(lines), _byte_len(content)


def _byte_len(value: str) -> int:
    return len(value.encode("utf-8"))


def _tail_bytes(value: str, max_bytes: int) -> str:
    data = value.encode("utf-8")
    if len(data) <= max_bytes:
        return value
    start = len(data) - max_bytes
    while start < len(data) and data[start] & 0xC0 == 0x80:
        start += 1
    return data[start:].decode("utf-8", errors="replace")


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "GREP_MAX_LINE_LENGTH",
    "TruncationResult",
    "format_size",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
]
