"""Audit sink protocol for M5 tool execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .types import PolicyDecision

RedactionStatus = Literal["not_required", "applied", "failed"]


class _AuditModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class AuditRecord(_AuditModel):
    runtime_session_id: str | None = Field(default=None, alias="runtimeSessionId")
    run_id: str | None = Field(default=None, alias="runId")
    actor: str
    tool_name: str = Field(alias="toolName")
    tool_call_id: str | None = Field(default=None, alias="toolCallId")
    args: dict[str, Any]
    policy_decision: PolicyDecision = Field(alias="policyDecision")
    started_at: str = Field(alias="startedAt")
    ended_at: str = Field(alias="endedAt")
    duration_ms: int = Field(alias="durationMs")
    is_error: bool = Field(alias="isError")
    truncation: Any = None
    affected_paths: list[str] = Field(default_factory=list, alias="affectedPaths")
    full_output_path: str | None = Field(default=None, alias="fullOutputPath")
    redaction_tags: list[str] = Field(default_factory=list, alias="redactionTags")
    redaction_status: RedactionStatus = Field(default="not_required", alias="redactionStatus")
    exception_class: str | None = Field(default=None, alias="exceptionClass")
    exception_message: str | None = Field(default=None, alias="exceptionMessage")


class AuditSink(Protocol):
    def record(self, record: AuditRecord) -> None | Awaitable[None]:
        """Persist or forward one audit record."""


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def record(self, record: AuditRecord) -> None:
        self.records.append(record)


AuditCallback = Callable[[AuditRecord], None | Awaitable[None]]


class CallbackAuditSink:
    def __init__(self, callback: AuditCallback) -> None:
        self._callback = callback

    def record(self, record: AuditRecord) -> None | Awaitable[None]:
        return self._callback(record)


__all__ = [
    "AuditCallback",
    "AuditRecord",
    "AuditSink",
    "CallbackAuditSink",
    "InMemoryAuditSink",
    "RedactionStatus",
]
