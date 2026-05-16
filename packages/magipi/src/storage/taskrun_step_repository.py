"""Postgres step helpers for TaskRunRepository.

Kept outside ``taskrun_repository`` so the main repository file stays readable
while P2 step execution adds write-side transitions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .ids import is_db_uuid, new_db_uuid
from .session_utils import dump_json as _dump_json
from .session_utils import jsonb as _jsonb
from .session_utils import utc_now_iso


def create_running_step_txn(
    conn: Any,
    schema: str,
    task_run_id: str,
    *,
    title: str,
    input: Mapping[str, Any],
    started_at: str | None = None,
    step_id: str | None = None,
    start_event_payload: Mapping[str, Any] | None = None,
    start_event_id: str | None = None,
):
    from .taskrun_repository import (
        TaskRunStepStartResult,
        _task_run_from_row,
        _task_step_from_row,
    )

    _validate_uuid("task_run_id", task_run_id)
    step_id = step_id or new_db_uuid()
    _validate_uuid("step_id", step_id)
    now = started_at or utc_now_iso()
    try:
        with conn.cursor() as cur:
            run_row, step_row = _create_running_step_rows(
                cur,
                schema,
                task_run_id=task_run_id,
                step_id=step_id,
                title=title,
                input=input,
                started_at=now,
                start_event_payload=start_event_payload,
                start_event_id=start_event_id,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return TaskRunStepStartResult(
        task_run=_task_run_from_row(run_row),
        step=_task_step_from_row(step_row),
    )


def _create_running_step_rows(
    cur: Any,
    schema: str,
    *,
    task_run_id: str,
    step_id: str,
    title: str,
    input: Mapping[str, Any],
    started_at: str,
    start_event_payload: Mapping[str, Any] | None,
    start_event_id: str | None,
):
    workspace_root = _workspace_root(cur, schema, task_run_id)
    _lock_workspace(cur, workspace_root)
    _require_step_ready_task_run(cur, schema, task_run_id)
    _require_no_running_workspace_task(cur, schema, workspace_root, task_run_id)
    step_row = _insert_running_step(
        cur,
        schema,
        task_run_id=task_run_id,
        step_id=step_id,
        title=title,
        input=input,
        started_at=started_at,
    )
    run_row = update_task_run_step_state_tx(
        cur,
        schema,
        task_run_id,
        status="running",
        current_step_id=step_id,
        heartbeat_at=started_at,
        updated_at=started_at,
    )
    if start_event_payload is not None:
        _insert_step_started_event(
            cur,
            schema,
            task_run_id=task_run_id,
            step_id=step_id,
            step_index=int(step_row[2]),
            payload=start_event_payload,
            occurred_at=started_at,
            event_id=start_event_id,
        )
    return run_row, step_row


def update_step_status_txn(
    conn: Any,
    schema: str,
    step_id: str,
    *,
    status: str,
    output: Mapping[str, Any],
    conclusion: str | None,
    ended_at: str | None = None,
):
    from .taskrun_repository import _task_step_from_row, _validate_taskstep_status

    _validate_uuid("step_id", step_id)
    _validate_taskstep_status(status)
    ended = ended_at or utc_now_iso()
    try:
        with conn.cursor() as cur:
            row = _update_step_status(
                cur,
                schema,
                step_id=step_id,
                status=status,
                output=output,
                conclusion=conclusion,
                ended_at=ended,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if row is None:
        raise KeyError(f"unknown TaskStep: {step_id}")
    return _task_step_from_row(row)


def update_task_run_step_state_txn(
    conn: Any,
    schema: str,
    task_run_id: str,
    *,
    status: str,
    current_step_id: str | None,
    heartbeat_at: str | None,
    updated_at: str | None = None,
):
    from .taskrun_repository import _task_run_from_row, _validate_taskrun_status

    _validate_uuid("task_run_id", task_run_id)
    if current_step_id is not None:
        _validate_uuid("current_step_id", current_step_id)
    _validate_taskrun_status(status)
    now = updated_at or utc_now_iso()
    try:
        with conn.cursor() as cur:
            row = update_task_run_step_state_tx(
                cur,
                schema,
                task_run_id,
                status=status,
                current_step_id=current_step_id,
                heartbeat_at=heartbeat_at,
                updated_at=now,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if row is None:
        raise KeyError(f"unknown TaskRun: {task_run_id}")
    return _task_run_from_row(row)


def lease_running_task_run_txn(
    conn: Any,
    schema: str,
    task_run_id: str,
    *,
    step_id: str,
    heartbeat_at: str | None = None,
) -> bool:
    _validate_uuid("task_run_id", task_run_id)
    _validate_uuid("step_id", step_id)
    now = heartbeat_at or utc_now_iso()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {schema}.task_runs
                SET heartbeat_at = %s, updated_at = %s
                WHERE id = %s
                  AND status = 'running'
                  AND current_step_id = %s
                """,
                (now, now, task_run_id, step_id),
            )
            updated = cur.rowcount == 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return updated


