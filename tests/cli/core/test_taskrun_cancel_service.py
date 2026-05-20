from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cli.core.taskrun_service import TaskRunServiceError
from storage.taskrun_repository import TaskStepRecord
from test_taskrun_service import _FakeTaskRunRepository, _seed_record, _service


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


def test_cancel_cancels_pending_taskrun_and_is_idempotent(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start("Analyze this repo", tmp_path)

    cancelled = service.cancel(started.task_run.id, tmp_path)
    cancelled_again = service.cancel(started.task_run.id, tmp_path)

    assert cancelled.task_run.status == "cancelled"
    assert cancelled_again.task_run.status == "cancelled"
    events = repo.list_events(started.task_run.id)
    assert [event.event_type for event in events].count("task_run_cancelled") == 1


def test_cancel_running_taskrun_records_interrupt_request(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    step_id = "019e2200-0000-7000-8000-000000000333"
    record = _seed_record(
        repo,
        tmp_path,
        status="running",
        heartbeat_at="2026-05-13T00:59:00+00:00",
        current_step_id=step_id,
    )
    repo.steps.append(
        TaskStepRecord(
            id=step_id,
            task_run_id=record.id,
            step_index=1,
            title="Step 1",
            status="running",
            input={},
            output={},
            started_at="2026-05-13T00:59:00+00:00",
        )
    )
    service = _service(repo, now=datetime(2026, 5, 13, 1, 0, tzinfo=UTC))

    requested = service.cancel(record.id, tmp_path)
    requested_again = service.cancel(record.id, tmp_path)

    assert requested.task_run.status == "running"
    assert requested_again.task_run.status == "running"
    events = repo.list_events(record.id)
    request_events = [
        event for event in events if event.event_type == "task_run_cancel_requested"
    ]
    assert len(request_events) == 1
    assert request_events[0].step_id == step_id
    assert request_events[0].payload["current_step_id"] == step_id


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
