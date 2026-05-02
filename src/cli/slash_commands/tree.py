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
        notice = runtime.consume_tree_summary_notice()
        if notice:
            ctx.controller.push_session_message(notice)
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
        "active path:",
    ]
    active_path = _active_path(nodes, current_leaf)
    if active_path:
        active_ids = {node.entry.id for node in active_path}
        for node in reversed(active_path):
            lines.append(_active_line(node, current_leaf=current_leaf, active_ids=active_ids))
    else:
        lines.extend(_root_lines(nodes, current_leaf=current_leaf))

    side_branches = _side_branch_lines(active_path, current_leaf=current_leaf)
    if side_branches:
        lines.append("side branches:")
        lines.extend(side_branches)
    return lines


def _active_path(nodes, current_leaf: str | None) -> list:
    if current_leaf is None:
        return []
    for node in nodes:
        path = _find_path(node, current_leaf)
        if path:
            return path
    return []


def _find_path(node, entry_id: str) -> list:
    if node.entry.id == entry_id:
        return [node]
    for child in node.children:
        path = _find_path(child, entry_id)
        if path:
            return [node, *path]
    return []


def _active_line(node, *, current_leaf: str | None, active_ids: set[str] | None = None) -> str:
    entry = node.entry
    marker = " \u2190 active" if entry.id == current_leaf else ""
    branch_count = _side_branch_count(node, active_ids=active_ids or set())
    branch_text = f" branches={branch_count}" if branch_count else ""
    return (
        f"* entry={_short_entry(entry.id)} {_entry_detail(entry)}{marker}"
        f"{branch_text} parent={_entry_ref(entry.parent_id)}"
    )


def _root_lines(nodes, *, current_leaf: str | None) -> list[str]:
    return [_active_line(node, current_leaf=current_leaf) for node in nodes]


def _side_branch_lines(active_path: list, *, current_leaf: str | None) -> list[str]:
    active_ids = {node.entry.id for node in active_path}
    lines: list[str] = []
    for node in reversed(active_path):
        for child in node.children:
            if child.entry.id in active_ids:
                continue
            lines.append(_side_branch_line(child, parent_id=node.entry.id))
    return lines


def _side_branch_line(node, *, parent_id: str) -> str:
    entry = node.entry
    return (
        f"| * entry={_short_entry(entry.id)} {_entry_detail(entry)} "
        f"from={_entry_ref(parent_id)}"
    )


def _side_branch_count(node, *, active_ids: set[str]) -> int:
    return sum(1 for child in node.children if child.entry.id not in active_ids)


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
