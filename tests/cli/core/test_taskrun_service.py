from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pytest

from cli.core.taskrun_projection import PROJECTION_NOTICE, TaskRunProjectionWriter
from cli.core.taskrun_service import TaskRunService, TaskRunServiceError
from policy.permission_profiles import build_permission_profile_snapshot
from storage.taskrun_repository import (
    TERMINAL_TASKRUN_STATUSES,
    TaskEventRecord,
    TaskRunCreateRequest,
    TaskRunRecord,
    TaskStepRecord,
)


class _FakeTaskRunRepository:
    def __init__(self) -> None:
        self.runs: dict[str, TaskRunRecord] = {}
        self.events: list[TaskEventRecord] = []
        self.steps: list[TaskStepRecord] = []
        self._counter = 1

    def create_task_run(self, request: TaskRunCreateRequest) -> TaskRunRecord:
        task_id = request.task_run_id or self._next_uuid()
        session_id = request.agent_session_id or self._next_uuid()
        now = request.created_at or "2026-05-13T00:00:00+00:00"
        record = TaskRunRecord(
            id=task_id,
            workspace_root=request.workspace_root,
            agent_session_id=session_id,
            goal=request.goal,
            status=request.status,
            permission_profile=dict(request.permission_profile),
            budget=dict(request.budget or {}),
            stop_conditions=dict(request.stop_conditions or {}),
            summary=dict(request.summary or {}),
            created_at=now,
            updated_at=now,
        )
        self.runs[task_id] = record
        return record

    def get_task_run(self, task_run_id: str) -> TaskRunRecord | None:
        return self.runs.get(task_run_id)

    def list_task_runs_for_workspace(
        self,
        workspace_root: str,
        *,
        include_terminal: bool = True,
    ) -> list[TaskRunRecord]:
        records = [
            record
            for record in self.runs.values()
            if record.workspace_root == workspace_root
            and (include_terminal or record.status not in TERMINAL_TASKRUN_STATUSES)
        ]
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def list_running_task_runs(self, workspace_root: str) -> list[TaskRunRecord]:
        return [
            record
            for record in self.runs.values()
            if record.workspace_root == workspace_root and record.status == "running"
        ]

    def find_task_runs_by_prefix(
        self,
        workspace_root: str,
        task_run_prefix: str,
    ) -> list[TaskRunRecord]:
        return [
            record
            for record in self.runs.values()
            if record.workspace_root == workspace_root and record.id.startswith(task_run_prefix)
        ]

    def update_task_run_status(
        self,
        task_run_id: str,
        *,
        status: str,
        heartbeat_at: str | None = None,
        summary: Mapping[str, Any] | None = None,
        closed_at: str | None = None,
        updated_at: str | None = None,
    ) -> TaskRunRecord:
        record = self.runs[task_run_id]
        record = replace(
            record,
            status=status,
            heartbeat_at=heartbeat_at,
            summary=dict(summary) if summary is not None else record.summary,
            closed_at=closed_at or record.closed_at,
            updated_at=updated_at or record.updated_at,
        )
        self.runs[task_run_id] = record
        return record

    def update_task_run_summary(
        self,
        task_run_id: str,
        summary: Mapping[str, Any],
        *,
        updated_at: str | None = None,
    ) -> TaskRunRecord:
        record = self.runs[task_run_id]
        record = replace(
            record,
            summary=dict(summary),
            updated_at=updated_at or record.updated_at,
        )
        self.runs[task_run_id] = record
        return record

    def append_event(
        self,
        *,
        task_run_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        step_id: str | None = None,
        occurred_at: str | None = None,
        event_id: str | None = None,
    ) -> TaskEventRecord:
        event = TaskEventRecord(
            id=event_id or self._next_uuid(),
            task_run_id=task_run_id,
            step_id=step_id,
            event_type=event_type,
            payload=dict(payload),
            occurred_at=occurred_at or "2026-05-13T00:00:00+00:00",
        )
        self.events.append(event)
        return event

    def list_events(self, task_run_id: str) -> list[TaskEventRecord]:
        return sorted(
            [event for event in self.events if event.task_run_id == task_run_id],
            key=lambda event: (event.occurred_at, event.id),
        )

    def list_steps(self, task_run_id: str) -> list[TaskStepRecord]:
        return sorted(
            [step for step in self.steps if step.task_run_id == task_run_id],
            key=lambda step: step.step_index,
        )

    def seed(self, record: TaskRunRecord) -> None:
        self.runs[record.id] = record

    def _next_uuid(self) -> str:
        value = f"019e2200-0000-7000-8000-{self._counter:012d}"
        self._counter += 1
        return value


def _service(repo: _FakeTaskRunRepository, *, now: datetime | None = None) -> TaskRunService:
    return TaskRunService(
        repo,
        projection_writer=TaskRunProjectionWriter(),
        clock=lambda: now or datetime(2026, 5, 13, tzinfo=UTC),
    )


