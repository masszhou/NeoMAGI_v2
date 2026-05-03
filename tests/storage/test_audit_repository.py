from __future__ import annotations

import pytest
from pydantic import ValidationError

from policy.audit import AuditRecord
from policy.types import PolicyDecision
from storage.audit_repository import InMemoryAuditRepository


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
