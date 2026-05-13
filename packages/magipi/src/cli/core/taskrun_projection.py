"""Workspace projection writer for TaskRun records.

Postgres remains the TaskRun truth. Files under ``.magipi/taskruns`` are
generated views for humans and can be overwritten at any time.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storage.ids import is_db_uuid
from storage.taskrun_repository import TaskEventRecord, TaskRunRecord, TaskStepRecord


PROJECTION_NOTICE = (
    "Generated TaskRun projection. Postgres is truth; manual edits are overwritten."
)


@dataclass(frozen=True, slots=True)
class TaskRunProjectionResult:
    path: Path
    state_path: Path
    events_path: Path
    summary_path: Path


class TaskRunProjectionError(RuntimeError):
    """Raised when projection generation cannot stay inside the workspace."""


class TaskRunProjectionWriter:
    def projection_path(self, workspace_root: str | Path, task_run_id: str) -> Path:
        if not is_db_uuid(task_run_id):
            raise TaskRunProjectionError(f"invalid TaskRun id for projection: {task_run_id}")
        root = Path(workspace_root).resolve()
        path = root / ".magipi" / "taskruns" / task_run_id
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise TaskRunProjectionError(
                f"TaskRun projection would escape workspace: {path}"
            ) from exc
        return path

    def result_for(
        self,
        *,
        workspace_root: str | Path,
        task_run_id: str,
    ) -> TaskRunProjectionResult:
        path = self.projection_path(workspace_root, task_run_id)
        return TaskRunProjectionResult(
            path=path,
            state_path=path / "state.json",
            events_path=path / "events.jsonl",
            summary_path=path / "summary.md",
        )

    def rebuild(
        self,
        *,
        task_run: TaskRunRecord,
        events: list[TaskEventRecord],
        steps: list[TaskStepRecord],
    ) -> TaskRunProjectionResult:
        result = self.result_for(
            workspace_root=task_run.workspace_root,
            task_run_id=task_run.id,
        )
        path = result.path
        path.mkdir(parents=True, exist_ok=True)

        state = {
            "notice": PROJECTION_NOTICE,
            "task_run": task_run_to_dict(task_run),
            "steps": [task_step_to_dict(step) for step in steps],
        }
        _atomic_write_text(result.state_path, _json_text(state))
        _atomic_write_text(result.events_path, _events_jsonl(events))
        _atomic_write_text(result.summary_path, _summary_markdown(task_run))
        return result


def task_run_to_dict(record: TaskRunRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "workspace_root": record.workspace_root,
        "agent_session_id": record.agent_session_id,
        "goal": record.goal,
        "status": record.status,
        "permission_profile": record.permission_profile,
        "budget": record.budget,
        "stop_conditions": record.stop_conditions,
        "current_step_id": record.current_step_id,
        "summary": record.summary,
        "heartbeat_at": record.heartbeat_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "closed_at": record.closed_at,
    }


def task_step_to_dict(record: TaskStepRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "task_run_id": record.task_run_id,
        "step_index": record.step_index,
        "title": record.title,
        "status": record.status,
        "input": record.input,
        "output": record.output,
        "conclusion": record.conclusion,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
    }


def task_event_to_dict(record: TaskEventRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "task_run_id": record.task_run_id,
        "step_id": record.step_id,
        "event_type": record.event_type,
        "payload": record.payload,
        "occurred_at": record.occurred_at,
    }


def _events_jsonl(events: list[TaskEventRecord]) -> str:
    lines = [_json_line(task_event_to_dict(event)) for event in events]
    return "\n".join(lines) + "\n"


def _summary_markdown(task_run: TaskRunRecord) -> str:
    summary = task_run.summary
    return "\n".join(
        [
            f"<!-- {PROJECTION_NOTICE} -->",
            f"# TaskRun {task_run.id}",
            "",
            f"- status: {task_run.status}",
            f"- goal: {task_run.goal}",
            f"- agent_session_id: {task_run.agent_session_id}",
            "",
            "## Structured Summary",
            "",
            "```json",
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _json_line(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _atomic_write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


__all__ = [
    "PROJECTION_NOTICE",
    "TaskRunProjectionError",
    "TaskRunProjectionResult",
    "TaskRunProjectionWriter",
    "task_event_to_dict",
    "task_run_to_dict",
    "task_step_to_dict",
]
