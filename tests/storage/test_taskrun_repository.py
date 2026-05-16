from __future__ import annotations

import pytest

from storage.config import DatabaseConfig
from storage.taskrun_repository import PostgresTaskRunRepository, TaskRunCreateRequest


TASK_ID = "019e2200-0000-7000-8000-000000000001"
SESSION_ID = "019e2200-0000-7000-8000-000000000002"
PERMISSION_ID = "019e2200-0000-7000-8000-000000000003"
STEP_ID = "019e2200-0000-7000-8000-000000000004"
TOOL_EXECUTION_ID = "019e2200-0000-7000-8000-000000000005"
_NO_ROW = object()


class _Cursor:
    def __init__(
        self,
        *,
        fail_task_insert: bool = False,
        fail_event_insert: bool = False,
        task_run_status: str = "pending",
        current_step_id: object | None = None,
        running_workspace_id: object | None = None,
    ) -> None:
        self.fail_task_insert = fail_task_insert
        self.fail_event_insert = fail_event_insert
        self.task_run_status = task_run_status
        self.current_step_id = current_step_id
        self.running_workspace_id = running_workspace_id
        self.queries: list[str] = []
        self._last_query = ""
        self._last_params: tuple[object, ...] = ()
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def execute(self, query: str, _params: tuple[object, ...] = ()) -> None:
        self.queries.append(query)
        self._last_query = query
        self._last_params = _params
        self.rowcount = 1 if query.lstrip().startswith("UPDATE") else 0
        if self.fail_task_insert and "INSERT INTO \"neomagi\".task_runs" in query:
            raise RuntimeError("injected task insert failure")
        if self.fail_event_insert and "INSERT INTO \"neomagi\".task_events" in query:
            raise RuntimeError("injected event insert failure")

    def fetchone(self):
        if (row := self._taskrun_start_guard_row()) is not _NO_ROW:
            return row
        if row := self._step_row():
            return row
        if "FROM \"neomagi\".agent_tool_executions" in self._last_query:
            return (TOOL_EXECUTION_ID,)
        if (
            "RETURNING id, task_run_id, step_id, tool_execution_id"
            in self._last_query
        ):
            return (
                PERMISSION_ID,
                TASK_ID,
                None,
                None,
                {"toolName": "read"},
                {"effect": "confirm", "auditTags": ["policy:confirm"]},
                {"effect": "block", "auditTags": ["policy:confirm", "permission:guarded:block"]},
                "guarded",
                "2026-05-13T00:00:00+00:00",
            )
        if row := self._task_run_step_state_row():
            return row
        if "RETURNING id, workspace_root" not in self._last_query:
            return None
        return self._task_run_row("pending", {"name": "interactive"})

    def _taskrun_start_guard_row(self):
        if "SELECT COALESCE(MAX(step_index), 0) + 1" in self._last_query:
            return (1,)
        if "SELECT workspace_root" in self._last_query:
            return ("/workspace",)
        if "SELECT status, current_step_id" in self._last_query:
            return (self.task_run_status, self.current_step_id)
        if (
            "WHERE workspace_root = %s" in self._last_query
            and "status = 'running'" in self._last_query
        ):
            return (self.running_workspace_id,) if self.running_workspace_id else None
        return _NO_ROW

    def _step_row(self):
        if "RETURNING id, task_run_id, step_index, title, status" not in self._last_query:
            return None
        if "UPDATE \"neomagi\".task_steps" in self._last_query:
            return (
                STEP_ID,
                TASK_ID,
                1,
                "Step 1",
                self._last_params[0],
                {"goal": "analyze repo"},
                {"next_action": "continue"},
                self._last_params[2],
                "2026-05-13T00:00:00+00:00",
                self._last_params[3],
            )
        return (
            STEP_ID,
            TASK_ID,
            1,
            "Step 1",
            "running",
            {"goal": "analyze repo"},
            {},
            None,
            "2026-05-13T00:00:00+00:00",
            None,
        )

    def _task_run_step_state_row(self):
        if (
            "UPDATE \"neomagi\".task_runs" not in self._last_query
            or "current_step_id = %s" not in self._last_query
        ):
            return None
        return self._task_run_row(
            self._last_params[0],
            {"name": "guarded", "nonInteractive": True, "scope": {}, "sources": ["builtin"]},
            current_step_id=self._last_params[1],
            heartbeat_at=self._last_params[2],
            updated_at=self._last_params[3],
        )

    def _task_run_row(
        self,
        status: str,
        profile: dict[str, object],
        *,
        current_step_id: object | None = None,
        heartbeat_at: object | None = None,
        updated_at: object = "2026-05-13T00:00:00+00:00",
    ):
        return (
            TASK_ID,
            "/workspace",
            SESSION_ID,
            "analyze repo",
            status,
            profile,
            {},
            {},
            current_step_id,
            {},
            heartbeat_at,
            "2026-05-13T00:00:00+00:00",
            updated_at,
            None,
        )

    def fetchall(self):
        if "FROM \"neomagi\".task_permission_decisions" in self._last_query:
            return [
                (
                    PERMISSION_ID,
                    TASK_ID,
                    None,
                    None,
                    {"toolName": "read"},
                    {"effect": "confirm", "normalizedArgs": {"path": "a.txt"}},
                    {"effect": "block", "auditTags": ["permission:guarded:block"]},
                    "guarded",
                    "2026-05-13T00:00:00+00:00",
                )
            ]
        return []


