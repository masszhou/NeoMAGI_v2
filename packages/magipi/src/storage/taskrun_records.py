"""TaskRun storage records and repository protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


TASKRUN_STATUSES = frozenset(
    {
        "pending",
        "running",
        "blocked",
        "completed",
        "failed",
        "cancelled",
        "archived",
    }
)
TASKSTEP_STATUSES = frozenset(
    {"pending", "running", "done", "failed", "blocked", "cancelled"}
)
TERMINAL_TASKRUN_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "archived"}
)


@dataclass(frozen=True, slots=True)
class TaskRunRecord:
    id: str
    workspace_root: str
    agent_session_id: str
    goal: str
    status: str
    permission_profile: dict[str, Any]
    budget: dict[str, Any]
    stop_conditions: dict[str, Any]
    summary: dict[str, Any]
    created_at: str
    updated_at: str
    current_step_id: str | None = None
    heartbeat_at: str | None = None
    closed_at: str | None = None


@dataclass(frozen=True, slots=True)
class TaskStepRecord:
    id: str
    task_run_id: str
    step_index: int
    title: str
    status: str
    input: dict[str, Any]
    output: dict[str, Any]
    conclusion: str | None = None
    started_at: str | None = None
    ended_at: str | None = None


@dataclass(frozen=True, slots=True)
class TaskEventRecord:
    id: str
    task_run_id: str
    event_type: str
    payload: dict[str, Any]
    occurred_at: str
    step_id: str | None = None


@dataclass(frozen=True, slots=True)
class TaskPermissionDecisionRecord:
    id: str
    task_run_id: str
    policy_request: dict[str, Any]
    raw_decision: dict[str, Any]
    resolved_decision: dict[str, Any]
    profile_name: str
    occurred_at: str
    step_id: str | None = None
    tool_execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class TaskRunCreateRequest:
    workspace_root: str
    goal: str
    permission_profile: Mapping[str, Any] = field(
        default_factory=lambda: {"name": "interactive"}
    )
    budget: Mapping[str, Any] | None = None
    stop_conditions: Mapping[str, Any] | None = None
    summary: Mapping[str, Any] | None = None
    status: str = "pending"
    task_run_id: str | None = None
    agent_session_id: str | None = None
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class TaskRunStepStartResult:
    task_run: TaskRunRecord
    step: TaskStepRecord


class TaskRunRepository(Protocol):
    def create_task_run(self, request: TaskRunCreateRequest) -> TaskRunRecord:
        ...

    def get_task_run(self, task_run_id: str) -> TaskRunRecord | None:
        ...

    def list_task_runs_for_workspace(
        self,
        workspace_root: str,
        *,
        include_terminal: bool = True,
    ) -> list[TaskRunRecord]:
        ...

    def list_running_task_runs(self, workspace_root: str) -> list[TaskRunRecord]:
        ...

    def find_task_runs_by_prefix(
        self,
        workspace_root: str,
        task_run_prefix: str,
    ) -> list[TaskRunRecord]:
        ...

    def update_task_run_status(
        self,
        task_run_id: str,
        *,
        status: str,
        heartbeat_at: str | None = None,
        summary: Mapping[str, Any] | None = None,
        closed_at: str | None = None,
        updated_at: str | None = None,
    ) -> TaskRunRecord:
        ...

    def update_task_run_summary(
        self,
        task_run_id: str,
        summary: Mapping[str, Any],
        *,
        updated_at: str | None = None,
    ) -> TaskRunRecord:
        ...

    def create_running_step(
        self,
        task_run_id: str,
        *,
        title: str,
        input: Mapping[str, Any],
        started_at: str | None = None,
        step_id: str | None = None,
        start_event_payload: Mapping[str, Any] | None = None,
        start_event_id: str | None = None,
    ) -> TaskRunStepStartResult:
        ...

    def update_step_status(
        self,
        step_id: str,
        *,
        status: str,
        output: Mapping[str, Any],
        conclusion: str | None,
        ended_at: str | None = None,
    ) -> TaskStepRecord:
        ...

    def update_task_run_step_state(
        self,
        task_run_id: str,
        *,
        status: str,
        current_step_id: str | None,
        heartbeat_at: str | None,
        updated_at: str | None = None,
    ) -> TaskRunRecord:
        ...

    def lease_running_task_run(
        self,
        task_run_id: str,
        *,
        step_id: str,
        heartbeat_at: str | None = None,
    ) -> bool:
        ...

    def append_event(
        self,
        *,
        task_run_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        step_id: str | None = None,
        occurred_at: str | None = None,
        event_id: str | None = None,
    ) -> TaskEventRecord:
        ...

    def list_events(self, task_run_id: str) -> list[TaskEventRecord]:
        ...

    def append_permission_decision(
        self,
        *,
        task_run_id: str,
        policy_request: Mapping[str, Any],
        raw_decision: Mapping[str, Any],
        resolved_decision: Mapping[str, Any],
        profile_name: str,
        step_id: str | None = None,
        tool_execution_id: str | None = None,
        occurred_at: str | None = None,
        decision_id: str | None = None,
    ) -> TaskPermissionDecisionRecord:
        ...

    def list_permission_decisions(
        self,
        task_run_id: str,
    ) -> list[TaskPermissionDecisionRecord]:
        ...

    def list_steps(self, task_run_id: str) -> list[TaskStepRecord]:
        ...

    def find_tool_execution_id(
        self,
        *,
        session_id: str,
        tool_call_id: str,
    ) -> str | None:
        ...


__all__ = [
    "TASKRUN_STATUSES",
    "TASKSTEP_STATUSES",
    "TERMINAL_TASKRUN_STATUSES",
    "TaskEventRecord",
    "TaskPermissionDecisionRecord",
    "TaskRunCreateRequest",
    "TaskRunRecord",
    "TaskRunRepository",
    "TaskRunStepStartResult",
    "TaskStepRecord",
]
