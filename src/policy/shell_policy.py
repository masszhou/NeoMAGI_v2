"""Conservative shell policy for local bash execution."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
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
_MAX_NESTED_COMMAND_DEPTH = 3
_SHELL_WRAPPERS = {"bash", "sh", "zsh", "dash", "ksh"}
_INTERPRETER_EVAL_OPTIONS = {
    "node": {"-e", "--eval"},
    "perl": {"-e"},
    "php": {"-r"},
    "ruby": {"-e"},
}
_DIRECT_SENSITIVE_PATHS = (
    "/etc/passwd",
    "/etc/shadow",
    "/private/etc/hosts",
)
_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"`]+")
_FILE_ACCESS_RE = re.compile(
    r"\b(?:open|Path|readFileSync|readFile|file_get_contents|File\.read|IO\.read)"
    r"\s*\(\s*(?P<quote>['\"])(?P<path>/[^'\"]+)(?P=quote)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _BlockedCommand:
    reason: str
    path_literal: str | None = None


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

    blocked = _blocked_command(command)
    if blocked:
        resolved_paths = (
            {"blockedPathLiteral": blocked.path_literal}
            if blocked.path_literal is not None
            else {}
        )
        return PolicyDecision(
            effect="block",
            reason=blocked.reason,
            resolvedPaths=resolved_paths,
            auditTags=["shell:block"],
        )

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
    blocked = _blocked_command(command)
    return blocked.reason if blocked is not None else None


def _blocked_command(command: str, *, depth: int = 0) -> _BlockedCommand | None:
    tokens = _split_command(command)
    blocked = _top_level_block(command, tokens)
    if blocked is not None:
        return blocked
    if depth >= _MAX_NESTED_COMMAND_DEPTH:
        return None
    return _nested_block(tokens, depth)


def _top_level_block(command: str, tokens: list[str]) -> _BlockedCommand | None:
    if tokens and tokens[0] in {"sudo", "su"}:
        return _BlockedCommand(f"{tokens[0]} is blocked by shell policy")
    if any(token == "sudo" for token in tokens):
        return _BlockedCommand("sudo is blocked by shell policy")
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return _BlockedCommand("destructive shell command is blocked by policy")
    for token in tokens:
        if _is_privileged_path(token):
            return _BlockedCommand(
                f"privileged path is blocked by shell policy: {token}",
                path_literal=token,
            )
    return None


def _nested_block(tokens: list[str], depth: int) -> _BlockedCommand | None:
    nested_script = _shell_wrapper_script(tokens)
    if nested_script is not None:
        return _blocked_command(nested_script, depth=depth + 1)
    code = _interpreter_code_string(tokens)
    if code is not None:
        literal = _blocked_eval_path_literal(code)
        if literal is not None:
            return _BlockedCommand(
                f"privileged path is blocked by shell policy: {literal}",
                path_literal=literal,
            )
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


def _shell_wrapper_script(tokens: list[str]) -> str | None:
    if not tokens or _command_basename(tokens[0]) not in _SHELL_WRAPPERS:
        return None
    for index, token in enumerate(tokens[1:], start=1):
        if token == "--":
            continue
        if token == "-c" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("-") and "c" in token[1:] and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def _interpreter_code_string(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    command = _command_basename(tokens[0])
    options = {"-c"} if _is_python_interpreter(command) else _INTERPRETER_EVAL_OPTIONS.get(command)
    if not options:
        return None
    for index, token in enumerate(tokens[1:], start=1):
        if token in options and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def _blocked_eval_path_literal(code: str) -> str | None:
    code = _mask_urls(code)
    for match in _FILE_ACCESS_RE.finditer(code):
        path = match.group("path")
        if _is_privileged_path(path):
            return path
    for path in _DIRECT_SENSITIVE_PATHS:
        if path in code:
            return path
    return None


def _command_basename(command: str) -> str:
    return Path(command).name


def _is_python_interpreter(command: str) -> bool:
    return bool(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", command))


def _mask_urls(value: str) -> str:
    return _URL_RE.sub("", value)


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
