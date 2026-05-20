"""Shared sensitive-path checks for path and shell policy."""

from __future__ import annotations

import os
import re
from pathlib import Path

_PROC_ENVIRON_RE = re.compile(r"^/proc/[^/]+/environ$")


def sensitive_path_reason(raw_path: str, *, cwd: str | Path | None = None) -> str | None:
    path = _normalize_path(raw_path, cwd)
    lowered = path.as_posix().lower()
    name = path.name.lower()
    parts = tuple(part.lower() for part in path.parts)

    if name == ".env" or ".env" in parts:
        return f"sensitive path is blocked by policy: {raw_path}"
    if name == ".netrc":
        return f"sensitive path is blocked by policy: {raw_path}"
    if name == "auth.json":
        return f"sensitive path is blocked by policy: {raw_path}"
    if len(parts) >= 2 and parts[-2] == ".aws" and name == "credentials":
        return f"sensitive path is blocked by policy: {raw_path}"
    if len(parts) >= 2 and parts[-2] == ".ssh" and name.startswith("id_"):
        return f"sensitive path is blocked by policy: {raw_path}"
    if lowered in {"/etc/sudoers", "/etc/group", "/private/etc/sudoers", "/private/etc/group"}:
        return f"sensitive path is blocked by policy: {raw_path}"
    if lowered in {"/etc/ssh", "/private/etc/ssh"} or lowered.startswith(("/etc/ssh/", "/private/etc/ssh/")):
        return f"sensitive path is blocked by policy: {raw_path}"
    if os.name == "posix" and _PROC_ENVIRON_RE.match(lowered):
        return f"sensitive path is blocked by policy: {raw_path}"
    return None


def is_sensitive_path(raw_path: str, *, cwd: str | Path | None = None) -> bool:
    return sensitive_path_reason(raw_path, cwd=cwd) is not None


def _normalize_path(raw_path: str, cwd: str | Path | None) -> Path:
    expanded = _expand_home_vars(raw_path)
    path = Path(expanded).expanduser()
    if not path.is_absolute() and cwd is not None:
        path = Path(cwd) / path
    return path.resolve(strict=False)


def _expand_home_vars(raw_path: str) -> str:
    home = str(Path.home())
    return raw_path.replace("${HOME}", home).replace("$HOME", home).replace("~", home, 1)


__all__ = ["is_sensitive_path", "sensitive_path_reason"]
