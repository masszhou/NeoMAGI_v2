"""Postgres reads for session-scoped audit events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schema import QuotedIdentifier
from .session_utils import iso as _iso


@dataclass(frozen=True, slots=True)
class SessionAuditEventRecord:
    id: str
    session_id: str
    event_type: str
    actor_type: str
    action: str
    target: dict[str, Any]
    decision: dict[str, Any]
    metadata: dict[str, Any]
    occurred_at: str
    entry_id: str | None = None
    tool_execution_id: str | None = None


def list_audit_events(conn, schema: QuotedIdentifier, session_id: str) -> list[SessionAuditEventRecord]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, session_id, entry_id, tool_execution_id, event_type,
                   actor_type, action, target, decision, metadata, occurred_at
            FROM {schema}.agent_audit_events
            WHERE session_id = %s
            ORDER BY occurred_at ASC, id ASC
            """,
            (session_id,),
        )
        return [_audit_event_from_row(row) for row in cur.fetchall()]


def _audit_event_from_row(row: Any) -> SessionAuditEventRecord:
    return SessionAuditEventRecord(
        id=str(row[0]),
        session_id=str(row[1]),
        entry_id=str(row[2]) if row[2] is not None else None,
        tool_execution_id=str(row[3]) if row[3] is not None else None,
        event_type=row[4],
        actor_type=row[5],
        action=row[6],
        target=dict(row[7] or {}),
        decision=dict(row[8] or {}),
        metadata=dict(row[9] or {}),
        occurred_at=_iso(row[10]),
    )


__all__ = ["SessionAuditEventRecord", "list_audit_events"]
