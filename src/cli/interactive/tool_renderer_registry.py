"""ToolRendererRegistry + generic fallback (plan W4 §`ToolRendererRegistry`).

The registry uses the local :class:`ToolRenderContext` dataclass as its
input — *not* a pydantic model, which keeps M1 acceptance §9 happy
(substrate / interactive layer never defines wire-level message models).

Time stamps live on the component owning the tool, **not** in the
registry; this file is pure: ``ctx`` in, ``list[str]`` out.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ToolRenderer = Callable[["ToolRenderContext", int], list[str]]


@dataclass(frozen=True)
class ToolRenderContext:
    """Snapshot fed to a tool renderer. All fields are pure data — the
    component is responsible for assembling them per frame."""

    tool_name: str
    tool_call_id: str
    args: Any
    partial_result: Any | None
    result: Any | None
    is_error: bool | None
    is_partial: bool
    started_at_ms: int
    last_update_at_ms: int | None
    ended_at_ms: int | None


def _truncate(text: str, limit: int = 200) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _summarise(value: Any, *, limit: int = 200) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _truncate(value, limit)
    try:
        return _truncate(json.dumps(value, ensure_ascii=False, default=str), limit)
    except (TypeError, ValueError):
        return _truncate(repr(value), limit)


def generic_tool_renderer(ctx: ToolRenderContext, width: int) -> list[str]:
    """Default renderer: name + args + (partial|final) result + state.

    No truncation indicator is emitted — that field doesn't exist on
    :class:`ToolExecutionEndEvent` yet (plan W4 explicitly defers this to
    M5 once policy populates ``result.metadata`` / ``result.details``).
    """

    head = f"⚙ {ctx.tool_name}({_summarise(ctx.args, limit=120)})"
    head = head[:width]

    rows: list[str] = [head]

    if ctx.is_partial:
        if ctx.partial_result is not None:
            partial = _summarise(ctx.partial_result, limit=180)
            rows.append(f"  partial: {partial}"[:width])
        else:
            rows.append("  partial: (no output yet)"[:width])
    else:
        result = _summarise(ctx.result, limit=180)
        flag = "error" if ctx.is_error else "ok"
        rows.append(f"  result [{flag}]: {result}"[:width])

    if ctx.ended_at_ms is not None:
        duration = max(0, ctx.ended_at_ms - ctx.started_at_ms)
        rows.append(f"  duration: {duration} ms"[:width])

    return rows


class ToolRendererRegistry:
    """Map ``tool_name -> renderer``. Falls back to :func:`generic_tool_renderer`."""

    def __init__(self) -> None:
        self._renderers: dict[str, ToolRenderer] = {}
        self._fallback: ToolRenderer = generic_tool_renderer

    def register(self, tool_name: str, renderer: ToolRenderer) -> None:
        self._renderers[tool_name] = renderer

    def render(self, ctx: ToolRenderContext, width: int) -> list[str]:
        renderer = self._renderers.get(ctx.tool_name, self._fallback)
        return renderer(ctx, width)


__all__ = [
    "ToolRenderContext",
    "ToolRenderer",
    "ToolRendererRegistry",
    "generic_tool_renderer",
]
