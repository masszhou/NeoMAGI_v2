"""TaskRun permission profile snapshots and non-interactive resolution."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .shell_policy import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS
from .types import PolicyDecision, PolicyRequest

PermissionProfileName = Literal["interactive", "guarded", "full"]
NetworkMode = Literal["deny", "allowlist"]
OnUnapprovedMode = Literal["block", "fail_step"]

BUILTIN_PERMISSION_PROFILE_NAMES: tuple[PermissionProfileName, ...] = (
    "interactive",
    "guarded",
    "full",
)
DEFAULT_MAX_CONSECUTIVE_DENIES = 3
DEFAULT_MAX_TOTAL_DENIES = 20

_READ_TOOLS = {"read", "grep", "find", "ls"}
_WRITE_TOOLS = {"write", "edit"}
_SHELL_WRAPPERS = {"bash", "sh", "zsh", "dash", "ksh"}
_INTERPRETER_EVAL_OPTIONS = {
    "node": {"-e", "--eval"},
    "perl": {"-e"},
    "php": {"-r"},
    "ruby": {"-e"},
}
_NETWORK_COMMANDS = {"curl", "wget", "http", "https"}
_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"`<>]+")


class PermissionProfileError(ValueError):
    """Raised when a permission profile cannot be built or applied safely."""


class _ProfileModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class PermissionPathScope(_ProfileModel):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    write_allow: list[str] = Field(default_factory=list, alias="writeAllow")

    @field_validator("allow", "deny", "write_allow", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class PermissionCommandScope(_ProfileModel):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    allow_eval: bool = Field(default=False, alias="allowEval")

    @field_validator("allow", "deny", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class PermissionNetworkScope(_ProfileModel):
    mode: NetworkMode = "deny"
    allow_hosts: list[str] = Field(default_factory=list, alias="allowHosts")

    @field_validator("allow_hosts", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class PermissionGitScope(_ProfileModel):
    allow_commit: bool = Field(default=False, alias="allowCommit")
    allow_reset: bool = Field(default=False, alias="allowReset")
    allow_revert: bool = Field(default=False, alias="allowRevert")
    allow_push: bool = Field(default=False, alias="allowPush")


class PermissionTimeoutScope(_ProfileModel):
    max_seconds: float | None = Field(default=None, alias="maxSeconds")

    @field_validator("max_seconds")
    @classmethod
    def _validate_max_seconds(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("timeouts.maxSeconds must be greater than zero")
        if value > MAX_TIMEOUT_SECONDS:
            raise ValueError(
                f"timeouts.maxSeconds exceeds hard cap of {MAX_TIMEOUT_SECONDS}s"
            )
        return float(value)


class PermissionOnUnapproved(_ProfileModel):
    mode: OnUnapprovedMode = "block"


class PermissionProfileScope(_ProfileModel):
    paths: PermissionPathScope = Field(default_factory=PermissionPathScope)
    commands: PermissionCommandScope = Field(default_factory=PermissionCommandScope)
    network: PermissionNetworkScope = Field(default_factory=PermissionNetworkScope)
    git: PermissionGitScope = Field(default_factory=PermissionGitScope)
    timeouts: PermissionTimeoutScope = Field(default_factory=PermissionTimeoutScope)
    on_unapproved: PermissionOnUnapproved = Field(
        default_factory=PermissionOnUnapproved,
        alias="onUnapproved",
    )


class PermissionProfileSnapshot(_ProfileModel):
    name: PermissionProfileName
    non_interactive: bool = Field(alias="nonInteractive")
    scope: PermissionProfileScope = Field(default_factory=PermissionProfileScope)
    sources: list[str] = Field(default_factory=lambda: ["builtin"])
    explicit_scope: bool = Field(default=False, alias="explicitScope")
    explicit_scope_keys: list[str] = Field(default_factory=list, alias="explicitScopeKeys")


@dataclass(frozen=True, slots=True)
class PermissionBudgetState:
    consecutive_denies: int = 0
    total_denies: int = 0


@dataclass(frozen=True, slots=True)
class PermissionProfileResolution:
    raw_decision: PolicyDecision
    resolved_decision: PolicyDecision
    profile: dict[str, Any]
    metadata: dict[str, Any]
    budget_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class _ScopeCheck:
    allowed: bool
    reason: str | None = None
    audit_tags: list[str] = field(default_factory=list)
    resolved_paths: dict[str, str] = field(default_factory=dict)


def validate_permission_profile_name(name: str) -> PermissionProfileName:
    if name not in BUILTIN_PERMISSION_PROFILE_NAMES:
        raise PermissionProfileError(f"unknown permission profile: {name}")
    return name  # type: ignore[return-value]


def build_permission_profile_snapshot(
    name: str,
    config: Mapping[str, Any] | None = None,
    *,
    sources: list[str] | tuple[str, ...] | None = None,
    explicit_scope: bool | None = None,
    explicit_scope_keys: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Render a stable TaskRun permission profile snapshot."""

    profile_name = validate_permission_profile_name(name)
    builtin = _builtin_profile(profile_name)
    overlay = _normalize_profile_config(dict(config or {}))
    scope_keys = sorted(set(explicit_scope_keys or _explicit_scope_keys(overlay)))
    has_explicit_scope = bool(scope_keys) if explicit_scope is None else bool(explicit_scope)
    merged = _deep_merge(builtin, overlay)
    merged["name"] = profile_name
    merged["sources"] = list(sources or ["builtin"])
    merged["explicitScope"] = has_explicit_scope
    merged["explicitScopeKeys"] = scope_keys
    if profile_name == "full" and not has_explicit_scope:
        raise PermissionProfileError(
            "permission profile 'full' requires explicit taskrun.permissionProfiles.full scope"
        )
    try:
        snapshot = PermissionProfileSnapshot.model_validate(merged)
    except ValidationError as exc:
        raise PermissionProfileError(f"invalid permission profile '{profile_name}': {exc}") from exc
    return snapshot.model_dump(by_alias=True, exclude_none=True)


