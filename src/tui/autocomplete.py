"""Slash-command + ``@``-file autocomplete (substrate primitives).

The actual command list is registered in :mod:`cli.slash_commands`; this
module only exposes the matchers + a lightweight file fuzzy scorer so
W2 / W6 / W4 don't need three different ranking implementations.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CompletionItem:
    label: str
    detail: str | None = None
    insert_text: str | None = None
    sort_key: tuple[int, str] = field(default=(0, ""))


def _score(label: str, query: str) -> tuple[int, str] | None:
    """Cheap ranking: prefix > substring > subsequence; absent → ``None``."""

    label_lower = label.lower()
    query_lower = query.lower()
    if not query_lower:
        return (0, label_lower)
    if label_lower.startswith(query_lower):
        return (1, label_lower)
    if query_lower in label_lower:
        return (2, label_lower)
    # Subsequence
    j = 0
    for ch in label_lower:
        if j < len(query_lower) and ch == query_lower[j]:
            j += 1
    if j == len(query_lower):
        return (3, label_lower)
    return None


def slash_completions(
    query: str,
    items: Iterable[tuple[str, str | None]],
) -> list[CompletionItem]:
    """Filter / sort registered slash command labels by ``query``.

    ``items`` is a sequence of ``(label, detail)`` pairs — typically supplied
    by ``cli.slash_commands.registry``. Labels include the leading slash.
    """

    results: list[CompletionItem] = []
    q = query[1:] if query.startswith("/") else query
    for label, detail in items:
        bare = label[1:] if label.startswith("/") else label
        score = _score(bare, q)
        if score is None:
            continue
        results.append(
            CompletionItem(
                label=label,
                detail=detail,
                insert_text=label + " ",
                sort_key=score,
            )
        )
    results.sort(key=lambda c: c.sort_key)
    return results


def file_completions(
    query: str,
    cwd: Path,
    *,
    limit: int = 50,
    is_excluded: Callable[[Path], bool] | None = None,
) -> list[CompletionItem]:
    """Breadth-first walk under ``cwd``; cap at ``limit`` to stay snappy."""

    if not cwd.is_dir():
        return []
    q = query[1:] if query.startswith("@") else query
    candidates: list[CompletionItem] = []
    seen: set[Path] = set()
    queue: list[Path] = [cwd]
    while queue and len(candidates) < limit * 4:
        current = queue.pop(0)
        try:
            for entry in sorted(current.iterdir()):
                if entry in seen:
                    continue
                seen.add(entry)
                if entry.name.startswith("."):
                    continue
                if is_excluded is not None and is_excluded(entry):
                    continue
                if entry.is_dir():
                    queue.append(entry)
                    label = entry.relative_to(cwd).as_posix() + "/"
                else:
                    label = entry.relative_to(cwd).as_posix()
                score = _score(label, q)
                if score is None:
                    continue
                candidates.append(
                    CompletionItem(
                        label=label,
                        insert_text=label,
                        sort_key=score,
                    )
                )
        except OSError:
            continue
    candidates.sort(key=lambda c: c.sort_key)
    return candidates[:limit]


__all__ = [
    "CompletionItem",
    "file_completions",
    "slash_completions",
]
