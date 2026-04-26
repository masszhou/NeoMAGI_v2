"""``/new`` — clear current message column, return to idle."""

from __future__ import annotations

from tui.editor import EditorState

from .registry import SlashCommandContext


def handle_new(ctx: SlashCommandContext) -> None:
    ctx.controller.messages.clear()
    ctx.controller.editor.set_state(EditorState.IDLE)
    ctx.controller.editor.set_footer("new session (M1 mock — session manager arrives in M6)")


__all__ = ["handle_new"]
