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
    path = Path(ctx.args[0])
    if path.suffix != ".jsonl":
        ctx.controller.push_session_message(
            "M6 /export only supports .jsonl; HTML/share/filter export is M10",
            level="warn",
        )
        return
    try:
        exported = runtime.export_jsonl(path)
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
    path = Path(ctx.args[0])
    if path.suffix != ".jsonl":
        ctx.controller.push_session_message(
            "M6 /import only supports .jsonl; structured imports are M10",
            level="warn",
        )
        return
    try:
        session = runtime.import_jsonl(path)
    except (RuntimeError, ValueError, OSError) as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    ctx.controller.refresh_after_session_switch(
        f"imported session {short_session_id(session.id)}"
    )


__all__ = ["handle_export", "handle_import"]
