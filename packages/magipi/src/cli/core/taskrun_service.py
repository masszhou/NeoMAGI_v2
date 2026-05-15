"""Product-layer TaskRun lifecycle semantics."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from cli.core.taskrun_projection import (
    TaskRunProjectionResult,
    TaskRunProjectionWriter,
)
from cli.core.taskrun_step import (
    STEP_INSTRUCTION,
    TaskRunRuntimeOptions,
    TaskRunStepContext,
    TaskRunStepOutcome,
    TaskRunStepRunner,
)
from policy.permission_profiles import (
    PermissionProfileError,
    build_permission_profile_snapshot,
    normalize_permission_profile_snapshot,
)
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


class TaskRunServiceError(RuntimeError):
    """Raised when a product-level TaskRun operation is invalid."""


class TaskRunService:
    def __init__(
        self,
        repository: TaskRunRepository,
        *,
        projection_writer: TaskRunProjectionWriter | None = None,
        clock: Callable[[], datetime] | None = None,
        stale_threshold: timedelta = STALE_RUNNING_THRESHOLD,
    ) -> None:
        self.repository = repository
        self.projection_writer = projection_writer or TaskRunProjectionWriter()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.stale_threshold = stale_threshold

    def start(
        self,
        goal: str,
        cwd: str | Path,
        *,
        permission_profile: Mapping[str, Any] | None = None,
    ) -> TaskRunResult:
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
            payload={
                "goal": record.goal,
                "status": record.status,
                "workspace_root": record.workspace_root,
                "agent_session_id": record.agent_session_id,
                "permission_profile": record.permission_profile,
            },
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

    def step(
        self,
        id_or_prefix: str | None,
        cwd: str | Path,
        *,
        runtime_options: TaskRunRuntimeOptions | None = None,
        runner: TaskRunStepRunner,
    ) -> TaskRunResult:
        workspace_root = _workspace_root(cwd)
        runtime_options = runtime_options or TaskRunRuntimeOptions()
        self.recover_stale_running(workspace_root)
        record = self._select_task_run_for_step(workspace_root, id_or_prefix)
        self._validate_step_ready(record, workspace_root, explicit=bool(id_or_prefix))
        pre_summary, task_run, step = self._start_step(record, runtime_options)
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

    def close(self, id_or_prefix: str | None, cwd: str | Path) -> TaskRunResult:
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
                payload={
                    "previous_status": previous_status,
                    "final_status": record.status,
                    "closed_at": now,
                },
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
        profile = _normalize_profile(record.permission_profile)
        if not bool(profile.get("nonInteractive")):
            raise TaskRunServiceError(
                "taskrun step is headless and cannot use interactive permission profile; "
                "create a TaskRun with --permission guarded or --permission full"
            )
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
    ) -> tuple[dict[str, object], TaskRunRecord, TaskStepRecord]:
        steps = self.repository.list_steps(record.id)
        pre_summary = self._build_summary(record, steps)
        started_at = self._now_iso()
        step_start = self.repository.create_running_step(
            record.id,
            title=f"Step {len(steps) + 1}",
            input=_step_input(record, pre_summary, runtime_options),
            started_at=started_at,
            start_event_payload=_step_started_payload(record.status, runtime_options),
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

        try:
            return runner.run(
                TaskRunStepContext(
                    task_run=task_run,
                    step=step,
                    summary=summary,
                    runtime_options=runtime_options,
                    workspace_root=workspace_root,
                    heartbeat=heartbeat,
                )
            )
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
    ) -> TaskRunResult:
        status = _normalize_step_outcome_status(outcome.status)
        ended_at = self._now_iso()
        output = _step_output(outcome, task_run.id, status)
        conclusion = _step_conclusion(outcome, status)
        updated_step = self.repository.update_step_status(
            step.id,
            status=status,
            output=output,
            conclusion=conclusion,
            ended_at=ended_at,
        )
        next_task_status = "pending" if status == "done" else "blocked"
        updated_run = self.repository.update_task_run_step_state(
            task_run.id,
            status=next_task_status,
            current_step_id=None,
            heartbeat_at=ended_at,
            updated_at=ended_at,
        )
        event_type = {
            "done": "task_step_completed",
            "failed": "task_step_failed",
            "blocked": "task_step_blocked",
            "cancelled": "task_step_cancelled",
        }[status]
        self.repository.append_event(
            task_run_id=updated_run.id,
            step_id=updated_step.id,
            event_type=event_type,
            payload={
                "step_id": updated_step.id,
                "step_index": updated_step.step_index,
                "status_from": "running",
                "status_to": status,
                "task_status_from": previous_status,
                "task_status_to": next_task_status,
                "model_ref": runtime_options.model_ref,
                "run_id": outcome.run_id,
                "reason": output.get("reason"),
            },
            occurred_at=ended_at,
        )
        result = self._summarize_and_project(updated_run)
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
        return {
            "goal": record.goal,
            "status": record.status,
            "current_step": _step_summary(current_step),
            "completed_steps": [_step_summary(step) for step in completed_steps],
            "blocked_steps": [_step_summary(step) for step in blocked_steps],
            "last_attempt": _step_summary(last_attempt),
            "current_best": None,
            "workspace_state": _workspace_state(record.workspace_root, projection_path),
            "permission_profile": dict(record.permission_profile or DEFAULT_PERMISSION_PROFILE),
            "next_action": _next_action(record, last_attempt),
        }

    def _is_stale(self, record: TaskRunRecord, now_dt: datetime) -> bool:
        if record.status != "running":
            return False
        heartbeat = _parse_datetime(record.heartbeat_at)
        if heartbeat is None:
            return True
        return heartbeat <= now_dt - self.stale_threshold

    def _now_iso(self) -> str:
        return _datetime_iso(self.clock())


def _workspace_root(cwd: str | Path) -> str:
    return str(Path(cwd).resolve())


def _datetime_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _step_summary(step: TaskStepRecord | None) -> dict[str, object] | None:
    if step is None:
        return None
    summary = {
        "id": step.id,
        "step_index": step.step_index,
        "title": step.title,
        "status": step.status,
        "conclusion": step.conclusion,
        "started_at": step.started_at,
        "ended_at": step.ended_at,
    }
    if step.output.get("next_action"):
        summary["next_action"] = step.output["next_action"]
    if step.output.get("reason"):
        summary["reason"] = step.output["reason"]
    return summary


def _step_input(
    record: TaskRunRecord,
    summary: Mapping[str, object],
    runtime_options: TaskRunRuntimeOptions,
) -> dict[str, object]:
    return {
        "goal": record.goal,
        "summary": dict(summary),
        "instruction": STEP_INSTRUCTION,
        "permission_profile": dict(record.permission_profile or {}),
        "model": runtime_options.model_ref,
        "thinking_level": runtime_options.thinking_level,
        "cache_retention": runtime_options.cache_retention,
    }


def _step_started_payload(
    previous_status: str,
    runtime_options: TaskRunRuntimeOptions,
) -> dict[str, object]:
    return {
        "status_from": previous_status,
        "model_ref": runtime_options.model_ref,
    }


def _step_output(
    outcome: TaskRunStepOutcome,
    task_run_id: str,
    status: str,
) -> dict[str, object]:
    reason = (
        outcome.block_reason
        if status == "blocked"
        else outcome.error_message if status in {"failed", "cancelled"} else None
    )
    next_action = outcome.next_action or _next_action_for_status(task_run_id, status, reason)
    output: dict[str, object] = {
        "status": status,
        "assistant_text_preview": _preview(outcome.assistant_text),
        "tool_count": outcome.tool_count,
        "permission_decision_count": outcome.permission_decision_count,
        "next_action": next_action,
    }
    if outcome.run_id:
        output["run_id"] = outcome.run_id
    if reason:
        output["reason"] = reason
    if outcome.error_message:
        output["error_message"] = outcome.error_message
    if outcome.block_reason:
        output["block_reason"] = outcome.block_reason
    if outcome.finalize_errors:
        output["finalize_errors"] = list(outcome.finalize_errors)
    return output


def _step_conclusion(outcome: TaskRunStepOutcome, status: str) -> str:
    if status == "done":
        return _preview(outcome.assistant_text) or "manual step completed"
    if status == "blocked":
        return _preview(outcome.block_reason) or "manual step blocked"
    if status == "cancelled":
        return _preview(outcome.error_message) or "manual step cancelled"
    return _preview(outcome.error_message) or "manual step failed"


def _preview(value: str | None, limit: int = 500) -> str:
    if not value:
        return ""
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _normalize_step_outcome_status(status: str) -> str:
    if status not in {"done", "failed", "blocked", "cancelled"}:
        raise TaskRunServiceError(f"invalid step outcome status: {status}")
    return status


def _normalize_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return normalize_permission_profile_snapshot(profile)
    except PermissionProfileError as exc:
        raise TaskRunServiceError(str(exc)) from exc


def _cancelled_outcome(task_run_id: str) -> TaskRunStepOutcome:
    return TaskRunStepOutcome(
        status="cancelled",
        error_message="cancelled by user interrupt",
        next_action=(
            "Resolve the cancellation context, then run "
            f"`magipi taskrun step {task_run_id[:8]}` to continue."
        ),
    )


def _failed_outcome(task_run_id: str, exc: Exception) -> TaskRunStepOutcome:
    return TaskRunStepOutcome(
        status="failed",
        error_message=str(exc),
        next_action=(
            "Inspect the failure, then run "
            f"`magipi taskrun step {task_run_id[:8]}` to retry manually."
        ),
    )


def _next_action(record: TaskRunRecord, last_attempt: TaskStepRecord | None) -> str:
    if record.status == "running" and record.current_step_id:
        return "Wait for the current manual step to finish."
    if record.status in TERMINAL_TASKRUN_STATUSES:
        return "TaskRun is terminal; inspect summary or archive when ready."
    if last_attempt is None:
        return f"Run `magipi taskrun step {record.id[:8]}` to execute the first manual step."
    reason = last_attempt.output.get("reason")
    if last_attempt.status == "done":
        return str(
            last_attempt.output.get("next_action")
            or f"Run `magipi taskrun step {record.id[:8]}` for the next manual step, or close when complete."
        )
    if last_attempt.status == "blocked":
        return _next_action_for_status(record.id, "blocked", str(reason) if reason else None)
    if last_attempt.status == "cancelled":
        return _next_action_for_status(record.id, "cancelled", str(reason) if reason else None)
    return _next_action_for_status(record.id, "failed", str(reason) if reason else None)


def _next_action_for_status(task_run_id: str, status: str, reason: str | None) -> str:
    prefix = f"`magipi taskrun step {task_run_id[:8]}`"
    if status == "done":
        return f"Run {prefix} for the next manual step, or close the TaskRun when complete."
    if status == "blocked":
        detail = f" ({reason})" if reason else ""
        return f"Resolve the blocker{detail}, then run {prefix} to continue."
    if status == "cancelled":
        return f"Review the cancellation, then run {prefix} to continue manually."
    detail = f" ({reason})" if reason else ""
    return f"Inspect the failure{detail}, then run {prefix} to retry manually."


def _workspace_state(workspace_root: str, projection_path: Path) -> dict[str, object]:
    state: dict[str, object] = {
        "workspace_root": workspace_root,
        "projection_path": str(projection_path),
        "git": {"status": "unknown"},
    }
    try:
        result = subprocess.run(
            ["git", "-C", workspace_root, "status", "--short", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return state
    if result.returncode != 0:
        return state
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    state["git"] = {
        "status": "dirty" if lines else "clean",
        "changed_tracked_paths": len(lines),
    }
    return state


def _ambiguous_message(prefix: str, matches: list[TaskRunRecord]) -> str:
    details = [
        f"{record.id} {record.status} {_goal_preview(record.goal)}"
        for record in matches
    ]
    return prefix + "; pass an id. Candidates: " + "; ".join(details)


def _goal_preview(goal: str, limit: int = 64) -> str:
    collapsed = " ".join(goal.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _is_task_run_id_prefix(value: str) -> bool:
    if len(value) < 8:
        return False
    return all(char in "0123456789abcdefABCDEF-" for char in value)


__all__ = [
    "DEFAULT_NEXT_ACTION",
    "DEFAULT_PERMISSION_PROFILE",
    "STALE_RUNNING_THRESHOLD",
    "TaskRunResult",
    "TaskRunRuntimeOptions",
    "TaskRunService",
    "TaskRunServiceError",
    "TaskRunStepContext",
    "TaskRunStepOutcome",
    "TaskRunStepRunner",
]
