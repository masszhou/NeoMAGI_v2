"""``/copy`` — copy the last assistant text from the active session."""

from __future__ import annotations

import platform
import subprocess

from .registry import SlashCommandContext


def handle_copy(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    if ctx.args:
        ctx.controller.push_session_message("/copy takes no arguments", level="warn")
        return
    try:
        text = runtime.last_assistant_text()
    except RuntimeError as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    if not text:
        ctx.controller.push_session_message("no assistant message to copy", level="warn")
        return
    if copy_to_clipboard(text):
        ctx.controller.push_session_message("copied last assistant message")
    else:
        ctx.controller.push_session_message(
            "clipboard helper unavailable; assistant text remains in the transcript",
            level="warn",
        )


def copy_to_clipboard(
    text: str,
    *,
    runner=subprocess.run,
    platform_name: str | None = None,
) -> bool:
    if (platform_name or platform.system()).lower() != "darwin":
        return False
    try:
        runner(
            ["pbcopy"],
            input=text,
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


__all__ = ["copy_to_clipboard", "handle_copy"]
