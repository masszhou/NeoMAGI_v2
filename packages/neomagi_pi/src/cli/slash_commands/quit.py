"""``/quit`` — Confirm overlay then exit lifecycle."""

from __future__ import annotations

from tui.overlay import Confirm

from .registry import SlashCommandContext


def handle_quit(ctx: SlashCommandContext) -> None:
    controller = ctx.controller
    confirm = Confirm(
        "Quit NeoMAGI?",
        on_choose=lambda yes: controller.exit() if yes else None,
    )
    controller.open_overlay(confirm)


__all__ = ["handle_quit"]
