"""Read-model DTOs and helpers for TaskRun status views."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from cli.core.taskrun_event_payloads import DERIVED_STEP_SUMMARY_EVENT_TYPES
from storage.taskrun_repository import (
    TERMINAL_TASKRUN_STATUSES,
    TaskEventRecord,
    TaskExperimentRecord,
    TaskPermissionDecisionRecord,
    TaskRunRecord,
    TaskStepRecord,
)


KEY_HISTORY_EVENT_TYPES = frozenset(
    {
        "task_run_started",
        "task_run_blocked_stale",
        "task_run_closed",
        "task_run_cancel_requested",
        "task_run_cancelled",
        "task_run_permission_profile_updated",
        "task_run_auto_run_started",
        "task_run_auto_run_iteration_finished",
        "task_run_auto_run_stopped",
        "task_run_auto_run_cancelled",
        "task_experiment_baseline_recorded",
        "task_experiment_trial_recorded",
        "task_experiment_decided",
        "task_experiment_reverted",
        "task_experiment_blocked",
        "task_step_started",
        "task_step_completed",
        "task_step_failed",
        "task_step_blocked",
        "task_step_cancelled",
    }
    | DERIVED_STEP_SUMMARY_EVENT_TYPES
)
STEP_REASON_EVENT_TYPES = frozenset(
    {
        "task_run_blocked_stale",
        "task_step_completed",
        "task_step_failed",
        "task_step_blocked",
        "task_step_cancelled",
    }
)


@dataclass(frozen=True, slots=True)
class TaskStepCounts:
    tool_count: int
    permission_decision_count: int


@dataclass(frozen=True, slots=True)
class TaskRunListItem:
    task_run: TaskRunRecord
    current_step: Mapping[str, object] | str | None
    permission_profile_name: str
    next_action: str


@dataclass(frozen=True, slots=True)
class TaskRunListResult:
    items: list[TaskRunListItem]
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class TaskRunHistoryStep:
    step: TaskStepRecord
    reason: str | None
    counts: TaskStepCounts
    experiments: list[TaskExperimentRecord] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TaskRunHistoryResult:
    task_run: TaskRunRecord
    steps: list[TaskRunHistoryStep]
    key_events: list[TaskEventRecord]
    next_action: str
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class TaskRunNextResult:
    task_run: TaskRunRecord
    pending_step: TaskStepRecord | None
    current_step: TaskStepRecord | None
    last_attempt: TaskStepRecord | None
    next_action: str
    blocked_or_failed_reason: str | None
    permission_profile: dict[str, Any]
    summary_snapshot: dict[str, object]
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class TaskRunEventsResult:
    task_run: TaskRunRecord
    events: list[TaskEventRecord]
    exit_code: int = 0


def build_taskrun_list(records: list[TaskRunRecord]) -> TaskRunListResult:
    return TaskRunListResult(
        items=[
            TaskRunListItem(
                task_run=record,
                current_step=_summary_current_step(record),
                permission_profile_name=_profile_name(record.permission_profile),
                next_action=_summary_next_action(record),
            )
            for record in records
            if record.status != "archived"
        ]
    )


def build_taskrun_history(
    record: TaskRunRecord,
    steps: list[TaskStepRecord],
    events: list[TaskEventRecord],
    permission_decisions: list[TaskPermissionDecisionRecord],
    experiments: list[TaskExperimentRecord],
    summary: Mapping[str, object],
) -> TaskRunHistoryResult:
    return TaskRunHistoryResult(
        task_run=record,
        steps=[
            TaskRunHistoryStep(
                step=step,
                reason=step_reason(step, _related_step_events(step, events)),
                counts=step_counts(step, permission_decisions),
                experiments=[
                    experiment
                    for experiment in experiments
                    if experiment.step_id == step.id
                ],
            )
            for step in steps
        ],
        key_events=[event for event in events if event.event_type in KEY_HISTORY_EVENT_TYPES],
        next_action=str(summary.get("next_action") or ""),
    )


def build_taskrun_next(
    record: TaskRunRecord,
    steps: list[TaskStepRecord],
    events: list[TaskEventRecord],
    summary: dict[str, object],
    default_permission_profile: Mapping[str, Any],
) -> TaskRunNextResult:
    pending_step = next((step for step in steps if step.status == "pending"), None)
    current_step = next((step for step in steps if step.id == record.current_step_id), None)
    attempted = [
        step
        for step in steps
        if step.status in {"done", "failed", "blocked", "cancelled"}
    ]
    last_attempt = attempted[-1] if attempted else None
    reason_step = _reason_source_step(record, current_step, last_attempt)
    return TaskRunNextResult(
        task_run=record,
        pending_step=pending_step,
        current_step=current_step,
        last_attempt=last_attempt,
        next_action=str(summary.get("next_action") or ""),
        blocked_or_failed_reason=(
            step_reason(reason_step, _related_step_events(reason_step, events))
            if reason_step is not None
            else None
        ),
        permission_profile=dict(record.permission_profile or default_permission_profile),
        summary_snapshot=summary,
    )


def step_summary(step: TaskStepRecord | None) -> dict[str, object] | None:
    if step is None:
        return None
    summary = {
        "id": step.id,
        "step_index": step.step_index,
        "title": step.title,
        "status": step.status,
        "conclusion": step.conclusion,
        "started_at": step.started_at,
        "ended_at": step.ended_at,
    }
    if step.output.get("next_action"):
        summary["next_action"] = step.output["next_action"]
    if step.output.get("reason"):
        summary["reason"] = step.output["reason"]
    return summary


def step_reason(
    step: TaskStepRecord,
    related_events: list[TaskEventRecord],
) -> str | None:
    for key in ("reason", "block_reason", "error_message"):
        reason = _mapping_string(step.output, key)
        if reason:
            return reason
    for event in related_events:
        reason = _mapping_string(event.payload, "reason")
        if reason:
            return reason
    return preview_text(step.conclusion) or None


def taskrun_next_action(record: TaskRunRecord, last_attempt: TaskStepRecord | None) -> str:
    if record.status == "running" and record.current_step_id:
        return "Wait for the current manual step to finish."
    if record.status in TERMINAL_TASKRUN_STATUSES:
        return "TaskRun is terminal; inspect summary or archive when ready."
    if last_attempt is None:
        return f"Run `magipi taskrun step {record.id[:8]}` to execute the first manual step."
    reason = step_reason(last_attempt, [])
    if last_attempt.status == "done":
        return str(
            last_attempt.output.get("next_action")
            or f"Run `magipi taskrun step {record.id[:8]}` for the next manual step, or close when complete."
        )
    if last_attempt.status == "blocked":
        return next_action_for_status(record.id, "blocked", reason)
    if last_attempt.status == "cancelled":
        return next_action_for_status(record.id, "cancelled", reason)
    return next_action_for_status(record.id, "failed", reason)


def next_action_for_status(task_run_id: str, status: str, reason: str | None) -> str:
    prefix = f"`magipi taskrun step {task_run_id[:8]}`"
    if status == "done":
        return f"Run {prefix} for the next manual step, or close the TaskRun when complete."
    if status == "blocked":
        detail = f" ({reason})" if reason else ""
        return f"Resolve the blocker{detail}, then run {prefix} to continue."
    if status == "cancelled":
        return f"Review the cancellation, then run {prefix} to continue manually."
    detail = f" ({reason})" if reason else ""
    return f"Inspect the failure{detail}, then run {prefix} to retry manually."


def step_counts(
    step: TaskStepRecord,
    permission_decisions: list[TaskPermissionDecisionRecord],
) -> TaskStepCounts:
    real_permission_count = sum(
        1 for decision in permission_decisions if decision.step_id == step.id
    )
    cached_permission_count = _mapping_int(step.output, "permission_decision_count")
    permission_decision_count = (
        real_permission_count if permission_decisions else cached_permission_count
    )
    return TaskStepCounts(
        tool_count=_mapping_int(step.output, "tool_count"),
        permission_decision_count=permission_decision_count,
    )


def _summary_current_step(record: TaskRunRecord) -> Mapping[str, object] | str | None:
    current_step = record.summary.get("current_step")
    if isinstance(current_step, Mapping):
        return current_step
    if record.current_step_id:
        return record.current_step_id
    return None


def _summary_next_action(record: TaskRunRecord) -> str:
    next_action = record.summary.get("next_action")
    return str(next_action) if next_action else ""


def _profile_name(profile: Mapping[str, Any]) -> str:
    name = profile.get("name")
    return str(name) if name else ""


def _related_step_events(
    step: TaskStepRecord,
    events: list[TaskEventRecord],
) -> list[TaskEventRecord]:
    return [
        event
        for event in events
        if event.step_id == step.id and event.event_type in STEP_REASON_EVENT_TYPES
    ]


def _reason_source_step(
    record: TaskRunRecord,
    current_step: TaskStepRecord | None,
    last_attempt: TaskStepRecord | None,
) -> TaskStepRecord | None:
    if current_step is not None and current_step.status in {"failed", "blocked", "cancelled"}:
        return current_step
    if (
        last_attempt is not None
        and record.status in {"blocked", "failed", "cancelled"}
        and last_attempt.status in {"failed", "blocked", "cancelled"}
    ):
        return last_attempt
    return None


def _mapping_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mapping_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    return 0


def preview_text(value: str | None, limit: int = 500) -> str:
    if not value:
        return ""
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


__all__ = [
    "TaskRunEventsResult",
    "TaskRunHistoryResult",
    "TaskRunHistoryStep",
    "TaskRunListItem",
    "TaskRunListResult",
    "TaskRunNextResult",
    "TaskStepCounts",
    "build_taskrun_history",
    "build_taskrun_list",
    "build_taskrun_next",
    "next_action_for_status",
    "preview_text",
    "step_counts",
    "step_reason",
    "step_summary",
    "taskrun_next_action",
]
