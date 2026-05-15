"""TaskRun repository abstractions and Postgres implementation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import DatabaseConfig
from .ids import (
    is_db_uuid,
    new_db_uuid,
    provider_cache_affinity_for_session,
)
from .schema import _quote_identifier
from .session_utils import dump_json as _dump_json
from .session_utils import iso as _iso
from .session_utils import jsonb as _jsonb
from .session_utils import utc_now_iso


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


class PostgresTaskRunRepository:
    def __init__(self, conn, config: DatabaseConfig) -> None:
        self._conn = conn
        self._schema = _quote_identifier(config.schema)

    def create_task_run(self, request: TaskRunCreateRequest) -> TaskRunRecord:
        _validate_taskrun_status(request.status)
        task_run_id = request.task_run_id or new_db_uuid()
        agent_session_id = request.agent_session_id or new_db_uuid()
        now = request.created_at or utc_now_iso()
        try:
            with self._conn.cursor() as cur:
                self._insert_owned_session_tx(
                    cur,
                    request=request,
                    task_run_id=task_run_id,
                    agent_session_id=agent_session_id,
                    now=now,
                )
                row = self._insert_task_run_tx(
                    cur,
                    request=request,
                    task_run_id=task_run_id,
                    agent_session_id=agent_session_id,
                    now=now,
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return _task_run_from_row(row)

    def _insert_owned_session_tx(
        self,
        cur,
        *,
        request: TaskRunCreateRequest,
        task_run_id: str,
        agent_session_id: str,
        now: str,
    ) -> None:
        cur.execute(
            f"""
            INSERT INTO {self._schema}.agent_sessions(
                id, parent_session_id, cwd, created_at, updated_at,
                provider_cache_affinity_id, source
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                agent_session_id,
                None,
                request.workspace_root,
                now,
                now,
                provider_cache_affinity_for_session(agent_session_id),
                _jsonb({"taskRunOwned": True, "taskRunId": task_run_id}),
            ),
        )

    def _insert_task_run_tx(
        self,
        cur,
        *,
        request: TaskRunCreateRequest,
        task_run_id: str,
        agent_session_id: str,
        now: str,
    ):
        cur.execute(
            f"""
            INSERT INTO {self._schema}.task_runs(
                id, workspace_root, agent_session_id, goal, status,
                permission_profile, budget, stop_conditions, summary,
                heartbeat_at, created_at, updated_at, closed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, workspace_root, agent_session_id, goal, status,
                      permission_profile, budget, stop_conditions,
                      current_step_id, summary, heartbeat_at, created_at,
                      updated_at, closed_at
            """,
            (
                task_run_id,
                request.workspace_root,
                agent_session_id,
                request.goal,
                request.status,
                _jsonb(_dump_json(dict(request.permission_profile))),
                _jsonb(_dump_json(dict(request.budget or {}))),
                _jsonb(_dump_json(dict(request.stop_conditions or {}))),
                _jsonb(_dump_json(dict(request.summary or {}))),
                None,
                now,
                now,
                None,
            ),
        )
        return cur.fetchone()

    def get_task_run(self, task_run_id: str) -> TaskRunRecord | None:
        if not is_db_uuid(task_run_id):
            return None
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, workspace_root, agent_session_id, goal, status,
                       permission_profile, budget, stop_conditions,
                       current_step_id, summary, heartbeat_at, created_at,
                       updated_at, closed_at
                FROM {self._schema}.task_runs
                WHERE id = %s
                """,
                (task_run_id,),
            )
            row = cur.fetchone()
        return _task_run_from_row(row) if row is not None else None

    def list_task_runs_for_workspace(
        self,
        workspace_root: str,
        *,
        include_terminal: bool = True,
    ) -> list[TaskRunRecord]:
        where = "workspace_root = %s"
        params: list[Any] = [workspace_root]
        if not include_terminal:
            where += " AND status <> ALL(%s)"
            params.append(list(TERMINAL_TASKRUN_STATUSES))
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, workspace_root, agent_session_id, goal, status,
                       permission_profile, budget, stop_conditions,
                       current_step_id, summary, heartbeat_at, created_at,
                       updated_at, closed_at
                FROM {self._schema}.task_runs
                WHERE {where}
                ORDER BY updated_at DESC, created_at DESC
                """,
                tuple(params),
            )
            rows = cur.fetchall()
        return [_task_run_from_row(row) for row in rows]

    def list_running_task_runs(self, workspace_root: str) -> list[TaskRunRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, workspace_root, agent_session_id, goal, status,
                       permission_profile, budget, stop_conditions,
                       current_step_id, summary, heartbeat_at, created_at,
                       updated_at, closed_at
                FROM {self._schema}.task_runs
                WHERE workspace_root = %s AND status = 'running'
                ORDER BY updated_at DESC, created_at DESC
                """,
                (workspace_root,),
            )
            rows = cur.fetchall()
        return [_task_run_from_row(row) for row in rows]

    def find_task_runs_by_prefix(
        self,
        workspace_root: str,
        task_run_prefix: str,
    ) -> list[TaskRunRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, workspace_root, agent_session_id, goal, status,
                       permission_profile, budget, stop_conditions,
                       current_step_id, summary, heartbeat_at, created_at,
                       updated_at, closed_at
                FROM {self._schema}.task_runs
                WHERE workspace_root = %s AND id::text LIKE %s
                ORDER BY updated_at DESC, created_at DESC
                """,
                (workspace_root, f"{task_run_prefix}%"),
            )
            rows = cur.fetchall()
        return [_task_run_from_row(row) for row in rows]

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
        _validate_taskrun_status(status)
        now = updated_at or utc_now_iso()
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._schema}.task_runs
                    SET status = %s,
                        heartbeat_at = %s,
                        summary = COALESCE(%s, summary),
                        closed_at = COALESCE(%s, closed_at),
                        updated_at = %s
                    WHERE id = %s
                    RETURNING id, workspace_root, agent_session_id, goal, status,
                              permission_profile, budget, stop_conditions,
                              current_step_id, summary, heartbeat_at, created_at,
                              updated_at, closed_at
                    """,
                    (
                        status,
                        heartbeat_at,
                        _jsonb(_dump_json(dict(summary))) if summary is not None else None,
                        closed_at,
                        now,
                        task_run_id,
                    ),
                )
                row = cur.fetchone()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        if row is None:
            raise KeyError(f"unknown TaskRun: {task_run_id}")
        return _task_run_from_row(row)

    def update_task_run_summary(
        self,
        task_run_id: str,
        summary: Mapping[str, Any],
        *,
        updated_at: str | None = None,
    ) -> TaskRunRecord:
        now = updated_at or utc_now_iso()
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._schema}.task_runs
                    SET summary = %s, updated_at = %s
                    WHERE id = %s
                    RETURNING id, workspace_root, agent_session_id, goal, status,
                              permission_profile, budget, stop_conditions,
                              current_step_id, summary, heartbeat_at, created_at,
                              updated_at, closed_at
                    """,
                    (_jsonb(_dump_json(dict(summary))), now, task_run_id),
                )
                row = cur.fetchone()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        if row is None:
            raise KeyError(f"unknown TaskRun: {task_run_id}")
        return _task_run_from_row(row)

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
        event_id = event_id or new_db_uuid()
        occurred_at = occurred_at or utc_now_iso()
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._schema}.task_events(
                        id, task_run_id, step_id, event_type, payload, occurred_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, task_run_id, step_id, event_type, payload, occurred_at
                    """,
                    (
                        event_id,
                        task_run_id,
                        step_id,
                        event_type,
                        _jsonb(_dump_json(dict(payload))),
                        occurred_at,
                    ),
                )
                row = cur.fetchone()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return _task_event_from_row(row)

    def list_events(self, task_run_id: str) -> list[TaskEventRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, task_run_id, step_id, event_type, payload, occurred_at
                FROM {self._schema}.task_events
                WHERE task_run_id = %s
                ORDER BY occurred_at ASC, id ASC
                """,
                (task_run_id,),
            )
            rows = cur.fetchall()
        return [_task_event_from_row(row) for row in rows]

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
        _validate_uuid("task_run_id", task_run_id)
        if step_id is not None:
            _validate_uuid("step_id", step_id)
        if tool_execution_id is not None:
            _validate_uuid("tool_execution_id", tool_execution_id)
        decision_id = decision_id or new_db_uuid()
        occurred_at = occurred_at or utc_now_iso()
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._schema}.task_permission_decisions(
                        id, task_run_id, step_id, tool_execution_id,
                        policy_request, raw_decision, resolved_decision,
                        profile_name, occurred_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, task_run_id, step_id, tool_execution_id,
                              policy_request, raw_decision, resolved_decision,
                              profile_name, occurred_at
                    """,
                    (
                        decision_id,
                        task_run_id,
                        step_id,
                        tool_execution_id,
                        _jsonb(_dump_json(dict(policy_request))),
                        _jsonb(_dump_json(dict(raw_decision))),
                        _jsonb(_dump_json(dict(resolved_decision))),
                        profile_name,
                        occurred_at,
                    ),
                )
                row = cur.fetchone()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return _task_permission_decision_from_row(row)

    def list_permission_decisions(
        self,
        task_run_id: str,
    ) -> list[TaskPermissionDecisionRecord]:
        _validate_uuid("task_run_id", task_run_id)
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, task_run_id, step_id, tool_execution_id,
                       policy_request, raw_decision, resolved_decision,
                       profile_name, occurred_at
                FROM {self._schema}.task_permission_decisions
                WHERE task_run_id = %s
                ORDER BY occurred_at ASC, id ASC
                """,
                (task_run_id,),
            )
            rows = cur.fetchall()
        return [_task_permission_decision_from_row(row) for row in rows]

    def list_steps(self, task_run_id: str) -> list[TaskStepRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, task_run_id, step_index, title, status, input, output,
                       conclusion, started_at, ended_at
                FROM {self._schema}.task_steps
                WHERE task_run_id = %s
                ORDER BY step_index ASC
                """,
                (task_run_id,),
            )
            rows = cur.fetchall()
        return [_task_step_from_row(row) for row in rows]


def _validate_taskrun_status(status: str) -> None:
    if status not in TASKRUN_STATUSES:
        raise ValueError(f"invalid TaskRun status: {status}")


def _validate_taskstep_status(status: str) -> None:
    if status not in TASKSTEP_STATUSES:
        raise ValueError(f"invalid TaskStep status: {status}")


def _validate_uuid(label: str, value: str) -> None:
    if not is_db_uuid(value):
        raise ValueError(f"invalid {label}: {value}")


def _task_run_from_row(row: Any) -> TaskRunRecord:
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


def _task_step_from_row(row: Any) -> TaskStepRecord:
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


def _task_event_from_row(row: Any) -> TaskEventRecord:
    return TaskEventRecord(
        id=str(row[0]),
        task_run_id=str(row[1]),
        step_id=str(row[2]) if row[2] is not None else None,
        event_type=row[3],
        payload=dict(row[4] or {}),
        occurred_at=_iso(row[5]),
    )


def _task_permission_decision_from_row(row: Any) -> TaskPermissionDecisionRecord:
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


__all__ = [
    "PostgresTaskRunRepository",
    "TASKRUN_STATUSES",
    "TASKSTEP_STATUSES",
    "TERMINAL_TASKRUN_STATUSES",
    "TaskEventRecord",
    "TaskPermissionDecisionRecord",
    "TaskRunCreateRequest",
    "TaskRunRecord",
    "TaskRunRepository",
    "TaskStepRecord",
]
