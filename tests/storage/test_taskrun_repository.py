from __future__ import annotations

import pytest

from storage.config import DatabaseConfig
from storage.taskrun_repository import PostgresTaskRunRepository, TaskRunCreateRequest


TASK_ID = "019e2200-0000-7000-8000-000000000001"
SESSION_ID = "019e2200-0000-7000-8000-000000000002"


class _Cursor:
    def __init__(self, *, fail_task_insert: bool = False) -> None:
        self.fail_task_insert = fail_task_insert
        self.queries: list[str] = []
        self._last_query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def execute(self, query: str, _params: tuple[object, ...] = ()) -> None:
        self.queries.append(query)
        self._last_query = query
        if self.fail_task_insert and "INSERT INTO \"neomagi\".task_runs" in query:
            raise RuntimeError("injected task insert failure")

    def fetchone(self):
        if "RETURNING id, workspace_root" not in self._last_query:
            return None
        return (
            TASK_ID,
            "/workspace",
            SESSION_ID,
            "analyze repo",
            "pending",
            {"name": "interactive"},
            {},
            {},
            None,
            {},
            None,
            "2026-05-13T00:00:00+00:00",
            "2026-05-13T00:00:00+00:00",
            None,
        )


class _Conn:
    def __init__(self, *, fail_task_insert: bool = False) -> None:
        self.cursor_obj = _Cursor(fail_task_insert=fail_task_insert)
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
