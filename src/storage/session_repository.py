"""Session repository abstractions and Postgres implementation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from cli.core.session_types import (
    MessageEntry,
    SessionEntry,
    SessionEntryAdapter,
    SessionHeader,
)

from .config import DatabaseConfig
from .ids import is_db_uuid, new_db_uuid, provider_cache_affinity_for_session
from .audit_queries import SessionAuditEventRecord
from .schema import _quote_identifier
from .session_utils import (
    allocate_entry_payload,
    context_participates as _context_participates,
    dump_entry_payload_json as _dump_entry_payload_json,
    dump_json as _dump_json,
    dump_message_payload_json as _dump_message_payload_json,
    duration_from_details as _duration_from_details,
    iso as _iso,
    jsonb as _jsonb,
    utc_now,
    utc_now_iso,
    validate_entry as _validate_entry,
)
from .tool_execution_records import ToolExecutionRecord


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    cwd: str
    provider_cache_affinity_id: str
    created_at: str
    updated_at: str
    parent_session_id: str | None = None
    current_leaf_entry_id: str | None = None
    display_name: str | None = None
    source: dict[str, Any] = field(default_factory=dict)
    deleted_at: str | None = None

    def header(self) -> SessionHeader:
        parent = (
            f"neomagi://session/{self.parent_session_id}"
            if self.parent_session_id
            else self.source.get("parentSessionPath")
        )
        return SessionHeader(
            id=self.id,
            timestamp=self.created_at,
            cwd=self.cwd,
            parentSession=parent,
        )


@dataclass(frozen=True, slots=True)
class EntryRecord:
    id: str
    session_id: str
    pi_export_id: str
    entry_type: str
    payload: SessionEntry
    occurred_at: str
    created_at: str
    parent_entry_id: str | None = None
    context_participates: bool = True


@dataclass(frozen=True, slots=True)
class _ToolExecutionEndRequest:
    session_id: str
    tool_call_id: str
    tool_name: str
    result_content: Any
    result_details: Any
    is_error: bool
    duration_ms: int | None = None

    @property
    def details(self) -> dict[str, Any]:
        return self.result_details if isinstance(self.result_details, dict) else {}

    @property
    def resolved_duration_ms(self) -> int | None:
        return self.duration_ms or _duration_from_details(self.details)

@dataclass(frozen=True, slots=True)
class _ToolExecutionBase:
    id: str
    args: Any
    started_at: str
    runtime_session_id: str | None
    run_id: str | None

class SessionRepository(Protocol):
    def create_session(
        self,
        *,
        cwd: str,
        parent_session_id: str | None = None,
        provider_cache_affinity_id: str | None = None,
        source: Mapping[str, Any] | None = None,
        session_id: str | None = None,
        created_at: str | None = None,
    ) -> SessionRecord:
        ...

    def get_session(self, session_id: str) -> SessionRecord | None:
        ...

    def list_recent_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int = 20,
    ) -> list[SessionRecord]:
        ...

    def update_session_name(self, session_id: str, name: str | None) -> SessionRecord:
        ...

    def update_session_leaf(self, session_id: str, entry_id: str | None) -> SessionRecord:
        ...

    def soft_delete_session(self, session_id: str) -> SessionRecord:
        ...

    def append_entry(
        self,
        session_id: str,
        payload: Mapping[str, Any] | SessionEntry,
        *,
        entry_id: str | None = None,
    ) -> EntryRecord:
        ...

    def get_entry(self, session_id: str, entry_id: str) -> EntryRecord | None:
        ...

    def list_entries(self, session_id: str) -> list[EntryRecord]:
        ...

    def list_tool_executions(self, session_id: str) -> list[ToolExecutionRecord]:
        ...

    def list_audit_events(self, session_id: str) -> list[SessionAuditEventRecord]:
        ...

    def record_tool_execution_start(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        args: Any,
        runtime_session_id: str | None = None,
        run_id: str | None = None,
    ) -> ToolExecutionRecord:
        ...

    def record_tool_execution_end(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        result_content: Any,
        result_details: Any,
        is_error: bool,
        duration_ms: int | None = None,
    ) -> ToolExecutionRecord:
        ...


class PostgresSessionRepository:
    def __init__(self, conn, config: DatabaseConfig) -> None:
        self._conn = conn
        self._schema = _quote_identifier(config.schema)

    def create_session(
        self,
        *,
        cwd: str,
        parent_session_id: str | None = None,
        provider_cache_affinity_id: str | None = None,
        source: Mapping[str, Any] | None = None,
        session_id: str | None = None,
        created_at: str | None = None,
    ) -> SessionRecord:
        session_id = session_id or new_db_uuid()
        provider_cache_affinity_id = (
            provider_cache_affinity_id or provider_cache_affinity_for_session(session_id)
        )
        now = created_at or utc_now_iso()
        source_payload = dict(source or {})
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._schema}.agent_sessions(
                        id, parent_session_id, cwd, created_at, updated_at,
                        provider_cache_affinity_id, source
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, parent_session_id, cwd, created_at, updated_at,
                              current_leaf_entry_id, provider_cache_affinity_id,
                              display_name, source, deleted_at
                    """,
                    (
                        session_id,
                        parent_session_id,
                        cwd,
                        now,
                        now,
                        provider_cache_affinity_id,
                        _jsonb(source_payload),
                    ),
                )
                row = cur.fetchone()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return _session_from_row(row)

    def get_session(self, session_id: str) -> SessionRecord | None:
        if not is_db_uuid(session_id):
            return None
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, parent_session_id, cwd, created_at, updated_at,
                       current_leaf_entry_id, provider_cache_affinity_id,
                       display_name, source, deleted_at
                FROM {self._schema}.agent_sessions
                WHERE id = %s AND deleted_at IS NULL
                """,
                (session_id,),
            )
            row = cur.fetchone()
        return _session_from_row(row) if row is not None else None

    def list_recent_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int = 20,
    ) -> list[SessionRecord]:
        args: list[Any] = []
        where = "deleted_at IS NULL"
        if cwd is not None:
            where += " AND cwd = %s"
            args.append(cwd)
        args.append(limit)
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, parent_session_id, cwd, created_at, updated_at,
                       current_leaf_entry_id, provider_cache_affinity_id,
                       display_name, source, deleted_at
                FROM {self._schema}.agent_sessions
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                tuple(args),
            )
            rows = cur.fetchall()
        return [_session_from_row(row) for row in rows]

    def update_session_name(self, session_id: str, name: str | None) -> SessionRecord:
        return self._update_session(
            """
            display_name = %s,
            updated_at = %s
            """,
            (name, utc_now_iso(), session_id),
        )

    def update_session_leaf(self, session_id: str, entry_id: str | None) -> SessionRecord:
        return self._update_session(
            """
            current_leaf_entry_id = %s,
            updated_at = %s
            """,
            (entry_id, utc_now_iso(), session_id),
        )

    def soft_delete_session(self, session_id: str) -> SessionRecord:
        now = utc_now_iso()
        return self._update_session(
            """
            deleted_at = %s,
            updated_at = %s
            """,
            (now, now, session_id),
        )

    def append_entry(
        self,
        session_id: str,
        payload: Mapping[str, Any] | SessionEntry,
        *,
        entry_id: str | None = None,
    ) -> EntryRecord:
        entry = _validate_entry(payload)
        existing = self.get_entry(session_id, entry.id)
        if existing is not None:
            return existing
        db_entry_id = entry_id or new_db_uuid()
        try:
            with self._conn.cursor() as cur:
                row = self._insert_entry_tx(cur, session_id, db_entry_id, entry)
                self._apply_entry_side_effects_tx(cur, session_id, db_entry_id, entry)
                self._update_current_leaf_tx(cur, session_id, db_entry_id)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return _entry_from_row(row)

    def get_entry(self, session_id: str, entry_id: str) -> EntryRecord | None:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, session_id, parent_entry_id, pi_export_id, entry_type,
                       occurred_at, payload, context_participates, created_at
                FROM {self._schema}.agent_session_entries
                WHERE session_id = %s AND (id::text = %s OR pi_export_id = %s)
                """,
                (session_id, entry_id, entry_id),
            )
            row = cur.fetchone()
        return _entry_from_row(row) if row is not None else None

    def list_entries(self, session_id: str) -> list[EntryRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, session_id, parent_entry_id, pi_export_id, entry_type,
                       occurred_at, payload, context_participates, created_at
                FROM {self._schema}.agent_session_entries
                WHERE session_id = %s
                ORDER BY occurred_at ASC, created_at ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
        return [_entry_from_row(row) for row in rows]

    def list_tool_executions(self, session_id: str) -> list[ToolExecutionRecord]:
        from .tool_execution_queries import list_tool_executions as _list_tool_executions
        return _list_tool_executions(self._conn, self._schema, session_id)

    def list_audit_events(self, session_id: str) -> list[SessionAuditEventRecord]:
        from .audit_queries import list_audit_events as _list_audit_events

        return _list_audit_events(self._conn, self._schema, session_id)

    def record_tool_execution_start(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        args: Any,
        runtime_session_id: str | None = None,
        run_id: str | None = None,
    ) -> ToolExecutionRecord:
        now = utc_now_iso()
        record_id = new_db_uuid()
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._schema}.agent_tool_executions(
                        id, session_id, tool_call_id, tool_name, args, started_at,
                        runtime_session_id, run_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record_id,
                        session_id,
                        tool_call_id,
                        tool_name,
                        _jsonb(_dump_json(args)),
                        now,
                        runtime_session_id,
                        run_id,
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return ToolExecutionRecord(
            id=record_id,
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args=_dump_json(args),
            started_at=now,
            runtime_session_id=runtime_session_id,
            run_id=run_id,
        )

    def record_tool_execution_end(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        result_content: Any,
        result_details: Any,
        is_error: bool,
        duration_ms: int | None = None,
    ) -> ToolExecutionRecord:
        try:
            with self._conn.cursor() as cur:
                record = self._record_tool_execution_end_tx(
                    cur,
                    _ToolExecutionEndRequest(
                        session_id=session_id,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        result_content=result_content,
                        result_details=result_details,
                        is_error=is_error,
                        duration_ms=duration_ms,
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return record

    def _insert_entry_tx(
        self,
        cur,
        session_id: str,
        entry_id: str,
        entry: SessionEntry,
    ):
        payload_json = _dump_entry_payload_json(entry)
        cur.execute(
            f"""
            INSERT INTO {self._schema}.agent_session_entries(
                id, session_id, parent_entry_id, pi_export_id, entry_type,
                occurred_at, payload, context_participates, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, session_id, parent_entry_id, pi_export_id, entry_type,
                      occurred_at, payload, context_participates, created_at
            """,
            (
                entry_id,
                session_id,
                self._entry_db_id_from_pi_id(session_id, entry.parent_id),
                entry.id,
                entry.type,
                entry.timestamp,
                _jsonb(payload_json),
                _context_participates(entry),
                utc_now_iso(),
            ),
        )
        return cur.fetchone()

    def _apply_entry_side_effects_tx(
        self,
        cur,
        session_id: str,
        entry_id: str,
        entry: SessionEntry,
    ) -> None:
        if isinstance(entry, MessageEntry):
            self._insert_message(cur, session_id, entry_id, entry)
            self._record_tool_result_end_tx(cur, session_id, entry)
        if entry.type == "label":
            self._upsert_label(cur, session_id, entry.target_id, entry.label)
        if entry.type == "session_info":
            self._update_display_name_tx(cur, session_id, entry.name)

    def _record_tool_result_end_tx(self, cur, session_id: str, entry: MessageEntry) -> None:
        if entry.message.role != "toolResult":
            return
        self._record_tool_execution_end_tx(
            cur,
            _ToolExecutionEndRequest(
                session_id=session_id,
                tool_call_id=entry.message.tool_call_id,
                tool_name=entry.message.tool_name,
                result_content=_dump_json(entry.message.content),
                result_details=_dump_json(entry.message.details),
                is_error=entry.message.is_error,
                duration_ms=_duration_from_details(entry.message.details),
            ),
        )

    def _update_display_name_tx(self, cur, session_id: str, name: str | None) -> None:
        cur.execute(
            f"""
            UPDATE {self._schema}.agent_sessions
            SET display_name = %s
            WHERE id = %s
            """,
            (name, session_id),
        )

    def _update_current_leaf_tx(self, cur, session_id: str, entry_id: str) -> None:
        cur.execute(
            f"""
            UPDATE {self._schema}.agent_sessions
            SET current_leaf_entry_id = %s, updated_at = %s
            WHERE id = %s
            """,
            (entry_id, utc_now_iso(), session_id),
        )

    def _record_tool_execution_end_tx(
        self,
        cur,
        request: _ToolExecutionEndRequest,
    ) -> ToolExecutionRecord:
        now = utc_now_iso()
        base = self._fetch_tool_execution_start(cur, request)
        if base is None:
            base = self._insert_tool_execution_end_without_start(cur, request, now)
        else:
            base = self._update_tool_execution_end_tx(cur, request, base, now)
        return ToolExecutionRecord(
            id=base.id,
            session_id=request.session_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            args=base.args,
            result_content=_dump_json(request.result_content),
            result_details=_dump_json(request.result_details),
            is_error=request.is_error,
            started_at=base.started_at,
            ended_at=now,
            duration_ms=request.resolved_duration_ms,
            truncation=request.details.get("truncation"),
            policy_decision=request.details.get("policyDecision"),
            sandbox=request.details.get("sandbox"),
            runtime_session_id=base.runtime_session_id,
            run_id=base.run_id,
        )

    def _fetch_tool_execution_start(
        self,
        cur,
        request: _ToolExecutionEndRequest,
    ) -> _ToolExecutionBase | None:
        cur.execute(
            f"""
            SELECT id, args, started_at, runtime_session_id, run_id
            FROM {self._schema}.agent_tool_executions
            WHERE session_id = %s AND tool_call_id = %s
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (request.session_id, request.tool_call_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _ToolExecutionBase(
            id=str(row[0]),
            args=row[1],
            started_at=_iso(row[2]),
            runtime_session_id=row[3],
            run_id=row[4],
        )

    def _insert_tool_execution_end_without_start(
        self,
        cur,
        request: _ToolExecutionEndRequest,
        now: str,
    ) -> _ToolExecutionBase:
        record_id = new_db_uuid()
        details = request.details
        args = _dump_json(details.get("args", {}))
        runtime_session_id = details.get("runtimeSessionId")
        run_id = details.get("runId")
        cur.execute(
            f"""
            INSERT INTO {self._schema}.agent_tool_executions(
                id, session_id, tool_call_id, tool_name, args,
                result_content, result_details, is_error, started_at,
                ended_at, duration_ms, truncation, policy_decision,
                sandbox, runtime_session_id, run_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record_id,
                request.session_id,
                request.tool_call_id,
                request.tool_name,
                _jsonb(args),
                _jsonb(_dump_json(request.result_content)),
                _jsonb(_dump_json(request.result_details)),
                request.is_error,
                now,
                now,
                request.resolved_duration_ms,
                _jsonb(details.get("truncation")),
                _jsonb(details.get("policyDecision")),
                _jsonb(details.get("sandbox")),
                runtime_session_id,
                run_id,
            ),
        )
        return _ToolExecutionBase(
            id=record_id,
            args=args,
            started_at=now,
            runtime_session_id=runtime_session_id,
            run_id=run_id,
        )

    def _update_tool_execution_end_tx(
        self,
        cur,
        request: _ToolExecutionEndRequest,
        base: _ToolExecutionBase,
        now: str,
    ) -> _ToolExecutionBase:
        details = request.details
        runtime_session_id = base.runtime_session_id or details.get("runtimeSessionId")
        run_id = base.run_id or details.get("runId")
        cur.execute(
            f"""
            UPDATE {self._schema}.agent_tool_executions
            SET result_content = %s, result_details = %s, is_error = %s,
                ended_at = %s, duration_ms = %s, truncation = %s,
                policy_decision = %s, sandbox = %s,
                runtime_session_id = %s, run_id = %s
            WHERE id = %s
            """,
            (
                _jsonb(_dump_json(request.result_content)),
                _jsonb(_dump_json(request.result_details)),
                request.is_error,
                now,
                request.resolved_duration_ms,
                _jsonb(details.get("truncation")),
                _jsonb(details.get("policyDecision")),
                _jsonb(details.get("sandbox")),
                runtime_session_id,
                run_id,
                base.id,
            ),
        )
        return _ToolExecutionBase(
            id=base.id,
            args=base.args,
            started_at=base.started_at,
            runtime_session_id=runtime_session_id,
            run_id=run_id,
        )

    def _update_session(self, assignments: str, args: tuple[Any, ...]) -> SessionRecord:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._schema}.agent_sessions
                    SET {assignments}
                    WHERE id = %s
                    RETURNING id, parent_session_id, cwd, created_at, updated_at,
                              current_leaf_entry_id, provider_cache_affinity_id,
                              display_name, source, deleted_at
                    """,
                    args,
                )
                row = cur.fetchone()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        if row is None:
            raise KeyError(f"unknown session: {args[-1]}")
        return _session_from_row(row)

    def _entry_db_id_from_pi_id(self, session_id: str, pi_id: str | None) -> str | None:
        if pi_id is None:
            return None
        entry = self.get_entry(session_id, pi_id)
        return entry.id if entry is not None else None

    def _insert_message(self, cur, session_id: str, entry_id: str, entry: MessageEntry) -> None:
        message = entry.message
        payload = _dump_message_payload_json(message)
        cur.execute(
            f"""
            INSERT INTO {self._schema}.agent_messages(
                id, session_entry_id, session_id, role, content, provider, api,
                model, response_id, stop_reason, usage, is_error, error_message,
                occurred_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                new_db_uuid(),
                entry_id,
                session_id,
                message.role,
                _jsonb(payload.get("content")),
                payload.get("provider"),
                payload.get("api"),
                payload.get("model"),
                payload.get("responseId"),
                payload.get("stopReason"),
                _jsonb(payload.get("usage")),
                bool(payload.get("isError") or payload.get("errorMessage")),
                payload.get("errorMessage"),
                entry.timestamp,
            ),
        )

    def _upsert_label(
        self,
        cur,
        session_id: str,
        target_pi_export_id: str,
        label: str | None,
    ) -> None:
        target = self.get_entry(session_id, target_pi_export_id)
        cur.execute(
            f"""
            INSERT INTO {self._schema}.agent_session_labels(
                session_id, target_entry_id, target_pi_export_id, label, updated_at
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (session_id, target_pi_export_id)
            DO UPDATE SET label = EXCLUDED.label, updated_at = EXCLUDED.updated_at
            """,
            (
                session_id,
                target.id if target is not None else None,
                target_pi_export_id,
                label,
                utc_now_iso(),
            ),
        )


def _session_from_row(row: Any) -> SessionRecord:
    return SessionRecord(
        id=str(row[0]),
        parent_session_id=str(row[1]) if row[1] is not None else None,
        cwd=row[2],
        created_at=_iso(row[3]),
        updated_at=_iso(row[4]),
        current_leaf_entry_id=str(row[5]) if row[5] is not None else None,
        provider_cache_affinity_id=row[6],
        display_name=row[7],
        source=dict(row[8] or {}),
        deleted_at=_iso(row[9]) if row[9] is not None else None,
    )

def _entry_from_row(row: Any) -> EntryRecord:
    payload = SessionEntryAdapter.validate_python(row[6])
    return EntryRecord(
        id=str(row[0]),
        session_id=str(row[1]),
        parent_entry_id=str(row[2]) if row[2] is not None else None,
        pi_export_id=row[3],
        entry_type=row[4],
        occurred_at=_iso(row[5]),
        payload=payload,
        context_participates=bool(row[7]),
        created_at=_iso(row[8]),
    )

__all__ = [
    "EntryRecord",
    "PostgresSessionRepository",
    "SessionAuditEventRecord",
    "SessionRecord",
    "SessionRepository",
    "ToolExecutionRecord",
    "allocate_entry_payload",
    "utc_now",
    "utc_now_iso",
]
