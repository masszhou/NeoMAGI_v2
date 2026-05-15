"""In-memory session repository for unit tests and product-layer fakes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from cli.core.session_types import MessageEntry, SessionEntry

from .ids import new_db_uuid, provider_cache_affinity_for_session
from .session_repository import (
    EntryRecord,
    SessionRecord,
    ToolExecutionRecord,
    SessionRepository,
)
from .session_utils import (
    context_participates as _context_participates,
    dump_json as _dump_json,
    duration_from_details as _duration_from_details,
    utc_now_iso,
    validate_entry as _validate_entry,
)


class InMemorySessionRepository(SessionRepository):
    """Deterministic repository for unit tests only."""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self.entries: dict[str, list[EntryRecord]] = {}
        self.tool_executions: list[ToolExecutionRecord] = []
        self._TEST_ONLY_fail_on_leaf_update: bool = False

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
        affinity_id = provider_cache_affinity_id or provider_cache_affinity_for_session(session_id)
        now = created_at or utc_now_iso()
        record = SessionRecord(
            id=session_id,
            cwd=str(cwd),
            parent_session_id=parent_session_id,
            provider_cache_affinity_id=affinity_id,
            created_at=now,
            updated_at=now,
            source=dict(source or {}),
        )
        self.sessions[session_id] = record
        self.entries[session_id] = []
        return record

    def get_session(
        self,
        session_id: str,
        *,
        include_taskrun_owned: bool = False,
    ) -> SessionRecord | None:
        _ = include_taskrun_owned
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
        record = EntryRecord(
            id=entry_id or new_db_uuid(),
            session_id=session_id,
            pi_export_id=entry.id,
            parent_entry_id=self._entry_db_id_from_pi_id(session_id, entry.parent_id),
            entry_type=entry.type,
            occurred_at=entry.timestamp,
            payload=entry,
            context_participates=_context_participates(entry),
            created_at=utc_now_iso(),
        )
        previous_entries = list(self.entries.setdefault(session_id, []))
        previous_session = self.sessions[session_id]
        previous_tools = list(self.tool_executions)
        try:
            self.entries[session_id].append(record)
            if self._TEST_ONLY_fail_on_leaf_update:
                raise RuntimeError("injected leaf update failure")
            self.update_session_leaf(session_id, record.id)
            self._apply_entry_side_effects(session_id, entry)
        except Exception:
            self.entries[session_id] = previous_entries
            self.sessions[session_id] = previous_session
            self.tool_executions = previous_tools
            raise
        return record

    def get_entry(self, session_id: str, entry_id: str) -> EntryRecord | None:
        for entry in self.entries.get(session_id, []):
            if entry.id == entry_id or entry.pi_export_id == entry_id:
                return entry
        return None

    def list_entries(self, session_id: str) -> list[EntryRecord]:
        return list(self.entries.get(session_id, []))

    def list_tool_executions(self, session_id: str) -> list[ToolExecutionRecord]:
        return [
            record
            for record in self.tool_executions
            if record.session_id == session_id
        ]

    def list_audit_events(self, _session_id: str):
        return []

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
                return self._update_tool_execution(
                    index,
                    record,
                    result_content=result_content,
                    result_details=result_details,
                    is_error=is_error,
                    duration_ms=duration_ms or _duration_from_details(details),
                )
        return self._insert_tool_execution_end(
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result_content=result_content,
            result_details=result_details,
            is_error=is_error,
            duration_ms=duration_ms or _duration_from_details(details),
        )

    def _apply_entry_side_effects(self, session_id: str, entry: SessionEntry) -> None:
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

    def _update_tool_execution(
        self,
        index: int,
        record: ToolExecutionRecord,
        *,
        result_content: Any,
        result_details: Any,
        is_error: bool,
        duration_ms: int | None,
    ) -> ToolExecutionRecord:
        details = result_details if isinstance(result_details, dict) else {}
        updated = replace(
            record,
            result_content=_dump_json(result_content),
            result_details=_dump_json(result_details),
            is_error=is_error,
            ended_at=utc_now_iso(),
            duration_ms=duration_ms,
            truncation=details.get("truncation"),
            policy_decision=details.get("policyDecision"),
            sandbox=details.get("sandbox"),
            run_id=record.run_id or details.get("runId"),
            runtime_session_id=record.runtime_session_id or details.get("runtimeSessionId"),
        )
        self.tool_executions[index] = updated
        return updated

    def _insert_tool_execution_end(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        result_content: Any,
        result_details: Any,
        is_error: bool,
        duration_ms: int | None,
    ) -> ToolExecutionRecord:
        details = result_details if isinstance(result_details, dict) else {}
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
            duration_ms=duration_ms,
            truncation=details.get("truncation"),
            policy_decision=details.get("policyDecision"),
            sandbox=details.get("sandbox"),
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


__all__ = ["InMemorySessionRepository"]
