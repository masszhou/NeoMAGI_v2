"""``/new`` — reset current in-memory interactive session."""

from __future__ import annotations

from .registry import SlashCommandContext


def handle_new(ctx: SlashCommandContext) -> None:
    ctx.controller.reset_session()


__all__ = ["handle_new"]
