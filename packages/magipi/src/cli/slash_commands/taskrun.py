"""``/taskrun`` bridge for foreground TaskRun inspection/control."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

from cli.taskrun_commands import run_taskrun_command

from .registry import SlashCommandContext

_SLASH_ALLOWED_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "cancel",
        "close",
        "events",
        "history",
        "list",
        "next",
        "start",
        "status",
        "summary",
    }
)


def handle_taskrun(ctx: SlashCommandContext) -> None:
    if not ctx.args or ctx.args[0] not in _SLASH_ALLOWED_SUBCOMMANDS:
        ctx.controller.push_session_message(_usage(), level="warn")
        return

    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = run_taskrun_command(ctx.args, prog="magipi")
    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else 2

    rendered = "\n".join(
        part.strip()
        for part in (stdout.getvalue(), stderr.getvalue())
        if part.strip()
    )
    if not rendered:
        rendered = f"taskrun command exited with status {exit_code}"
    ctx.controller.push_session_message(
        rendered,
        level="info" if exit_code == 0 else "warn",
    )


def _usage() -> str:
    return (
        "usage: /taskrun "
        "status|list|summary|next|history|events|start|cancel|close ..."
    )


__all__ = ["handle_taskrun"]
