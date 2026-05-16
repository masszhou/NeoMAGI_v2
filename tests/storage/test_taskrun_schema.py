from __future__ import annotations

from storage.config import DatabaseConfig
from storage.schema import NEOMAGI_TASKRUN_SCHEMA_VERSION, ensure_schema


class _Cursor:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.queries.append((query, params))

    def fetchone(self):
        return None


class _Conn:
    def __init__(self) -> None:
        self.cursor_obj = _Cursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _Cursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _config() -> DatabaseConfig:
    return DatabaseConfig(
        host="localhost",
        port=5432,
        user="user",
        password="pw",
        database="db",
        schema="neomagi",
    )


def test_ensure_schema_bootstraps_taskrun_tables_and_meta() -> None:
    conn = _Conn()

    ensure_schema(conn, _config())

    sql = "\n".join(query for query, _params in conn.cursor_obj.queries)
    meta_params = [params for _query, params in conn.cursor_obj.queries if params]
    assert "CREATE TABLE IF NOT EXISTS \"neomagi\".task_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS \"neomagi\".task_steps" in sql
    assert "CREATE TABLE IF NOT EXISTS \"neomagi\".task_events" in sql
    assert "CREATE TABLE IF NOT EXISTS \"neomagi\".task_permission_decisions" in sql
    assert "CREATE TABLE IF NOT EXISTS \"neomagi\".task_experiments" in sql
    assert "task_runs_one_running_per_workspace_idx" in sql
    assert "task_experiments_task_run_order_idx" in sql
    assert "task_experiments_step_idx" in sql
    assert ("neomagi_taskrun_schema_version", NEOMAGI_TASKRUN_SCHEMA_VERSION) in meta_params
    assert conn.commits == 1
    assert conn.rollbacks == 0
