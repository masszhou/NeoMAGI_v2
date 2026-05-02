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
    aborted: bool = False
    """``True`` when the controller's ``handle_abort`` cut the tool off
    before a ``tool_execution_end`` event arrived. Renderers should keep
    showing the last ``partial_result`` (if any) plus an unmistakable
    abort marker, NOT a synthesised "result [error]: aborted" line —
    that visually conflates an interrupted tool with one that completed
    with an error response. Manual P1-M1 §5.3 caught this."""


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


def single_line_summary(value: Any, *, limit: int = 200) -> str:
    """Return a row-safe summary for command-mode live rendering."""

    if value is None:
        return ""
    text = str(value)
    lines = text.splitlines()
    if not lines:
        return ""
    summary = lines[0]
    if len(lines) > 1:
        summary = f"{summary} (+{len(lines) - 1} lines)"
    return _truncate(summary, limit)


def generic_tool_renderer(ctx: ToolRenderContext, width: int) -> list[str]:
    """Default renderer: name + args + (partial|final|aborted) state.

    Three visual modes:
      - **In-flight** (``is_partial`` and not aborted): show the most
        recent ``partial_result`` snapshot.
      - **Completed** (``result`` populated, not aborted): show the
        result summary tagged ``[ok]`` or ``[error]`` + duration.
      - **Aborted** (``aborted``): keep the last partial visible so the
        user can see how far the tool got, append
        ``[aborted after N ms]`` so it's unmistakable.

    No truncation indicator is emitted — that field doesn't exist on
    :class:`ToolExecutionEndEvent` yet (plan W4 explicitly defers this to
    M5 once policy populates ``result.metadata`` / ``result.details``).
    """

    head = f"⚙ {ctx.tool_name}({_summarise(ctx.args, limit=120)})"
    head = head[:width]

    rows: list[str] = [head]

    if ctx.aborted:
        # Aborted mid-flight: keep the last partial visible (if any),
        # then make the abort unambiguous. Don't render a synthetic
        # ``result [error]: ...`` line — that would visually conflate
        # an interrupted tool with one that returned an error response.
        if ctx.partial_result is not None:
            partial = _summarise(ctx.partial_result, limit=180)
            rows.append(f"  partial: {partial}"[:width])
        else:
            rows.append("  partial: (no output before abort)"[:width])
        if ctx.ended_at_ms is not None:
            duration = max(0, ctx.ended_at_ms - ctx.started_at_ms)
            rows.append(f"  [aborted after {duration} ms]"[:width])
        else:
            rows.append("  [aborted]"[:width])
        return rows

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


def read_tool_renderer(ctx: ToolRenderContext, width: int) -> list[str]:
    if ctx.aborted:
        return generic_tool_renderer(ctx, width)
    details = _result_details(ctx)
    path = details.get("path") or _arg(ctx.args, "path") or "?"
    rows = [f"read {path}"[:width]]
    truncation = details.get("truncation") if isinstance(details.get("truncation"), dict) else {}
    if truncation.get("truncated"):
        rows.append(
            f"  truncated: {truncation.get('outputLines')} of {truncation.get('totalLines')} lines"[:width]
        )
    if ctx.is_partial:
        output = _text_output(ctx.partial_result)
        if output:
            rows.append(f"  partial: {output.splitlines()[0]}"[:width])
    rows.extend(_final_status_rows(ctx, width))
    return rows


def bash_tool_renderer(ctx: ToolRenderContext, width: int) -> list[str]:
    if ctx.aborted:
        return generic_tool_renderer(ctx, width)
    details = _result_details(ctx)
    command = _arg(ctx.args, "command") or "$"
    rows = [f"$ {single_line_summary(command, limit=max(1, width - 2))}"[:width]]
    output = _text_output(ctx.partial_result if ctx.is_partial else ctx.result)
    if output:
        rows.extend(f"  {line}"[:width] for line in output.strip().splitlines()[-5:])
    status = []
    if details.get("exitCode") is not None:
        status.append(f"exit={details.get('exitCode')}")
    if details.get("cancelled"):
        status.append("cancelled")
    truncation = details.get("truncation") if isinstance(details.get("truncation"), dict) else {}
    if truncation.get("truncated"):
        status.append("truncated")
    if status:
        rows.append(f"  ({', '.join(status)})"[:width])
    rows.extend(_final_status_rows(ctx, width))
    return rows


def edit_tool_renderer(ctx: ToolRenderContext, width: int) -> list[str]:
    if ctx.aborted:
        return generic_tool_renderer(ctx, width)
    details = _result_details(ctx)
    path = details.get("path") or _arg(ctx.args, "path") or "?"
    rows = [f"edit {path}"[:width]]
    if details.get("firstChangedLine") is not None:
        rows.append(f"  first changed line: {details.get('firstChangedLine')}"[:width])
    diff = details.get("unifiedDiff")
    if isinstance(diff, str) and diff:
        rows.extend(f"  {line}"[:width] for line in diff.splitlines()[:8])
    rows.extend(_final_status_rows(ctx, width))
    return rows


def write_tool_renderer(ctx: ToolRenderContext, width: int) -> list[str]:
    if ctx.aborted:
        return generic_tool_renderer(ctx, width)
    details = _result_details(ctx)
    path = details.get("path") or _arg(ctx.args, "path") or "?"
    rows = [f"write {path}"[:width]]
    output = _text_output(ctx.result)
    if output:
        rows.append(f"  {output.splitlines()[0]}"[:width])
    rows.extend(_final_status_rows(ctx, width))
    return rows


def _result_details(ctx: ToolRenderContext) -> dict[str, Any]:
    source = ctx.result if ctx.result is not None else ctx.partial_result
    details = source.get("details") if isinstance(source, dict) else getattr(source, "details", None)
    return details if isinstance(details, dict) else {}


def _text_output(result: Any) -> str:
    if result is None:
        return ""
    content = result.get("content") if isinstance(result, dict) else getattr(result, "content", [])
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            parts.append(str(block.text))
    return "\n".join(parts)


def _arg(args: Any, name: str) -> Any:
    return args.get(name) if isinstance(args, dict) else getattr(args, name, None)


def _final_status_rows(ctx: ToolRenderContext, width: int) -> list[str]:
    if ctx.is_partial:
        return ["  running"[:width]]
    flag = "error" if ctx.is_error else "ok"
    rows = [f"  [{flag}]"[:width]]
    if ctx.ended_at_ms is not None:
        rows.append(f"  duration: {max(0, ctx.ended_at_ms - ctx.started_at_ms)} ms"[:width])
    return rows


class ToolRendererRegistry:
    """Map ``tool_name -> renderer``. Falls back to :func:`generic_tool_renderer`."""

    def __init__(self) -> None:
        self._renderers: dict[str, ToolRenderer] = {}
        self._fallback: ToolRenderer = generic_tool_renderer
        self.register("read", read_tool_renderer)
        self.register("bash", bash_tool_renderer)
        self.register("edit", edit_tool_renderer)
        self.register("write", write_tool_renderer)

    def register(self, tool_name: str, renderer: ToolRenderer) -> None:
        self._renderers[tool_name] = renderer

    def render(self, ctx: ToolRenderContext, width: int) -> list[str]:
        renderer = self._renderers.get(ctx.tool_name, self._fallback)
        return renderer(ctx, width)


__all__ = [
    "ToolRenderContext",
    "ToolRenderer",
    "ToolRendererRegistry",
    "bash_tool_renderer",
    "edit_tool_renderer",
    "generic_tool_renderer",
    "read_tool_renderer",
    "write_tool_renderer",
]
