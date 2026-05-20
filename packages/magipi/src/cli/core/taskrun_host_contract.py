"""Internal TaskRun host-readiness contract.

This module is an implementation seam for P2 conformance tests and future P3
extraction work. It is not a public Gateway, transport, or channel API.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from cli.core.taskrun_event_payloads import (
    DERIVED_EVENT_TYPES,
    DERIVED_RUNTIME_EVENT_TYPES,
    DERIVED_STEP_SUMMARY_EVENT_TYPES,
    DERIVED_TIER1_ONLY_EVENT_TYPES,
    DERIVED_TOOL_DETAIL_EVENT_TYPES,
    PAYLOAD_VERSIONS,
)
from storage.taskrun_repository import TaskEventRecord, TaskStepRecord


TaskRunHostSource = Literal["cli", "tui", "test", "host"]
TaskRunOperationStatus = Literal[
    "implemented",
    "covered_by",
    "deferred_to_p3",
    "deferred_to_p2_followup",
]

CALLER_PROVENANCE_PAYLOAD_KEY: Final[str] = "caller_provenance"
TASKRUN_HOST_SOURCES: Final[frozenset[str]] = frozenset({"cli", "tui", "test", "host"})
_HOST_CONTEXT_FIELDS: Final[frozenset[str]] = frozenset(
    {"source", "request_id", "actor"}
)


@dataclass(frozen=True, slots=True)
class TaskRunHostContext:
    source: TaskRunHostSource = "cli"
    request_id: str | None = None
    actor: str | None = None

    def __post_init__(self) -> None:
        if self.source not in TASKRUN_HOST_SOURCES:
            raise ValueError(f"unknown TaskRun host context source: {self.source!r}")
        _validate_optional_string("request_id", self.request_id)
        _validate_optional_string("actor", self.actor)

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> TaskRunHostContext:
        unknown = set(values) - _HOST_CONTEXT_FIELDS
        if unknown:
            keys = ", ".join(sorted(unknown))
            raise ValueError(f"unknown TaskRun host context field(s): {keys}")
        source = values.get("source", "cli")
        if not isinstance(source, str):
            raise ValueError("TaskRun host context source must be a string")
        request_id = _optional_string_from_mapping(values, "request_id")
        actor = _optional_string_from_mapping(values, "actor")
        return cls(
            source=_source(source),
            request_id=request_id,
            actor=actor,
        )

    def to_event_payload(self) -> dict[str, str]:
        payload = {"source": self.source}
        if self.request_id:
            payload["request_id"] = self.request_id
        if self.actor:
            payload["actor"] = self.actor
        return payload


@dataclass(frozen=True, slots=True)
class TaskRunOperationManifestItem:
    operation: str
    status: TaskRunOperationStatus
    evidence_or_reason: str


TASKRUN_OPERATION_MANIFEST: Final[tuple[TaskRunOperationManifestItem, ...]] = (
    TaskRunOperationManifestItem(
        "start",
        "implemented",
        "TaskRunService.start / magipi taskrun start",
    ),
    TaskRunOperationManifestItem(
        "status",
        "implemented",
        "TaskRunService.status / magipi taskrun status; stale recovery is the write exception",
    ),
    TaskRunOperationManifestItem(
        "summary",
        "implemented",
        "TaskRunService.summary / magipi taskrun summary; DB-backed projection rebuild",
    ),
    TaskRunOperationManifestItem(
        "list",
        "implemented",
        "TaskRunService.list / magipi taskrun list; workspace-scoped read view",
    ),
    TaskRunOperationManifestItem(
        "history",
        "implemented",
        "TaskRunService.history / magipi taskrun history; key events and step timeline",
    ),
    TaskRunOperationManifestItem(
        "next",
        "implemented",
        "TaskRunService.next / magipi taskrun next; deterministic next-step snapshot",
    ),
    TaskRunOperationManifestItem(
        "events",
        "implemented",
        "TaskRunService.events / magipi taskrun events; DB event JSONL source",
    ),
    TaskRunOperationManifestItem(
        "step",
        "implemented",
        "TaskRunService.step / magipi taskrun step; one bounded headless step",
    ),
    TaskRunOperationManifestItem(
        "run",
        "implemented",
        "TaskRunService.run / magipi taskrun run; bounded foreground auto loop",
    ),
    TaskRunOperationManifestItem(
        "close",
        "implemented",
        "TaskRunService.close / magipi taskrun close; local close semantics",
    ),
    TaskRunOperationManifestItem(
        "set_permission_profile",
        "covered_by",
        "covered by magipi taskrun run --permission profile persistence path",
    ),
    TaskRunOperationManifestItem(
        "resume",
        "covered_by",
        "covered by explicit magipi taskrun step/run <id> for blocked TaskRuns",
    ),
    TaskRunOperationManifestItem(
        "cancel",
        "implemented",
        "TaskRunService.cancel / magipi taskrun cancel; event-backed local interrupt request",
    ),
    TaskRunOperationManifestItem(
        "archive",
        "deferred_to_p3",
        "requires closeout/archive policy beyond P2 local close semantics",
    ),
    TaskRunOperationManifestItem(
        "cleanup",
        "deferred_to_p3",
        "requires work-persisted proof and host cleanup policy",
    ),
    TaskRunOperationManifestItem(
        "compaction_in_headless_step",
        "implemented",
        "TaskRunHeadlessRunner wires D14 compaction and auto-retry derived events",
    ),
)
TASKRUN_OPERATION_MANIFEST_BY_NAME: Final[dict[str, TaskRunOperationManifestItem]] = {
    item.operation: item for item in TASKRUN_OPERATION_MANIFEST
}


@dataclass(frozen=True, slots=True)
class TaskRunOperationSnapshot:
    operation: str
    task_run_id: str
    status: str
    step_id: str | None
    exit_code: int
    summary: dict[str, Any]
    step_verification_state: dict[str, Any] | None
    projection_path: str | None
    stop_reason: str | None
    event_count: int
    iteration_count: int | None = None
    experiment_attempt_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation": self.operation,
            "task_run_id": self.task_run_id,
            "status": self.status,
            "step_id": self.step_id,
            "exit_code": self.exit_code,
            "summary": dict(self.summary),
            "step_verification_state": (
                dict(self.step_verification_state)
                if self.step_verification_state is not None
                else None
            ),
            "projection_path": self.projection_path,
            "stop_reason": self.stop_reason,
            "event_count": self.event_count,
        }
        if self.iteration_count is not None:
            payload["iteration_count"] = self.iteration_count
        if self.experiment_attempt_count is not None:
            payload["experiment_attempt_count"] = self.experiment_attempt_count
        return payload


@dataclass(frozen=True, slots=True)
class TaskRunEventSnapshot:
    id: str
    task_run_id: str
    step_id: str | None
    event_type: str
    payload: dict[str, Any]
    occurred_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_run_id": self.task_run_id,
            "step_id": self.step_id,
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "occurred_at": self.occurred_at,
        }


def normalize_host_context(
    host_context: TaskRunHostContext | Mapping[str, object] | None,
) -> TaskRunHostContext:
    if host_context is None:
        return TaskRunHostContext()
    if isinstance(host_context, TaskRunHostContext):
        return host_context
    if isinstance(host_context, Mapping):
        return TaskRunHostContext.from_mapping(host_context)
    raise TypeError("TaskRun host context must be a TaskRunHostContext or mapping")


def event_payload_with_host_context(
    payload: Mapping[str, object],
    host_context: TaskRunHostContext | Mapping[str, object] | None,
) -> dict[str, object]:
    normalized = normalize_host_context(host_context)
    event_payload = dict(payload)
    event_payload[CALLER_PROVENANCE_PAYLOAD_KEY] = normalized.to_event_payload()
    return event_payload


def operation_snapshot(
    operation: str,
    result: object,
) -> TaskRunOperationSnapshot:
    task_run = getattr(result, "task_run")
    step = _snapshot_step(result)
    projection = getattr(result, "projection", None)
    events = list(getattr(result, "events", []) or [])
    iterations = getattr(result, "iterations", None)
    experiment_attempts = getattr(result, "experiment_attempts", None)
    return TaskRunOperationSnapshot(
        operation=operation,
        task_run_id=task_run.id,
        status=task_run.status,
        step_id=step.id if step is not None else None,
        exit_code=int(getattr(result, "exit_code", 0) or 0),
        summary=_summary_from_result(result, task_run),
        step_verification_state=_step_verification_state(step),
        projection_path=str(projection.path) if projection is not None else None,
        stop_reason=getattr(result, "stop_reason", None),
        event_count=len(events),
        iteration_count=len(iterations) if iterations is not None else None,
        experiment_attempt_count=(
            len(experiment_attempts) if experiment_attempts is not None else None
        ),
    )


def event_snapshot(event: TaskEventRecord) -> TaskRunEventSnapshot:
    return TaskRunEventSnapshot(
        id=event.id,
        task_run_id=event.task_run_id,
        step_id=event.step_id,
        event_type=event.event_type,
        payload=dict(event.payload),
        occurred_at=event.occurred_at,
    )


def event_snapshots(result: object) -> list[TaskRunEventSnapshot]:
    return [event_snapshot(event) for event in list(getattr(result, "events", []) or [])]


def _snapshot_step(result: object) -> TaskStepRecord | None:
    step = getattr(result, "step", None)
    if step is not None:
        return step
    iterations = getattr(result, "iterations", None)
    if iterations:
        return iterations[-1].step
    return None


def _summary_from_result(result: object, task_run: object) -> dict[str, Any]:
    summary = getattr(result, "summary", None)
    if isinstance(summary, Mapping):
        return dict(summary)
    task_run_summary = getattr(task_run, "summary", None)
    return dict(task_run_summary) if isinstance(task_run_summary, Mapping) else {}


def _step_verification_state(step: TaskStepRecord | None) -> dict[str, Any] | None:
    if step is None:
        return None
    verification = step.output.get("verification_state")
    if not isinstance(verification, Mapping):
        return None
    state = verification.get("state")
    payload: dict[str, Any] = {"state": state} if state is not None else {}
    reason = verification.get("reason")
    if reason:
        payload["reason"] = reason
    for key in ("missing_kinds", "inconsistent_kinds"):
        values = verification.get(key)
        if values:
            payload[key] = list(values) if not isinstance(values, str) else [values]
    return payload


def _source(value: str) -> TaskRunHostSource:
    if value not in TASKRUN_HOST_SOURCES:
        raise ValueError(f"unknown TaskRun host context source: {value!r}")
    return value  # type: ignore[return-value]


def _optional_string_from_mapping(
    values: Mapping[str, object],
    key: str,
) -> str | None:
    value = values.get(key)
    _validate_optional_string(key, value)
    return value if isinstance(value, str) and value else None


def _validate_optional_string(name: str, value: object) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"TaskRun host context {name} must be a string when set")


__all__ = [
    "CALLER_PROVENANCE_PAYLOAD_KEY",
    "DERIVED_EVENT_TYPES",
    "DERIVED_RUNTIME_EVENT_TYPES",
    "DERIVED_STEP_SUMMARY_EVENT_TYPES",
    "DERIVED_TIER1_ONLY_EVENT_TYPES",
    "DERIVED_TOOL_DETAIL_EVENT_TYPES",
    "PAYLOAD_VERSIONS",
    "TASKRUN_HOST_SOURCES",
    "TASKRUN_OPERATION_MANIFEST",
    "TASKRUN_OPERATION_MANIFEST_BY_NAME",
    "TaskRunEventSnapshot",
    "TaskRunHostContext",
    "TaskRunOperationManifestItem",
    "TaskRunOperationSnapshot",
    "event_payload_with_host_context",
    "event_snapshot",
    "event_snapshots",
    "normalize_host_context",
    "operation_snapshot",
]
