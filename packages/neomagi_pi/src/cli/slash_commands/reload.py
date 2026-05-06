"""Reload extensions, skills, prompt templates and context resources."""

from __future__ import annotations

from .extensions import refresh_extension_commands
from .registry import SlashCommandContext


def handle_reload(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.status.push_notification("reload requires an interactive runtime", level="warn")
        return
    try:
        summary = runtime.reload_resources()
    except Exception as exc:
        ctx.controller.status.push_notification(str(exc), level="error", ttl_seconds=6.0)
        return
    refresh_extension_commands(ctx.controller)
    ctx.controller.push_session_message(summary)
    ctx.controller.editor.set_footer(runtime.footer_summary)


__all__ = ["handle_reload"]
