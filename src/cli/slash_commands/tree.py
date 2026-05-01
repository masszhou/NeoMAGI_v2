"""``/tree`` — inspect or switch the current durable session leaf."""

from __future__ import annotations

from .registry import SlashCommandContext


def handle_tree(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    if ctx.args:
        try:
            session = runtime.select_session_leaf(ctx.args[0])
        except RuntimeError as exc:
            ctx.controller.push_session_message(str(exc), level="error")
            return
        ctx.controller.refresh_after_session_switch(
            f"selected leaf in session {session.id.split('-', 1)[0]}"
        )
        return
    tree = runtime.session_tree()
    if not tree:
        ctx.controller.push_session_message("current session has no entries")
        return
    ctx.controller.push_session_message("\n".join(_tree_lines(tree)[:16]))


def _tree_lines(nodes, *, indent: str = "") -> list[str]:
    lines: list[str] = []
    for node in nodes:
        entry = node.entry
        detail = entry.type
        if entry.type == "message":
            detail = f"message:{entry.message.role}"
        lines.append(f"{indent}{entry.id}  {detail}")
        lines.extend(_tree_lines(node.children, indent=indent + "  "))
    return lines


__all__ = ["handle_tree"]