def normalize_permission_profile_snapshot(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return build_permission_profile_snapshot("interactive")
    raw = dict(value)
    name = raw.get("name")
    if not isinstance(name, str):
        raise PermissionProfileError("permission profile snapshot must include a name")
    if "scope" in raw and "nonInteractive" in raw:
        try:
            snapshot = PermissionProfileSnapshot.model_validate(raw)
            if snapshot.name == "full" and not snapshot.explicit_scope:
                raise PermissionProfileError(
                    "permission profile 'full' requires explicit taskrun.permissionProfiles.full scope"
                )
            return snapshot.model_dump(
                by_alias=True,
                exclude_none=True,
            )
        except ValidationError as exc:
            raise PermissionProfileError(f"invalid permission profile snapshot: {exc}") from exc
    return build_permission_profile_snapshot(name, raw)


def profile_settings_has_explicit_scope(config: Mapping[str, Any] | None) -> bool:
    return bool(_explicit_scope_keys(dict(config or {})))


def profile_explicit_scope_keys(config: Mapping[str, Any] | None) -> list[str]:
    return sorted(_explicit_scope_keys(dict(config or {})))


class PermissionProfileResolver:
    """Resolve raw policy decisions against a TaskRun permission profile."""

    max_consecutive_denies_default = DEFAULT_MAX_CONSECUTIVE_DENIES
    max_total_denies_default = DEFAULT_MAX_TOTAL_DENIES

    def resolve(
        self,
        request: PolicyRequest,
        raw_decision: PolicyDecision,
        profile_snapshot: Mapping[str, Any],
        *,
        ui_available: bool = False,
        budget: Mapping[str, Any] | None = None,
        budget_state: PermissionBudgetState | None = None,
    ) -> PermissionProfileResolution:
        try:
            profile = normalize_permission_profile_snapshot(profile_snapshot)
        except PermissionProfileError as exc:
            metadata = {
                "name": "invalid",
                "sources": [],
                "nonInteractive": True,
                "explicitScope": False,
                "scopeReason": str(exc),
            }
            resolved = _block(
                raw_decision,
                str(exc),
                ["permission:profile:block"],
            )
            return self._with_budget(raw_decision, resolved, {}, metadata, budget, budget_state)
        profile_name = profile["name"]
        metadata: dict[str, Any] = {
            "name": profile_name,
            "sources": list(profile.get("sources") or []),
            "nonInteractive": bool(profile.get("nonInteractive")),
            "explicitScope": bool(profile.get("explicitScope")),
        }
        if profile_name == "interactive" and not ui_available:
            resolved = _block(
                raw_decision,
                "interactive permission profile cannot run headless; choose guarded or full",
                ["permission:interactive:headless"],
            )
            return self._with_budget(raw_decision, resolved, profile, metadata, budget, budget_state)
        if raw_decision.effect == "block":
            resolved = _block(
                raw_decision,
                raw_decision.reason or "blocked by base policy",
                ["permission:raw:block"],
            )
            return self._with_budget(raw_decision, resolved, profile, metadata, budget, budget_state)
        if profile_name == "interactive":
            if raw_decision.effect == "confirm":
                resolved = _block(
                    raw_decision,
                    "interactive confirmation adapter is not available",
                    ["permission:interactive:confirm-unavailable"],
                )
                return self._with_budget(raw_decision, resolved, profile, metadata, budget, budget_state)
            resolved = _allow_with_tags(raw_decision, ["permission:interactive:allow"])
            return PermissionProfileResolution(raw_decision, resolved, profile, metadata)

        scope_check = _check_scope(request, profile)
        metadata["scopeReason"] = scope_check.reason
        if not scope_check.allowed:
            resolved = _block(
                raw_decision,
                scope_check.reason or f"permission profile '{profile_name}' scope denied request",
                [*scope_check.audit_tags, f"permission:{profile_name}:block"],
            )
            return self._with_budget(raw_decision, resolved, profile, metadata, budget, budget_state)

        if raw_decision.effect == "confirm" and profile_name == "guarded":
            resolved = _block(
                raw_decision,
                "permission profile 'guarded' does not auto-approve confirmation",
                ["permission:guarded:confirm:block"],
            )
            return self._with_budget(raw_decision, resolved, profile, metadata, budget, budget_state)

        resolved = _allow_with_tags(
            raw_decision,
            [*scope_check.audit_tags, f"permission:{profile_name}:allow"],
            resolved_paths=scope_check.resolved_paths,
        )
        return PermissionProfileResolution(raw_decision, resolved, profile, metadata)

    def _with_budget(
        self,
        raw_decision: PolicyDecision,
        resolved: PolicyDecision,
        profile: dict[str, Any],
        metadata: dict[str, Any],
        budget: Mapping[str, Any] | None,
        budget_state: PermissionBudgetState | None,
    ) -> PermissionProfileResolution:
        if resolved.effect != "block":
            return PermissionProfileResolution(raw_decision, resolved, profile, metadata)
        state = budget_state or PermissionBudgetState()
        max_consecutive = _budget_int(
            budget,
            "max_consecutive_denies",
            self.max_consecutive_denies_default,
        )
        max_total = _budget_int(
            budget,
            "max_total_denies",
            self.max_total_denies_default,
        )
        next_consecutive = state.consecutive_denies + 1
        next_total = state.total_denies + 1
        if next_consecutive < max_consecutive and next_total < max_total:
            return PermissionProfileResolution(raw_decision, resolved, profile, metadata)
        reason = (
            "permission deny budget exhausted "
            f"(consecutive={next_consecutive}/{max_consecutive}, total={next_total}/{max_total})"
        )
        exhausted = _block(
            resolved,
            reason,
            ["permission:budget:exhausted"],
        )
        metadata["budgetExhausted"] = True
        return PermissionProfileResolution(
            raw_decision,
            exhausted,
            profile,
            metadata,
            budget_exhausted=True,
        )


def _builtin_profile(name: PermissionProfileName) -> dict[str, Any]:
    if name == "interactive":
        return {
            "name": "interactive",
            "nonInteractive": False,
            "scope": {
                "paths": {},
                "commands": {},
                "network": {"mode": "deny"},
                "git": {},
                "timeouts": {},
                "onUnapproved": {"mode": "block"},
            },
            "sources": ["builtin"],
        }
    if name == "guarded":
        return {
            "name": "guarded",
            "nonInteractive": True,
            "scope": {
                "paths": {"allow": ["$WORKSPACE/**"], "deny": []},
                "commands": {"allow": [], "deny": []},
                "network": {"mode": "deny", "allowHosts": []},
                "git": {
                    "allowCommit": False,
                    "allowReset": False,
                    "allowRevert": False,
                    "allowPush": False,
                },
                "timeouts": {"maxSeconds": DEFAULT_TIMEOUT_SECONDS},
                "onUnapproved": {"mode": "block"},
            },
            "sources": ["builtin"],
        }
    return {
        "name": "full",
        "nonInteractive": True,
        "scope": {
            "paths": {"allow": [], "deny": []},
            "commands": {"allow": [], "deny": []},
            "network": {"mode": "deny", "allowHosts": []},
            "git": {
                "allowCommit": False,
                "allowReset": False,
                "allowRevert": False,
                "allowPush": False,
            },
            "timeouts": {},
            "onUnapproved": {"mode": "block"},
        },
        "sources": ["builtin"],
    }


def _deep_merge(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(merged.get(key), Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_profile_config(config: dict[str, Any]) -> dict[str, Any]:
    scope_keys = {
        "paths",
        "commands",
        "network",
        "git",
        "timeouts",
        "onUnapproved",
        "on_unapproved",
    }
    scope = dict(config.get("scope") or {}) if isinstance(config.get("scope"), Mapping) else {}
    changed = False
    for key in list(config):
        if key in scope_keys:
            scope[key] = config.pop(key)
            changed = True
    if changed or scope:
        config["scope"] = scope
    return config


def _explicit_scope_keys(config: Mapping[str, Any]) -> set[str]:
    scope = config.get("scope") if isinstance(config.get("scope"), Mapping) else config
    keys: set[str] = set()
    for key in ("paths", "commands", "network", "git", "timeouts"):
        value = scope.get(key) if isinstance(scope, Mapping) else None
        if _has_meaningful_scope_value(key, value):
            keys.add(key)
    return keys


def _has_meaningful_scope_value(key: str, value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if key == "network":
        return value.get("mode") not in (None, "deny") or bool(value.get("allowHosts") or value.get("allow_hosts"))
    if key == "timeouts":
        return value.get("maxSeconds") is not None or value.get("max_seconds") is not None
    return any(bool(item) for item in value.values())


def _check_scope(request: PolicyRequest, profile: Mapping[str, Any]) -> _ScopeCheck:
    profile_name = str(profile.get("name") or "")
    scope = PermissionProfileScope.model_validate(profile.get("scope") or {})
    if request.tool_name in _READ_TOOLS:
        return _check_path_tool(request, profile_name, scope, mode="read", explicit_paths=True)
    if request.tool_name in _WRITE_TOOLS:
        explicit_paths = "paths" in set(profile.get("explicitScopeKeys") or [])
        return _check_path_tool(request, profile_name, scope, mode="write", explicit_paths=explicit_paths)
    if request.tool_name == "bash":
        return _check_bash_scope(request, profile_name, scope, set(profile.get("explicitScopeKeys") or []))
    return _ScopeCheck(False, f"permission profile '{profile_name}' does not allow tool: {request.tool_name}")


def _check_path_tool(
    request: PolicyRequest,
    profile_name: str,
    scope: PermissionProfileScope,
    *,
    mode: Literal["read", "write"],
    explicit_paths: bool,
) -> _ScopeCheck:
    resolved = _resolve_request_path(request.cwd, request.args.get("path"))
    if isinstance(resolved, _ScopeCheck):
        return resolved
    patterns = _path_allow_patterns(profile_name, scope, mode, explicit_paths)
    denied = _path_matches_any(resolved, scope.paths.deny, request.cwd)
    if denied:
        return _ScopeCheck(
            False,
            f"path denied by permission profile scope: {resolved}",
            ["permission:path:deny"],
            {"path": str(resolved)},
        )
    if not patterns:
        return _ScopeCheck(
            False,
            f"permission profile '{profile_name}' does not allow {mode} path access",
            ["permission:path:block"],
            {"path": str(resolved)},
        )
    if not _path_matches_any(resolved, patterns, request.cwd):
        return _ScopeCheck(
            False,
            f"path outside permission profile scope: {resolved}",
            ["permission:path:block"],
            {"path": str(resolved)},
        )
    return _ScopeCheck(True, "path scope matched", [f"permission:path:{mode}:allow"], {"path": str(resolved)})


def _path_allow_patterns(
    profile_name: str,
    scope: PermissionProfileScope,
    mode: Literal["read", "write"],
    explicit_paths: bool,
) -> list[str]:
    if mode == "read":
        return list(scope.paths.allow)
    if scope.paths.write_allow:
        return list(scope.paths.write_allow)
    if profile_name == "full":
        return list(scope.paths.allow)
    if explicit_paths:
        return list(scope.paths.allow)
    return []


def _resolve_request_path(cwd: str, raw: Any) -> Path | _ScopeCheck:
    try:
        root = Path(cwd).expanduser().resolve(strict=True)
    except Exception as exc:
        return _ScopeCheck(False, f"cwd cannot be resolved: {exc}", ["permission:path:block"])
    if raw is None or raw == "":
        raw = "."
    if not isinstance(raw, str):
        return _ScopeCheck(False, "path must be a string", ["permission:path:block"])
    target = Path(raw).expanduser()
    path = target if target.is_absolute() else root / target
    return path.resolve(strict=False)


def _path_matches_any(path: Path, patterns: list[str], cwd: str) -> bool:
    return any(_path_matches(path, pattern, cwd) for pattern in patterns)


def _path_matches(path: Path, pattern: str, cwd: str) -> bool:
    try:
        root = Path(cwd).expanduser().resolve(strict=True)
    except Exception:
        return False
    expanded = _expand_path_pattern(pattern, root)
    if expanded is None:
        return False
    base, recursive = expanded
    if recursive:
        return path == base or _is_relative_to(path, base)
    return path == base


def _expand_path_pattern(pattern: str, workspace: Path) -> tuple[Path, bool] | None:
    recursive = pattern.endswith("/**")
    raw = pattern[:-3] if recursive else pattern
    if raw == "$WORKSPACE":
        base = workspace
    elif raw.startswith("$WORKSPACE/"):
        base = workspace / raw[len("$WORKSPACE/") :]
    elif raw == "~":
        base = Path.home()
    elif raw.startswith("~/"):
        base = Path.home() / raw[2:]
    else:
        candidate = Path(raw).expanduser()
        base = candidate if candidate.is_absolute() else workspace / candidate
    try:
        return base.resolve(strict=False), recursive
    except Exception:
        return None


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _check_bash_scope(
    request: PolicyRequest,
    profile_name: str,
    scope: PermissionProfileScope,
    explicit_keys: set[str],
) -> _ScopeCheck:
    command = request.args.get("command")
    if not isinstance(command, str) or not command.strip():
        return _ScopeCheck(False, "shell command must be a non-empty string", ["permission:command:block"])
    if _has_dynamic_shell_construct(command):
        return _ScopeCheck(
            False,
            "dynamic shell command substitution is outside permission profile scope",
            ["permission:command:block"],
        )
    timeout = request.args.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, int | float):
        return _ScopeCheck(False, "timeout must be a number", ["permission:timeout:block"])
    if timeout <= 0:
        return _ScopeCheck(False, "timeout must be greater than zero", ["permission:timeout:block"])
    if scope.timeouts.max_seconds is not None and timeout > scope.timeouts.max_seconds:
        return _ScopeCheck(
            False,
            f"timeout exceeds permission profile cap of {scope.timeouts.max_seconds:g}s",
            ["permission:timeout:block"],
        )
    if timeout > MAX_TIMEOUT_SECONDS:
        return _ScopeCheck(
            False,
            f"timeout exceeds hard cap of {MAX_TIMEOUT_SECONDS}s",
            ["permission:timeout:block"],
        )

    segments = _shell_segments(command)
    if segments is None:
        return _ScopeCheck(False, "shell command cannot be parsed safely", ["permission:command:block"])
    if not segments:
        return _ScopeCheck(False, "shell command must include an executable", ["permission:command:block"])

    resolved_paths: dict[str, str] = {}
    for segment in segments:
        check = _check_shell_segment(segment, request.cwd, profile_name, scope, explicit_keys)
        if not check.allowed:
            return check
        resolved_paths.update(check.resolved_paths)
    return _ScopeCheck(True, "command scope matched", ["permission:command:allow"], resolved_paths)


def _check_shell_segment(
    tokens: list[str],
    cwd: str,
    profile_name: str,
    scope: PermissionProfileScope,
    explicit_keys: set[str],
) -> _ScopeCheck:
    executable_index = _executable_index(tokens)
    if executable_index is None:
        return _ScopeCheck(False, "shell command segment cannot be classified", ["permission:command:block"])
    executable = _command_basename(tokens[executable_index])
    if executable in {_command_basename(item) for item in scope.commands.deny}:
        return _ScopeCheck(False, f"command denied by permission profile: {executable}", ["permission:command:deny"])
    if not scope.commands.allow:
        return _ScopeCheck(
            False,
            f"permission profile '{profile_name}' does not allow shell commands",
            ["permission:command:block"],
        )
    allowed = {_command_basename(item) for item in scope.commands.allow}
    if executable not in allowed:
        return _ScopeCheck(False, f"command outside permission profile scope: {executable}", ["permission:command:block"])

    git_check = _check_git_scope(executable, tokens[executable_index + 1 :], scope)
    if not git_check.allowed:
        return git_check

    dynamic_check = _check_dynamic_shell_constructs(tokens)
    if not dynamic_check.allowed:
        return dynamic_check

    nested = _nested_script(executable, tokens[executable_index + 1 :])
    if nested is not None:
        nested_segments = _shell_segments(nested)
        if nested_segments is None:
            return _ScopeCheck(False, "nested shell command cannot be parsed safely", ["permission:command:block"])
        for segment in nested_segments:
            check = _check_shell_segment(segment, cwd, profile_name, scope, explicit_keys)
            if not check.allowed:
                return check
    if _interpreter_eval_code(executable, tokens[executable_index + 1 :]) is not None and not scope.commands.allow_eval:
        return _ScopeCheck(
            False,
            f"interpreter eval is outside permission profile scope: {executable}",
            ["permission:command:block"],
        )

    network_check = _check_network_scope(executable, tokens, scope)
    if not network_check.allowed:
        return network_check

    return _check_output_paths(tokens, cwd, profile_name, scope, explicit_keys)


def _check_git_scope(
    executable: str,
    args: list[str],
    scope: PermissionProfileScope,
) -> _ScopeCheck:
    if executable != "git":
        return _ScopeCheck(True)
    subcommand = _git_subcommand(args)
    if subcommand in {"commit"} and not scope.git.allow_commit:
        return _ScopeCheck(False, "git commit is outside permission profile scope", ["permission:git:block"])
    if subcommand in {"reset", "checkout", "clean"} and not scope.git.allow_reset:
        return _ScopeCheck(False, f"git {subcommand} is outside permission profile scope", ["permission:git:block"])
    if subcommand == "revert" and not scope.git.allow_revert:
        return _ScopeCheck(False, "git revert is outside permission profile scope", ["permission:git:block"])
    if subcommand == "push" and not scope.git.allow_push:
        return _ScopeCheck(False, "git push is outside permission profile scope", ["permission:git:block"])
    return _ScopeCheck(True)


def _git_subcommand(args: list[str]) -> str | None:
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if token in {"-C", "-c"}:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        return token
    return None


def _check_network_scope(
    executable: str,
    tokens: list[str],
    scope: PermissionProfileScope,
) -> _ScopeCheck:
    urls = _extract_urls(tokens)
    if executable in _NETWORK_COMMANDS and not urls:
        return _ScopeCheck(False, "network command host cannot be proven from static arguments", ["permission:network:block"])
    if not urls:
        return _ScopeCheck(True)
    if scope.network.mode == "deny":
        return _ScopeCheck(False, "network access denied by permission profile", ["permission:network:block"])
    allowed_hosts = {host.lower() for host in scope.network.allow_hosts}
    for url in urls:
        host = (urlparse(url).hostname or "").lower()
        if not host or host not in allowed_hosts:
            return _ScopeCheck(False, f"network host outside permission profile scope: {host or url}", ["permission:network:block"])
    return _ScopeCheck(True)


def _check_dynamic_shell_constructs(tokens: list[str]) -> _ScopeCheck:
    for token in tokens:
        if _has_dynamic_shell_construct(token):
            return _ScopeCheck(
                False,
                "dynamic shell command substitution is outside permission profile scope",
                ["permission:command:block"],
            )
    return _ScopeCheck(True)


def _has_dynamic_shell_construct(value: str) -> bool:
    return "$(" in value or "`" in value or "<(" in value or ">(" in value


def _check_output_paths(
    tokens: list[str],
    cwd: str,
    profile_name: str,
    scope: PermissionProfileScope,
    explicit_keys: set[str],
) -> _ScopeCheck:
    for index, token in enumerate(tokens):
        target = _output_target(tokens, index)
        if target is None:
            continue
        fake_request = PolicyRequest(
            toolName="write",
            args={"path": target},
            cwd=cwd,
        )
        check = _check_path_tool(
            fake_request,
            profile_name,
            scope,
            mode="write",
            explicit_paths="paths" in explicit_keys,
        )
        if not check.allowed:
            return check
    return _ScopeCheck(True)


def _shell_segments(command: str) -> list[list[str]] | None:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in {";", "&&", "||", "|"}:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _executable_index(tokens: list[str]) -> int | None:
    index = 0
    if tokens and _command_basename(tokens[0]) == "env":
        index = 1
        while index < len(tokens) and (tokens[index].startswith("-") or "=" in tokens[index]):
            index += 1
    while index < len(tokens) and _is_env_assignment(tokens[index]):
        index += 1
    if index >= len(tokens):
        return None
    return index


def _is_env_assignment(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token))


def _nested_script(executable: str, args: list[str]) -> str | None:
    if executable not in _SHELL_WRAPPERS:
        return None
    for index, token in enumerate(args):
        if token == "--":
            continue
        if token == "-c" and index + 1 < len(args):
            return args[index + 1]
        if token.startswith("-") and "c" in token[1:] and index + 1 < len(args):
            return args[index + 1]
    return None


def _interpreter_eval_code(executable: str, args: list[str]) -> str | None:
    options = {"-c"} if _is_python_interpreter(executable) else _INTERPRETER_EVAL_OPTIONS.get(executable)
    if not options:
        return None
    for index, token in enumerate(args):
        if token in options and index + 1 < len(args):
            return args[index + 1]
    return None


def _is_python_interpreter(executable: str) -> bool:
    return bool(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", executable))


def _extract_urls(tokens: list[str]) -> list[str]:
    urls: list[str] = []
    for token in tokens:
        for match in _URL_RE.finditer(token):
            urls.append(match.group(0).rstrip("),."))
    return urls


def _output_target(tokens: list[str], index: int) -> str | None:
    token = tokens[index]
    if token in {">", ">>"} and index + 1 < len(tokens):
        return tokens[index + 1]
    if token in {"-o", "--output", "-O"} and index + 1 < len(tokens):
        return tokens[index + 1]
    if token.startswith("--output="):
        return token.split("=", 1)[1]
    compact = re.match(r"^(?:\d*|&)?>>?(.+)$", token)
    if compact and compact.group(1):
        return compact.group(1)
    if token.startswith("-o") and len(token) > 2:
        return token[2:]
    return None


def _command_basename(command: str) -> str:
    return Path(command).name


def _block(decision: PolicyDecision, reason: str, audit_tags: list[str]) -> PolicyDecision:
    return decision.model_copy(
        update={
            "effect": "block",
            "reason": reason,
            "audit_tags": [*decision.audit_tags, *audit_tags],
        }
    )


def _allow_with_tags(
    decision: PolicyDecision,
    audit_tags: list[str],
    *,
    resolved_paths: dict[str, str] | None = None,
) -> PolicyDecision:
    paths = dict(decision.resolved_paths)
    paths.update(resolved_paths or {})
    return decision.model_copy(
        update={
            "effect": "allow",
            "reason": None,
            "resolved_paths": paths,
            "audit_tags": [*decision.audit_tags, *audit_tags],
        }
    )


def _budget_int(
    budget: Mapping[str, Any] | None,
    snake_key: str,
    default: int,
) -> int:
    if not budget:
        return default
    camel_key = _snake_to_camel(snake_key)
    value = budget.get(snake_key, budget.get(camel_key))
    if not isinstance(value, int) or value <= 0:
        return default
    return value


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


__all__ = [
    "BUILTIN_PERMISSION_PROFILE_NAMES",
    "DEFAULT_MAX_CONSECUTIVE_DENIES",
    "DEFAULT_MAX_TOTAL_DENIES",
    "NetworkMode",
    "OnUnapprovedMode",
    "PermissionBudgetState",
    "PermissionCommandScope",
    "PermissionGitScope",
    "PermissionNetworkScope",
    "PermissionOnUnapproved",
    "PermissionPathScope",
    "PermissionProfileError",
    "PermissionProfileName",
    "PermissionProfileResolution",
    "PermissionProfileResolver",
    "PermissionProfileScope",
    "PermissionProfileSnapshot",
    "PermissionTimeoutScope",
    "build_permission_profile_snapshot",
    "normalize_permission_profile_snapshot",
    "profile_explicit_scope_keys",
    "profile_settings_has_explicit_scope",
    "validate_permission_profile_name",
]
