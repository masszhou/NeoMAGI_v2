"""Shared durable session record and repository protocol types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from cli.core.session_types import SessionEntry, SessionHeader
from storage.audit_queries import SessionAuditEventRecord
from storage.tool_execution_records import ToolExecutionRecord


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
    ) -> SessionRecord: ...

    def get_session(
        self,
        session_id: str,
        *,
        include_taskrun_owned: bool = False,
    ) -> SessionRecord | None: ...

    def list_recent_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int = 20,
    ) -> list[SessionRecord]: ...

    def update_session_name(self, session_id: str, name: str | None) -> SessionRecord: ...

    def update_session_leaf(self, session_id: str, entry_id: str | None) -> SessionRecord: ...

    def soft_delete_session(self, session_id: str) -> SessionRecord: ...

    def append_entry(
        self,
        session_id: str,
        payload: Mapping[str, Any] | SessionEntry,
        *,
        entry_id: str | None = None,
    ) -> EntryRecord: ...

    def get_entry(self, session_id: str, entry_id: str) -> EntryRecord | None: ...

    def list_entries(self, session_id: str) -> list[EntryRecord]: ...

    def list_tool_executions(self, session_id: str) -> list[ToolExecutionRecord]: ...

    def list_audit_events(self, session_id: str) -> list[SessionAuditEventRecord]: ...

    def record_tool_execution_start(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        args: Any,
        runtime_session_id: str | None = None,
        run_id: str | None = None,
    ) -> ToolExecutionRecord: ...

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
    ) -> ToolExecutionRecord: ...


__all__ = ["EntryRecord", "SessionRecord", "SessionRepository"]
