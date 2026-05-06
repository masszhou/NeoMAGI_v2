"""``/share`` boundary: no upload in P1 core."""

from __future__ import annotations

from .registry import SlashCommandContext


def handle_share(ctx: SlashCommandContext) -> None:
    ctx.controller.push_session_message(
        "/share does not upload in P1; use /export <file.session.json|file.html> "
        "and pass the local artifact to an external publisher",
        level="info",
    )


__all__ = ["handle_share"]
