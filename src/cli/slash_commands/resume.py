"""``/resume`` — switch to a durable session."""

from __future__ import annotations

from storage.ids import short_session_id

from .registry import SlashCommandContext


def handle_resume(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    if not ctx.args:
        recent = runtime.list_recent_sessions(limit=8)
        if not recent:
            ctx.controller.push_session_message("no sessions to resume")
            return
        lines = ["recent sessions:"]
        for session in recent:
            name = f" {session.display_name}" if session.display_name else ""
            lines.append(f"{short_session_id(session.id)}  {session.id}{name}")
        ctx.controller.push_session_message("\n".join(lines))
        return
    session_id = ctx.args[0]
    try:
        session = runtime.resume_session(session_id)
    except RuntimeError as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    ctx.controller.refresh_after_session_switch(
        f"resumed session {short_session_id(session.id)}"
    )


__all__ = ["handle_resume"]
