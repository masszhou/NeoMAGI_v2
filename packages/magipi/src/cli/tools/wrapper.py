"""ToolDefinition -> RuntimeAgentTool wrapper with policy and audit."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from ai_provider.tools import validate_tool_arguments
from ai_provider.types import Tool
from agent_core.runtime_types import AbortSignal, RuntimeAgentTool, ToolUpdateCallback, maybe_await
from agent_core.types import AgentToolResult
from policy.audit import AuditRecord, AuditSink, InMemoryAuditSink
from policy.path_policy import decide_path_access
from policy.redaction import (
    redact_literal_values as _redact_literal_values,
    redact_secret_keys as _redact_secrets,
    redacted_command_preview as _redacted_command_preview,
)
from policy.permission_profiles import (
    PermissionBudgetState,
    PermissionProfileResolver,
)
from policy.shell_policy import decide_shell_access
from policy.types import PolicyActor, PolicyDecision, PolicyRequest

from .definitions import SkillEnvGrant, ToolDefinition, ToolExecutionContext

PolicyDecider = Callable[[PolicyRequest], PolicyDecision | Awaitable[PolicyDecision]]
TaskPermissionDecisionRecorder = Callable[..., None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TaskRunPermissionContext:
    task_run_id: str
    permission_profile: Mapping[str, Any]
    budget: Mapping[str, Any] | None = None
    budget_state: PermissionBudgetState = field(default_factory=PermissionBudgetState)
    step_id: str | None = None
    tool_execution_id: str | None = None
    ui_available: bool = False
    record_permission_decision: TaskPermissionDecisionRecorder | None = None


@dataclass(frozen=True, slots=True)
class _ResolvedPolicy:
    raw: PolicyDecision
    resolved: PolicyDecision
    permission_profile: dict[str, Any] | None = None


class ToolRuntime:
    def __init__(
        self,
        *,
        cwd: str,
        runtime_session_id: str | None = None,
        run_id: str | None = None,
        run_id_provider: Callable[[], str | None] | None = None,
        actor: PolicyActor = "model",
        audit_sink: AuditSink | None = None,
        policy_decider: PolicyDecider | None = None,
        permission_resolver: PermissionProfileResolver | None = None,
        taskrun_permission_context: TaskRunPermissionContext | None = None,
        skill_env_grant_provider: Callable[[], SkillEnvGrant | None] | None = None,
    ) -> None:
        self.cwd = cwd
        self.runtime_session_id = runtime_session_id
        self.run_id = run_id
        self.run_id_provider = run_id_provider
        self.actor = actor
        self.audit_sink = audit_sink or InMemoryAuditSink()
        self.policy_decider = policy_decider or default_policy_decider
        self.permission_resolver = permission_resolver
        self.taskrun_permission_context = taskrun_permission_context
        self.skill_env_grant_provider = skill_env_grant_provider

    @property
    def current_run_id(self) -> str | None:
        if self.run_id_provider is not None:
            return self.run_id_provider() or self.run_id
        return self.run_id

    @property
    def current_skill_env_grant(self) -> SkillEnvGrant | None:
        if self.skill_env_grant_provider is None:
            return None
        return self.skill_env_grant_provider()


def wrap_tool_definition(definition: ToolDefinition, runtime: ToolRuntime) -> RuntimeAgentTool:
    async def execute(
        tool_call_id: str,
        args: Any,
        signal: AbortSignal | None,
        on_update: ToolUpdateCallback | None,
    ) -> AgentToolResult:
        if definition.prepare_arguments is not None:
            args = definition.prepare_arguments(args)
        if not isinstance(args, dict):
            return _error_result("tool arguments must be an object", is_error=True)
        return await _execute_governed(definition, runtime, tool_call_id, args, signal, on_update)

    return RuntimeAgentTool(
        name=definition.name,
        label=definition.label,
        description=definition.description,
        parameters=definition.parameters,
        execute=execute,
        prepare_arguments=definition.prepare_arguments,
        execution_mode=definition.execution_mode,
    )


async def _execute_governed(
    definition: ToolDefinition,
    runtime: ToolRuntime,
    tool_call_id: str,
    args: dict[str, Any],
    signal: AbortSignal | None,
    on_update: ToolUpdateCallback | None,
) -> AgentToolResult:
    started = _now_iso()
    start_monotonic = time.monotonic()
    run_id = runtime.current_run_id
    decision = PolicyDecision.block("policy did not run")
    raw_decision: PolicyDecision | None = None
    request: PolicyRequest | None = None
    permission_profile: dict[str, Any] | None = None
    result: AgentToolResult | None = None
    exception: Exception | None = None
    try:
        validation_error = _validate_args(definition, args)
        if validation_error is not None:
            decision = PolicyDecision.block("schema validation failed", audit_tags=["schema:block"])
            result = _error_result(validation_error, is_error=True)
            result = _with_common_details(result, decision, raw_decision, permission_profile, run_id, started, start_monotonic)
            return result
        request = _policy_request(definition, runtime, tool_call_id, args, run_id)
        resolved = await _resolve_policy_decision(runtime, request)
        raw_decision = resolved.raw
        decision = resolved.resolved
        permission_profile = resolved.permission_profile
        result = await _run_or_block_tool(
            definition,
            runtime,
            tool_call_id,
            args,
            run_id,
            decision,
            signal,
            on_update,
        )
        result = _with_common_details(result, decision, raw_decision, permission_profile, run_id, started, start_monotonic)
        return result
    except Exception as exc:  # convert all tool exceptions into structured results
        exception = exc
        result = _error_result(str(exc), is_error=True)
        result = _with_common_details(result, decision, raw_decision, permission_profile, run_id, started, start_monotonic, exception=exc)
        return result
    finally:
        if result is not None:
            finalize_errors = await _finalize_governed_execution(
                runtime, definition, tool_call_id, args, request,
                raw_decision, decision, permission_profile, result,
                run_id, started, start_monotonic, exception,
            )
            if finalize_errors:
                _attach_finalize_errors(result, finalize_errors)


async def _finalize_governed_execution(
    runtime: ToolRuntime,
    definition: ToolDefinition,
    tool_call_id: str,
    args: dict[str, Any],
    request: PolicyRequest | None,
    raw_decision: PolicyDecision | None,
    decision: PolicyDecision,
    permission_profile: dict[str, Any] | None,
    result: AgentToolResult,
    run_id: str | None,
    started: str,
    start_monotonic: float,
    exception: Exception | None,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    try:
        await _record_task_permission_decision(
            runtime,
            request,
            raw_decision,
            decision,
            permission_profile,
            started,
        )
    except Exception as exc:
        errors.append(_finalize_error("task_permission_decision", exc))
    try:
        await _audit(
            runtime,
            definition,
            tool_call_id,
            args,
            decision,
            raw_decision,
            permission_profile,
            result,
            run_id,
            started,
            start_monotonic,
            exception,
        )
    except Exception as exc:
        errors.append(_finalize_error("audit", exc))
    return errors


def _finalize_error(sink: str, exc: Exception) -> dict[str, str]:
    return {
        "sink": sink,
        "exceptionClass": type(exc).__name__,
        "message": str(exc),
    }


def _attach_finalize_errors(
    result: AgentToolResult,
    errors: list[dict[str, str]],
) -> None:
    details = result.details if isinstance(result.details, dict) else {}
    details["toolFinalizeErrors"] = errors
    result.details = details


def _policy_request(
    definition: ToolDefinition,
    runtime: ToolRuntime,
    tool_call_id: str,
    args: dict[str, Any],
    run_id: str | None,
) -> PolicyRequest:
    return PolicyRequest(
        runtimeSessionId=runtime.runtime_session_id,
        runId=run_id,
        toolName=definition.name,
        args=args,
        cwd=runtime.cwd,
        actor=runtime.actor,
        source={
            "tool_call_id": tool_call_id,
            "input_origin": _input_origin(runtime.actor),
            "actor_role": runtime.actor,
        },
    )


def _input_origin(actor: PolicyActor) -> str:
    if actor == "model":
        return "model"
    if actor == "user":
        return "user_bash"
    return "extension"


async def _resolve_policy_decision(runtime: ToolRuntime, request: PolicyRequest) -> _ResolvedPolicy:
    decision = await maybe_await(runtime.policy_decider(request))
    if not isinstance(decision, PolicyDecision):
        decision = PolicyDecision.model_validate(decision)
    if runtime.taskrun_permission_context is not None:
        context = runtime.taskrun_permission_context
        resolver = runtime.permission_resolver or PermissionProfileResolver()
        resolution = resolver.resolve(
            request,
            decision,
            context.permission_profile,
            ui_available=context.ui_available,
            budget=context.budget,
            budget_state=context.budget_state,
        )
        return _ResolvedPolicy(
            raw=resolution.raw_decision,
            resolved=resolution.resolved_decision,
            permission_profile=resolution.metadata,
        )
    if decision.effect == "confirm":
        return _ResolvedPolicy(
            raw=decision,
            resolved=decision.model_copy(
                update={
                    "effect": "block",
                    "reason": decision.reason or "confirmation denied in M5",
                    "audit_tags": [*decision.audit_tags, "confirm:denied"],
                }
            ),
        )
    return _ResolvedPolicy(raw=decision, resolved=decision)


async def _record_task_permission_decision(
    runtime: ToolRuntime,
    request: PolicyRequest | None,
    raw_decision: PolicyDecision | None,
    resolved_decision: PolicyDecision,
    permission_profile: dict[str, Any] | None,
    occurred_at: str,
) -> None:
    context = runtime.taskrun_permission_context
    if (
        context is None
        or context.record_permission_decision is None
        or request is None
        or raw_decision is None
    ):
        return
    profile_name = (
        str(permission_profile.get("name"))
        if isinstance(permission_profile, dict) and permission_profile.get("name")
        else str(context.permission_profile.get("name", "unknown"))
    )
    await maybe_await(
        context.record_permission_decision(
            task_run_id=context.task_run_id,
            step_id=context.step_id,
            tool_execution_id=context.tool_execution_id,
            policy_request=request.model_dump(by_alias=True, exclude_none=True),
            raw_decision=raw_decision.model_dump(by_alias=True, exclude_none=True),
            resolved_decision=resolved_decision.model_dump(by_alias=True, exclude_none=True),
            profile_name=profile_name,
            occurred_at=occurred_at,
        )
    )


async def _run_or_block_tool(
    definition: ToolDefinition,
    runtime: ToolRuntime,
    tool_call_id: str,
    args: dict[str, Any],
    run_id: str | None,
    decision: PolicyDecision,
    signal: AbortSignal | None,
    on_update: ToolUpdateCallback | None,
) -> AgentToolResult:
    if decision.effect != "allow":
        return _error_result(decision.reason or "tool execution blocked by policy", is_error=True)
    context = ToolExecutionContext(
        tool_call_id=tool_call_id,
        cwd=runtime.cwd,
        policy_decision=decision,
        runtime_session_id=runtime.runtime_session_id,
        run_id=run_id,
        skill_env_grant=(
            runtime.current_skill_env_grant
            if runtime.actor == "model" and definition.name == "bash"
            else None
        ),
    )
    effective_args = args if decision.normalized_args is None else decision.normalized_args
    result = await maybe_await(definition.execute(effective_args, context, signal, on_update))
    if not isinstance(result, AgentToolResult):
        return AgentToolResult.model_validate(result)
    return result


def _validate_args(definition: ToolDefinition, args: dict[str, Any]) -> str | None:
    try:
        validate_tool_arguments(_provider_tool(definition), args)
    except Exception as exc:
        return str(exc)
    return None


def _provider_tool(definition: ToolDefinition) -> Tool:
    return Tool(
        name=definition.name,
        description=definition.description,
        parameters=definition.parameters,
    )


def default_policy_decider(request: PolicyRequest) -> PolicyDecision:
    if request.tool_name == "bash":
        return decide_shell_access(request)
    if request.tool_name in {"edit", "write"}:
        return decide_path_access(request, mode="write")
    if request.tool_name in {"read", "grep", "find", "ls"}:
        return decide_path_access(request, mode="read")
    return PolicyDecision.block(f"unknown built-in tool: {request.tool_name}")


def _with_common_details(
    result: AgentToolResult,
    decision: PolicyDecision,
    raw_decision: PolicyDecision | None,
    permission_profile: dict[str, Any] | None,
    run_id: str | None,
    started: str,
    start_monotonic: float,
    *,
    exception: Exception | None = None,
) -> AgentToolResult:
    ended = _now_iso()
    duration = max(0, int((time.monotonic() - start_monotonic) * 1000))
    details = result.details if isinstance(result.details, dict) else {}
    details = {
        **details,
        "policyDecision": _decision_details(decision, run_id),
        "auditTags": list(decision.audit_tags),
        "durationMs": duration,
        "startedAt": started,
        "endedAt": ended,
    }
    if raw_decision is not None and permission_profile is not None:
        details["rawPolicyDecision"] = _decision_details(raw_decision, run_id)
        details["permissionProfile"] = dict(permission_profile)
    if exception is not None:
        details["exceptionClass"] = type(exception).__name__
        details["exceptionMessage"] = str(exception)
    return AgentToolResult(content=result.content, details=details, isError=bool(result.is_error))


async def _audit(
    runtime: ToolRuntime,
    definition: ToolDefinition,
    tool_call_id: str,
    args: dict[str, Any],
    decision: PolicyDecision,
    raw_decision: PolicyDecision | None,
    permission_profile: dict[str, Any] | None,
    result: AgentToolResult,
    run_id: str | None,
    started: str,
    start_monotonic: float,
    exception: Exception | None,
) -> None:
    details = result.details if isinstance(result.details, dict) else {}
    ended = details.get("endedAt") if isinstance(details.get("endedAt"), str) else _now_iso()
    duration = details.get("durationMs")
    try:
        audit_args, redaction_status = _audit_args(
            definition.name,
            args,
            runtime.cwd,
            skill_env_grant=(
                runtime.current_skill_env_grant
                if runtime.actor == "model" and definition.name == "bash"
                else None
            ),
        )
    except Exception:
        audit_args = {"redactionError": "audit argument redaction failed"}
        redaction_status = "failed"
    record = AuditRecord(
        runtimeSessionId=runtime.runtime_session_id,
        runId=run_id,
        actor=runtime.actor,
        toolName=definition.name,
        toolCallId=tool_call_id,
        args=audit_args,
        policyDecision=_audit_policy_decision(decision),
        rawPolicyDecision=(
            _audit_policy_decision(raw_decision).model_dump(by_alias=True, exclude_none=True)
            if raw_decision is not None and permission_profile is not None
            else None
        ),
        permissionProfile=dict(permission_profile) if permission_profile is not None else None,
        startedAt=started,
        endedAt=ended,
        durationMs=duration if isinstance(duration, int) else int((time.monotonic() - start_monotonic) * 1000),
        isError=bool(result.is_error),
        truncation=details.get("truncation"),
        affectedPaths=_affected_paths(details, decision),
        fullOutputPath=details.get("fullOutputPath") if isinstance(details.get("fullOutputPath"), str) else None,
        redactionTags=list(decision.redaction_tags),
        redactionStatus=redaction_status,
        exceptionClass=type(exception).__name__ if exception else details.get("exceptionClass"),
        exceptionMessage=str(exception) if exception else details.get("exceptionMessage"),
    )
    await maybe_await(runtime.audit_sink.record(record))


def _error_result(message: str, *, is_error: bool) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": message}], details={}, isError=is_error)


def _decision_details(decision: PolicyDecision, run_id: str | None) -> dict[str, Any]:
    details = decision.model_dump(
        by_alias=True,
        exclude_none=True,
        exclude={"normalized_args"},
    )
    if run_id is not None:
        details["runId"] = run_id
    return details


def _audit_policy_decision(decision: PolicyDecision) -> PolicyDecision:
    return decision.model_copy(update={"normalized_args": None})


def _affected_paths(details: dict[str, Any], decision: PolicyDecision) -> list[str]:
    paths = []
    for value in decision.resolved_paths.values():
        if value not in paths:
            paths.append(value)
    path = details.get("resolvedPath")
    if isinstance(path, str) and path not in paths:
        paths.append(path)
    return paths


def _audit_args(
    tool_name: str,
    args: dict[str, Any],
    cwd: str,
    *,
    skill_env_grant: SkillEnvGrant | None = None,
) -> tuple[dict[str, Any], str]:
    if tool_name == "bash":
        return _bash_audit_args(args, cwd, skill_env_grant=skill_env_grant)
    if tool_name == "read":
        return _read_audit_args(args), "not_required"
    if tool_name == "grep":
        return _grep_audit_args(args), "applied" if isinstance(args.get("pattern"), str) else "not_required"
    if tool_name == "find":
        return _find_audit_args(args), "applied" if isinstance(args.get("pattern"), str) else "not_required"
    if tool_name == "ls":
        return _ls_audit_args(args), "not_required"
    if tool_name == "write":
        return _write_audit_args(args), "applied"
    if tool_name == "edit":
        return _edit_audit_args(args), "applied"
    redacted, applied = _redact_secrets(args)
    return _ensure_dict(redacted), "applied" if applied else "not_required"


def _bash_audit_args(
    args: dict[str, Any],
    cwd: str,
    *,
    skill_env_grant: SkillEnvGrant | None = None,
) -> tuple[dict[str, Any], str]:
    command = args.get("command")
    command_text = command if isinstance(command, str) else ""
    preview, applied = _redacted_command_preview(command_text)
    if skill_env_grant is not None:
        preview, literal_applied = _redact_literal_values(preview, skill_env_grant.values)
        applied = applied or literal_applied
    result: dict[str, Any] = {
        "commandPreview": preview,
        "commandLength": len(command_text),
        "cwd": cwd,
    }
    if "timeout" in args:
        result["timeout"] = args["timeout"]
    if skill_env_grant is not None:
        result["skillEnvNames"] = list(skill_env_grant.names)
        if skill_env_grant.source is not None:
            result["skillEnvSource"] = skill_env_grant.source
    return result, "applied" if applied else "not_required"


def _read_audit_args(args: dict[str, Any]) -> dict[str, Any]:
    return _select_args(args, "path", "offset", "limit")


def _grep_audit_args(args: dict[str, Any]) -> dict[str, Any]:
    result = _select_args(args, "path", "glob", "ignoreCase", "literal", "context", "limit")
    if isinstance(args.get("pattern"), str):
        result["patternLength"] = len(args["pattern"])
    return result


def _find_audit_args(args: dict[str, Any]) -> dict[str, Any]:
    result = _select_args(args, "path", "limit")
    if isinstance(args.get("pattern"), str):
        result["patternLength"] = len(args["pattern"])
    return result


def _ls_audit_args(args: dict[str, Any]) -> dict[str, Any]:
    return _select_args(args, "path", "limit")


def _write_audit_args(args: dict[str, Any]) -> dict[str, Any]:
    result = _select_args(args, "path")
    content = args.get("content")
    if isinstance(content, str):
        result["contentBytes"] = len(content.encode("utf-8"))
    return result


def _edit_audit_args(args: dict[str, Any]) -> dict[str, Any]:
    result = _select_args(args, "path")
    edits = args.get("edits")
    if isinstance(edits, list):
        result["editsCount"] = len(edits)
        result["oldTextBytes"] = sum(
            len(item.get("oldText", "").encode("utf-8"))
            for item in edits
            if isinstance(item, dict) and isinstance(item.get("oldText"), str)
        )
        result["newTextBytes"] = sum(
            len(item.get("newText", "").encode("utf-8"))
            for item in edits
            if isinstance(item, dict) and isinstance(item.get("newText"), str)
        )
    return result


def _select_args(args: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: args[key] for key in keys if key in args}


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"value": value}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "PolicyDecider",
    "TaskPermissionDecisionRecorder",
    "TaskRunPermissionContext",
    "ToolRuntime",
    "default_policy_decider",
    "wrap_tool_definition",
]
