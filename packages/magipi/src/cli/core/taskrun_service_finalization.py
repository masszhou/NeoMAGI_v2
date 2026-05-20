"""Step finalization helpers for ``TaskRunService``."""

from __future__ import annotations

from collections.abc import Mapping

from cli.core.taskrun_service_helpers import step_conclusion
from cli.core.taskrun_step import TaskRunRuntimeOptions, TaskRunStepOutcome
from storage.taskrun_repository import (
    TaskRunRecord,
    TaskRunRepository,
    TaskStepRecord,
)


def update_finalized_step(
    repository: TaskRunRepository,
    step: TaskStepRecord,
    status: str,
    output: dict[str, object],
    outcome: TaskRunStepOutcome,
    ended_at: str,
) -> TaskStepRecord:
    return repository.update_step_status(
        step.id,
        status=status,
        output=output,
        conclusion=step_conclusion(outcome, status),
        ended_at=ended_at,
    )


def next_task_status_after_step(status: str, cancel_requested: bool) -> str:
    if status == "done":
        return "pending"
    if cancel_requested:
        return "cancelled"
    return "blocked"


def update_run_after_step(
    repository: TaskRunRepository,
    task_run: TaskRunRecord,
    *,
    next_task_status: str,
    ended_at: str,
) -> TaskRunRecord:
    return repository.update_task_run_step_state(
        task_run.id,
        status=next_task_status,
        current_step_id=None,
        heartbeat_at=ended_at,
        updated_at=ended_at,
    )


def close_cancelled_run(
    repository: TaskRunRepository,
    task_run: TaskRunRecord,
    ended_at: str,
) -> TaskRunRecord:
    return repository.update_task_run_status(
        task_run.id,
        status="cancelled",
        heartbeat_at=None,
        closed_at=ended_at,
        updated_at=ended_at,
    )


def append_step_finalized_event(
    repository: TaskRunRepository,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    previous_status: str,
    next_task_status: str,
    runtime_options: TaskRunRuntimeOptions,
    outcome: TaskRunStepOutcome,
    output: Mapping[str, object],
    ended_at: str,
) -> None:
    status = step.status
    event_type = {
        "done": "task_step_completed",
        "failed": "task_step_failed",
        "blocked": "task_step_blocked",
        "cancelled": "task_step_cancelled",
    }[status]
    repository.append_event(
        task_run_id=task_run.id,
        step_id=step.id,
        event_type=event_type,
        payload={
            "step_id": step.id,
            "step_index": step.step_index,
            "status_from": "running",
            "status_to": status,
            "task_status_from": previous_status,
            "task_status_to": next_task_status,
            "model_ref": runtime_options.model_ref,
            "run_id": outcome.run_id,
            "reason": output.get("reason"),
        },
        occurred_at=ended_at,
    )


def append_run_cancelled_event(
    repository: TaskRunRepository,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    ended_at: str,
) -> None:
    repository.append_event(
        task_run_id=task_run.id,
        step_id=step.id,
        event_type="task_run_cancelled",
        payload={
            "previous_status": "running",
            "final_status": task_run.status,
            "cancelled_at": ended_at,
            "step_id": step.id,
        },
        occurred_at=ended_at,
    )


__all__ = [
    "append_run_cancelled_event",
    "append_step_finalized_event",
    "close_cancelled_run",
    "next_task_status_after_step",
    "update_finalized_step",
    "update_run_after_step",
]
