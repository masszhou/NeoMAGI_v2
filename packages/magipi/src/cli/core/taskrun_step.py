"""TaskRun manual step service types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from ai_provider.types import CacheRetention, ThinkingLevel
from cli.cli_args import DEFAULT_MODEL_REF
from storage.taskrun_repository import TaskRunRecord, TaskStepRecord


STEP_INSTRUCTION = "Take exactly one bounded step toward the TaskRun goal."


def _never_cancel_requested() -> bool:
    return False


@dataclass(frozen=True, slots=True)
class TaskRunRuntimeOptions:
    model_ref: str = DEFAULT_MODEL_REF
    thinking_level: ThinkingLevel = "off"
    cache_retention: CacheRetention | None = None


@dataclass(frozen=True, slots=True)
class TaskRunStepContext:
    task_run: TaskRunRecord
    step: TaskStepRecord
    summary: dict[str, object]
    runtime_options: TaskRunRuntimeOptions
    workspace_root: str
    heartbeat: Callable[[], None]
    cancel_requested: Callable[[], bool] = _never_cancel_requested


@dataclass(frozen=True, slots=True)
class TaskRunStepOutcome:
    status: str
    assistant_text: str | None = None
    run_id: str | None = None
    tool_count: int = 0
    permission_decision_count: int = 0
    block_reason: str | None = None
    error_message: str | None = None
    next_action: str | None = None
    finalize_errors: list[dict[str, str]] = field(default_factory=list)
    verification_state: str | None = None
    verification_reason: str | None = None
    verification_missing_kinds: tuple[str, ...] = ()
    verification_inconsistent_kinds: tuple[str, ...] = ()


class TaskRunStepRunner(Protocol):
    def run(self, context: TaskRunStepContext) -> TaskRunStepOutcome:
        ...


__all__ = [
    "STEP_INSTRUCTION",
    "TaskRunRuntimeOptions",
    "TaskRunStepContext",
    "TaskRunStepOutcome",
    "TaskRunStepRunner",
]
