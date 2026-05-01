"""Conservative shell policy for local bash execution."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from .path_policy import resolve_cwd, resolve_cwd_path
from .types import PolicyDecision, PolicyRequest

DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600

_DESTRUCTIVE_PATTERNS = (
    re.compile(r"(^|[;&|]\s*)rm\s+[^;&|]*\s(-[^\s]*r[^\s]*f|-f[^\s]*r)", re.IGNORECASE),
    re.compile(r"(^|[;&|]\s*)rm\s+-rf\s+(/|\*|\.($|/|\s))", re.IGNORECASE),
    re.compile(r"(^|[;&|]\s*)mkfs(\.|$|\s)", re.IGNORECASE),
    re.compile(r"(^|[;&|]\s*)dd\s+[^;&|]*\bof=/dev/", re.IGNORECASE),
    re.compile(r":\s*\(\)\s*\{\s*:\s*\|\s*:\s*;", re.IGNORECASE),
)
_PRIVILEGED_PATHS = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/private/etc",
    "/sbin",
    "/System",
    "/usr/bin",
    "/usr/lib",
    "/usr/sbin",
)


def decide_shell_access(request: PolicyRequest) -> PolicyDecision:
    command = request.args.get("command")
    if not isinstance(command, str) or not command.strip():
        return PolicyDecision.block("shell command must be a non-empty string", audit_tags=["shell:block"])

    timeout_decision = _decide_timeout(request)
    if timeout_decision.effect != "allow":
        return timeout_decision

    try:
        cwd = resolve_cwd(request.cwd)
    except Exception as exc:
        return PolicyDecision.block(str(exc), audit_tags=["shell:cwd:block"])

    blocked = _blocked_command_reason(command)
    if blocked:
        return PolicyDecision.block(blocked, audit_tags=["shell:block"])

    output_block = _blocked_output_path_reason(command, cwd)
    if output_block:
        return PolicyDecision.block(output_block, audit_tags=["shell:path:block"])

    normalized_args = dict(timeout_decision.normalized_args)
    normalized_args["command"] = command
    return PolicyDecision.allow(
        normalized_args=normalized_args,
        resolved_paths={"cwd": str(cwd)},
        audit_tags=["shell:allow"],
    )


def _decide_timeout(request: PolicyRequest) -> PolicyDecision:
    timeout = request.args.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, int | float):
        return PolicyDecision.block("timeout must be a number", audit_tags=["shell:timeout:block"])
    if timeout <= 0:
        return PolicyDecision.block("timeout must be greater than zero", audit_tags=["shell:timeout:block"])
    if timeout > MAX_TIMEOUT_SECONDS:
        return PolicyDecision.block(
            f"timeout exceeds hard cap of {MAX_TIMEOUT_SECONDS}s",
            audit_tags=["shell:timeout:block"],
        )
    normalized = dict(request.args)
    normalized["timeout"] = float(timeout)
    return PolicyDecision.allow(normalized_args=normalized, audit_tags=["shell:timeout:allow"])


def _blocked_command_reason(command: str) -> str | None:
    tokens = _split_command(command)
    if tokens and tokens[0] in {"sudo", "su"}:
        return f"{tokens[0]} is blocked by shell policy"
    if any(token == "sudo" for token in tokens):
        return "sudo is blocked by shell policy"
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return "destructive shell command is blocked by policy"
    for token in tokens:
        if _is_privileged_path(token):
            return f"privileged path is blocked by shell policy: {token}"
    return None


def _blocked_output_path_reason(command: str, cwd: Path) -> str | None:
    tokens = _split_command(command)
    for index, token in enumerate(tokens):
        reason = _blocked_output_option_reason(tokens, index, cwd)
        if reason:
            return reason
        reason = _blocked_redirect_reason(tokens, index, cwd)
        if reason:
            return reason
    return None


def _blocked_output_option_reason(tokens: list[str], index: int, cwd: Path) -> str | None:
    output = _output_option_target(tokens, index)
    if output and _path_escapes(output, cwd):
        return f"shell output path escapes cwd: {output}"
    return None


def _output_option_target(tokens: list[str], index: int) -> str | None:
    token = tokens[index]
    if token in {"-o", "--output", "-O"} and index + 1 < len(tokens):
        return tokens[index + 1]
    if token.startswith("--output="):
        return token.split("=", 1)[1]
    return _compact_output_option_target(token)


def _blocked_redirect_reason(tokens: list[str], index: int, cwd: Path) -> str | None:
    target = _redirect_target(tokens, index)
    if target and _path_escapes(target, cwd):
        return f"shell redirect path escapes cwd: {target}"
    return None


def _redirect_target(tokens: list[str], index: int) -> str | None:
    token = tokens[index]
    if token in {">", ">>"} and index + 1 < len(tokens):
        return tokens[index + 1]
    return _compact_redirect_target(token)


def _path_escapes(raw: str, cwd: Path) -> bool:
    if not raw or raw.startswith("-") or "://" in raw:
        return False
    try:
        resolve_cwd_path(cwd, raw)
    except Exception:
        return True
    return False


def _is_privileged_path(token: str) -> bool:
    if not token.startswith("/"):
        return False
    path = token.rstrip("/").split("=", 1)[-1]
    return any(path == base or path.startswith(f"{base}/") for base in _PRIVILEGED_PATHS)


def _compact_output_option_target(token: str) -> str | None:
    if token.startswith("-o") and len(token) > 2 and token != "--output":
        return token[2:]
    return None


def _compact_redirect_target(token: str) -> str | None:
    match = re.match(r"^(?:\d*|&)?>>?(.+)$", token)
    if not match:
        return None
    target = match.group(1)
    return target or None


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "decide_shell_access",
]
