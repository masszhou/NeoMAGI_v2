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

from tui.width import truncate_to_width

from .transcript import TOOL_ERROR, TOOL_OK, child_rows, header_row, row

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
    """Default compact transcript renderer."""

    label = f"Ran {ctx.tool_name}"
    status = _header_status(ctx)
    if status:
        label += f" {status}"
    rows: list[str] = [header_row(label, width, color=_tool_color(ctx))]

    if ctx.aborted:
        rows.extend(child_rows([_summarise(ctx.partial_result, limit=180)], width, empty="(no output before abort)"))
        rows.append(row(f"  [aborted{_duration_suffix(ctx)}]", width))
        return rows

    if ctx.is_partial:
        rows.extend(child_rows([_summarise(ctx.partial_result, limit=180)], width, empty="(no output yet)"))
    else:
        rows.extend(child_rows([_summarise(ctx.result, limit=180)], width, empty="(no output)"))

    return rows


def read_tool_renderer(ctx: ToolRenderContext, width: int) -> list[str]:
    if ctx.aborted:
        return generic_tool_renderer(ctx, width)
    details = _result_details(ctx)
    path = details.get("path") or _arg(ctx.args, "path") or "?"
    line_range = _line_range(details)
    label = f"Read {path}{line_range}{_header_status(ctx, force_errors=True)}"
    rows = [header_row(label, width, color=_tool_color(ctx))]
    truncation = details.get("truncation") if isinstance(details.get("truncation"), dict) else {}
    output = _text_output(ctx.partial_result if ctx.is_partial else ctx.result)
    rows.extend(child_rows(output.splitlines(), width, max_lines=_preview_limit(width), empty=None))
    if truncation.get("truncated"):
        rows.append(row(f"    {_truncate_notice(truncation)}", width))
    return rows


def bash_tool_renderer(ctx: ToolRenderContext, width: int) -> list[str]:
    if ctx.aborted:
        return generic_tool_renderer(ctx, width)
    details = _result_details(ctx)
    command = _arg(ctx.args, "command") or "$"
    label = f"Ran {single_line_summary(command, limit=max(1, width - 8))}{_header_status(ctx)}"
    rows = [header_row(label, width, color=_tool_color(ctx))]
    output = _text_output(ctx.partial_result if ctx.is_partial else ctx.result)
    if output:
        rows.extend(child_rows(output.strip().splitlines()[:_preview_limit(width)], width, max_lines=_preview_limit(width)))
    elif not ctx.is_partial and ctx.is_error:
        rows.extend(child_rows(["(no output)"], width))
    status = []
    if details.get("exitCode") is not None:
        status.append(f"exit={details.get('exitCode')}")
    if details.get("cancelled"):
        status.append("cancelled")
    truncation = details.get("truncation") if isinstance(details.get("truncation"), dict) else {}
    if truncation.get("truncated"):
        status.append("truncated")
    if status:
        rows.append(row(f"  ({', '.join(status)})", width))
    return rows


def edit_tool_renderer(ctx: ToolRenderContext, width: int) -> list[str]:
    if ctx.aborted:
        return generic_tool_renderer(ctx, width)
    details = _result_details(ctx)
    path = details.get("path") or _arg(ctx.args, "path") or "?"
    diff = details.get("unifiedDiff")
    additions, deletions = _diff_stats(diff if isinstance(diff, str) else "")
    label = f"Edited {path} (+{additions} -{deletions}){_header_status(ctx)}"
    rows = [header_row(label, width, color=_tool_color(ctx))]
    diff = details.get("unifiedDiff")
    if isinstance(diff, str) and diff:
        rows.extend(_diff_preview_rows(diff, width))
    else:
        output = _text_output(ctx.result)
        rows.extend(child_rows(output.splitlines(), width, max_lines=2))
    return rows


def write_tool_renderer(ctx: ToolRenderContext, width: int) -> list[str]:
    if ctx.aborted:
        return generic_tool_renderer(ctx, width)
    details = _result_details(ctx)
    path = details.get("path") or _arg(ctx.args, "path") or "?"
    rows = [header_row(f"Wrote {path}{_header_status(ctx)}", width, color=_tool_color(ctx))]
    output = _text_output(ctx.result)
    if output:
        rows.extend(child_rows(output.splitlines(), width, max_lines=2))
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


def _tool_color(ctx: ToolRenderContext) -> str:
    return TOOL_ERROR if ctx.aborted or ctx.is_error else TOOL_OK


def _header_status(ctx: ToolRenderContext, *, force_errors: bool = False) -> str:
    if ctx.aborted:
        return " [aborted]"
    if ctx.is_error:
        return " [error]"
    if ctx.is_partial:
        return " [running]"
    duration = _duration_ms(ctx)
    if duration is not None and duration >= 2_000:
        return f"  {_format_duration(duration)}"
    if force_errors:
        return ""
    return ""


def _duration_suffix(ctx: ToolRenderContext) -> str:
    duration = _duration_ms(ctx)
    return f" after {duration} ms" if duration is not None else ""


def _duration_ms(ctx: ToolRenderContext) -> int | None:
    if ctx.ended_at_ms is None:
        return None
    return max(0, ctx.ended_at_ms - ctx.started_at_ms)


def _format_duration(duration_ms: int) -> str:
    if duration_ms < 10_000:
        return f"{duration_ms / 1000:.1f}s"
    return f"{round(duration_ms / 1000)}s"


def _line_range(details: dict[str, Any]) -> str:
    start = details.get("lineStart")
    end = details.get("lineEnd")
    if isinstance(start, int) and isinstance(end, int):
        return f":{start}-{end}"
    return ""


def _preview_limit(width: int) -> int:
    return 1 if width < 50 else 4


def _truncate_notice(truncation: dict[str, Any]) -> str:
    output = truncation.get("outputLines")
    total = truncation.get("totalLines")
    if output is not None and total is not None:
        return f"⋮ truncated: {output} of {total} lines"
    return "⋮ truncated"


def _diff_stats(diff: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def _diff_preview_rows(diff: str, width: int) -> list[str]:
    rows: list[str] = []
    kept = 0
    first_changed_line: str | None = None
    for line in diff.splitlines():
        if line.startswith("@@"):
            first_changed_line = _hunk_new_start(line)
            continue
        if line.startswith(("---", "+++")):
            continue
        if not line.startswith(("+", "-", " ")):
            continue
        if first_changed_line is not None:
            rows.append(row(f"    {first_changed_line}", width))
            first_changed_line = None
        rows.append(row("    " + truncate_to_width(line, max(1, width - 4)), width))
        kept += 1
        if kept >= 6:
            rows.append(row("    ⋮", width))
            break
    return rows


def _hunk_new_start(header: str) -> str | None:
    marker = " +"
    start = header.find(marker)
    if start < 0:
        return None
    token = header[start + len(marker) :].split(" ", 1)[0]
    line = token.split(",", 1)[0]
    return line if line.isdigit() else None


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
