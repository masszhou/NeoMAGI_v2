"""Product-layer TaskRun lifecycle semantics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from cli.core.taskrun_projection import (
    TaskRunProjectionResult,
    TaskRunProjectionWriter,
)
from cli.core.taskrun_autorun import (
    TaskRunAutoRunOptions,
    TaskRunAutoRunResult,
    run_taskrun_auto_loop,
)
from cli.core.taskrun_errors import TaskRunServiceError
from cli.core.taskrun_experiment_summary import (
    current_best_experiment,
    experiment_next_action,
    experiment_preview,
)
from cli.core.taskrun_host_contract import (
    TaskRunHostContext,
    event_payload_with_host_context,
    normalize_host_context,
)
from cli.core.taskrun_service_helpers import (
    ambiguous_message as _ambiguous_message,
    cancel_requested_outcome as _cancel_requested_outcome,
    cancelled_outcome as _cancelled_outcome,
    datetime_iso as _datetime_iso,
    failed_outcome as _failed_outcome,
    is_task_run_id_prefix as _is_task_run_id_prefix,
    normalize_step_outcome_status as _normalize_step_outcome_status,
    parse_datetime as _parse_datetime,
    step_input as _step_input,
    step_output as _step_output,
    step_started_payload as _step_started_payload,
    validate_headless_profile as _validate_headless_profile,
    workspace_root as _workspace_root,
)
from cli.core.taskrun_service_finalization import (
    append_run_cancelled_event as _append_run_cancelled_event,
    append_step_finalized_event as _append_step_finalized_event,
    close_cancelled_run as _close_cancelled_run,
    next_task_status_after_step as _next_task_status_after_step,
    update_finalized_step as _update_finalized_step,
    update_run_after_step as _update_run_after_step,
)
from cli.core.taskrun_step import (
    TaskRunRuntimeOptions,
    TaskRunStepContext,
    TaskRunStepOutcome,
    TaskRunStepRunner,
)
from cli.core.taskrun_views import (
    TaskRunEventsResult,
    TaskRunHistoryResult,
    TaskRunListResult,
    TaskRunNextResult,
    build_taskrun_history,
    build_taskrun_list,
    build_taskrun_next,
    step_summary,
    taskrun_next_action,
)
from cli.core.taskrun_workspace_state import workspace_state
from policy.permission_profiles import (
    PermissionProfileError,
    build_permission_profile_snapshot,
    normalize_permission_profile_snapshot,
)
from policy.audit import AuditSink
from storage.ids import is_db_uuid
from storage.taskrun_repository import (
    TERMINAL_TASKRUN_STATUSES,
    TaskEventRecord,
    TaskRunCreateRequest,
    TaskRunRecord,
    TaskRunRepository,
    TaskStepRecord,
)


STALE_RUNNING_THRESHOLD = timedelta(minutes=30)
DEFAULT_PERMISSION_PROFILE = build_permission_profile_snapshot("interactive")
DEFAULT_NEXT_ACTION = "Run `magipi taskrun step` to execute the next manual step."


@dataclass(frozen=True, slots=True)
class TaskRunResult:
    task_run: TaskRunRecord
    projection: TaskRunProjectionResult
    events: list[TaskEventRecord]
    steps: list[TaskStepRecord]
    step: TaskStepRecord | None = None
    exit_code: int = 0

    @property
    def summary(self) -> dict[str, object]:
        return dict(self.task_run.summary)


class TaskRunService:
    def __init__(
        self,
        repository: TaskRunRepository,
        *,
        projection_writer: TaskRunProjectionWriter | None = None,
        clock: Callable[[], datetime] | None = None,
        stale_threshold: timedelta = STALE_RUNNING_THRESHOLD,
        host_command_audit_sink: AuditSink | None = None,
    ) -> None:
        self.repository = repository
        self.projection_writer = projection_writer or TaskRunProjectionWriter()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.stale_threshold = stale_threshold
        self.host_command_audit_sink = host_command_audit_sink

    def start(
        self,
        goal: str,
        cwd: str | Path,
        *,
        permission_profile: Mapping[str, Any] | None = None,
        host_context: TaskRunHostContext | Mapping[str, object] | None = None,
    ) -> TaskRunResult:
        host_context = normalize_host_context(host_context)
        goal = goal.strip()
        if not goal:
            raise TaskRunServiceError("TaskRun goal must not be empty")
        workspace_root = _workspace_root(cwd)
        try:
            profile_snapshot = normalize_permission_profile_snapshot(
                permission_profile or DEFAULT_PERMISSION_PROFILE
            )
        except PermissionProfileError as exc:
            raise TaskRunServiceError(str(exc)) from exc
        self.recover_stale_running(workspace_root)
        record = self.repository.create_task_run(
            TaskRunCreateRequest(
                workspace_root=workspace_root,
                goal=goal,
                permission_profile=profile_snapshot,
            )
        )
        self.repository.append_event(
            task_run_id=record.id,
            event_type="task_run_started",
            payload=event_payload_with_host_context(
                {
                    "goal": record.goal,
                    "status": record.status,
                    "workspace_root": record.workspace_root,
                    "agent_session_id": record.agent_session_id,
                    "permission_profile": record.permission_profile,
                },
                host_context,
            ),
        )
        return self._summarize_and_project(record)

    def status(self, id_or_prefix: str | None, cwd: str | Path) -> TaskRunResult:
        workspace_root = _workspace_root(cwd)
        self.recover_stale_running(workspace_root)
        record = self._select_task_run(workspace_root, id_or_prefix)
        return self._summarize_and_project(
            record,
            update_summary=False,
            rebuild_projection=False,
        )

    def summary(self, id_or_prefix: str | None, cwd: str | Path) -> TaskRunResult:
        workspace_root = _workspace_root(cwd)
        self.recover_stale_running(workspace_root)
        record = self._select_task_run(workspace_root, id_or_prefix)
        return self._summarize_and_project(record)

    def list(self, cwd: str | Path) -> TaskRunListResult:
        workspace_root = _workspace_root(cwd)
        self.recover_stale_running(workspace_root)
        records = self.repository.list_task_runs_for_workspace(
            workspace_root,
            include_terminal=True,
        )
        return build_taskrun_list(records)

    def history(self, id_or_prefix: str | None, cwd: str | Path) -> TaskRunHistoryResult:
        workspace_root = _workspace_root(cwd)
        self.recover_stale_running(workspace_root)
        record = self._select_task_run(workspace_root, id_or_prefix)
        steps = self.repository.list_steps(record.id)
        events = self.repository.list_events(record.id)
        permission_decisions = self.repository.list_permission_decisions(record.id)
        experiments = self.repository.list_experiments(record.id)
        return build_taskrun_history(
            record,
            steps,
            events,
            permission_decisions,
            experiments,
            self._build_summary(record, steps),
        )

    def next(self, id_or_prefix: str | None, cwd: str | Path) -> TaskRunNextResult:
        workspace_root = _workspace_root(cwd)
        self.recover_stale_running(workspace_root)
        record = self._select_task_run(workspace_root, id_or_prefix)
        steps = self.repository.list_steps(record.id)
        events = self.repository.list_events(record.id)
        return build_taskrun_next(
            record,
            steps,
            events,
            self._build_summary(record, steps),
            DEFAULT_PERMISSION_PROFILE,
        )

    def events(
        self,
        id_or_prefix: str | None,
        cwd: str | Path,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> TaskRunEventsResult:
        workspace_root = _workspace_root(cwd)
        self.recover_stale_running(workspace_root)
        record = self._select_task_run(workspace_root, id_or_prefix)
        return TaskRunEventsResult(
            task_run=record,
            events=self.repository.list_events(
                record.id,
                after_event_id=after_event_id,
                limit=limit,
            ),
        )

    def step(
        self,
        id_or_prefix: str | None,
        cwd: str | Path,
        *,
        runtime_options: TaskRunRuntimeOptions | None = None,
        runner: TaskRunStepRunner,
        host_context: TaskRunHostContext | Mapping[str, object] | None = None,
    ) -> TaskRunResult:
        host_context = normalize_host_context(host_context)
        workspace_root = _workspace_root(cwd)
        runtime_options = runtime_options or TaskRunRuntimeOptions()
        self.recover_stale_running(workspace_root)
        record = self._select_task_run_for_step(workspace_root, id_or_prefix)
        self._validate_step_ready(record, workspace_root, explicit=bool(id_or_prefix))
        pre_summary, task_run, step = self._start_step(
            record,
            runtime_options,
            host_context=host_context,
        )
        outcome = self._run_step_runner(
            runner,
            task_run=task_run,
            step=step,
            summary=pre_summary,
            runtime_options=runtime_options,
            workspace_root=workspace_root,
        )
        return self._finalize_step(
            task_run=task_run,
            step=step,
            previous_status=record.status,
            outcome=outcome,
            runtime_options=runtime_options,
        )

    def run(
        self,
        id_or_prefix: str | None,
        cwd: str | Path,
        *,
        options: TaskRunAutoRunOptions,
        runner: TaskRunStepRunner,
        permission_profile: Mapping[str, Any] | None = None,
        host_context: TaskRunHostContext | Mapping[str, object] | None = None,
    ) -> TaskRunAutoRunResult:
        return run_taskrun_auto_loop(
            self,
            id_or_prefix,
            _workspace_root(cwd),
            options=options,
            runner=runner,
            permission_profile=permission_profile,
            host_context=host_context,
        )

    def close(
        self,
        id_or_prefix: str | None,
        cwd: str | Path,
        *,
        host_context: TaskRunHostContext | Mapping[str, object] | None = None,
    ) -> TaskRunResult:
        host_context = normalize_host_context(host_context)
        workspace_root = _workspace_root(cwd)
        self.recover_stale_running(workspace_root)
        record = self._select_task_run(workspace_root, id_or_prefix)
        if record.status not in TERMINAL_TASKRUN_STATUSES:
            previous_status = record.status
            if previous_status == "running":
                raise TaskRunServiceError(
                    f"cannot close active running TaskRun {record.id}; "
                    "wait for stale recovery or cancel the running step first"
                )
            running_steps = [
                step for step in self.repository.list_steps(record.id) if step.status == "running"
            ]
            if running_steps:
                raise TaskRunServiceError(
                    f"cannot close TaskRun {record.id}: running step exists"
                )
            now = self._now_iso()
            record = self.repository.update_task_run_status(
                record.id,
                status="cancelled",
                heartbeat_at=None,
                closed_at=now,
                updated_at=now,
            )
            self.repository.append_event(
                task_run_id=record.id,
                event_type="task_run_closed",
                payload=event_payload_with_host_context(
                    {
                        "previous_status": previous_status,
                        "final_status": record.status,
                        "closed_at": now,
                    },
                    host_context,
                ),
                occurred_at=now,
            )
        return self._summarize_and_project(record)

    def cancel(
        self,
        id_or_prefix: str | None,
        cwd: str | Path,
        *,
        host_context: TaskRunHostContext | Mapping[str, object] | None = None,
    ) -> TaskRunResult:
        host_context = normalize_host_context(host_context)
        workspace_root = _workspace_root(cwd)
        self.recover_stale_running(workspace_root)
        record = self._select_task_run(workspace_root, id_or_prefix)
        if record.status in TERMINAL_TASKRUN_STATUSES:
            return self._summarize_and_project(
                record,
                update_summary=False,
                rebuild_projection=False,
            )
        now = self._now_iso()
        if record.status == "running":
            if not self._cancel_requested(record.id, record.current_step_id):
                self.repository.append_event(
                    task_run_id=record.id,
                    step_id=record.current_step_id,
                    event_type="task_run_cancel_requested",
                    payload=event_payload_with_host_context(
                        {
                            "previous_status": record.status,
                            "current_step_id": record.current_step_id,
                            "requested_at": now,
                        },
                        host_context,
                    ),
                    occurred_at=now,
                )
            return self._summarize_and_project(record)

        previous_status = record.status
        record = self.repository.update_task_run_status(
            record.id,
            status="cancelled",
            heartbeat_at=None,
            closed_at=now,
            updated_at=now,
        )
        self.repository.append_event(
            task_run_id=record.id,
            event_type="task_run_cancelled",
            payload=event_payload_with_host_context(
                {
                    "previous_status": previous_status,
                    "final_status": record.status,
                    "cancelled_at": now,
                },
                host_context,
            ),
            occurred_at=now,
        )
        return self._summarize_and_project(record)

    def recover_stale_running(self, cwd: str | Path) -> list[TaskRunRecord]:
        workspace_root = _workspace_root(cwd)
        now_dt = self.clock()
        recovered: list[TaskRunRecord] = []
        for record in self.repository.list_running_task_runs(workspace_root):
            if not self._is_stale(record, now_dt):
                continue
            recovered_at = _datetime_iso(now_dt)
            last_heartbeat = record.heartbeat_at
            if record.current_step_id is not None:
                self.repository.update_step_status(
                    record.current_step_id,
                    status="blocked",
                    output={
                        "reason": "running heartbeat exceeded stale threshold",
                        "last_heartbeat": last_heartbeat,
                        "recovery_time": recovered_at,
                    },
                    conclusion="stale running step blocked by recovery",
                    ended_at=recovered_at,
                )
            blocked = self.repository.update_task_run_step_state(
                record.id,
                status="blocked",
                current_step_id=None,
                heartbeat_at=last_heartbeat,
                updated_at=recovered_at,
            )
            self.repository.append_event(
                task_run_id=record.id,
                event_type="task_run_blocked_stale",
                payload={
                    "previous_status": record.status,
                    "last_heartbeat": last_heartbeat,
                    "recovery_time": recovered_at,
                    "reason": "running heartbeat exceeded stale threshold",
                },
                occurred_at=recovered_at,
            )
            summary = self._build_summary(blocked, self.repository.list_steps(blocked.id))
            blocked = self.repository.update_task_run_summary(
                blocked.id,
                summary,
                updated_at=recovered_at,
            )
            recovered.append(blocked)
        return recovered

    def _select_task_run(
        self,
        workspace_root: str,
        id_or_prefix: str | None,
    ) -> TaskRunRecord:
        if id_or_prefix:
            ref = id_or_prefix.strip()
            if not _is_task_run_id_prefix(ref):
                raise TaskRunServiceError(f"invalid TaskRun id prefix: {ref}")
            matches = self.repository.find_task_runs_by_prefix(workspace_root, ref)
            if is_db_uuid(ref):
                matches = [match for match in matches if match.id == ref]
            if not matches:
                raise TaskRunServiceError(f"unknown TaskRun in this workspace: {ref}")
            if len(matches) > 1:
                raise TaskRunServiceError(
                    _ambiguous_message(f"ambiguous TaskRun id prefix: {ref}", matches)
                )
            return matches[0]

        candidates = self.repository.list_task_runs_for_workspace(
            workspace_root,
            include_terminal=False,
        )
        if not candidates:
            raise TaskRunServiceError("no non-terminal TaskRun in this workspace")
        if len(candidates) > 1:
            raise TaskRunServiceError(
                _ambiguous_message("multiple non-terminal TaskRuns in this workspace", candidates)
            )
        return candidates[0]

    def _select_task_run_for_step(
        self,
        workspace_root: str,
        id_or_prefix: str | None,
    ) -> TaskRunRecord:
        if id_or_prefix:
            return self._select_task_run(workspace_root, id_or_prefix)
        candidates = [
            record
            for record in self.repository.list_task_runs_for_workspace(
                workspace_root,
                include_terminal=False,
            )
            if record.status == "pending"
        ]
        if not candidates:
            raise TaskRunServiceError(
                "no pending TaskRun in this workspace; pass an id to step a blocked TaskRun"
            )
        if len(candidates) > 1:
            raise TaskRunServiceError(
                _ambiguous_message("multiple pending TaskRuns in this workspace", candidates)
            )
        return candidates[0]

    def _validate_step_ready(
        self,
        record: TaskRunRecord,
        workspace_root: str,
        *,
        explicit: bool,
    ) -> None:
        if record.status in TERMINAL_TASKRUN_STATUSES:
            raise TaskRunServiceError(f"cannot step terminal TaskRun {record.id}: {record.status}")
        if record.status == "running":
            raise TaskRunServiceError(f"cannot step active running TaskRun {record.id}")
        if record.status == "blocked" and not explicit:
            raise TaskRunServiceError(
                f"blocked TaskRun {record.id} requires explicit id/prefix to step"
            )
        _validate_headless_profile(record.permission_profile, command="step")
        self._validate_no_other_running(record, workspace_root)

    def _validate_no_other_running(
        self,
        record: TaskRunRecord,
        workspace_root: str,
    ) -> None:
        running = [
            candidate
            for candidate in self.repository.list_running_task_runs(workspace_root)
            if candidate.id != record.id
        ]
        if running:
            raise TaskRunServiceError(
                _ambiguous_message("another TaskRun is already running in this workspace", running)
            )

    def _start_step(
        self,
        record: TaskRunRecord,
        runtime_options: TaskRunRuntimeOptions,
        *,
        host_context: TaskRunHostContext | Mapping[str, object] | None = None,
    ) -> tuple[dict[str, object], TaskRunRecord, TaskStepRecord]:
        steps = self.repository.list_steps(record.id)
        pre_summary = self._build_summary(record, steps)
        started_at = self._now_iso()
        step_start = self.repository.create_running_step(
            record.id,
            title=f"Step {len(steps) + 1}",
            input=_step_input(record, pre_summary, runtime_options),
            started_at=started_at,
            start_event_payload=_step_started_payload(
                record.status,
                runtime_options,
                host_context=host_context,
            ),
        )
        return pre_summary, step_start.task_run, step_start.step

    def _run_step_runner(
        self,
        runner: TaskRunStepRunner,
        *,
        task_run: TaskRunRecord,
        step: TaskStepRecord,
        summary: dict[str, object],
        runtime_options: TaskRunRuntimeOptions,
        workspace_root: str,
    ) -> TaskRunStepOutcome:
        def heartbeat() -> None:
            self.repository.lease_running_task_run(
                task_run.id,
                step_id=step.id,
                heartbeat_at=self._now_iso(),
            )

        def cancel_requested() -> bool:
            return self._cancel_requested(task_run.id, step.id)

        try:
            outcome = runner.run(
                TaskRunStepContext(
                    task_run=task_run,
                    step=step,
                    summary=summary,
                    runtime_options=runtime_options,
                    workspace_root=workspace_root,
                    heartbeat=heartbeat,
                    cancel_requested=cancel_requested,
                )
            )
            if cancel_requested() and outcome.status != "cancelled":
                return _cancel_requested_outcome(task_run.id)
            return outcome
        except KeyboardInterrupt:
            return _cancelled_outcome(task_run.id)
        except Exception as exc:
            return _failed_outcome(task_run.id, exc)

    def _finalize_step(
        self,
        *,
        task_run: TaskRunRecord,
        step: TaskStepRecord,
        previous_status: str,
        outcome: TaskRunStepOutcome,
        runtime_options: TaskRunRuntimeOptions,
        rebuild_projection: bool = True,
    ) -> TaskRunResult:
        status = _normalize_step_outcome_status(outcome.status)
        ended_at = self._now_iso()
        output = _step_output(outcome, task_run.id, status)
        updated_step = _update_finalized_step(
            self.repository,
            step,
            status,
            output,
            outcome,
            ended_at,
        )
        cancel_requested = status == "cancelled" and self._cancel_requested(
            task_run.id,
            step.id,
        )
        next_task_status = _next_task_status_after_step(status, cancel_requested)
        updated_run = _update_run_after_step(
            self.repository,
            task_run,
            next_task_status=next_task_status,
            ended_at=ended_at,
        )
        if cancel_requested:
            updated_run = _close_cancelled_run(self.repository, updated_run, ended_at)
        _append_step_finalized_event(
            self.repository,
            updated_run,
            updated_step,
            previous_status=previous_status,
            next_task_status=next_task_status,
            runtime_options=runtime_options,
            outcome=outcome,
            output=output,
            ended_at=ended_at,
        )
        if cancel_requested:
            _append_run_cancelled_event(
                self.repository,
                updated_run,
                updated_step,
                ended_at,
            )
        result = self._summarize_and_project(updated_run, rebuild_projection=rebuild_projection)
        matching_step = next(
            (candidate for candidate in result.steps if candidate.id == updated_step.id),
            updated_step,
        )
        exit_code = 130 if status == "cancelled" else 1 if status == "failed" else 0
        return replace(result, step=matching_step, exit_code=exit_code)

    def _summarize_and_project(
        self,
        record: TaskRunRecord,
        *,
        update_summary: bool = True,
        rebuild_projection: bool = True,
    ) -> TaskRunResult:
        steps = self.repository.list_steps(record.id)
        summary = self._build_summary(record, steps)
        if update_summary and record.summary != summary:
            record = self.repository.update_task_run_summary(record.id, summary)
            self.repository.append_event(
                task_run_id=record.id,
                event_type="task_run_summary_updated",
                payload={"summary": summary},
            )
        elif record.summary != summary:
            record = replace(record, summary=summary)
        projection = self.projection_writer.result_for(
            workspace_root=record.workspace_root,
            task_run_id=record.id,
        )
        if rebuild_projection:
            self.repository.append_event(
                task_run_id=record.id,
                event_type="task_run_projection_rebuilt",
                payload={"projection_path": str(projection.path)},
            )
        events = self.repository.list_events(record.id)
        if rebuild_projection:
            projection = self.projection_writer.rebuild(
                task_run=record,
                events=events,
                steps=steps,
            )
        return TaskRunResult(
            task_run=record,
            projection=projection,
            events=events,
            steps=steps,
        )

    def _build_summary(
        self,
        record: TaskRunRecord,
        steps: list[TaskStepRecord],
    ) -> dict[str, object]:
        projection_path = self.projection_writer.projection_path(
            record.workspace_root,
            record.id,
        )
        experiments = self.repository.list_experiments(record.id)
        current_step = next(
            (step for step in steps if step.id == record.current_step_id),
            None,
        )
        completed_steps = [step for step in steps if step.status == "done"]
        blocked_steps = [step for step in steps if step.status == "blocked"]
        attempted = [
            step
            for step in steps
            if step.status in {"done", "failed", "blocked", "cancelled"}
        ]
        last_attempt = attempted[-1] if attempted else None
        latest_experiment = experiments[-1] if experiments else None
        last_attempt_summary = step_summary(last_attempt)
        if last_attempt_summary is not None and latest_experiment is not None:
            last_attempt_summary["experiment"] = experiment_preview(latest_experiment)
        return {
            "goal": record.goal,
            "status": record.status,
            "current_step": step_summary(current_step),
            "completed_steps": [step_summary(step) for step in completed_steps],
            "blocked_steps": [step_summary(step) for step in blocked_steps],
            "last_attempt": last_attempt_summary,
            "current_best": current_best_experiment(experiments),
            "workspace_state": workspace_state(record.workspace_root, projection_path),
            "permission_profile": dict(record.permission_profile or DEFAULT_PERMISSION_PROFILE),
            "next_action": experiment_next_action(record, latest_experiment)
            or taskrun_next_action(record, last_attempt),
        }

    def _is_stale(self, record: TaskRunRecord, now_dt: datetime) -> bool:
        if record.status != "running":
            return False
        heartbeat = _parse_datetime(record.heartbeat_at)
        if heartbeat is None:
            return True
        return heartbeat <= now_dt - self.stale_threshold

    def _cancel_requested(self, task_run_id: str, step_id: str | None = None) -> bool:
        return self.repository.cancel_requested_exists(task_run_id, step_id=step_id)

    def _now_iso(self) -> str:
        return _datetime_iso(self.clock())
