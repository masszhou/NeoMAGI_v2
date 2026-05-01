"""``/fork`` — branch before a historical user message."""

from __future__ import annotations

from storage.ids import short_session_id

from .registry import SlashCommandContext


def handle_fork(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    if not ctx.args:
        ctx.controller.push_session_message("/fork requires a user message entry id", level="warn")
        return
    try:
        result = runtime.fork_session(ctx.args[0])
    except RuntimeError as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    ctx.controller.refresh_after_session_switch(
        f"forked session {short_session_id(result.session.id)}",
        editor_prefill=result.editor_prefill,
    )


__all__ = ["handle_fork"]
