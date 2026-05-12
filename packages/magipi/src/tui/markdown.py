"""Minimal ANSI markdown formatter (no external deps).

Just enough to render assistant text + code fences + lists in the M1 TUI.
``rich`` is *not* introduced — see ADR-0015. Heavier formatting waits for a
later ADR after we have a real performance / fidelity gap to point at.
"""

from __future__ import annotations

import re

from .width import wrap_to_width

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_FG_GRAY = "\x1b[90m"
_FG_CYAN = "\x1b[36m"
_FG_GREEN = "\x1b[32m"
_FG_YELLOW = "\x1b[33m"

_INLINE_CODE = re.compile(r"`([^`]+)`")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_ITEM = re.compile(r"^([*\-+]|\d+\.)\s+(.*)$")
_FENCE = re.compile(r"^```([A-Za-z0-9_+-]*)\s*$")


def _style_inline(text: str) -> str:
    """Apply inline code highlighting only (bold/italic deferred)."""

    def repl(match: re.Match[str]) -> str:
        return f"{_FG_CYAN}{match.group(1)}{_RESET}"

    return _INLINE_CODE.sub(repl, text)


def render_markdown(source: str, width: int) -> list[str]:
    """Convert ``source`` markdown into ANSI-styled lines that fit ``width``.

    Block model (intentionally tiny):

    - ``# heading`` → bold + colored
    - ``- item`` / ``1. item`` → bullet with hanging indent
    - triple-backtick fenced code blocks (gray prefix, no syntax
      highlighting in M1)
    - paragraphs → wrapped at ``width``
    """

    if width <= 0:
        return []

    lines = source.splitlines() or [""]
    out: list[str] = []
    in_fence = False
    fence_lang = ""

    for raw in lines:
        fence_match = _FENCE.match(raw.strip())
        if fence_match:
            if in_fence:
                in_fence = False
                out.append(f"{_FG_GRAY}```{_RESET}")
            else:
                in_fence = True
                fence_lang = fence_match.group(1)
                tag = f"```{fence_lang}".strip()
                out.append(f"{_FG_GRAY}{tag}{_RESET}")
            continue

        if in_fence:
            for ln in wrap_to_width(raw, width - 2):
                out.append(f"{_DIM}  {ln}{_RESET}")
            continue

        heading = _HEADING.match(raw)
        if heading:
            level = len(heading.group(1))
            content = heading.group(2)
            color = _FG_GREEN if level == 1 else _FG_YELLOW if level == 2 else _FG_CYAN
            for i, ln in enumerate(wrap_to_width(content, width - 2)):
                prefix = f"{color}{_BOLD}" if i == 0 else f"{color}"
                out.append(f"{prefix}{ln}{_RESET}")
            continue

        list_item = _LIST_ITEM.match(raw)
        if list_item:
            bullet = list_item.group(1)
            body = _style_inline(list_item.group(2))
            wrapped = wrap_to_width(body, max(1, width - len(bullet) - 1))
            if not wrapped:
                wrapped = [""]
            out.append(f"{bullet} {wrapped[0]}")
            indent = " " * (len(bullet) + 1)
            for cont in wrapped[1:]:
                out.append(indent + cont)
            continue

        if raw == "":
            out.append("")
            continue

        styled = _style_inline(raw)
        out.extend(wrap_to_width(styled, width))

    return out


__all__ = ["render_markdown"]
