"""Audit event persistence for M5 records in M6 storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from policy.audit import AuditRecord

from .config import DatabaseConfig
from .ids import new_db_uuid
from .schema import _quote_identifier
from .session_repository import utc_now_iso


@dataclass(frozen=True, slots=True)
class AuditEventRecord:
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


class AuditRepository(Protocol):
    def record(
        self,
        *,
        session_id: str,
        record: AuditRecord,
        entry_id: str | None = None,
        tool_execution_id: str | None = None,
    ) -> AuditEventRecord:
        ...


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.records: list[AuditEventRecord] = []

    def record(
        self,
        *,
        session_id: str,
        record: AuditRecord,
        entry_id: str | None = None,
        tool_execution_id: str | None = None,
    ) -> AuditEventRecord:
        event = _event_from_audit_record(
            session_id=session_id,
            record=record,
            entry_id=entry_id,
            tool_execution_id=tool_execution_id,
        )
        self.records.append(event)
        return event


class PostgresAuditRepository:
    def __init__(self, conn, config: DatabaseConfig) -> None:
        self._conn = conn
        self._schema = _quote_identifier(config.schema)

    def record(
        self,
        *,
        session_id: str,
        record: AuditRecord,
        entry_id: str | None = None,
        tool_execution_id: str | None = None,
    ) -> AuditEventRecord:
        event = _event_from_audit_record(
            session_id=session_id,
            record=record,
            entry_id=entry_id,
            tool_execution_id=tool_execution_id,
        )
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._schema}.agent_audit_events(
                        id, session_id, entry_id, tool_execution_id, event_type,
                        actor_type, action, target, decision, metadata, occurred_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.id,
                        event.session_id,
                        event.entry_id,
                        event.tool_execution_id,
                        event.event_type,
                        event.actor_type,
                        event.action,
                        _jsonb(event.target),
                        _jsonb(event.decision),
                        _jsonb(event.metadata),
                        event.occurred_at,
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return event


def _event_from_audit_record(
    *,
    session_id: str,
    record: AuditRecord,
    entry_id: str | None,
    tool_execution_id: str | None,
) -> AuditEventRecord:
    decision = record.policy_decision.model_dump(
        by_alias=True,
        exclude_none=True,
        exclude={"normalized_args"},
    )
    return AuditEventRecord(
        id=new_db_uuid(),
        session_id=session_id,
        entry_id=entry_id,
        tool_execution_id=tool_execution_id,
        event_type="tool_execution",
        actor_type=record.actor,
        action=record.tool_name,
        target={"toolCallId": record.tool_call_id, "toolName": record.tool_name},
        decision=decision,
        metadata=_metadata_from_audit_record(record),
        occurred_at=record.ended_at or utc_now_iso(),
    )


def _metadata_from_audit_record(record: AuditRecord) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "args": record.args,
        "startedAt": record.started_at,
        "endedAt": record.ended_at,
        "durationMs": record.duration_ms,
        "isError": record.is_error,
        "redactionStatus": record.redaction_status,
    }
    optional_fields: tuple[tuple[str, Any], ...] = (
        ("runtimeSessionId", record.runtime_session_id),
        ("runId", record.run_id),
        ("truncation", record.truncation),
        ("affectedPaths", record.affected_paths),
        ("fullOutputPath", record.full_output_path),
        ("redactionTags", record.redaction_tags),
        ("exceptionClass", record.exception_class),
        ("exceptionMessage", record.exception_message),
    )
    for key, value in optional_fields:
        if value not in (None, [], {}):
            metadata[key] = value
    return metadata


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ModuleNotFoundError:  # pragma: no cover - dependency bootstrap
        return value
    return Jsonb(value)


__all__ = [
    "AuditEventRecord",
    "AuditRepository",
    "InMemoryAuditRepository",
    "PostgresAuditRepository",
]
