"""Path filtering helpers for cwd-bound file traversal."""

from __future__ import annotations

from pathlib import Path


def resolves_within(path: Path, root: Path) -> bool:
    base = root if root.is_dir() else root.parent
    try:
        path.resolve(strict=True).relative_to(base.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


__all__ = ["resolves_within"]
