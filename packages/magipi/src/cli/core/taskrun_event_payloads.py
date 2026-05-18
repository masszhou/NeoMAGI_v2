"""D10 TaskRun semantic event taxonomy and payload builders.

This module owns the closed taxonomy of *derived* TaskRun events that the
white-box runtime layer (`TaskRunAgentSession`, the `before_tool_call` hook,
the evidence classifier, and the compaction event emitter) writes to
``task_events``. The agent_core protocol surface remains untouched
(ADR-0023): all white-box semantics live in the TaskRun derived layer.

Two-tier model (binding for amendment D10):

* Tier 1 — ``task_events`` truth: every derived event is written here.
* Tier 2 — ``KEY_HISTORY_EVENT_TYPES``: a curated subset for the
  ``taskrun history`` summary view. Only step-summary events graduate.

Tool-detail (``task_tool_*``) and runtime-level (``task_runtime_*``)
events are Tier 1 only by design — a step with 50 tool calls otherwise
floods the history view with one row per call.

Each payload carries ``payload_version`` (per event_type) so families
evolve independently without a global schema bump.
"""

from __future__ import annotations

from typing import Any, Final


TASK_TOOL_OBSERVED: Final[str] = "task_tool_observed"
TASK_TOOL_POLICY_RESOLVED: Final[str] = "task_tool_policy_resolved"
TASK_TOOL_POLICY_BLOCKED: Final[str] = "task_tool_policy_blocked"
TASK_RUNTIME_COMPACTION_OBSERVED: Final[str] = "task_runtime_compaction_observed"
TASK_RUNTIME_AUTO_RETRY_OBSERVED: Final[str] = "task_runtime_auto_retry_observed"
TASK_STEP_EVIDENCE_RECORDED: Final[str] = "task_step_evidence_recorded"
TASK_STEP_EVIDENCE_MISSING: Final[str] = "task_step_evidence_missing"
TASK_STEP_BLOCKER_DETECTED: Final[str] = "task_step_blocker_detected"
TASK_STEP_OUTCOME_SUPPORTED: Final[str] = "task_step_outcome_supported"
TASK_STEP_OUTCOME_UNSUPPORTED: Final[str] = "task_step_outcome_unsupported"
TASK_STEP_RESUME_CONTEXT_GENERATED: Final[str] = "task_step_resume_context_generated"


# Step-summary events graduate to KEY_HISTORY (Tier 2). Tool-detail and
# runtime-level events stay Tier 1 only.
DERIVED_STEP_SUMMARY_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        TASK_STEP_EVIDENCE_RECORDED,
        TASK_STEP_EVIDENCE_MISSING,
        TASK_STEP_BLOCKER_DETECTED,
        TASK_STEP_OUTCOME_SUPPORTED,
        TASK_STEP_OUTCOME_UNSUPPORTED,
        TASK_STEP_RESUME_CONTEXT_GENERATED,
    }
)
DERIVED_TOOL_DETAIL_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        TASK_TOOL_OBSERVED,
        TASK_TOOL_POLICY_RESOLVED,
        TASK_TOOL_POLICY_BLOCKED,
    }
)
DERIVED_RUNTIME_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        TASK_RUNTIME_COMPACTION_OBSERVED,
        TASK_RUNTIME_AUTO_RETRY_OBSERVED,
    }
)
DERIVED_TIER1_ONLY_EVENT_TYPES: Final[frozenset[str]] = (
    DERIVED_TOOL_DETAIL_EVENT_TYPES | DERIVED_RUNTIME_EVENT_TYPES
)
DERIVED_EVENT_TYPES: Final[frozenset[str]] = (
    DERIVED_STEP_SUMMARY_EVENT_TYPES | DERIVED_TIER1_ONLY_EVENT_TYPES
)

# Per-event-type payload version. Bump locally when the schema for one event
# type evolves; do NOT bump a single global schema_version (D10 design).
PAYLOAD_VERSIONS: Final[dict[str, int]] = {
    TASK_TOOL_OBSERVED: 1,
    TASK_TOOL_POLICY_RESOLVED: 1,
    TASK_TOOL_POLICY_BLOCKED: 1,
    TASK_RUNTIME_COMPACTION_OBSERVED: 1,
    TASK_RUNTIME_AUTO_RETRY_OBSERVED: 1,
    TASK_STEP_EVIDENCE_RECORDED: 1,
    TASK_STEP_EVIDENCE_MISSING: 1,
    TASK_STEP_BLOCKER_DETECTED: 1,
    TASK_STEP_OUTCOME_SUPPORTED: 1,
    TASK_STEP_OUTCOME_UNSUPPORTED: 1,
    TASK_STEP_RESUME_CONTEXT_GENERATED: 1,
}


