"""Protocol for the TaskRunService surface used by the auto-run driver."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from cli.core.taskrun_projection import TaskRunProjectionResult
from cli.core.taskrun_step import (
    TaskRunRuntimeOptions,
    TaskRunStepOutcome,
    TaskRunStepRunner,
)
from storage.taskrun_repository import (
    TaskEventRecord,
    TaskRunRecord,
    TaskRunRepository,
    TaskStepRecord,
)


class TaskRunResultLike(Protocol):
    task_run: TaskRunRecord
    projection: TaskRunProjectionResult
    events: list[TaskEventRecord]
    steps: list[TaskStepRecord]
    step: TaskStepRecord | None
    exit_code: int


class TaskRunServiceInternals(Protocol):
    repository: TaskRunRepository
    clock: Callable[[], datetime]

    def recover_stale_running(self, cwd: str) -> list[TaskRunRecord]: ...

    def _select_task_run(
        self,
        workspace_root: str,
        id_or_prefix: str | None,
    ) -> TaskRunRecord: ...

    def _start_step(
        self,
        record: TaskRunRecord,
        runtime_options: TaskRunRuntimeOptions,
    ) -> tuple[dict[str, object], TaskRunRecord, TaskStepRecord]: ...

    def _run_step_runner(
        self,
        runner: TaskRunStepRunner,
        *,
        task_run: TaskRunRecord,
        step: TaskStepRecord,
        summary: dict[str, object],
        runtime_options: TaskRunRuntimeOptions,
        workspace_root: str,
    ) -> TaskRunStepOutcome: ...

    def _finalize_step(
        self,
        *,
        task_run: TaskRunRecord,
        step: TaskStepRecord,
        previous_status: str,
        outcome: TaskRunStepOutcome,
        runtime_options: TaskRunRuntimeOptions,
        rebuild_projection: bool = True,
    ) -> TaskRunResultLike: ...

    def _summarize_and_project(
        self,
        record: TaskRunRecord,
        *,
        update_summary: bool = True,
        rebuild_projection: bool = True,
    ) -> TaskRunResultLike: ...

    def _now_iso(self) -> str: ...
