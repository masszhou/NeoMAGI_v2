"""Safe cut-point selection for compaction.

The selector works at durable-entry granularity. It may keep only part of a
turn, but it refuses to leave a tool result without its corresponding assistant
tool call in the retained suffix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .tokens import estimate_entry_tokens


@dataclass(frozen=True, slots=True)
class CutPointSelection:
    ok: bool
    first_kept_entry_id: str | None
    keep_from_index: int | None
    tokens_before: int
    tokens_after: int
    reason: str | None = None


def select_cut_point(
    entries: list[Any],
    *,
    keep_recent_tokens: int,
    target_budget: int | None = None,
    pinned_entry_ids: set[str] | None = None,
) -> CutPointSelection:
    if not entries:
        return _selection_failure(
            tokens_before=0,
            tokens_after=0,
            reason="empty-context",
        )

    weights = [estimate_entry_tokens(entry) for entry in entries]
    tokens_before = sum(weights)
    budget = _effective_budget(keep_recent_tokens, target_budget)
    if tokens_before <= budget:
        return _selection_failure(
            tokens_before=tokens_before,
            tokens_after=tokens_before,
            reason="under-budget",
        )

    pinned = pinned_entry_ids or set()
    candidate = _suffix_index_under_budget(entries, weights, budget, pinned)
    if candidate is None:
        return _selection_failure(
            tokens_before=tokens_before,
            tokens_after=tokens_before,
            reason="over-budget",
        )

    return _validated_selection(
        entries,
        weights,
        candidate=candidate,
        tokens_before=tokens_before,
        keep_recent_tokens=keep_recent_tokens,
        target_budget=target_budget,
    )


def _selection_failure(
    *,
    tokens_before: int,
    tokens_after: int,
    reason: str,
) -> CutPointSelection:
    return CutPointSelection(
        ok=False,
        first_kept_entry_id=None,
        keep_from_index=None,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        reason=reason,
    )


def _validated_selection(
    entries: list[Any],
    weights: list[int],
    *,
    candidate: int,
    tokens_before: int,
    keep_recent_tokens: int,
    target_budget: int | None,
) -> CutPointSelection:
    adjusted = _expand_to_tool_call_boundary(entries, candidate)
    tokens_after = sum(weights[adjusted:])
    if target_budget is not None and tokens_after > target_budget:
        return _selection_failure(
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            reason="over-budget",
        )
    if tokens_after > keep_recent_tokens and adjusted < candidate:
        return _selection_failure(
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            reason="no-safe-cut",
        )
    return CutPointSelection(
        ok=True,
        first_kept_entry_id=_entry_pi_id(entries[adjusted]),
        keep_from_index=adjusted,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
    )


def _effective_budget(keep_recent_tokens: int, target_budget: int | None) -> int:
    budget = max(0, keep_recent_tokens)
    if target_budget is not None:
        budget = min(budget, max(0, target_budget))
    return budget


def _suffix_index_under_budget(
    entries: list[Any],
    weights: list[int],
    budget: int,
    pinned_entry_ids: set[str],
) -> int | None:
    total = 0
    index = len(entries)
    while index > 0:
        candidate = index - 1
        candidate_id = _entry_pi_id(entries[candidate])
        next_total = total + weights[candidate]
        if next_total > budget and candidate_id not in pinned_entry_ids:
            break
        total = next_total
        index = candidate
    if index >= len(entries):
        return None
    return index


def _expand_to_tool_call_boundary(entries: list[Any], index: int) -> int:
    retained_tool_calls = _assistant_tool_call_ids(entries[index:])
    for offset, entry in enumerate(entries[index:], start=index):
        call_id = _tool_result_call_id(entry)
        if call_id is None or call_id in retained_tool_calls:
            continue
        assistant_index = _find_assistant_tool_call(entries, call_id, before=offset)
        if assistant_index is None:
            return index
        index = min(index, assistant_index)
        retained_tool_calls.add(call_id)
    return index


def _find_assistant_tool_call(entries: list[Any], call_id: str, *, before: int) -> int | None:
    for index in range(before - 1, -1, -1):
        if call_id in _assistant_tool_call_ids([entries[index]]):
            return index
    return None


def _assistant_tool_call_ids(entries: list[Any]) -> set[str]:
    ids: set[str] = set()
    for entry in entries:
        message = _entry_message(entry)
        if getattr(message, "role", None) != "assistant":
            continue
        for block in getattr(message, "content", []) or []:
            if getattr(block, "type", None) == "toolCall":
                ids.add(str(block.id))
    return ids


def _tool_result_call_id(entry: Any) -> str | None:
    message = _entry_message(entry)
    if getattr(message, "role", None) != "toolResult":
        return None
    return str(message.tool_call_id)


def _entry_message(entry: Any) -> Any:
    payload = getattr(entry, "payload", entry)
    return getattr(payload, "message", None)


def _entry_pi_id(entry: Any) -> str:
    return str(getattr(entry, "pi_export_id", getattr(entry, "id", "")))


__all__ = ["CutPointSelection", "select_cut_point"]
