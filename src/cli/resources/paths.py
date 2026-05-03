"""Path helpers for resource discovery."""

from __future__ import annotations

import os
from pathlib import Path


def default_agent_dir() -> Path:
    """Return the Pi-compatible global agent directory.

    Tests can set ``NEOMAGI_AGENT_DIR`` to avoid touching the real home dir.
    """

    override = os.environ.get("NEOMAGI_AGENT_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".pi" / "agent").resolve()


def resolve_resource_path(value: str | Path, *, base_dir: Path, cwd: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    if str(value).startswith("."):
        return (cwd / path).resolve()
    return (base_dir / path).resolve()


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def ancestors_root_to_cwd(cwd: Path) -> list[Path]:
    resolved = cwd.resolve()
    return list(reversed([resolved, *resolved.parents]))


__all__ = ["ancestors_root_to_cwd", "default_agent_dir", "is_relative_to", "resolve_resource_path"]
