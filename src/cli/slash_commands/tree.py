"""``/tree`` — inspect or switch the current durable session leaf."""

from __future__ import annotations

from storage.ids import short_session_id

from .registry import SlashCommandContext


def handle_tree(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    if ctx.args:
        entry_id = ctx.args[0]
        try:
            session = runtime.select_session_leaf(entry_id)
        except RuntimeError as exc:
            ctx.controller.push_session_message(_tree_error_message(str(exc), entry_id), level="error")
            return
        ctx.controller.refresh_after_session_switch(
            f"selected leaf in session {short_session_id(session.id)}",
            summary=runtime.session_switch_summary("selected"),
        )
        return
    tree = runtime.session_tree()
    if not tree:
        ctx.controller.push_session_message("current session has no entries")
        return
    ctx.controller.push_session_message(
        "\n".join(_tree_lines(tree, stats=runtime.session_stats())[:16])
    )


def _tree_lines(nodes, *, stats=None) -> list[str]:
    session_id = short_session_id(stats.session_id) if stats is not None else "unknown"
    current_leaf = stats.current_leaf if stats is not None else None
    lines = [
        f"session={session_id} current={_entry_ref(current_leaf)}",
        "entries:",
    ]
    for node in _flatten_tree(nodes):
        entry = node.entry
        marker = " \u2190 active" if entry.id == current_leaf else ""
        lines.append(
            f"+- entry={_short_entry(entry.id)} "
            f"parent={_entry_ref(entry.parent_id)} {_entry_detail(entry)}{marker}"
        )
    return lines


def _flatten_tree(nodes) -> list:
    flattened = []
    for node in nodes:
        flattened.append(node)
        flattened.extend(_flatten_tree(node.children))
    return flattened


def _entry_detail(entry) -> str:
    if entry.type == "message":
        return f"message:{entry.message.role}"
    return entry.type


def _entry_ref(value: str | None) -> str:
    return f"entry:{_short_entry(value)}" if value else "none"


def _short_entry(value: str | None) -> str:
    return (value or "none")[:8]


def _tree_error_message(message: str, entry_id: str) -> str:
    if message.startswith("unknown entry") and _looks_like_session_id_prefix(entry_id):
        return f"unknown entry id: {entry_id}; use /resume for session ids"
    if message.startswith("unknown entry"):
        return f"unknown entry id: {entry_id}"
    return message


def _looks_like_session_id_prefix(value: str) -> bool:
    return len(value) >= 8 and all(char in "0123456789abcdefABCDEF-" for char in value)


__all__ = ["handle_tree"]
