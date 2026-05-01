"""Session repository abstractions and Postgres implementation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from cli.core.session_types import (
    MessageEntry,
    SessionEntry,
    SessionEntryAdapter,
    SessionHeader,
)

from .config import DatabaseConfig
from .ids import new_db_uuid, new_pi_export_id, provider_cache_affinity_for_session
from .schema import _quote_identifier


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


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
class ToolExecutionRecord:
    id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    args: Any
    result_content: Any = None
    result_details: Any = None
    is_error: bool | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    runtime_session_id: str | None = None
    run_id: str | None = None


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


class InMemorySessionRepository:
    """Deterministic repository for unit tests and product-layer fakes only."""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self.entries: dict[str, list[EntryRecord]] = {}
        self.tool_executions: list[ToolExecutionRecord] = []

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
        record = SessionRecord(
            id=session_id,
            cwd=str(cwd),
            parent_session_id=parent_session_id,
            provider_cache_affinity_id=provider_cache_affinity_id,
            created_at=now,
            updated_at=now,
            source=dict(source or {}),
        )
        self.sessions[session_id] = record
        self.entries[session_id] = []
        return record

    def get_session(self, session_id: str) -> SessionRecord | None:
        record = self.sessions.get(session_id)
        if record is None or record.deleted_at is not None:
            return None
        return record

    def list_recent_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int = 20,
    ) -> list[SessionRecord]:
        sessions = [
            session
            for session in self.sessions.values()
            if session.deleted_at is None and (cwd is None or session.cwd == cwd)
        ]
        sessions.sort(key=lambda session: session.updated_at, reverse=True)
        return sessions[:limit]

    def update_session_name(self, session_id: str, name: str | None) -> SessionRecord:
        record = self._require_session(session_id)
        updated = replace(record, display_name=name, updated_at=utc_now_iso())
        self.sessions[session_id] = updated
        return updated

    def update_session_leaf(self, session_id: str, entry_id: str | None) -> SessionRecord:
        record = self._require_session(session_id)
        updated = replace(record, current_leaf_entry_id=entry_id, updated_at=utc_now_iso())
        self.sessions[session_id] = updated
        return updated

    def soft_delete_session(self, session_id: str) -> SessionRecord:
        record = self._require_session(session_id)
        updated = replace(record, deleted_at=utc_now_iso(), updated_at=utc_now_iso())
        self.sessions[session_id] = updated
        return updated

    def append_entry(
        self,
        session_id: str,
        payload: Mapping[str, Any] | SessionEntry,
        *,
        entry_id: str | None = None,
    ) -> EntryRecord:
        self._require_session(session_id)
        entry = _validate_entry(payload)
        existing = self.get_entry(session_id, entry.id)
        if existing is not None:
            return existing
        parent_db_id = self._entry_db_id_from_pi_id(session_id, entry.parent_id)
        record = EntryRecord(
            id=entry_id or new_db_uuid(),
            session_id=session_id,
            pi_export_id=entry.id,
            parent_entry_id=parent_db_id,
            entry_type=entry.type,
            occurred_at=entry.timestamp,
            payload=entry,
            context_participates=_context_participates(entry),
            created_at=utc_now_iso(),
        )
        self.entries.setdefault(session_id, []).append(record)
        self.update_session_leaf(session_id, record.id)
        if entry.type == "label":
            self._upsert_label(session_id, entry.target_id, entry.label)
        if entry.type == "session_info":
            self.update_session_name(session_id, entry.name)
        if isinstance(entry, MessageEntry) and entry.message.role == "toolResult":
            self.record_tool_execution_end(
                session_id=session_id,
                tool_call_id=entry.message.tool_call_id,
                tool_name=entry.message.tool_name,
                result_content=_dump_json(entry.message.content),
                result_details=_dump_json(entry.message.details),
                is_error=entry.message.is_error,
                duration_ms=_duration_from_details(entry.message.details),
            )
        return record

    def get_entry(self, session_id: str, entry_id: str) -> EntryRecord | None:
        for entry in self.entries.get(session_id, []):
            if entry.id == entry_id or entry.pi_export_id == entry_id:
                return entry
        return None

    def list_entries(self, session_id: str) -> list[EntryRecord]:
        return list(self.entries.get(session_id, []))

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
        record = ToolExecutionRecord(
            id=new_db_uuid(),
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args=_dump_json(args),
            started_at=utc_now_iso(),
            runtime_session_id=runtime_session_id,
            run_id=run_id,
        )
        self.tool_executions.append(record)
        return record

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
        details = result_details if isinstance(result_details, dict) else {}
        for index in range(len(self.tool_executions) - 1, -1, -1):
            record = self.tool_executions[index]
            if record.session_id == session_id and record.tool_call_id == tool_call_id:
                updated = replace(
                    record,
                    result_content=_dump_json(result_content),
                    result_details=_dump_json(result_details),
                    is_error=is_error,
                    ended_at=utc_now_iso(),
                    duration_ms=duration_ms or _duration_from_details(details),
                    run_id=record.run_id or details.get("runId"),
                    runtime_session_id=record.runtime_session_id
                    or details.get("runtimeSessionId"),
                )
                self.tool_executions[index] = updated
                return updated
        record = ToolExecutionRecord(
            id=new_db_uuid(),
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args=_dump_json(details.get("args", {})),
            result_content=_dump_json(result_content),
            result_details=_dump_json(result_details),
            is_error=is_error,
            started_at=utc_now_iso(),
            ended_at=utc_now_iso(),
            duration_ms=duration_ms or _duration_from_details(details),
            run_id=details.get("runId"),
            runtime_session_id=details.get("runtimeSessionId"),
        )
        self.tool_executions.append(record)
        return record

    def _require_session(self, session_id: str) -> SessionRecord:
        record = self.get_session(session_id)
        if record is None:
            raise KeyError(f"unknown session: {session_id}")
        return record

    def _entry_db_id_from_pi_id(self, session_id: str, pi_id: str | None) -> str | None:
        if pi_id is None:
            return None
        entry = self.get_entry(session_id, pi_id)
        return entry.id if entry is not None else None

    def _upsert_label(self, _session_id: str, _target_id: str, _label: str | None) -> None:
        return None


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
        entry_id = entry_id or new_db_uuid()
        parent_db_id = self._entry_db_id_from_pi_id(session_id, entry.parent_id)
        payload_json = entry.model_dump(by_alias=True, exclude_none=True)
        try:
            with self._conn.cursor() as cur:
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
                        parent_db_id,
                        entry.id,
                        entry.type,
                        entry.timestamp,
                        _jsonb(payload_json),
                        _context_participates(entry),
                        utc_now_iso(),
                    ),
                )
                row = cur.fetchone()
                if isinstance(entry, MessageEntry):
                    self._insert_message(cur, session_id, entry_id, entry)
                if entry.type == "label":
                    self._upsert_label(cur, session_id, entry.target_id, entry.label)
                if entry.type == "session_info":
                    cur.execute(
                        f"""
                        UPDATE {self._schema}.agent_sessions
                        SET display_name = %s
                        WHERE id = %s
                        """,
                        (entry.name, session_id),
                    )
                cur.execute(
                    f"""
                    UPDATE {self._schema}.agent_sessions
                    SET current_leaf_entry_id = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (entry_id, utc_now_iso(), session_id),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        if isinstance(entry, MessageEntry) and entry.message.role == "toolResult":
            self.record_tool_execution_end(
                session_id=session_id,
                tool_call_id=entry.message.tool_call_id,
                tool_name=entry.message.tool_name,
                result_content=_dump_json(entry.message.content),
                result_details=_dump_json(entry.message.details),
                is_error=entry.message.is_error,
                duration_ms=_duration_from_details(entry.message.details),
            )
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
        details = result_details if isinstance(result_details, dict) else {}
        now = utc_now_iso()
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, args, started_at, runtime_session_id, run_id
                    FROM {self._schema}.agent_tool_executions
                    WHERE session_id = %s AND tool_call_id = %s
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    (session_id, tool_call_id),
                )
                row = cur.fetchone()
                if row is None:
                    record_id = new_db_uuid()
                    args = _dump_json(details.get("args", {}))
                    started_at = now
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
                            session_id,
                            tool_call_id,
                            tool_name,
                            _jsonb(args),
                            _jsonb(_dump_json(result_content)),
                            _jsonb(_dump_json(result_details)),
                            is_error,
                            started_at,
                            now,
                            duration_ms or _duration_from_details(details),
                            _jsonb(details.get("truncation")),
                            _jsonb(details.get("policyDecision")),
                            _jsonb(details.get("sandbox")),
                            runtime_session_id,
                            run_id,
                        ),
                    )
                else:
                    record_id = str(row[0])
                    args = row[1]
                    started_at = _iso(row[2])
                    runtime_session_id = row[3] or details.get("runtimeSessionId")
                    run_id = row[4] or details.get("runId")
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
                            _jsonb(_dump_json(result_content)),
                            _jsonb(_dump_json(result_details)),
                            is_error,
                            now,
                            duration_ms or _duration_from_details(details),
                            _jsonb(details.get("truncation")),
                            _jsonb(details.get("policyDecision")),
                            _jsonb(details.get("sandbox")),
                            runtime_session_id,
                            run_id,
                            record_id,
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
            args=args,
            result_content=_dump_json(result_content),
            result_details=_dump_json(result_details),
            is_error=is_error,
            started_at=started_at,
            ended_at=now,
            duration_ms=duration_ms or _duration_from_details(details),
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
        payload = message.model_dump(by_alias=True, exclude_none=True)
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