def find_tool_execution_id(
    conn: Any,
    schema: str,
    *,
    session_id: str,
    tool_call_id: str,
) -> str | None:
    _validate_uuid("session_id", session_id)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id
            FROM {schema}.agent_tool_executions
            WHERE session_id = %s AND tool_call_id = %s
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            (session_id, tool_call_id),
        )
        row = cur.fetchone()
    return str(row[0]) if row is not None else None


def update_task_run_step_state_tx(
    cur: Any,
    schema: str,
    task_run_id: str,
    *,
    status: str,
    current_step_id: str | None,
    heartbeat_at: str | None,
    updated_at: str,
):
    cur.execute(
        f"""
        UPDATE {schema}.task_runs
        SET status = %s,
            current_step_id = %s,
            heartbeat_at = %s,
            updated_at = %s
        WHERE id = %s
        RETURNING id, workspace_root, agent_session_id, goal, status,
                  permission_profile, budget, stop_conditions,
                  current_step_id, summary, heartbeat_at, created_at,
                  updated_at, closed_at
        """,
        (status, current_step_id, heartbeat_at, updated_at, task_run_id),
    )
    return cur.fetchone()


def _insert_running_step(
    cur: Any,
    schema: str,
    *,
    task_run_id: str,
    step_id: str,
    title: str,
    input: Mapping[str, Any],
    started_at: str,
):
    cur.execute(
        f"SELECT COALESCE(MAX(step_index), 0) + 1 FROM {schema}.task_steps WHERE task_run_id = %s",
        (task_run_id,),
    )
    step_index = int(cur.fetchone()[0])
    cur.execute(
        f"""
        INSERT INTO {schema}.task_steps(
            id, task_run_id, step_index, title, status, input,
            output, conclusion, started_at, ended_at
        )
        VALUES (%s, %s, %s, %s, 'running', %s, %s, %s, %s, %s)
        RETURNING id, task_run_id, step_index, title, status,
                  input, output, conclusion, started_at, ended_at
        """,
        (step_id, task_run_id, step_index, title, _jsonb(_dump_json(dict(input))), _jsonb({}), None, started_at, None),
    )
    return cur.fetchone()


def _insert_step_started_event(
    cur: Any,
    schema: str,
    *,
    task_run_id: str,
    step_id: str,
    step_index: int,
    payload: Mapping[str, Any],
    occurred_at: str,
    event_id: str | None,
) -> None:
    event_payload = {
        **dict(payload),
        "step_id": step_id,
        "step_index": step_index,
        "status_to": "running",
    }
    cur.execute(
        f"""
        INSERT INTO {schema}.task_events(
            id, task_run_id, step_id, event_type, payload, occurred_at
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            event_id or new_db_uuid(),
            task_run_id,
            step_id,
            "task_step_started",
            _jsonb(_dump_json(event_payload)),
            occurred_at,
        ),
    )


def _update_step_status(
    cur: Any,
    schema: str,
    *,
    step_id: str,
    status: str,
    output: Mapping[str, Any],
    conclusion: str | None,
    ended_at: str,
):
    cur.execute(
        f"""
        UPDATE {schema}.task_steps
        SET status = %s,
            output = %s,
            conclusion = %s,
            ended_at = %s
        WHERE id = %s
        RETURNING id, task_run_id, step_index, title, status,
                  input, output, conclusion, started_at, ended_at
        """,
        (status, _jsonb(_dump_json(dict(output))), conclusion, ended_at, step_id),
    )
    return cur.fetchone()


def _workspace_root(cur: Any, schema: str, task_run_id: str) -> str:
    cur.execute(
        f"""
        SELECT workspace_root
        FROM {schema}.task_runs
        WHERE id = %s
        """,
        (task_run_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"unknown TaskRun: {task_run_id}")
    return str(row[0])


def _lock_workspace(cur: Any, workspace_root: str) -> None:
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (workspace_root,))


def _require_step_ready_task_run(cur: Any, schema: str, task_run_id: str) -> None:
    cur.execute(
        f"""
        SELECT status, current_step_id
        FROM {schema}.task_runs
        WHERE id = %s
        FOR UPDATE
        """,
        (task_run_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"unknown TaskRun: {task_run_id}")
    status = str(row[0])
    current_step_id = row[1]
    if status not in {"pending", "blocked"} or current_step_id is not None:
        raise ValueError(
            "TaskRun is not ready to start a manual step: "
            f"status={status} current_step_id={current_step_id}"
        )


def _require_no_running_workspace_task(
    cur: Any,
    schema: str,
    workspace_root: str,
    task_run_id: str,
) -> None:
    cur.execute(
        f"""
        SELECT id
        FROM {schema}.task_runs
        WHERE workspace_root = %s
          AND status = 'running'
          AND id <> %s
        LIMIT 1
        """,
        (workspace_root, task_run_id),
    )
    row = cur.fetchone()
    if row is not None:
        raise ValueError(f"another TaskRun is already running in this workspace: {row[0]}")


def _validate_uuid(label: str, value: str) -> None:
    if not is_db_uuid(value):
        raise ValueError(f"invalid {label}: {value}")


__all__ = [
    "create_running_step_txn",
    "find_tool_execution_id",
    "lease_running_task_run_txn",
    "update_step_status_txn",
    "update_task_run_step_state_tx",
    "update_task_run_step_state_txn",
]
