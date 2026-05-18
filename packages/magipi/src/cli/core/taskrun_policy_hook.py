"""D11 ``before_tool_call`` hook + back-fill event hook factories.

These factories wire the TaskRun permission resolver into the
``before_tool_call`` boundary of ``agent_core`` so that policy is decided
*before* the tool body runs (R6). The resolved decision is staged in a
``PolicyResolutionStore`` for the wrapper to consume; the wrapper then
skips its legacy ``_resolve_policy_decision`` evaluation. Derived
``task_tool_policy_resolved`` / ``task_tool_policy_blocked`` events are
written here so the step view can answer "did the resolver allow this
call" without depending on ``result.details.policyDecision``.

The back-fill event hook is invoked by ``TaskRunAgentSession`` whenever
the listener consumer processes a ``tool_execution_start`` event. At that
point the ``agent_tool_executions`` row exists (the synchronous listener
already wrote it), so we look up its id and back-fill the hook-written
``task_permission_decisions`` row. The back-fill is fail-closed: any
exception or non-1 affected-row count is propagated to the session's
error sink and the step finalizes as ``failed``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from agent_core.runtime_types import AbortSignal, BeforeToolCallHook
from agent_core.types import BeforeToolCallContext, BeforeToolCallResult
from cli.core.evidence_classifier import (
    EvidenceObservation,
    classify_tool_evidence,
    summarize_command,
)
from cli.core.taskrun_event_payloads import (
    TASK_TOOL_OBSERVED,
    TASK_TOOL_POLICY_BLOCKED,
    TASK_TOOL_POLICY_RESOLVED,
    build_tool_observed_payload,
    build_tool_policy_blocked_payload,
    build_tool_policy_resolved_payload,
)
from cli.tools.policy_resolution_store import PolicyResolutionStore
from cli.tools.wrapper import default_policy_decider
from policy.permission_profiles import (
    PermissionBudgetState,
    PermissionProfileResolver,
)
from policy.types import PolicyDecision, PolicyRequest
from storage.taskrun_repository import TaskRunRepository


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _profile_name(metadata: Mapping[str, Any] | None, fallback: Mapping[str, Any]) -> str:
    if isinstance(metadata, Mapping) and metadata.get("name"):
        return str(metadata["name"])
    return str(fallback.get("name", "unknown"))


def build_before_tool_call_hook(
    *,
    task_repository: TaskRunRepository,
    task_run_id: str,
    step_id: str,
    agent_session_id: str,
    permission_profile: Mapping[str, Any],
    budget: Mapping[str, Any] | None,
    budget_state: PermissionBudgetState,
    policy_resolution_store: PolicyResolutionStore,
    cwd: str,
    runtime_session_id: str,
    run_id_provider: Callable[[], str | None],
) -> BeforeToolCallHook:
    resolver = PermissionProfileResolver()

    async def hook(
        context: BeforeToolCallContext,
        _signal: AbortSignal | None,
    ) -> BeforeToolCallResult | None:
        tool_call = context.tool_call if isinstance(context.tool_call, Mapping) else {}
        tool_call_id = str(tool_call.get("id") or tool_call.get("toolCallId") or "")
        tool_name = str(tool_call.get("name") or tool_call.get("toolName") or "")
        args = context.args if isinstance(context.args, dict) else {}
        request = PolicyRequest(
            runtimeSessionId=runtime_session_id,
            runId=run_id_provider(),
            toolName=tool_name,
            args=args,
            cwd=cwd,
            actor="model",
            source={
                "tool_call_id": tool_call_id,
                "input_origin": "model",
                "actor_role": "model",
            },
        )
        raw_decision = default_policy_decider(request)
        if not isinstance(raw_decision, PolicyDecision):
            raw_decision = PolicyDecision.model_validate(raw_decision)
        resolution = resolver.resolve(
            request,
            raw_decision,
            permission_profile,
            ui_available=False,
            budget=budget,
            budget_state=budget_state,
        )
        occurred_at = _utc_now_iso()
        profile_name = _profile_name(resolution.metadata, permission_profile)
        task_repository.append_permission_decision(
            task_run_id=task_run_id,
            step_id=step_id,
            tool_execution_id=None,
            policy_request=request.model_dump(by_alias=True, exclude_none=True),
            raw_decision=resolution.raw_decision.model_dump(by_alias=True, exclude_none=True),
            resolved_decision=resolution.resolved_decision.model_dump(by_alias=True, exclude_none=True),
            profile_name=profile_name,
            occurred_at=occurred_at,
        )
        effect = resolution.resolved_decision.effect
        reason = resolution.resolved_decision.reason
        if effect == "block":
            block_text = reason or "tool execution blocked by policy"
            # Side-channel the block reason so the session consumer can
            # surface it on the matching ``tool_execution_end`` event —
            # the agent_core error result for a hook block carries no
            # ``policyDecision`` details (per ADR-0023 we don't extend
            # the protocol surface to add them), so the collector relies
            # on the store rather than ``result.details``. We deliberately
            # do NOT call ``store.put`` on the block branch: the wrapper
            # is short-circuited by ``_ImmediateToolCallOutcome`` and
            # never reaches ``store.consume``, so a put() here would
            # leak a stale entry that violates the store's
            # read-and-remove contract.
            policy_resolution_store.record_block(tool_call_id, block_text)
            task_repository.append_event(
                task_run_id=task_run_id,
                step_id=step_id,
                event_type=TASK_TOOL_POLICY_BLOCKED,
                payload=build_tool_policy_blocked_payload(
                    tool_call_id=tool_call_id,
                    permission_profile_name=profile_name,
                    effect=effect,
                    reason=reason,
                ),
                occurred_at=occurred_at,
            )
            return BeforeToolCallResult(block=True, reason=block_text)
        policy_resolution_store.put(
            tool_call_id,
            raw=resolution.raw_decision,
            resolved=resolution.resolved_decision,
            permission_profile=resolution.metadata,
        )
        task_repository.append_event(
            task_run_id=task_run_id,
            step_id=step_id,
            event_type=TASK_TOOL_POLICY_RESOLVED,
            payload=build_tool_policy_resolved_payload(
                tool_call_id=tool_call_id,
                permission_profile_name=profile_name,
                effect=effect,
                reason=reason,
            ),
            occurred_at=occurred_at,
        )
        return None

    return hook


def build_back_fill_event_hook(
    *,
    task_repository: TaskRunRepository,
    task_run_id: str,
    step_id: str,
    agent_session_id: str,
    remember_tool_execution_id: Callable[[str, str], None] | None = None,
) -> Callable[[Any], None]:
    """Return an event_hook to install on ``TaskRunAgentSession``.

    On ``tool_execution_start`` it looks up ``agent_tool_executions.id``
    via the existing session-scoped finder and stamps it onto the
    hook-written ``task_permission_decisions`` row. Exactly 1 row must be
    affected (the spec invariant); 0 indicates the hook did not write,
    >1 indicates a schema breakage. Either failure raises so the session
    consumer records a fail-closed error and the step finalizes as
    ``failed``.

    ``remember_tool_execution_id`` is an optional callable invoked once
    back-fill succeeds — used by the D12 classifier (W5) to surface
    ``tool_execution_id`` on the step-scope state map. Passing ``None`` is
    fine when only back-fill is needed (W4).
    """

    def event_hook(event: Any) -> None:
        if getattr(event, "type", "") != "tool_execution_start":
            return
        tool_call_id = getattr(event, "tool_call_id", None)
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise RuntimeError("tool_execution_start without tool_call_id")
        tool_execution_id = task_repository.find_tool_execution_id(
            session_id=agent_session_id,
            tool_call_id=tool_call_id,
        )
        if tool_execution_id is None:
            raise RuntimeError(
                f"could not back-fill task permission decision: "
                f"agent_tool_executions row missing for tool_call_id={tool_call_id}"
            )
        affected = task_repository.backfill_permission_decision_tool_execution_id(
            task_run_id=task_run_id,
            step_id=step_id,
            tool_call_id=tool_call_id,
            tool_execution_id=tool_execution_id,
        )
        if affected != 1:
            raise RuntimeError(
                f"task_permission_decisions back-fill affected {affected} rows "
                f"(expected 1) for tool_call_id={tool_call_id}"
            )
        if remember_tool_execution_id is not None:
            remember_tool_execution_id(tool_call_id, tool_execution_id)

    return event_hook


def _duration_from_result(result: Any) -> int | None:
    if isinstance(result, Mapping):
        details = result.get("details") or {}
    else:
        details = getattr(result, "details", None) or {}
    if not isinstance(details, Mapping):
        return None
    value = details.get("durationMs")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def build_evidence_event_hook(
    *,
    task_repository: TaskRunRepository,
    task_run_id: str,
    step_id: str,
    tool_call_state_lookup: Callable[[str], Any],
    record_observation: Callable[[EvidenceObservation], None],
) -> Callable[[Any], None]:
    """Return an event_hook that, on each ``tool_execution_end``,
    classifies the call into an ``evidence_kind`` and writes the D10
    ``task_tool_observed`` derived event.

    The session's ``tool_call_state`` map is the source of truth for
    ``tool_name`` and ``args`` (the end event does not carry args). A
    missing entry indicates a lost ``tool_execution_start`` — we raise so
    the session marks the step ``failed`` rather than silently defaulting
    to ``generic`` evidence.
    """

    def hook(event: Any) -> None:
        if getattr(event, "type", "") != "tool_execution_end":
            return
        tool_call_id = getattr(event, "tool_call_id", None)
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise RuntimeError("tool_execution_end without tool_call_id")
        state = tool_call_state_lookup(tool_call_id)
        if state is None:
            raise RuntimeError(
                "tool_call state missing on tool_execution_end "
                f"for tool_call_id={tool_call_id}; lost start event"
            )
        is_error = bool(getattr(event, "is_error", False))
        evidence_kind = classify_tool_evidence(state.tool_name, state.args, is_error)
        command_summary = summarize_command(state.args)
        duration_ms = _duration_from_result(getattr(event, "result", None))
        tool_execution_id = state.tool_execution_id
        observation = EvidenceObservation(
            tool_call_id=tool_call_id,
            tool_name=state.tool_name,
            is_error=is_error,
            evidence_kind=evidence_kind,
            command_summary=command_summary,
            duration_ms=duration_ms,
        )
        record_observation(observation)
        task_repository.append_event(
            task_run_id=task_run_id,
            step_id=step_id,
            event_type=TASK_TOOL_OBSERVED,
            payload=build_tool_observed_payload(
                tool_call_id=tool_call_id,
                tool_name=state.tool_name,
                is_error=is_error,
                evidence_kind=evidence_kind,
                tool_execution_id=tool_execution_id,
                command_summary=command_summary,
                duration_ms=duration_ms,
            ),
        )

    return hook


def chain_event_hooks(*hooks: Callable[[Any], None]) -> Callable[[Any], None]:
    """Compose multiple session event_hooks into one. Each hook fires in
    the order provided; any exception propagates so the session's
    fail-closed error sink captures it."""

    def chained(event: Any) -> None:
        for hook in hooks:
            hook(event)

    return chained


__all__ = [
    "build_back_fill_event_hook",
    "build_before_tool_call_hook",
    "build_evidence_event_hook",
    "chain_event_hooks",
]
