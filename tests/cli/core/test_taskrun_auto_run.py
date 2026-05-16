from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import cli.core.taskrun_autorun as taskrun_autorun
from cli.core.taskrun_autorun import TaskRunAutoRunOptions
from cli.core.taskrun_service import (
    TaskRunRuntimeOptions,
    TaskRunServiceError,
    TaskRunStepContext,
    TaskRunStepOutcome,
)
from policy.permission_profiles import build_permission_profile_snapshot
from storage.taskrun_repository import TaskRunRecord
from test_taskrun_service import (
    _FakeRunner,
    _FakeTaskRunRepository,
    _guarded_profile,
    _seed_record,
    _service,
)


class _SequenceRunner:
    def __init__(self, outcomes: list[TaskRunStepOutcome]) -> None:
        self.outcomes = list(outcomes)
        self.contexts: list[TaskRunStepContext] = []

    def run(self, context: TaskRunStepContext) -> TaskRunStepOutcome:
        self.contexts.append(context)
        context.heartbeat()
        if not self.outcomes:
            raise AssertionError("runner called more times than expected")
        return self.outcomes.pop(0)


def _full_profile() -> dict[str, object]:
    return build_permission_profile_snapshot(
        "full",
        {"paths": {"allow": ["$WORKSPACE/**"]}},
        sources=["builtin", "project"],
        explicit_scope=True,
        explicit_scope_keys=["paths"],
    )


def test_run_executes_max_steps_then_stops_with_structured_events(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start(
        "Analyze this repo",
        tmp_path,
        permission_profile=_guarded_profile(),
    )
    runner = _SequenceRunner(
        [
            TaskRunStepOutcome(status="done", assistant_text="first"),
            TaskRunStepOutcome(status="done", assistant_text="second"),
        ]
    )

    result = service.run(
        None,
        tmp_path,
        options=TaskRunAutoRunOptions(
            max_steps=2,
            runtime_options=TaskRunRuntimeOptions(model_ref="faux/local/faux-1"),
        ),
        runner=runner,
    )

    assert result.exit_code == 0
    assert result.stop_reason == "max_steps_reached"
    assert result.task_run.status == "pending"
    assert result.task_run.current_step_id is None
    assert [iteration.step.status for iteration in result.iterations] == ["done", "done"]
    assert len(runner.contexts) == 2
    event_types = [event.event_type for event in repo.list_events(started.task_run.id)]
    assert "task_run_auto_run_started" in event_types
    assert event_types.count("task_run_auto_run_iteration_finished") == 2
    assert "task_run_auto_run_stopped" in event_types
    stop_events = [
        event
        for event in repo.list_events(started.task_run.id)
        if event.event_type == "task_run_auto_run_stopped"
    ]
    assert stop_events[-1].step_id is None
    history = service.history(started.task_run.id, tmp_path)
    assert "task_run_auto_run_stopped" in [
        event.event_type for event in history.key_events
    ]


def test_run_requires_explicit_id_for_blocked_taskrun(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        status="blocked",
        permission_profile=_guarded_profile(),
    )
    service = _service(repo)

    with pytest.raises(TaskRunServiceError, match="no pending TaskRun"):
        service.run(
            None,
            tmp_path,
            options=TaskRunAutoRunOptions(1, TaskRunRuntimeOptions()),
            runner=_FakeRunner(TaskRunStepOutcome(status="done")),
        )

    result = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(1, TaskRunRuntimeOptions()),
        runner=_FakeRunner(TaskRunStepOutcome(status="done", assistant_text="done")),
    )

    assert result.stop_reason == "max_steps_reached"
    assert result.task_run.status == "pending"
    assert result.iterations[0].step.step_index == 1


