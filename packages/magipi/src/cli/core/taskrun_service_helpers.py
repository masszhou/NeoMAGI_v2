"""Small pure helpers for TaskRun service lifecycle code."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from cli.core.taskrun_errors import TaskRunServiceError
from cli.core.taskrun_host_contract import (
    TaskRunHostContext,
    event_payload_with_host_context,
)
from cli.core.taskrun_step import STEP_INSTRUCTION, TaskRunRuntimeOptions, TaskRunStepOutcome
from cli.core.taskrun_views import next_action_for_status, preview_text
from policy.permission_profiles import (
    PermissionProfileError,
    normalize_permission_profile_snapshot,
)
from storage.taskrun_repository import TaskRunRecord


def workspace_root(cwd: str | Path) -> str:
    return str(Path(cwd).resolve())


def datetime_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def validate_headless_profile(
    permission_profile: Mapping[str, Any],
    *,
    command: str,
) -> None:
    profile = normalize_profile(permission_profile)
    if not bool(profile.get("nonInteractive")):
        raise TaskRunServiceError(
            f"taskrun {command} is headless and cannot use interactive permission profile; "
            "create a TaskRun with --permission guarded or --permission full"
        )


def step_input(
    record: TaskRunRecord,
    summary: Mapping[str, object],
    runtime_options: TaskRunRuntimeOptions,
) -> dict[str, object]:
    return {
        "goal": record.goal,
        "summary": dict(summary),
        "instruction": STEP_INSTRUCTION,
        "permission_profile": dict(record.permission_profile or {}),
        "model": runtime_options.model_ref,
        "thinking_level": runtime_options.thinking_level,
        "cache_retention": runtime_options.cache_retention,
    }


def step_started_payload(
    previous_status: str,
    runtime_options: TaskRunRuntimeOptions,
    *,
    host_context: TaskRunHostContext | Mapping[str, object] | None = None,
) -> dict[str, object]:
    return event_payload_with_host_context(
        {"status_from": previous_status, "model_ref": runtime_options.model_ref},
        host_context,
    )


def step_output(
    outcome: TaskRunStepOutcome,
    task_run_id: str,
    status: str,
) -> dict[str, object]:
    reason = (
        outcome.block_reason
        if status == "blocked"
        else outcome.error_message if status in {"failed", "cancelled"} else None
    )
    next_action = outcome.next_action or next_action_for_status(task_run_id, status, reason)
    output: dict[str, object] = {
        "status": status,
        "assistant_text_preview": preview_text(outcome.assistant_text),
        "tool_count": outcome.tool_count,
        "permission_decision_count": outcome.permission_decision_count,
        "next_action": next_action,
    }
    _add_optional_step_output(output, outcome, reason)
    return output


def step_conclusion(outcome: TaskRunStepOutcome, status: str) -> str:
    if status == "done":
        return preview_text(outcome.assistant_text) or "manual step completed"
    if status == "blocked":
        return preview_text(outcome.block_reason) or "manual step blocked"
    if status == "cancelled":
        return preview_text(outcome.error_message) or "manual step cancelled"
    return preview_text(outcome.error_message) or "manual step failed"


def normalize_step_outcome_status(status: str) -> str:
    if status not in {"done", "failed", "blocked", "cancelled"}:
        raise TaskRunServiceError(f"invalid step outcome status: {status}")
    return status


def normalize_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return normalize_permission_profile_snapshot(profile)
    except PermissionProfileError as exc:
        raise TaskRunServiceError(str(exc)) from exc


def cancelled_outcome(task_run_id: str) -> TaskRunStepOutcome:
    return TaskRunStepOutcome(
        status="cancelled",
        error_message="cancelled by user interrupt",
        next_action=(
            "Resolve the cancellation context, then run "
            f"`magipi taskrun step {task_run_id[:8]}` to continue."
        ),
    )


def cancel_requested_outcome(task_run_id: str) -> TaskRunStepOutcome:
    return TaskRunStepOutcome(
        status="cancelled",
        error_message="cancelled by TaskRun cancel request",
        next_action=(
            "Review the cancellation, then run "
            f"`magipi taskrun step {task_run_id[:8]}` to continue manually."
        ),
    )


def failed_outcome(task_run_id: str, exc: Exception) -> TaskRunStepOutcome:
    return TaskRunStepOutcome(
        status="failed",
        error_message=str(exc),
        next_action=(
            "Inspect the failure, then run "
            f"`magipi taskrun step {task_run_id[:8]}` to retry manually."
        ),
    )


def ambiguous_message(prefix: str, matches: list[TaskRunRecord]) -> str:
    details = [
        f"{record.id} {record.status} {goal_preview(record.goal)}"
        for record in matches
    ]
    return prefix + "; pass an id. Candidates: " + "; ".join(details)


def goal_preview(goal: str, limit: int = 64) -> str:
    collapsed = " ".join(goal.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def is_task_run_id_prefix(value: str) -> bool:
    if len(value) < 8:
        return False
    return all(char in "0123456789abcdefABCDEF-" for char in value)


def _add_optional_step_output(
    output: dict[str, object],
    outcome: TaskRunStepOutcome,
    reason: str | None,
) -> None:
    if outcome.run_id:
        output["run_id"] = outcome.run_id
    if reason:
        output["reason"] = reason
    if outcome.error_message:
        output["error_message"] = outcome.error_message
    if outcome.block_reason:
        output["block_reason"] = outcome.block_reason
    if outcome.finalize_errors:
        output["finalize_errors"] = list(outcome.finalize_errors)
    verification = _verification_block(outcome)
    if verification is not None:
        output["verification_state"] = verification


def _verification_block(outcome: TaskRunStepOutcome) -> dict[str, object] | None:
    if outcome.verification_state is None:
        return None
    verification: dict[str, object] = {"state": outcome.verification_state}
    if outcome.verification_reason:
        verification["reason"] = outcome.verification_reason
    if outcome.verification_missing_kinds:
        verification["missing_kinds"] = list(outcome.verification_missing_kinds)
    if outcome.verification_inconsistent_kinds:
        verification["inconsistent_kinds"] = list(outcome.verification_inconsistent_kinds)
    return verification


__all__ = [
    "ambiguous_message",
    "cancel_requested_outcome",
    "cancelled_outcome",
    "datetime_iso",
    "failed_outcome",
    "goal_preview",
    "is_task_run_id_prefix",
    "normalize_profile",
    "normalize_step_outcome_status",
    "parse_datetime",
    "step_conclusion",
    "step_input",
    "step_output",
    "step_started_payload",
    "validate_headless_profile",
    "workspace_root",
]
