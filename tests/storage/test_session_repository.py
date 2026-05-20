from __future__ import annotations

import pytest

from storage.config import DatabaseConfig
from storage.in_memory_session_repository import InMemorySessionRepository
from storage.session_repository import PostgresSessionRepository


class _Cursor:
    def __init__(self, row):
        self._row = row
        self._last_query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, query, _params=()):
        self._last_query = query

    def fetchone(self):
        if "RETURNING id, session_id" in self._last_query:
            return self._row
        return None


class _Conn:
    def __init__(self, row):
        self.row = row
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _Cursor(self.row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _ReadCursor:
    def __init__(self):
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, query, _params=()):
        self.queries.append(query)

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _ReadConn:
    def __init__(self):
        self.cursor_obj = _ReadCursor()

    def cursor(self):
        return self.cursor_obj


def _config() -> DatabaseConfig:
    return DatabaseConfig(
        host="localhost",
        port=5432,
        user="user",
        password="pw",
        database="db",
        schema="neomagi",
    )


def test_postgres_append_entry_rolls_back_when_leaf_update_fails(monkeypatch) -> None:
    payload = {
        "type": "message",
        "id": "01entry",
        "timestamp": "2026-05-03T00:00:00Z",
        "message": {"role": "user", "content": "hello", "timestamp": 1},
    }
    row = (
        "entry-db-id",
        "session-id",
        None,
        "01entry",
        "message",
        "2026-05-03T00:00:00Z",
        payload,
        True,
        "2026-05-03T00:00:00Z",
    )
    conn = _Conn(row)
    repo = PostgresSessionRepository(
        conn,
        _config(),
    )

    def fail_leaf_update(_cur, _session_id, _entry_id):
        raise RuntimeError("injected leaf update failure")

    monkeypatch.setattr(repo, "_update_current_leaf_tx", fail_leaf_update)

    with pytest.raises(RuntimeError, match="injected leaf update failure"):
        repo.append_entry("session-id", payload)

    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_session_reads_exclude_taskrun_owned_sessions_by_fk() -> None:
    conn = _ReadConn()
    repo = PostgresSessionRepository(conn, _config())

    repo.get_session("019e2200-0000-7000-8000-000000000001")
    repo.list_recent_sessions(cwd="/workspace")

    sql = "\n".join(conn.cursor_obj.queries)
    assert "FROM \"neomagi\".task_runs tr" in sql
    assert "tr.agent_session_id = s.id" in sql


def test_session_repository_redacts_tool_results_before_persistence(tmp_path) -> None:
    repo = InMemorySessionRepository()
    session = repo.create_session(cwd=str(tmp_path))
    secret = "AWS_SECRET_ACCESS_KEY=fake-secret-value"

    repo.record_tool_execution_start(
        session_id=session.id,
        tool_call_id="call-1",
        tool_name="bash",
        args={"command": "cat .env"},
    )
    record = repo.record_tool_execution_end(
        session_id=session.id,
        tool_call_id="call-1",
        tool_name="bash",
        result_content=[{"type": "text", "text": secret}],
        result_details={"path": ".env", "fullOutputPath": str(tmp_path / "raw.out")},
        is_error=False,
    )

    assert "fake-secret-value" not in str(record)
    assert record.result_content[0]["text"] == "[redacted]"
    assert record.result_details["path"] == "[redacted-path]"


def test_session_entry_payload_uses_redacted_tool_result_for_resume_context(tmp_path) -> None:
    repo = InMemorySessionRepository()
    session = repo.create_session(cwd=str(tmp_path))
    payload = {
        "type": "message",
        "id": "tool-result-1",
        "timestamp": "2026-05-19T00:00:00Z",
        "message": {
            "role": "toolResult",
            "toolCallId": "call-1",
            "toolName": "bash",
            "content": [{"type": "text", "text": "OPENAI_API_KEY=sk-secret-value"}],
            "details": {"path": ".env"},
            "isError": False,
            "timestamp": 1,
        },
    }

    entry = repo.append_entry(session.id, payload)

    assert entry.payload.message.content[0].text == "[redacted]"
    assert repo.list_entries(session.id)[0].payload.message.content[0].text == "[redacted]"