def test_run_permission_update_persists_before_first_step(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start(
        "Analyze this repo",
        tmp_path,
        permission_profile=_guarded_profile(),
    )
    full_profile = _full_profile()

    result = service.run(
        started.task_run.id,
        tmp_path,
        options=TaskRunAutoRunOptions(1, TaskRunRuntimeOptions()),
        runner=_FakeRunner(TaskRunStepOutcome(status="done", assistant_text="done")),
        permission_profile=full_profile,
    )

    assert result.task_run.permission_profile["name"] == "full"
    assert result.iterations[0].step.input["permission_profile"]["name"] == "full"
    update_events = [
        event
        for event in repo.list_events(started.task_run.id)
        if event.event_type == "task_run_permission_profile_updated"
    ]
    assert update_events[0].payload["previous_profile_name"] == "guarded"
    assert update_events[0].payload["new_profile_name"] == "full"
    assert update_events[0].payload["explicit_scope_keys"] == ["paths"]


def test_run_permission_update_allows_stored_interactive_profile(
    tmp_path: Path,
) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start("Analyze this repo", tmp_path)
    full_profile = _full_profile()

    result = service.run(
        started.task_run.id,
        tmp_path,
        options=TaskRunAutoRunOptions(1, TaskRunRuntimeOptions()),
        runner=_FakeRunner(TaskRunStepOutcome(status="done", assistant_text="done")),
        permission_profile=full_profile,
    )

    assert result.stop_reason == "max_steps_reached"
    assert result.task_run.status == "pending"
    assert result.task_run.permission_profile["name"] == "full"
    assert result.iterations[0].step.input["permission_profile"]["name"] == "full"
    update_events = [
        event
        for event in repo.list_events(started.task_run.id)
        if event.event_type == "task_run_permission_profile_updated"
    ]
    assert update_events[0].payload["previous_profile_name"] == "interactive"
    assert update_events[0].payload["new_profile_name"] == "full"


def test_run_rejects_interactive_profile_without_mutation(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start(
        "Analyze this repo",
        tmp_path,
        permission_profile=_guarded_profile(),
    )
    event_count = len(repo.events)

    with pytest.raises(TaskRunServiceError, match="interactive permission profile"):
        service.run(
            started.task_run.id,
            tmp_path,
            options=TaskRunAutoRunOptions(1, TaskRunRuntimeOptions()),
            runner=_FakeRunner(TaskRunStepOutcome(status="done")),
            permission_profile=build_permission_profile_snapshot("interactive"),
        )

    assert repo.runs[started.task_run.id].permission_profile["name"] == "guarded"
    assert len(repo.events) == event_count
    assert repo.list_steps(started.task_run.id) == []


def test_run_rejects_stored_interactive_profile_before_creating_step(
    tmp_path: Path,
) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start("Analyze this repo", tmp_path)

    with pytest.raises(TaskRunServiceError, match="interactive permission profile"):
        service.run(
            started.task_run.id,
            tmp_path,
            options=TaskRunAutoRunOptions(1, TaskRunRuntimeOptions()),
            runner=_FakeRunner(TaskRunStepOutcome(status="done")),
        )

    assert repo.list_steps(started.task_run.id) == []


@pytest.mark.parametrize(
    ("max_steps", "message"),
    [
        (0, "--max-steps must be >= 1"),
        (-1, "--max-steps must be >= 1"),
        (51, "--max-steps must be <= 50"),
    ],
)
def test_run_rejects_invalid_max_steps_before_creating_step(
    tmp_path: Path,
    max_steps: int,
    message: str,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        permission_profile=_guarded_profile(),
    )
    service = _service(repo)

    with pytest.raises(TaskRunServiceError, match=message):
        service.run(
            record.id,
            tmp_path,
            options=TaskRunAutoRunOptions(max_steps, TaskRunRuntimeOptions()),
            runner=_FakeRunner(TaskRunStepOutcome(status="done")),
        )

    assert repo.list_steps(record.id) == []


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("running", "active running TaskRun"),
        ("completed", "terminal TaskRun"),
    ],
)
def test_run_rejects_non_runnable_taskrun_statuses(
    tmp_path: Path,
    status: str,
    message: str,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        status=status,
        heartbeat_at="2026-05-13T00:00:00+00:00" if status == "running" else None,
        current_step_id="019e2200-0000-7000-8000-000000000999"
        if status == "running"
        else None,
        permission_profile=_guarded_profile(),
    )
    service = _service(repo)

    with pytest.raises(TaskRunServiceError, match=message):
        service.run(
            record.id,
            tmp_path,
            options=TaskRunAutoRunOptions(1, TaskRunRuntimeOptions()),
            runner=_FakeRunner(TaskRunStepOutcome(status="done")),
        )

    assert repo.list_steps(record.id) == []