def _validate_entry(payload: Mapping[str, Any] | SessionEntry) -> SessionEntry:
    if hasattr(payload, "model_dump"):
        raw = payload.model_dump(by_alias=True, exclude_none=True)  # type: ignore[union-attr]
    else:
        raw = dict(payload)
    return SessionEntryAdapter.validate_python(raw)


def _context_participates(entry: SessionEntry) -> bool:
    if entry.type in {"custom", "label", "session_info"}:
        return False
    if isinstance(entry, MessageEntry) and getattr(entry.message, "exclude_from_context", False):
        return False
    return True


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


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ModuleNotFoundError:  # pragma: no cover - dependency bootstrap
        return value
    return Jsonb(value)


def _dump_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_dump_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump_json(item) for key, item in value.items()}
    return value


def _duration_from_details(details: Any) -> int | None:
    if not isinstance(details, dict):
        return None
    value = details.get("durationMs")
    return value if isinstance(value, int) else None


def allocate_entry_payload(
    *,
    entry_type: str,
    parent_id: str | None,
    payload: dict[str, Any],
    existing_ids: Iterable[str] = (),
    timestamp: str | None = None,
) -> dict[str, Any]:
    existing = set(existing_ids)
    base = {
        "type": entry_type,
        "id": new_pi_export_id(existing.__contains__),
        "parentId": parent_id,
        "timestamp": timestamp or utc_now_iso(),
    }
    base.update(payload)
    return {key: value for key, value in base.items() if value is not None}


__all__ = [
    "EntryRecord",
    "InMemorySessionRepository",
    "PostgresSessionRepository",
    "SessionRecord",
    "SessionRepository",
    "ToolExecutionRecord",
    "allocate_entry_payload",
    "utc_now",
    "utc_now_iso",
]
