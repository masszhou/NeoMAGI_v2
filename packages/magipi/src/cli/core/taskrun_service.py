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
DEFAULT_NEXT_ACTION = "step execution is not implemented until P2-M3"


@dataclass(frozen=True, slots=True)
class TaskRunResult:
    task_run: TaskRunRecord
    projection: TaskRunProjectionResult
    events: list[TaskEventRecord]
    steps: list[TaskStepRecord]

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
            blocked = self.repository.update_task_run_status(
                record.id,
                status="blocked",
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
            "next_action": DEFAULT_NEXT_ACTION,
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
    return {
        "id": step.id,
        "step_index": step.step_index,
        "title": step.title,
        "status": step.status,
        "conclusion": step.conclusion,
        "started_at": step.started_at,
        "ended_at": step.ended_at,
    }


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
    "TaskRunService",
    "TaskRunServiceError",
]
