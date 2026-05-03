"""JSONL-only M6 import/export slash commands."""

from __future__ import annotations

from pathlib import Path

from storage.ids import short_session_id

from .registry import SlashCommandContext


def handle_export(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    if len(ctx.args) != 1:
        ctx.controller.push_session_message("/export requires a .jsonl path", level="warn")
        return
    try:
        path = _jsonl_relative_path(ctx.args[0], verb="/export")
    except ValueError as exc:
        ctx.controller.push_session_message(
            str(exc),
            level="warn",
        )
        return
    try:
        exported = runtime.export_jsonl(path, allowed_root=runtime.cwd)
    except (RuntimeError, ValueError, OSError) as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    ctx.controller.push_session_message(f"exported session JSONL: {exported}")


def handle_import(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    if len(ctx.args) != 1:
        ctx.controller.push_session_message("/import requires a .jsonl path", level="warn")
        return
    try:
        path = _jsonl_relative_path(ctx.args[0], verb="/import")
    except ValueError as exc:
        ctx.controller.push_session_message(
            str(exc),
            level="warn",
        )
        return
    try:
        session = runtime.import_jsonl(path, allowed_root=runtime.cwd)
    except (RuntimeError, ValueError, OSError) as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    ctx.controller.refresh_after_session_switch(
        f"imported session {short_session_id(session.id)}"
    )


def _jsonl_relative_path(value: str, *, verb: str) -> Path:
    path = Path(value)
    if path.suffix != ".jsonl":
        raise ValueError(f"M6 {verb} only supports .jsonl; structured exports are M10")
    if value.startswith("~"):
        raise ValueError(f"{verb} path must be relative to the current workspace")
    if path.is_absolute():
        raise ValueError(f"{verb} path must be relative to the current workspace")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"{verb} path must stay inside the current workspace")
    return path


__all__ = ["handle_export", "handle_import"]
