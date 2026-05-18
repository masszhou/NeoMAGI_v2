"""D14 Compaction / Auto-Retry event emitter core.

Two consumers exist for compaction lifecycle observability:

* **Interactive**: the TUI session bridge wants pi-compatible session events
  (``CompactionStartEvent``, ``CompactionEndEvent``, ``AutoRetryStartEvent``,
  ``AutoRetryEndEvent``) on its in-process queue so the controller draws
  status lines and the durable session writer can persist transcripts.

* **Headless TaskRun**: M7 wants ``task_runtime_compaction_observed`` and
  ``task_runtime_auto_retry_observed`` derived events written to
  ``task_events`` (Tier 1 only — never KEY_HISTORY, per D10).

The two shapes are different but the *triggers* are identical (one
"compaction finished" call comes from the same place in the runtime).
This module factors the shared event-emitting core so both contexts can
hook the same lifecycle without duplicating the trigger logic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from cli.core.compaction.service import CompactionAppendResult
from cli.core.session_types import (
    AgentSessionEvent,
    AutoRetryEndEvent,
    AutoRetryStartEvent,
    CompactionEndEvent,
    CompactionStartEvent,
)
from cli.core.taskrun_event_payloads import (
    TASK_RUNTIME_AUTO_RETRY_OBSERVED,
    TASK_RUNTIME_COMPACTION_OBSERVED,
    build_runtime_auto_retry_observed_payload,
    build_runtime_compaction_observed_payload,
)
from storage.taskrun_repository import TaskRunRepository


class CompactionEventEmitter(Protocol):
    """Trigger surface shared by the interactive and TaskRun-headless
    runtimes. Implementations decide what backing event(s) to write."""

    def compaction_started(self, *, reason: str) -> None: ...

    def compaction_succeeded(
        self,
        *,
        reason: str,
        result: CompactionAppendResult,
        will_retry: bool,
    ) -> None: ...

    def compaction_failed(self, *, reason: str, error: Exception) -> None: ...

    def auto_retry_started(
        self,
        *,
        attempt: int,
        max_attempts: int,
        delay_ms: int = 0,
        error_message: str | None = None,
    ) -> None: ...

    def auto_retry_finished(
        self,
        *,
        attempt: int,
        max_attempts: int,
        success: bool,
        final_error: str | None = None,
    ) -> None: ...


class InteractiveCompactionEventEmitter:
    """Translates lifecycle triggers into pi-compatible session events.

    Behavior matches the legacy direct ``self._emit_session_event(...)``
    calls in ``CompactionRuntimeMixin`` — this class only collects them
    behind a stable interface so the headless emitter can share the same
    trigger surface."""

    def __init__(self, emit_session_event: Callable[[AgentSessionEvent], None]) -> None:
        self._emit = emit_session_event

    def compaction_started(self, *, reason: str) -> None:
        self._emit(CompactionStartEvent(reason=reason))

    def compaction_succeeded(
        self,
        *,
        reason: str,
        result: CompactionAppendResult,
        will_retry: bool,
    ) -> None:
        self._emit(
            CompactionEndEvent(
                reason=reason,
                result=result.result,
                aborted=False,
                willRetry=will_retry,
            )
        )

    def compaction_failed(self, *, reason: str, error: Exception) -> None:
        self._emit(
            CompactionEndEvent(
                reason=reason,
                result=None,
                aborted=True,
                willRetry=False,
                errorMessage=str(error),
            )
        )

    def auto_retry_started(
        self,
        *,
        attempt: int,
        max_attempts: int,
        delay_ms: int = 0,
        error_message: str | None = None,
    ) -> None:
        self._emit(
            AutoRetryStartEvent(
                attempt=attempt,
                maxAttempts=max_attempts,
                delayMs=delay_ms,
                errorMessage=error_message or "",
            )
        )

    def auto_retry_finished(
        self,
        *,
        attempt: int,
        max_attempts: int,
        success: bool,
        final_error: str | None = None,
    ) -> None:
        # ``AutoRetryEndEvent`` does not carry ``max_attempts`` (pi-mono
        # session schema parity); we still accept it on the protocol so
        # the headless path can persist the true value.
        _ = max_attempts
        self._emit(
            AutoRetryEndEvent(
                success=success,
                attempt=attempt,
                finalError=final_error,
            )
        )


class TaskRunCompactionEventEmitter:
    """Writes D10 ``task_runtime_*_observed`` derived events into
    ``task_events`` (Tier 1 only). The headless TaskRun path holds this
    emitter on ``TaskRunAgentSession`` so any compaction trigger that
    fires during a step lands in TaskRun truth with stable payload
    fields."""

    def __init__(
        self,
        *,
        task_repository: TaskRunRepository,
        task_run_id: str,
        step_id: str,
    ) -> None:
        self._task_repository = task_repository
        self._task_run_id = task_run_id
        self._step_id = step_id

    def compaction_started(self, *, reason: str) -> None:
        # No derived event on start: the step view only needs the
        # observed outcome (consumers correlate via run_id + step_id).
        # Kept for protocol parity with the interactive emitter.
        _ = reason

    def compaction_succeeded(
        self,
        *,
        reason: str,
        result: CompactionAppendResult,
        will_retry: bool,
    ) -> None:
        tokens_before = getattr(getattr(result, "result", None), "tokens_before", None)
        self._task_repository.append_event(
            task_run_id=self._task_run_id,
            step_id=self._step_id,
            event_type=TASK_RUNTIME_COMPACTION_OBSERVED,
            payload=build_runtime_compaction_observed_payload(
                reason=reason,
                outcome="success",
                will_retry=will_retry,
                tokens_before=tokens_before if isinstance(tokens_before, int) else None,
            ),
        )

    def compaction_failed(self, *, reason: str, error: Exception) -> None:
        self._task_repository.append_event(
            task_run_id=self._task_run_id,
            step_id=self._step_id,
            event_type=TASK_RUNTIME_COMPACTION_OBSERVED,
            payload=build_runtime_compaction_observed_payload(
                reason=reason,
                outcome="failed",
                will_retry=False,
                error_message=str(error),
            ),
        )

    def auto_retry_started(
        self,
        *,
        attempt: int,
        max_attempts: int,
        delay_ms: int = 0,
        error_message: str | None = None,
    ) -> None:
        # The matched "finished" emit carries the success bit; start is a
        # lifecycle marker only. Emit nothing here to keep payload count
        # bounded (one observed event per retry attempt).
        _ = (attempt, max_attempts, delay_ms, error_message)

    def auto_retry_finished(
        self,
        *,
        attempt: int,
        max_attempts: int,
        success: bool,
        final_error: str | None = None,
    ) -> None:
        self._task_repository.append_event(
            task_run_id=self._task_run_id,
            step_id=self._step_id,
            event_type=TASK_RUNTIME_AUTO_RETRY_OBSERVED,
            payload=build_runtime_auto_retry_observed_payload(
                attempt=attempt,
                max_attempts=max_attempts,
                success=success,
                error_message=final_error,
            ),
        )


__all__ = [
    "CompactionEventEmitter",
    "InteractiveCompactionEventEmitter",
    "TaskRunCompactionEventEmitter",
]
