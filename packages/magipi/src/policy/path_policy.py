"""Cwd-bound path policy helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .sensitive_paths import sensitive_path_reason
from .types import PolicyDecision, PolicyRequest

PathMode = Literal["read", "write"]


def resolve_cwd(cwd: str | Path) -> Path:
    root = Path(cwd).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd}")
    return root


def resolve_cwd_path(cwd: str | Path, path: str | None = None) -> Path:
    root = resolve_cwd(cwd)
    raw = Path(path).expanduser() if path else root
    target = raw if raw.is_absolute() else root / raw
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"path escapes cwd: {path or '.'}") from exc
    return resolved


def decide_path_access(
    request: PolicyRequest,
    *,
    path_arg: str = "path",
    mode: PathMode = "read",
) -> PolicyDecision:
    try:
        resolved = resolve_cwd_path(request.cwd, request.args.get(path_arg))
    except Exception as exc:
        return PolicyDecision.block(str(exc), audit_tags=[f"path:{mode}:block"])
    sensitive_reason = sensitive_path_reason(str(request.args.get(path_arg) or "."), cwd=request.cwd)
    if sensitive_reason is not None:
        return PolicyDecision.block(sensitive_reason, audit_tags=[f"path:{mode}:sensitive:block"])

    normalized_args = dict(request.args)
    normalized_args[path_arg] = str(request.args.get(path_arg) or ".")
    return PolicyDecision.allow(
        normalized_args=normalized_args,
        resolved_paths={path_arg: str(resolved)},
        audit_tags=[f"path:{mode}:allow"],
    )


__all__ = [
    "PathMode",
    "decide_path_access",
    "resolve_cwd",
    "resolve_cwd_path",
]
