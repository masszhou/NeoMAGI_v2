"""D14: compaction event emitters keep interactive shape AND wire derived
``task_runtime_*_observed`` events for the headless TaskRun path."""

from __future__ import annotations

from typing import Any

from cli.core.compaction.models import CompactionResult
from cli.core.compaction.service import CompactionAppendResult
from cli.core.compaction_event_emitter import (
    InteractiveCompactionEventEmitter,
    TaskRunCompactionEventEmitter,
)
from cli.core.session_types import (
    AutoRetryEndEvent,
    AutoRetryStartEvent,
    CompactionEndEvent,
    CompactionStartEvent,
)


class _FakeRepo:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_event(self, **kwargs: Any) -> None:
        self.events.append(dict(kwargs))


def _result(tokens_before: int = 1234) -> CompactionAppendResult:
    summary = CompactionResult(
        summary="summary",
        firstKeptEntryId="entry:1",
        tokensBefore=tokens_before,
        tokensAfter=tokens_before // 2,
        reason="manual",
    )
    return CompactionAppendResult(entry=None, result=summary)


def test_interactive_emitter_emits_session_event_shapes() -> None:
    events: list[Any] = []
    emitter = InteractiveCompactionEventEmitter(events.append)
    emitter.compaction_started(reason="manual")
    emitter.compaction_succeeded(reason="manual", result=_result(), will_retry=False)
    emitter.compaction_failed(reason="manual", error=RuntimeError("boom"))
    emitter.auto_retry_started(
        attempt=2,
        max_attempts=3,
        delay_ms=10,
        error_message="overflow",
    )
    emitter.auto_retry_finished(attempt=2, max_attempts=3, success=True)

    types = [type(event) for event in events]
    assert types == [
        CompactionStartEvent,
        CompactionEndEvent,
        CompactionEndEvent,
        AutoRetryStartEvent,
        AutoRetryEndEvent,
    ]
    assert events[0].reason == "manual"
    assert events[1].aborted is False and events[1].will_retry is False
    assert events[2].aborted is True and events[2].error_message == "boom"
    assert events[3].attempt == 2 and events[3].delay_ms == 10
    assert events[4].success is True


def test_taskrun_emitter_writes_derived_events_for_observed_outcomes() -> None:
    repo = _FakeRepo()
    emitter = TaskRunCompactionEventEmitter(
        task_repository=repo,
        task_run_id="tr-1",
        step_id="step-1",
    )
    emitter.compaction_started(reason="manual")  # silent on start by design.
    emitter.compaction_succeeded(reason="threshold", result=_result(tokens_before=5000), will_retry=True)
    emitter.compaction_failed(reason="overflow", error=RuntimeError("hook cancelled"))
    emitter.auto_retry_started(attempt=2, max_attempts=3)  # silent on start.
    emitter.auto_retry_finished(attempt=2, max_attempts=3, success=False, final_error="still over")

    types = [event["event_type"] for event in repo.events]
    assert types == [
        "task_runtime_compaction_observed",
        "task_runtime_compaction_observed",
        "task_runtime_auto_retry_observed",
    ]

    # Each event carries payload_version + the derived shape.
    success_payload = repo.events[0]["payload"]
    assert success_payload["payload_version"] == 1
    assert success_payload["reason"] == "threshold"
    assert success_payload["outcome"] == "success"
    assert success_payload["will_retry"] is True
    assert success_payload["tokens_before"] == 5000

    failed_payload = repo.events[1]["payload"]
    assert failed_payload["outcome"] == "failed"
    assert failed_payload["will_retry"] is False
    assert failed_payload["error_message"] == "hook cancelled"

    retry_payload = repo.events[2]["payload"]
    assert retry_payload["attempt"] == 2
    assert retry_payload["max_attempts"] == 3
    assert retry_payload["success"] is False
    assert retry_payload["error_message"] == "still over"