class _Conn:
    def __init__(
        self,
        *,
        fail_task_insert: bool = False,
        fail_event_insert: bool = False,
        task_run_status: str = "pending",
        current_step_id: object | None = None,
        running_workspace_id: object | None = None,
    ) -> None:
        self.cursor_obj = _Cursor(
            fail_task_insert=fail_task_insert,
            fail_event_insert=fail_event_insert,
            task_run_status=task_run_status,
            current_step_id=current_step_id,
            running_workspace_id=running_workspace_id,
        )
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _Cursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _repo(conn: _Conn) -> PostgresTaskRunRepository:
    return PostgresTaskRunRepository(
        conn,
        DatabaseConfig(
            host="localhost",
            port=5432,
            user="user",
            password="pw",
            database="db",
            schema="neomagi",
        ),
    )


def test_create_task_run_inserts_owned_session_and_task_run_atomically() -> None:
    conn = _Conn()
    repo = _repo(conn)

    record = repo.create_task_run(
        TaskRunCreateRequest(
            workspace_root="/workspace",
            goal="analyze repo",
            task_run_id=TASK_ID,
            agent_session_id=SESSION_ID,
            created_at="2026-05-13T00:00:00+00:00",
        )
    )

    sql = "\n".join(conn.cursor_obj.queries)
    assert record.id == TASK_ID
    assert record.agent_session_id == SESSION_ID
    assert record.status == "pending"
    assert "INSERT INTO \"neomagi\".agent_sessions" in sql
    assert "INSERT INTO \"neomagi\".task_runs" in sql
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_create_task_run_rolls_back_owned_session_on_task_insert_failure() -> None:
    conn = _Conn(fail_task_insert=True)
    repo = _repo(conn)

    with pytest.raises(RuntimeError, match="injected task insert failure"):
        repo.create_task_run(
            TaskRunCreateRequest(
                workspace_root="/workspace",
                goal="analyze repo",
                task_run_id=TASK_ID,
                agent_session_id=SESSION_ID,
                created_at="2026-05-13T00:00:00+00:00",
            )
        )

    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_create_running_step_inserts_step_and_marks_taskrun_running() -> None:
    conn = _Conn()
    repo = _repo(conn)

    result = repo.create_running_step(
        TASK_ID,
        title="Step 1",
        input={"goal": "analyze repo"},
        started_at="2026-05-13T00:00:00+00:00",
        step_id=STEP_ID,
        start_event_payload={"status_from": "pending", "model_ref": "faux/local/faux-1"},
    )

    sql = "\n".join(conn.cursor_obj.queries)
    assert result.step.id == STEP_ID
    assert result.step.step_index == 1
    assert result.task_run.status == "running"
    assert result.task_run.current_step_id == STEP_ID
    assert "pg_advisory_xact_lock" in sql
    assert "INSERT INTO \"neomagi\".task_steps" in sql
    assert "current_step_id = %s" in sql
    assert "INSERT INTO \"neomagi\".task_events" in sql
    assert conn.commits == 1


