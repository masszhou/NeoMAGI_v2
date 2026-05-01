"""``/clone`` — copy the active branch into a new durable session."""

from __future__ import annotations

from storage.ids import short_session_id

from .registry import SlashCommandContext


def handle_clone(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    try:
        result = runtime.clone_session()
    except RuntimeError as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    ctx.controller.refresh_after_session_switch(
        f"cloned session {short_session_id(result.session.id)}"
    )


__all__ = ["handle_clone"]
