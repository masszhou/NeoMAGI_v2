"""``/compact`` manual durable-session compaction."""

from __future__ import annotations

from storage.ids import short_session_id

from .registry import SlashCommandContext


def handle_compact(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    instructions = _compact_instructions(ctx.raw)
    try:
        result = runtime.compact_session(custom_instructions=instructions)
    except RuntimeError as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    session_id = runtime.state.durable_session_id or "none"
    ctx.controller.push_session_message(
        "compacted "
        f"session={short_session_id(session_id)} "
        f"firstKept=entry:{result.result.first_kept_entry_id[:8]} "
        f"tokensBefore={result.result.tokens_before}"
    )
    ctx.controller.editor.set_footer(runtime.footer_summary)


def _compact_instructions(raw: str) -> str | None:
    text = raw.lstrip()
    if not text.startswith("/compact"):
        return None
    tail = text[len("/compact") :]
    return tail if tail.strip() else None


__all__ = ["handle_compact"]
