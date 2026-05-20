"""Audit helpers for TaskRun experiment host commands."""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from policy.audit import AuditRecord
from policy.redaction import redacted_command_preview
from policy.types import PolicyDecision


def record_host_command_audit(
    service: Any,
    auto_run_id: str,
    result: Any,
) -> None:
    sink = getattr(service, "host_command_audit_sink", None)
    if sink is None:
        return
    preview, redaction_applied = redacted_command_preview(result.command)
    record = AuditRecord(
        runtimeSessionId=None,
        runId=auto_run_id,
        actor="extension",
        toolName="host_command",
        args={
            "phase": result.phase,
            "commandPreview": preview,
            "exitCode": result.exit_code,
        },
        policyDecision=PolicyDecision(
            effect=result.policy_effect,
            reason=result.reason,
        ),
        startedAt=result.started_at or result.ended_at or service._now_iso(),
        endedAt=result.ended_at or service._now_iso(),
        durationMs=result.duration_ms,
        isError=not result.succeeded,
        redactionTags=["command_preview"] if redaction_applied else [],
        redactionStatus="applied" if redaction_applied else "not_required",
        exceptionMessage=result.reason if result.policy_effect == "allow" else None,
    )
    maybe_awaitable = sink.record(record)
    if inspect.isawaitable(maybe_awaitable):
        asyncio.run(maybe_awaitable)


def elapsed_ms(started_monotonic: float) -> int:
    return max(0, int((time.monotonic() - started_monotonic) * 1000))


__all__ = ["elapsed_ms", "record_host_command_audit"]
