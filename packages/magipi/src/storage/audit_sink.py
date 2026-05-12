"""AuditSink adapter backed by the current durable session."""

from __future__ import annotations

from collections.abc import Callable

from policy.audit import AuditRecord, AuditSink

from .audit_repository import AuditRepository


class PostgresAuditSink(AuditSink):
    def __init__(
        self,
        *,
        repository: AuditRepository,
        session_id_provider: Callable[[], str],
    ) -> None:
        self._repository = repository
        self._session_id_provider = session_id_provider

    def record(self, record: AuditRecord) -> None:
        self._repository.record(
            session_id=self._session_id_provider(),
            record=record,
        )


__all__ = ["PostgresAuditSink"]
