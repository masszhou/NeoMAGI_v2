"""Path helpers for resource discovery."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def user_config_root() -> Path:
    """Return the NeoMAGI user config root per ADR-0020."""

    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return (Path(xdg).expanduser() / "neomagi").resolve()
    if sys.platform == "win32":
        appdata = (os.environ.get("APPDATA") or "").strip()
        if appdata:
            return (Path(appdata) / "neomagi").resolve()
    return (Path.home() / ".config" / "neomagi").resolve()


def default_magipi_resource_root() -> Path:
    """Return the global MagiPi non-skill resource root per ADR-0020."""

    return user_config_root() / "magipi"


def default_agent_dir() -> Path:
    """Backward-compatible name for the global MagiPi resource root."""

    return default_magipi_resource_root()


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


__all__ = [
    "ancestors_root_to_cwd",
    "default_agent_dir",
    "default_magipi_resource_root",
    "is_relative_to",
    "resolve_resource_path",
    "user_config_root",
]