def _seed_record(
    repo: _FakeTaskRunRepository,
    workspace: Path,
    *,
    task_id: str = "019e2200-0000-7000-8000-000000000111",
    status: str = "pending",
    heartbeat_at: str | None = None,
    goal: str = "seeded goal",
) -> TaskRunRecord:
    record = TaskRunRecord(
        id=task_id,
        workspace_root=str(workspace.resolve()),
        agent_session_id="019e2200-0000-7000-8000-000000000222",
        goal=goal,
        status=status,
        permission_profile={"name": "interactive"},
        budget={},
        stop_conditions={},
        summary={},
        heartbeat_at=heartbeat_at,
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T00:00:00+00:00",
    )
    repo.seed(record)
    return record


def test_start_creates_pending_taskrun_summary_and_projection(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)

    result = service.start("Analyze this repo", tmp_path)

    assert result.task_run.status == "pending"
    assert result.task_run.agent_session_id
    assert result.summary["goal"] == "Analyze this repo"
    assert result.summary["permission_profile"]["name"] == "interactive"
    assert result.summary["permission_profile"]["sources"] == ["builtin"]
    assert result.summary["current_step"] is None
    assert (result.projection.path / "state.json").is_file()
    assert (result.projection.path / "events.jsonl").is_file()
    assert (result.projection.path / "summary.md").is_file()
    event_lines = (result.projection.path / "events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(event_lines) == len(result.events)
    assert all(json.loads(line)["event_type"] for line in event_lines)


def test_status_after_new_service_reads_repository_truth(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    first = _service(repo).start("Analyze this repo", tmp_path)
    event_count = len(repo.list_events(first.task_run.id))

    second = _service(repo).status(first.task_run.id[:12], tmp_path)

    assert second.task_run.id == first.task_run.id
    assert second.task_run.status == "pending"
    assert len(repo.list_events(first.task_run.id)) == event_count


def test_stale_running_is_blocked_before_status_selection(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        status="running",
        heartbeat_at="2026-05-13T00:00:00+00:00",
    )
    service = _service(repo, now=datetime(2026, 5, 13, 1, 0, tzinfo=UTC))

    result = service.status(record.id, tmp_path)

    assert result.task_run.status == "blocked"
    assert result.summary["status"] == "blocked"
    event_types = [event.event_type for event in repo.list_events(record.id)]
    assert "task_run_blocked_stale" in event_types


def test_close_cancels_pending_taskrun_and_is_idempotent(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start("Analyze this repo", tmp_path)

    closed = service.close(started.task_run.id, tmp_path)
    closed_again = service.close(started.task_run.id, tmp_path)

    assert closed.task_run.status == "cancelled"
    assert closed_again.task_run.status == "cancelled"
    events = repo.list_events(started.task_run.id)
    assert [event.event_type for event in events].count("task_run_closed") == 1


def test_close_rejects_fresh_running_taskrun(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        status="running",
        heartbeat_at="2026-05-13T00:59:00+00:00",
    )
    service = _service(repo, now=datetime(2026, 5, 13, 1, 0, tzinfo=UTC))

    with pytest.raises(TaskRunServiceError, match="active running TaskRun"):
        service.close(record.id, tmp_path)

    assert repo.runs[record.id].status == "running"


def test_omitted_id_with_multiple_active_candidates_fails(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    _seed_record(
        repo,
        tmp_path,
        task_id="019e2200-0000-7000-8000-000000000101",
        goal="first",
    )
    _seed_record(
        repo,
        tmp_path,
        task_id="019e2200-0000-7000-8000-000000000102",
        goal="second",
    )
    service = _service(repo)

    with pytest.raises(TaskRunServiceError, match="multiple non-terminal"):
        service.status(None, tmp_path)


def test_invalid_id_prefix_fails_before_repository_lookup(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)

    with pytest.raises(TaskRunServiceError, match="invalid TaskRun id prefix"):
        service.status("%", tmp_path)


def test_summary_regenerates_and_overwrites_projection(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start("Analyze this repo", tmp_path)
    summary_path = started.projection.path / "summary.md"
    summary_path.write_text("USER EDIT\n", encoding="utf-8")

    regenerated = service.summary(started.task_run.id, tmp_path)

    body = (regenerated.projection.path / "summary.md").read_text(encoding="utf-8")
    assert "USER EDIT" not in body
    assert PROJECTION_NOTICE in body


def test_start_persists_explicit_permission_profile_snapshot(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    profile = build_permission_profile_snapshot(
        "guarded",
        {"paths": {"allow": ["$WORKSPACE/**"]}},
        sources=["builtin", "project"],
        explicit_scope=True,
        explicit_scope_keys=["paths"],
    )

    result = service.start("Analyze this repo", tmp_path, permission_profile=profile)

    assert result.task_run.permission_profile["name"] == "guarded"
    assert result.task_run.permission_profile["sources"] == ["builtin", "project"]
    assert result.summary["permission_profile"]["name"] == "guarded"