def test_run_rejects_cli_max_steps_above_task_budget(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        permission_profile=_guarded_profile(),
        budget={"max_steps": 1},
    )
    service = _service(repo)

    with pytest.raises(TaskRunServiceError, match="exceeds task_run budget"):
        service.run(
            record.id,
            tmp_path,
            options=TaskRunAutoRunOptions(2, TaskRunRuntimeOptions()),
            runner=_FakeRunner(TaskRunStepOutcome(status="done")),
        )

    assert repo.list_steps(record.id) == []


def test_run_invalid_deadline_fails_before_profile_update(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        permission_profile=_guarded_profile(),
        budget={"deadline_utc": "tomorrow"},
    )
    service = _service(repo)
    full_profile = _full_profile()

    with pytest.raises(TaskRunServiceError, match="budget.deadline_utc"):
        service.run(
            record.id,
            tmp_path,
            options=TaskRunAutoRunOptions(1, TaskRunRuntimeOptions()),
            runner=_FakeRunner(TaskRunStepOutcome(status="done")),
            permission_profile=full_profile,
        )

    assert repo.runs[record.id].permission_profile["name"] == "guarded"
    assert not [
        event
        for event in repo.list_events(record.id)
        if event.event_type == "task_run_permission_profile_updated"
    ]


def test_run_expired_deadline_stops_without_creating_step(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        permission_profile=_guarded_profile(),
        budget={"deadline_utc": "2026-05-12T23:59:00Z"},
    )
    service = _service(repo, now=datetime(2026, 5, 13, tzinfo=UTC))

    result = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(3, TaskRunRuntimeOptions()),
        runner=_FakeRunner(TaskRunStepOutcome(status="done")),
    )

    assert result.exit_code == 1
    assert result.stop_reason == "budget_exhausted"
    assert result.iterations == []
    assert repo.list_steps(record.id) == []
    assert [
        event.payload["stop_reason"]
        for event in repo.list_events(record.id)
        if event.event_type == "task_run_auto_run_stopped"
    ] == ["budget_exhausted"]


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("on_workspace_dirty", "fail", "unsupported in M5"),
        ("on_workspace_dirty", "warn", "unsupported value"),
        ("on_irrecoverable_test_failure", "fail", "unsupported"),
        ("on_irrecoverable_test_failure", "warn", "unsupported value"),
    ],
)
def test_run_rejects_unsupported_stop_conditions_before_creating_step(
    tmp_path: Path,
    key: str,
    value: str,
    message: str,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        permission_profile=_guarded_profile(),
        stop_conditions={key: value},
    )
    service = _service(repo)

    with pytest.raises(TaskRunServiceError, match=message):
        service.run(
            record.id,
            tmp_path,
            options=TaskRunAutoRunOptions(1, TaskRunRuntimeOptions()),
            runner=_FakeRunner(TaskRunStepOutcome(status="done")),
        )

    assert repo.list_steps(record.id) == []


def test_run_stops_when_failure_budget_is_exhausted(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        permission_profile=_guarded_profile(),
        budget={"max_consecutive_failures": 2},
    )
    service = _service(repo)
    runner = _SequenceRunner(
        [
            TaskRunStepOutcome(status="failed", error_message="first failure"),
            TaskRunStepOutcome(status="failed", error_message="second failure"),
        ]
    )

    result = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(5, TaskRunRuntimeOptions()),
        runner=runner,
    )

    assert result.exit_code == 1
    assert result.stop_reason == "consecutive_failures_exhausted"
    assert result.task_run.status == "blocked"
    assert [step.status for step in repo.list_steps(record.id)] == ["failed", "failed"]
    stop_events = [
        event
        for event in repo.list_events(record.id)
        if event.event_type == "task_run_auto_run_stopped"
    ]
    assert stop_events[-1].step_id == result.iterations[-1].step.id