def test_create_running_step_rolls_back_when_started_event_fails() -> None:
    conn = _Conn(fail_event_insert=True)
    repo = _repo(conn)

    with pytest.raises(RuntimeError, match="injected event insert failure"):
        repo.create_running_step(
            TASK_ID,
            title="Step 1",
            input={"goal": "analyze repo"},
            started_at="2026-05-13T00:00:00+00:00",
            step_id=STEP_ID,
            start_event_payload={"status_from": "pending"},
        )

    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_create_running_step_rechecks_taskrun_state_in_transaction() -> None:
    conn = _Conn(task_run_status="running", current_step_id=STEP_ID)
    repo = _repo(conn)

    with pytest.raises(ValueError, match="not ready"):
        repo.create_running_step(
            TASK_ID,
            title="Step 1",
            input={"goal": "analyze repo"},
            started_at="2026-05-13T00:00:00+00:00",
            step_id=STEP_ID,
        )

    sql = "\n".join(conn.cursor_obj.queries)
    assert "INSERT INTO \"neomagi\".task_steps" not in sql
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_create_running_step_rejects_another_running_taskrun_in_workspace() -> None:
    conn = _Conn(running_workspace_id="019e2200-0000-7000-8000-000000000099")
    repo = _repo(conn)

    with pytest.raises(ValueError, match="another TaskRun is already running"):
        repo.create_running_step(
            TASK_ID,
            title="Step 1",
            input={"goal": "analyze repo"},
            started_at="2026-05-13T00:00:00+00:00",
            step_id=STEP_ID,
        )

    sql = "\n".join(conn.cursor_obj.queries)
    assert "INSERT INTO \"neomagi\".task_steps" not in sql
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_list_task_runs_for_workspace_orders_by_recent_activity() -> None:
    conn = _Conn()
    repo = _repo(conn)

    assert repo.list_task_runs_for_workspace("/workspace") == []

    sql = "\n".join(conn.cursor_obj.queries)
    assert "WHERE workspace_root = %s" in sql
    assert "status <> ALL" not in sql
    assert "ORDER BY updated_at DESC, created_at DESC" in sql


def test_list_task_runs_for_workspace_can_exclude_terminal_statuses() -> None:
    conn = _Conn()
    repo = _repo(conn)

    assert repo.list_task_runs_for_workspace("/workspace", include_terminal=False) == []

    sql = "\n".join(conn.cursor_obj.queries)
    assert "status <> ALL(%s)" in sql


def test_list_events_orders_by_occurred_at_then_id() -> None:
    conn = _Conn()
    repo = _repo(conn)

    assert repo.list_events(TASK_ID) == []

    sql = "\n".join(conn.cursor_obj.queries)
    assert "FROM \"neomagi\".task_events" in sql
    assert "ORDER BY occurred_at ASC, id ASC" in sql


def test_update_step_status_persists_output_and_conclusion() -> None:
    conn = _Conn()
    repo = _repo(conn)

    step = repo.update_step_status(
        STEP_ID,
        status="done",
        output={"next_action": "continue"},
        conclusion="done",
        ended_at="2026-05-13T00:01:00+00:00",
    )

    sql = "\n".join(conn.cursor_obj.queries)
    assert step.status == "done"
    assert step.output["next_action"] == "continue"
    assert "UPDATE \"neomagi\".task_steps" in sql
    assert conn.commits == 1


def test_find_tool_execution_id_returns_latest_match() -> None:
    repo = _repo(_Conn())

    assert (
        repo.find_tool_execution_id(session_id=SESSION_ID, tool_call_id="call_1")
        == TOOL_EXECUTION_ID
    )


def test_append_permission_decision_allows_null_tool_execution_id() -> None:
    conn = _Conn()
    repo = _repo(conn)

    record = repo.append_permission_decision(
        task_run_id=TASK_ID,
        step_id=None,
        tool_execution_id=None,
        policy_request={"toolName": "read"},
        raw_decision={"effect": "confirm", "auditTags": ["policy:confirm"]},
        resolved_decision={
            "effect": "block",
            "auditTags": ["policy:confirm", "permission:guarded:block"],
        },
        profile_name="guarded",
        decision_id=PERMISSION_ID,
        occurred_at="2026-05-13T00:00:00+00:00",
    )

    sql = "\n".join(conn.cursor_obj.queries)
    assert record.id == PERMISSION_ID
    assert record.tool_execution_id is None
    assert record.raw_decision["auditTags"] == ["policy:confirm"]
    assert "INSERT INTO \"neomagi\".task_permission_decisions" in sql
    assert conn.commits == 1


def test_list_permission_decisions_preserves_alias_json() -> None:
    conn = _Conn()
    repo = _repo(conn)

    records = repo.list_permission_decisions(TASK_ID)

    assert len(records) == 1
    assert records[0].raw_decision["normalizedArgs"] == {"path": "a.txt"}
    assert records[0].resolved_decision["auditTags"] == ["permission:guarded:block"]


def test_append_permission_decision_rejects_invalid_task_run_id() -> None:
    repo = _repo(_Conn())

    with pytest.raises(ValueError, match="invalid task_run_id"):
        repo.append_permission_decision(
            task_run_id="not-a-uuid",
            policy_request={"toolName": "read"},
            raw_decision={"effect": "allow"},
            resolved_decision={"effect": "allow"},
            profile_name="guarded",
        )
