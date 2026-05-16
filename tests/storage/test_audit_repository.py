from __future__ import annotations

import pytest
from pydantic import ValidationError

from policy.audit import AuditRecord
from policy.types import PolicyDecision
from storage.audit_repository import InMemoryAuditRepository, PostgresAuditRepository
from storage.config import DatabaseConfig


SESSION_ID = "019e3000-0000-7000-8000-000000000001"
TOOL_EXECUTION_ID = "019e3000-0000-7000-8000-000000000002"
EXPLICIT_TOOL_EXECUTION_ID = "019e3000-0000-7000-8000-000000000003"


def test_audit_repository_stores_allowlisted_metadata_only() -> None:
    record = _record(
        args={"path": "secret.txt", "contentBytes": 12},
        decision=PolicyDecision.allow(
            normalized_args={"path": "secret.txt", "content": "raw secret"},
            audit_tags=["path:write:allow"],
        ),
    )
    repository = InMemoryAuditRepository()

    event = repository.record(session_id="session-1", record=record)

    assert event.decision["effect"] == "allow"
    assert "normalizedArgs" not in event.decision
    assert event.metadata["args"] == {"path": "secret.txt", "contentBytes": 12}
    assert "policyDecision" not in event.metadata
    assert "content" not in event.metadata["args"]


def test_audit_record_redaction_status_is_validated() -> None:
    with pytest.raises(ValidationError):
        _record(
            args={"path": "a.txt"},
            decision=PolicyDecision.allow(),
            redactionStatus="not_applied",
        )


def test_postgres_audit_repository_uses_explicit_tool_execution_id_without_lookup() -> None:
    conn = _Conn(lookup_tool_execution_id=TOOL_EXECUTION_ID)
    repository = PostgresAuditRepository(conn, _config())

    event = repository.record(
        session_id=SESSION_ID,
        record=_record(args={"path": "a.txt"}, decision=PolicyDecision.allow()),
        tool_execution_id=EXPLICIT_TOOL_EXECUTION_ID,
    )

    assert event.tool_execution_id == EXPLICIT_TOOL_EXECUTION_ID
    assert conn.cursor_obj.insert_params[3] == EXPLICIT_TOOL_EXECUTION_ID
    assert not conn.cursor_obj.lookup_queries
    assert conn.commits == 1


def test_postgres_audit_repository_backfills_tool_execution_id_from_tool_call_id() -> None:
    conn = _Conn(lookup_tool_execution_id=TOOL_EXECUTION_ID)
    repository = PostgresAuditRepository(conn, _config())

    event = repository.record(
        session_id=SESSION_ID,
        record=_record(args={"path": "a.txt"}, decision=PolicyDecision.allow()),
    )

    assert event.tool_execution_id == TOOL_EXECUTION_ID
    assert conn.cursor_obj.lookup_params == (SESSION_ID, "call-1")
    assert conn.cursor_obj.insert_params[3] == TOOL_EXECUTION_ID
    assert conn.commits == 1


def test_postgres_audit_repository_keeps_null_tool_execution_id_when_lookup_misses() -> None:
    conn = _Conn(lookup_tool_execution_id=None)
    repository = PostgresAuditRepository(conn, _config())

    event = repository.record(
        session_id=SESSION_ID,
        record=_record(args={"path": "a.txt"}, decision=PolicyDecision.allow()),
    )

    assert event.tool_execution_id is None
    assert conn.cursor_obj.lookup_params == (SESSION_ID, "call-1")
    assert conn.cursor_obj.insert_params[3] is None
    assert conn.commits == 1


def test_postgres_audit_repository_skips_lookup_without_tool_call_id() -> None:
    conn = _Conn(lookup_tool_execution_id=TOOL_EXECUTION_ID)
    repository = PostgresAuditRepository(conn, _config())

    event = repository.record(
        session_id=SESSION_ID,
        record=_record(
            args={"path": "a.txt"},
            decision=PolicyDecision.allow(),
            toolCallId=None,
        ),
    )

    assert event.tool_execution_id is None
    assert not conn.cursor_obj.lookup_queries
    assert conn.cursor_obj.insert_params[3] is None
    assert conn.commits == 1


def _record(
    *,
    args: dict[str, object],
    decision: PolicyDecision,
    **overrides: object,
) -> AuditRecord:
    payload = {
        "runtimeSessionId": "runtime-1",
        "runId": "run-1",
        "actor": "model",
        "toolName": "write",
        "toolCallId": "call-1",
        "args": args,
        "policyDecision": decision,
        "startedAt": "2026-01-01T00:00:00+00:00",
        "endedAt": "2026-01-01T00:00:01+00:00",
        "durationMs": 10,
        "isError": False,
        "redactionStatus": "applied",
    }
    payload.update(overrides)
    return AuditRecord(**payload)


def _config() -> DatabaseConfig:
    return DatabaseConfig(
        host="localhost",
        port=5432,
        user="user",
        password="password",
        database="neomagi",
        schema="neomagi",
    )


class _Conn:
    def __init__(self, *, lookup_tool_execution_id: str | None) -> None:
        self.cursor_obj = _Cursor(lookup_tool_execution_id=lookup_tool_execution_id)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> "_Cursor":
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _Cursor:
    def __init__(self, *, lookup_tool_execution_id: str | None) -> None:
        self.lookup_tool_execution_id = lookup_tool_execution_id
        self.lookup_queries: list[str] = []
        self.lookup_params: tuple[object, ...] | None = None
        self.insert_params: tuple[object, ...] = ()
        self._last_query = ""

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self._last_query = query
        if "FROM \"neomagi\".agent_tool_executions" in query:
            self.lookup_queries.append(query)
            self.lookup_params = params
            return
        if "INSERT INTO \"neomagi\".agent_audit_events" in query:
            self.insert_params = params

    def fetchone(self):
        if "FROM \"neomagi\".agent_tool_executions" not in self._last_query:
            return None
        if self.lookup_tool_execution_id is None:
            return None
        return (self.lookup_tool_execution_id,)
