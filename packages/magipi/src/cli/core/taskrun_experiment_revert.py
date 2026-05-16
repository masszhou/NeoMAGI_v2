"""Safe-revert checks for TaskRun experiment diffs."""

from __future__ import annotations

from collections.abc import Mapping


def safe_revert_check(
    before_snapshot: Mapping[str, object],
    after_snapshot: Mapping[str, object],
    before_status: list[str],
    after_status: list[str],
    numstat: str,
) -> tuple[bool, str | None]:
    safe, reason = _snapshot_check(
        before_snapshot,
        after_snapshot,
        before_status,
        after_status,
    )
    if not safe:
        return safe, reason
    safe, reason = _status_check(after_status)
    if not safe:
        return safe, reason
    return _binary_check(numstat)


def _snapshot_check(
    before_snapshot: Mapping[str, object],
    after_snapshot: Mapping[str, object],
    before_status: list[str],
    after_status: list[str],
) -> tuple[bool, str | None]:
    if not bool(before_snapshot.get("git_available")) or not bool(after_snapshot.get("git_available")):
        return False, "git workspace unavailable"
    if before_snapshot.get("git_head") != after_snapshot.get("git_head"):
        return False, "git HEAD changed during experiment step"
    if before_status:
        return False, "workspace was dirty before experiment step"
    if not after_status:
        return False, "no tracked diff to revert"
    return True, None


def _status_check(after_status: list[str]) -> tuple[bool, str | None]:
    for line in after_status:
        if line.startswith("??"):
            return False, "untracked files are outside safe revert v1"
        if len(line) < 3:
            return False, "unrecognized git status output"
        index_status = line[0]
        worktree_status = line[1]
        if index_status != " ":
            return False, "staged changes are outside safe revert v1"
        if worktree_status not in {"M", "D"}:
            return False, f"git status {line[:2]!r} is outside safe revert v1"
    return True, None


def _binary_check(numstat: str) -> tuple[bool, str | None]:
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and "-" in {parts[0], parts[1]}:
            return False, "binary diff is outside safe revert v1"
    return True, None


__all__ = ["safe_revert_check"]
