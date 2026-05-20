"""Row mappers for TaskRun repository query results."""

from __future__ import annotations

from typing import Any

from .session_utils import iso as _iso
from .taskrun_records import (
    TASKRUN_STATUSES,
    TASKSTEP_STATUSES,
    TaskEventRecord,
    TaskExperimentRecord,
    TaskPermissionDecisionRecord,
    TaskRunRecord,
    TaskStepRecord,
)


def task_run_from_row(row: Any) -> TaskRunRecord:
    _validate_taskrun_status(row[4])
    return TaskRunRecord(
        id=str(row[0]),
        workspace_root=row[1],
        agent_session_id=str(row[2]),
        goal=row[3],
        status=row[4],
        permission_profile=dict(row[5] or {}),
        budget=dict(row[6] or {}),
        stop_conditions=dict(row[7] or {}),
        current_step_id=str(row[8]) if row[8] is not None else None,
        summary=dict(row[9] or {}),
        heartbeat_at=_iso(row[10]) if row[10] is not None else None,
        created_at=_iso(row[11]),
        updated_at=_iso(row[12]),
        closed_at=_iso(row[13]) if row[13] is not None else None,
    )


def task_step_from_row(row: Any) -> TaskStepRecord:
    _validate_taskstep_status(row[4])
    return TaskStepRecord(
        id=str(row[0]),
        task_run_id=str(row[1]),
        step_index=int(row[2]),
        title=row[3],
        status=row[4],
        input=dict(row[5] or {}),
        output=dict(row[6] or {}),
        conclusion=row[7],
        started_at=_iso(row[8]) if row[8] is not None else None,
        ended_at=_iso(row[9]) if row[9] is not None else None,
    )


def task_event_from_row(row: Any) -> TaskEventRecord:
    return TaskEventRecord(
        id=str(row[0]),
        task_run_id=str(row[1]),
        step_id=str(row[2]) if row[2] is not None else None,
        event_type=row[3],
        payload=dict(row[4] or {}),
        occurred_at=_iso(row[5]),
    )


def task_permission_decision_from_row(row: Any) -> TaskPermissionDecisionRecord:
    return TaskPermissionDecisionRecord(
        id=str(row[0]),
        task_run_id=str(row[1]),
        step_id=str(row[2]) if row[2] is not None else None,
        tool_execution_id=str(row[3]) if row[3] is not None else None,
        policy_request=dict(row[4] or {}),
        raw_decision=dict(row[5] or {}),
        resolved_decision=dict(row[6] or {}),
        profile_name=row[7],
        occurred_at=_iso(row[8]),
    )


def task_experiment_from_row(row: Any) -> TaskExperimentRecord:
    return TaskExperimentRecord(
        id=str(row[0]),
        task_run_id=str(row[1]),
        step_id=str(row[2]),
        hypothesis=row[3],
        change=dict(row[4] or {}),
        command=dict(row[5] or {}),
        metrics=dict(row[6] or {}),
        result=dict(row[7] or {}),
        decision=row[8],
        diff_ref=dict(row[9] or {}),
        created_at=_iso(row[10]),
    )


def _validate_taskrun_status(status: str) -> None:
    if status not in TASKRUN_STATUSES:
        raise ValueError(f"invalid TaskRun status: {status}")


def _validate_taskstep_status(status: str) -> None:
    if status not in TASKSTEP_STATUSES:
        raise ValueError(f"invalid TaskStep status: {status}")


__all__ = [
    "_validate_taskrun_status",
    "_validate_taskstep_status",
    "task_event_from_row",
    "task_experiment_from_row",
    "task_permission_decision_from_row",
    "task_run_from_row",
    "task_step_from_row",
]
