"""Shared bounded TaskRun auto-loop state and stop policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from cli.core.taskrun_errors import TaskRunServiceError
from cli.core.taskrun_experiments import (
    TaskRunExperimentAttempt,
    TaskRunExperimentOptions,
    experiment_started_payload,
)
from cli.core.taskrun_projection import TaskRunProjectionResult
from cli.core.taskrun_step import TaskRunRuntimeOptions
from policy.permission_profiles import PermissionProfileError, normalize_permission_profile_snapshot
from storage.taskrun_repository import (
    TERMINAL_TASKRUN_STATUSES,
    TaskEventRecord,
    TaskPermissionDecisionRecord,
    TaskRunRecord,
    TaskStepRecord,
)


@dataclass(frozen=True, slots=True)
class TaskRunAutoRunOptions:
    max_steps: int
    runtime_options: TaskRunRuntimeOptions
    experiment_options: TaskRunExperimentOptions | None = None


@dataclass(frozen=True, slots=True)
class TaskRunAutoRunIteration:
    step: TaskStepRecord
    task_run_status: str
    stop_candidate: str | None = None


@dataclass(frozen=True, slots=True)
class TaskRunAutoRunResult:
    task_run: TaskRunRecord
    iterations: list[TaskRunAutoRunIteration]
    stop_reason: str
    projection: TaskRunProjectionResult
    events: list[TaskEventRecord]
    exit_code: int = 0
    experiment_attempts: list[TaskRunExperimentAttempt] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AutoRunBudget:
    max_steps: int | None
    max_consecutive_failures: int
    max_consecutive_denies: int
    max_total_denies: int
    deadline_utc: datetime | None


@dataclass(frozen=True, slots=True)
class AutoRunCounters:
    steps_run: int = 0
    consecutive_failures: int = 0
    consecutive_denies: int = 0
    total_denies: int = 0


@dataclass(frozen=True, slots=True)
class AutoRunStopDecision:
    should_stop: bool
    stop_reason: str | None
    next_task_status: str | None
    exit_code: int


def count_step_denies(
    permission_decisions: list[TaskPermissionDecisionRecord],
    step_id: str,
) -> int:
    return sum(
        1
        for decision in permission_decisions
        if decision.step_id == step_id
        and decision.resolved_decision.get("effect") not in {None, "allow"}
    )


def advance_auto_run_counters(
    counters: AutoRunCounters,
    step: TaskStepRecord,
    step_denies: int,
) -> AutoRunCounters:
    return AutoRunCounters(
        steps_run=counters.steps_run + 1,
        consecutive_failures=(
            counters.consecutive_failures + 1 if step.status == "failed" else 0
        ),
        consecutive_denies=(counters.consecutive_denies + 1 if step_denies > 0 else 0),
        total_denies=counters.total_denies + step_denies,
    )


def evaluate_auto_run_stop(
    *,
    last_step: TaskStepRecord,
    counters: AutoRunCounters,
    options: TaskRunAutoRunOptions,
    budget: AutoRunBudget,
    step_denies: int,
) -> AutoRunStopDecision:
    if last_step.status == "cancelled":
        return AutoRunStopDecision(True, "user_cancelled", "blocked", 130)
    if step_denies > 0:
        return _deny_stop_decision(last_step, counters, budget)
    if last_step.status == "blocked":
        return AutoRunStopDecision(True, "runner_error", "blocked", 1)
    if _failure_budget_exhausted(last_step, counters, budget):
        return AutoRunStopDecision(True, "consecutive_failures_exhausted", "blocked", 1)
    if counters.steps_run >= options.max_steps:
        exit_code = 0 if last_step.status == "done" else 1
        return AutoRunStopDecision(True, "max_steps_reached", None, exit_code)
    return AutoRunStopDecision(False, None, None, 0)


def append_auto_run_event(
    service: Any,
    record: TaskRunRecord,
    *,
    event_type: str,
    auto_run_id: str,
    max_steps: int,
    runtime_options: TaskRunRuntimeOptions,
    experiment_options: TaskRunExperimentOptions | None = None,
    counters: AutoRunCounters,
    iteration_index: int,
    step: TaskStepRecord | None = None,
    stop_reason: str | None,
) -> TaskEventRecord:
    return service.repository.append_event(
        task_run_id=record.id,
        step_id=step.id if step is not None else None,
        event_type=event_type,
        payload=_auto_run_event_payload(
            record,
            auto_run_id=auto_run_id,
            max_steps=max_steps,
            runtime_options=runtime_options,
            experiment_options=experiment_options,
            counters=counters,
            iteration_index=iteration_index,
            step=step,
            stop_reason=stop_reason,
        ),
        occurred_at=service._now_iso(),
    )


def validate_auto_run_ready(
    service: Any,
    record: TaskRunRecord,
    workspace_root: str,
    *,
    explicit: bool,
    effective_permission_profile: Mapping[str, Any] | None = None,
) -> None:
    if record.status in TERMINAL_TASKRUN_STATUSES:
        raise TaskRunServiceError(f"cannot run terminal TaskRun {record.id}: {record.status}")
    if record.status == "running":
        raise TaskRunServiceError(f"cannot run active running TaskRun {record.id}")
    if record.status == "blocked" and not explicit:
        raise TaskRunServiceError(f"blocked TaskRun {record.id} requires explicit id/prefix to run")
    validate_headless_profile(
        effective_permission_profile or record.permission_profile,
        command="run",
    )
    running = [
        candidate
        for candidate in service.repository.list_running_task_runs(workspace_root)
        if candidate.id != record.id
    ]
    if running:
        details = "; ".join(f"{item.id} {item.status}" for item in running)
        raise TaskRunServiceError(
            "another TaskRun is already running in this workspace; "
            f"pass an id. Candidates: {details}"
        )


def validate_headless_profile(
    permission_profile: Mapping[str, Any],
    *,
    command: str,
) -> None:
    try:
        profile = normalize_permission_profile_snapshot(permission_profile)
    except PermissionProfileError as exc:
        raise TaskRunServiceError(str(exc)) from exc
    if not bool(profile.get("nonInteractive")):
        raise TaskRunServiceError(
            f"taskrun {command} is headless and cannot use interactive permission profile; "
            "create a TaskRun with --permission guarded or --permission full"
        )


def _deny_stop_decision(
    last_step: TaskStepRecord,
    counters: AutoRunCounters,
    budget: AutoRunBudget,
) -> AutoRunStopDecision:
    if counters.consecutive_denies >= budget.max_consecutive_denies:
        return AutoRunStopDecision(True, "consecutive_denies_exhausted", "blocked", 1)
    if counters.total_denies >= budget.max_total_denies:
        return AutoRunStopDecision(True, "total_denies_exhausted", "blocked", 1)
    if last_step.status == "blocked":
        return AutoRunStopDecision(True, "permission_block", "blocked", 1)
    return AutoRunStopDecision(False, None, None, 0)


def _failure_budget_exhausted(
    last_step: TaskStepRecord,
    counters: AutoRunCounters,
    budget: AutoRunBudget,
) -> bool:
    return (
        last_step.status == "failed"
        and counters.consecutive_failures >= budget.max_consecutive_failures
    )


def _auto_run_event_payload(
    record: TaskRunRecord,
    *,
    auto_run_id: str,
    max_steps: int,
    runtime_options: TaskRunRuntimeOptions,
    experiment_options: TaskRunExperimentOptions | None,
    counters: AutoRunCounters,
    iteration_index: int,
    step: TaskStepRecord | None,
    stop_reason: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "auto_run_id": auto_run_id,
        "task_run_id": record.id,
        "max_steps": max_steps,
        "iteration_index": iteration_index,
        "step_id": step.id if step is not None else None,
        "step_status": step.status if step is not None else None,
        "stop_reason": stop_reason,
        "consecutive_failures": counters.consecutive_failures,
        "consecutive_denies": counters.consecutive_denies,
        "total_denies": counters.total_denies,
        "model_ref": runtime_options.model_ref,
    }
    if experiment_options is not None:
        payload["experiment"] = experiment_started_payload(
            experiment_options,
            record.workspace_root,
        )
    return payload


__all__ = [
    "AutoRunBudget",
    "AutoRunCounters",
    "AutoRunStopDecision",
    "TaskRunAutoRunIteration",
    "TaskRunAutoRunOptions",
    "TaskRunAutoRunResult",
    "advance_auto_run_counters",
    "append_auto_run_event",
    "count_step_denies",
    "evaluate_auto_run_stop",
    "validate_auto_run_ready",
    "validate_headless_profile",
]