def derived_payload(event_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Stamp ``payload_version`` per event_type onto a derived event payload.

    Raises ``KeyError`` for unknown derived event types so accidental typos
    in writer call sites fail loudly rather than producing version-less rows.
    """

    version = PAYLOAD_VERSIONS[event_type]
    payload = {"payload_version": version}
    payload.update(fields)
    return payload


def build_tool_observed_payload(
    *,
    tool_call_id: str,
    tool_name: str,
    is_error: bool,
    evidence_kind: str,
    tool_execution_id: str | None = None,
    command_summary: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "is_error": is_error,
        "evidence_kind": evidence_kind,
    }
    if tool_execution_id is not None:
        payload["tool_execution_id"] = tool_execution_id
    if command_summary is not None:
        payload["command_summary"] = command_summary
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    return derived_payload(TASK_TOOL_OBSERVED, payload)


def build_tool_policy_resolved_payload(
    *,
    tool_call_id: str,
    permission_profile_name: str,
    effect: str,
    reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "permission_profile_name": permission_profile_name,
        "effect": effect,
    }
    if reason is not None:
        payload["reason"] = reason
    return derived_payload(TASK_TOOL_POLICY_RESOLVED, payload)


def build_tool_policy_blocked_payload(
    *,
    tool_call_id: str,
    permission_profile_name: str,
    effect: str,
    reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "permission_profile_name": permission_profile_name,
        "effect": effect,
    }
    if reason is not None:
        payload["reason"] = reason
    return derived_payload(TASK_TOOL_POLICY_BLOCKED, payload)


def build_step_evidence_recorded_payload(
    *,
    evidence_kind: str,
    source_tool_call_ids: list[str],
    claim_summary: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "evidence_kind": evidence_kind,
        "source_tool_call_ids": list(source_tool_call_ids),
    }
    if claim_summary is not None:
        payload["claim_summary"] = claim_summary
    return derived_payload(TASK_STEP_EVIDENCE_RECORDED, payload)


def build_step_evidence_missing_payload(
    *,
    evidence_kind: str,
    source_tool_call_ids: list[str] | None = None,
    claim_summary: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "evidence_kind": evidence_kind,
        "source_tool_call_ids": list(source_tool_call_ids or []),
    }
    if claim_summary is not None:
        payload["claim_summary"] = claim_summary
    return derived_payload(TASK_STEP_EVIDENCE_MISSING, payload)


def build_step_blocker_detected_payload(
    *,
    reason: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"reason": reason}
    if detail is not None:
        payload["detail"] = dict(detail)
    return derived_payload(TASK_STEP_BLOCKER_DETECTED, payload)


def build_step_outcome_supported_payload(
    *,
    verification_state: str,
    claim_summary: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"verification_state": verification_state}
    if claim_summary is not None:
        payload["claim_summary"] = claim_summary
    return derived_payload(TASK_STEP_OUTCOME_SUPPORTED, payload)


def build_step_outcome_unsupported_payload(
    *,
    verification_state: str,
    claim_summary: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"verification_state": verification_state}
    if claim_summary is not None:
        payload["claim_summary"] = claim_summary
    if reason is not None:
        payload["reason"] = reason
    return derived_payload(TASK_STEP_OUTCOME_UNSUPPORTED, payload)


def build_step_resume_context_generated_payload(
    *,
    next_action: str | None = None,
    context_summary: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if next_action is not None:
        payload["next_action"] = next_action
    if context_summary is not None:
        payload["context_summary"] = context_summary
    return derived_payload(TASK_STEP_RESUME_CONTEXT_GENERATED, payload)


def build_runtime_compaction_observed_payload(
    *,
    reason: str,
    outcome: str,
    will_retry: bool,
    error_message: str | None = None,
    tokens_before: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reason": reason,
        "outcome": outcome,
        "will_retry": will_retry,
    }
    if error_message is not None:
        payload["error_message"] = error_message
    if tokens_before is not None:
        payload["tokens_before"] = tokens_before
    return derived_payload(TASK_RUNTIME_COMPACTION_OBSERVED, payload)


def build_runtime_auto_retry_observed_payload(
    *,
    attempt: int,
    max_attempts: int,
    success: bool,
    error_message: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "attempt": attempt,
        "max_attempts": max_attempts,
        "success": success,
    }
    if error_message is not None:
        payload["error_message"] = error_message
    return derived_payload(TASK_RUNTIME_AUTO_RETRY_OBSERVED, payload)


__all__ = [
    "DERIVED_EVENT_TYPES",
    "DERIVED_RUNTIME_EVENT_TYPES",
    "DERIVED_STEP_SUMMARY_EVENT_TYPES",
    "DERIVED_TIER1_ONLY_EVENT_TYPES",
    "DERIVED_TOOL_DETAIL_EVENT_TYPES",
    "PAYLOAD_VERSIONS",
    "TASK_RUNTIME_AUTO_RETRY_OBSERVED",
    "TASK_RUNTIME_COMPACTION_OBSERVED",
    "TASK_STEP_BLOCKER_DETECTED",
    "TASK_STEP_EVIDENCE_MISSING",
    "TASK_STEP_EVIDENCE_RECORDED",
    "TASK_STEP_OUTCOME_SUPPORTED",
    "TASK_STEP_OUTCOME_UNSUPPORTED",
    "TASK_STEP_RESUME_CONTEXT_GENERATED",
    "TASK_TOOL_OBSERVED",
    "TASK_TOOL_POLICY_BLOCKED",
    "TASK_TOOL_POLICY_RESOLVED",
    "build_runtime_auto_retry_observed_payload",
    "build_runtime_compaction_observed_payload",
    "build_step_blocker_detected_payload",
    "build_step_evidence_missing_payload",
    "build_step_evidence_recorded_payload",
    "build_step_outcome_supported_payload",
    "build_step_outcome_unsupported_payload",
    "build_step_resume_context_generated_payload",
    "build_tool_observed_payload",
    "build_tool_policy_blocked_payload",
    "build_tool_policy_resolved_payload",
    "derived_payload",
]
