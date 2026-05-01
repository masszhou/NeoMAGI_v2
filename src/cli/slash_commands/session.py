"""M6 durable session slash commands."""

from __future__ import annotations

from storage.ids import short_session_id

from .registry import SlashCommandContext


def handle_session(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    stats = runtime.session_stats()
    state = runtime.state
    if stats is None:
        ctx.controller.push_session_message(
            f"runtime session {state.runtime_session_id}; durable session unavailable",
            level="warn",
        )
        return
    name = stats.name or "(unnamed)"
    leaf = stats.current_leaf or "none"
    parent = short_session_id(stats.parent_session_id)
    message = (
        f"session {short_session_id(stats.session_id)} name={name} "
        f"cwd={stats.cwd} entries={stats.entry_count} messages={stats.message_count} "
        f"leaf={leaf} parent={parent} cache={stats.provider_cache_affinity_id} "
        f"runtime={state.runtime_session_id}"
    )
    ctx.controller.push_session_message(message)


def handle_name(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    name = " ".join(ctx.args).strip()
    if not name:
        stats = runtime.session_stats()
        current = stats.name if stats is not None and stats.name else "(unnamed)"
        ctx.controller.push_session_message(f"current session name: {current}")
        return
    try:
        session = runtime.rename_session(name)
    except RuntimeError as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    ctx.controller.push_session_message(
        f"renamed session {short_session_id(session.id)} to {name}"
    )


__all__ = ["handle_name", "handle_session"]