def test_run_counters_are_scoped_to_one_auto_run(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        permission_profile=_guarded_profile(),
        budget={"max_consecutive_failures": 2},
    )
    service = _service(repo)

    first = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(2, TaskRunRuntimeOptions()),
        runner=_SequenceRunner(
            [
                TaskRunStepOutcome(status="failed", error_message="first run"),
                TaskRunStepOutcome(status="done", assistant_text="recovered"),
            ]
        ),
    )
    second = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(2, TaskRunRuntimeOptions()),
        runner=_SequenceRunner(
            [
                TaskRunStepOutcome(status="failed", error_message="second run"),
                TaskRunStepOutcome(status="done", assistant_text="recovered again"),
            ]
        ),
    )

    assert first.stop_reason == "max_steps_reached"
    assert second.stop_reason == "max_steps_reached"
    assert [iteration.step.status for iteration in second.iterations] == ["failed", "done"]


def test_run_step_keyboard_interrupt_cancels_current_step(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        permission_profile=_guarded_profile(),
    )
    service = _service(repo)

    class InterruptingRunner:
        def run(self, context: TaskRunStepContext) -> TaskRunStepOutcome:
            context.heartbeat()
            raise KeyboardInterrupt

    result = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(2, TaskRunRuntimeOptions()),
        runner=InterruptingRunner(),
    )

    assert result.exit_code == 130
    assert result.stop_reason == "user_cancelled"
    assert result.task_run.status == "blocked"
    assert result.iterations[0].step.status == "cancelled"
    stop_events = [
        event
        for event in repo.list_events(record.id)
        if event.event_type == "task_run_auto_run_cancelled"
    ]
    assert stop_events[-1].step_id == result.iterations[0].step.id


def test_run_gap_keyboard_interrupt_is_run_scoped(tmp_path: Path, monkeypatch) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        permission_profile=_guarded_profile(),
    )
    service = _service(repo)
    original_validate = taskrun_autorun._validate_run_ready
    call_count = 0

    def interrupt_before_second_step(
        service_arg: taskrun_autorun.TaskRunServiceInternals,
        record_arg: TaskRunRecord,
        workspace_root: str,
        *,
        explicit: bool,
        effective_permission_profile: Mapping[str, Any] | None = None,
    ) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise KeyboardInterrupt
        original_validate(
            service_arg,
            record_arg,
            workspace_root,
            explicit=explicit,
            effective_permission_profile=effective_permission_profile,
        )

    monkeypatch.setattr(
        taskrun_autorun,
        "_validate_run_ready",
        interrupt_before_second_step,
    )

    result = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(3, TaskRunRuntimeOptions()),
        runner=_FakeRunner(TaskRunStepOutcome(status="done", assistant_text="done")),
    )

    assert result.exit_code == 130
    assert result.stop_reason == "user_cancelled"
    assert [iteration.step.status for iteration in result.iterations] == ["done"]
    stop_events = [
        event
        for event in repo.list_events(record.id)
        if event.event_type == "task_run_auto_run_cancelled"
    ]
    assert stop_events[-1].step_id is None


def test_run_counts_permission_denies_from_decision_truth(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()

    class DenyRunner:
        def run(self, context: TaskRunStepContext) -> TaskRunStepOutcome:
            repo.append_permission_decision(
                task_run_id=context.task_run.id,
                step_id=context.step.id,
                policy_request={"toolName": "write"},
                raw_decision={"effect": "confirm"},
                resolved_decision={"effect": "block"},
                profile_name="guarded",
            )
            return TaskRunStepOutcome(status="blocked", block_reason="scope blocked")

    record = _seed_record(
        repo,
        tmp_path,
        permission_profile=_guarded_profile(),
        budget={"max_consecutive_denies": 1},
    )
    service = _service(repo)

    result = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(5, TaskRunRuntimeOptions()),
        runner=DenyRunner(),
    )

    assert result.exit_code == 1
    assert result.stop_reason == "consecutive_denies_exhausted"
    stop_events = [
        event
        for event in repo.list_events(record.id)
        if event.event_type == "task_run_auto_run_stopped"
    ]
    assert stop_events[-1].payload["consecutive_denies"] == 1
    assert stop_events[-1].payload["total_denies"] == 1
