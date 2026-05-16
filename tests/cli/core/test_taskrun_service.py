from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pytest

from cli.core.taskrun_projection import PROJECTION_NOTICE, TaskRunProjectionWriter
from cli.core.taskrun_service import (
    TaskRunRuntimeOptions,
    TaskRunService,
    TaskRunServiceError,
    TaskRunStepContext,
    TaskRunStepOutcome,
)
from cli.core.taskrun_views import (
    step_counts,
    step_reason,
)
from policy.permission_profiles import build_permission_profile_snapshot
from storage.taskrun_repository import (
    TERMINAL_TASKRUN_STATUSES,
    TaskEventRecord,
    TaskExperimentRecord,
    TaskPermissionDecisionRecord,
    TaskRunCreateRequest,
    TaskRunRecord,
    TaskStepRecord,
)


class _FakeTaskRunRepository:
    def __init__(self) -> None:
        self.runs: dict[str, TaskRunRecord] = {}
        self.events: list[TaskEventRecord] = []
        self.steps: list[TaskStepRecord] = []
        self.permission_decisions: list[TaskPermissionDecisionRecord] = []
        self.experiments: list[TaskExperimentRecord] = []
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

    def update_task_run_permission_profile(
        self,
        task_run_id: str,
        permission_profile: Mapping[str, Any],
        *,
        updated_at: str | None = None,
    ) -> TaskRunRecord:
        record = self.runs[task_run_id]
        record = replace(
            record,
            permission_profile=dict(permission_profile),
            updated_at=updated_at or record.updated_at,
        )
        self.runs[task_run_id] = record
        return record

    def create_running_step(
        self,
        task_run_id: str,
        *,
        title: str,
        input: Mapping[str, Any],
        started_at: str | None = None,
        step_id: str | None = None,
        start_event_payload: Mapping[str, Any] | None = None,
        start_event_id: str | None = None,
    ):
        from storage.taskrun_repository import TaskRunStepStartResult

        record = self.runs[task_run_id]
        self._require_step_ready(record)
        self._require_no_workspace_running(record)
        step = self._running_step_record(task_run_id, title, input, started_at, step_id)
        self.steps.append(step)
        updated = self._mark_taskrun_running(record, step)
        self.runs[task_run_id] = updated
        if start_event_payload is not None:
            self._append_step_started_event(step, start_event_payload, start_event_id)
        return TaskRunStepStartResult(task_run=updated, step=step)

    def _require_step_ready(self, record: TaskRunRecord) -> None:
        if record.status in {"pending", "blocked"} and record.current_step_id is None:
            return
        raise ValueError(
            "TaskRun is not ready to start a manual step: "
            f"status={record.status} current_step_id={record.current_step_id}"
        )

    def _require_no_workspace_running(self, record: TaskRunRecord) -> None:
        running = [
            candidate
            for candidate in self.runs.values()
            if candidate.workspace_root == record.workspace_root
            and candidate.status == "running"
            and candidate.id != record.id
        ]
        if running:
            raise ValueError(
                f"another TaskRun is already running in this workspace: {running[0].id}"
            )

    def _running_step_record(
        self,
        task_run_id: str,
        title: str,
        input: Mapping[str, Any],
        started_at: str | None,
        step_id: str | None,
    ) -> TaskStepRecord:
        return TaskStepRecord(
            id=step_id or self._next_uuid(),
            task_run_id=task_run_id,
            step_index=len(self.list_steps(task_run_id)) + 1,
            title=title,
            status="running",
            input=dict(input),
            output={},
            started_at=started_at or "2026-05-13T00:00:00+00:00",
        )

    def _mark_taskrun_running(
        self,
        record: TaskRunRecord,
        step: TaskStepRecord,
    ) -> TaskRunRecord:
        return replace(
            record,
            status="running",
            current_step_id=step.id,
            heartbeat_at=step.started_at,
            updated_at=step.started_at or record.updated_at,
        )

    def _append_step_started_event(
        self,
        step: TaskStepRecord,
        payload: Mapping[str, Any],
        event_id: str | None,
    ) -> None:
        self.append_event(
            task_run_id=step.task_run_id,
            step_id=step.id,
            event_type="task_step_started",
            payload={
                **dict(payload),
                "step_id": step.id,
                "step_index": step.step_index,
                "status_to": "running",
            },
            occurred_at=step.started_at,
            event_id=event_id,
        )

    def update_step_status(
        self,
        step_id: str,
        *,
        status: str,
        output: Mapping[str, Any],
        conclusion: str | None,
        ended_at: str | None = None,
    ) -> TaskStepRecord:
        for index, step in enumerate(self.steps):
            if step.id != step_id:
                continue
            updated = replace(
                step,
                status=status,
                output=dict(output),
                conclusion=conclusion,
                ended_at=ended_at or "2026-05-13T00:00:00+00:00",
            )
            self.steps[index] = updated
            return updated
        raise KeyError(step_id)

    def update_task_run_step_state(
        self,
        task_run_id: str,
        *,
        status: str,
        current_step_id: str | None,
        heartbeat_at: str | None,
        updated_at: str | None = None,
    ) -> TaskRunRecord:
        record = self.runs[task_run_id]
        updated = replace(
            record,
            status=status,
            current_step_id=current_step_id,
            heartbeat_at=heartbeat_at,
            updated_at=updated_at or record.updated_at,
        )
        self.runs[task_run_id] = updated
        return updated

    def lease_running_task_run(
        self,
        task_run_id: str,
        *,
        step_id: str,
        heartbeat_at: str | None = None,
    ) -> bool:
        record = self.runs[task_run_id]
        if record.status != "running" or record.current_step_id != step_id:
            return False
        self.runs[task_run_id] = replace(
            record,
            heartbeat_at=heartbeat_at or record.heartbeat_at,
            updated_at=heartbeat_at or record.updated_at,
        )
        return True

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

    def append_permission_decision(
        self,
        *,
        task_run_id: str,
        policy_request: Mapping[str, Any],
        raw_decision: Mapping[str, Any],
        resolved_decision: Mapping[str, Any],
        profile_name: str,
        step_id: str | None = None,
        tool_execution_id: str | None = None,
        occurred_at: str | None = None,
        decision_id: str | None = None,
    ) -> TaskPermissionDecisionRecord:
        decision = TaskPermissionDecisionRecord(
            id=decision_id or self._next_uuid(),
            task_run_id=task_run_id,
            step_id=step_id,
            tool_execution_id=tool_execution_id,
            policy_request=dict(policy_request),
            raw_decision=dict(raw_decision),
            resolved_decision=dict(resolved_decision),
            profile_name=profile_name,
            occurred_at=occurred_at or "2026-05-13T00:00:00+00:00",
        )
        self.permission_decisions.append(decision)
        return decision

    def list_permission_decisions(
        self,
        task_run_id: str,
    ) -> list[TaskPermissionDecisionRecord]:
        return sorted(
            [
                decision
                for decision in self.permission_decisions
                if decision.task_run_id == task_run_id
            ],
            key=lambda decision: (decision.occurred_at, decision.id),
        )

    def append_experiment(
        self,
        *,
        task_run_id: str,
        step_id: str,
        hypothesis: str,
        change: Mapping[str, Any],
        command: Mapping[str, Any],
        metrics: Mapping[str, Any],
        result: Mapping[str, Any],
        decision: str,
        diff_ref: Mapping[str, Any],
        created_at: str | None = None,
        experiment_id: str | None = None,
    ) -> TaskExperimentRecord:
        experiment = TaskExperimentRecord(
            id=experiment_id or self._next_uuid(),
            task_run_id=task_run_id,
            step_id=step_id,
            hypothesis=hypothesis,
            change=dict(change),
            command=dict(command),
            metrics=dict(metrics),
            result=dict(result),
            decision=decision,
            diff_ref=dict(diff_ref),
            created_at=created_at or "2026-05-13T00:00:00+00:00",
        )
        self.experiments.append(experiment)
        return experiment

    def list_experiments(self, task_run_id: str) -> list[TaskExperimentRecord]:
        return sorted(
            [
                experiment
                for experiment in self.experiments
                if experiment.task_run_id == task_run_id
            ],
            key=lambda experiment: (experiment.created_at, experiment.id),
        )

    def list_experiments_for_step(self, step_id: str) -> list[TaskExperimentRecord]:
        return sorted(
            [
                experiment
                for experiment in self.experiments
                if experiment.step_id == step_id
            ],
            key=lambda experiment: (experiment.created_at, experiment.id),
        )

    def list_steps(self, task_run_id: str) -> list[TaskStepRecord]:
        return sorted(
            [step for step in self.steps if step.task_run_id == task_run_id],
            key=lambda step: step.step_index,
        )

    def find_tool_execution_id(self, *, session_id: str, tool_call_id: str) -> str | None:
        return None

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
    summary: Mapping[str, Any] | None = None,
    permission_profile: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
    stop_conditions: Mapping[str, Any] | None = None,
    updated_at: str = "2026-05-13T00:00:00+00:00",
    current_step_id: str | None = None,
) -> TaskRunRecord:
    record = TaskRunRecord(
        id=task_id,
        workspace_root=str(workspace.resolve()),
        agent_session_id="019e2200-0000-7000-8000-000000000222",
        goal=goal,
        status=status,
        permission_profile=dict(permission_profile or {"name": "interactive"}),
        budget=dict(budget or {}),
        stop_conditions=dict(stop_conditions or {}),
        summary=dict(summary or {}),
        heartbeat_at=heartbeat_at,
        current_step_id=current_step_id,
        created_at="2026-05-13T00:00:00+00:00",
        updated_at=updated_at,
    )
    repo.seed(record)
    return record


class _FakeRunner:
    def __init__(self, outcome: TaskRunStepOutcome) -> None:
        self.outcome = outcome
        self.contexts: list[TaskRunStepContext] = []

    def run(self, context: TaskRunStepContext) -> TaskRunStepOutcome:
        self.contexts.append(context)
        context.heartbeat()
        return self.outcome


def _guarded_profile() -> dict[str, Any]:
    return build_permission_profile_snapshot("guarded")


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


def test_step_done_returns_taskrun_to_pending_and_records_step(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start(
        "Analyze this repo",
        tmp_path,
        permission_profile=_guarded_profile(),
    )
    runner = _FakeRunner(
        TaskRunStepOutcome(
            status="done",
            assistant_text="Read the repo and identified the next file.",
            run_id="run-test",
            tool_count=1,
            permission_decision_count=1,
            next_action="Run another manual step.",
        )
    )

    result = service.step(
        None,
        tmp_path,
        runtime_options=TaskRunRuntimeOptions(model_ref="faux/local/faux-1"),
        runner=runner,
    )

    assert result.exit_code == 0
    assert result.task_run.status == "pending"
    assert result.task_run.current_step_id is None
    assert result.step is not None
    assert result.step.status == "done"
    assert result.step.input["instruction"] == "Take exactly one bounded step toward the TaskRun goal."
    assert result.step.output["tool_count"] == 1
    assert result.summary["last_attempt"]["status"] == "done"
    assert result.summary["next_action"] == "Run another manual step."
    assert runner.contexts[0].step.id == result.step.id
    assert [event.event_type for event in repo.list_events(started.task_run.id)] == [
        "task_run_started",
        "task_run_summary_updated",
        "task_run_projection_rebuilt",
        "task_step_started",
        "task_step_completed",
        "task_run_summary_updated",
        "task_run_projection_rebuilt",
    ]


def test_step_blocked_requires_explicit_id_to_resume(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start(
        "Analyze this repo",
        tmp_path,
        permission_profile=_guarded_profile(),
    )
    first = service.step(
        started.task_run.id,
        tmp_path,
        runner=_FakeRunner(
            TaskRunStepOutcome(status="blocked", block_reason="path outside scope")
        ),
    )

    assert first.task_run.status == "blocked"
    with pytest.raises(TaskRunServiceError, match="no pending TaskRun"):
        service.step(
            None,
            tmp_path,
            runner=_FakeRunner(TaskRunStepOutcome(status="done", assistant_text="done")),
        )

    resumed = service.step(
        started.task_run.id,
        tmp_path,
        runner=_FakeRunner(TaskRunStepOutcome(status="done", assistant_text="done")),
    )

    assert resumed.task_run.status == "pending"
    assert [step.status for step in resumed.steps] == ["blocked", "done"]
    assert resumed.steps[1].step_index == 2


def test_step_rejects_interactive_profile_before_creating_step(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start("Analyze this repo", tmp_path)

    with pytest.raises(TaskRunServiceError, match="interactive permission profile"):
        service.step(
            started.task_run.id,
            tmp_path,
            runner=_FakeRunner(TaskRunStepOutcome(status="done")),
        )

    assert repo.list_steps(started.task_run.id) == []


def test_step_runner_exception_finalizes_failed(tmp_path: Path) -> None:
    class FailingRunner:
        def run(self, _context: TaskRunStepContext) -> TaskRunStepOutcome:
            raise RuntimeError("provider exploded")

    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start(
        "Analyze this repo",
        tmp_path,
        permission_profile=_guarded_profile(),
    )

    result = service.step(started.task_run.id, tmp_path, runner=FailingRunner())

    assert result.exit_code == 1
    assert result.task_run.status == "blocked"
    assert result.step is not None
    assert result.step.status == "failed"
    assert result.step.output["error_message"] == "provider exploded"


def test_step_keyboard_interrupt_finalizes_cancelled(tmp_path: Path) -> None:
    class InterruptingRunner:
        def run(self, _context: TaskRunStepContext) -> TaskRunStepOutcome:
            raise KeyboardInterrupt

    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start(
        "Analyze this repo",
        tmp_path,
        permission_profile=_guarded_profile(),
    )

    result = service.step(started.task_run.id, tmp_path, runner=InterruptingRunner())

    assert result.exit_code == 130
    assert result.task_run.status == "blocked"
    assert result.step is not None
    assert result.step.status == "cancelled"
    assert result.step.output["error_message"] == "cancelled by user interrupt"


def test_step_runner_heartbeat_leases_current_running_step(tmp_path: Path) -> None:
    class HeartbeatRunner:
        def run(self, context: TaskRunStepContext) -> TaskRunStepOutcome:
            context.heartbeat()
            active = repo.runs[context.task_run.id]
            assert active.status == "running"
            assert active.current_step_id == context.step.id
            assert active.heartbeat_at == "2026-05-13T00:02:00+00:00"
            return TaskRunStepOutcome(status="done", assistant_text="done")

    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start(
        "Analyze this repo",
        tmp_path,
        permission_profile=_guarded_profile(),
    )
    times = iter(
        [
            datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 5, 13, 0, 1, tzinfo=UTC),
            datetime(2026, 5, 13, 0, 2, tzinfo=UTC),
            datetime(2026, 5, 13, 0, 3, tzinfo=UTC),
        ]
    )
    service.clock = lambda: next(times)

    result = service.step(started.task_run.id, tmp_path, runner=HeartbeatRunner())

    assert result.task_run.status == "pending"
    assert result.task_run.heartbeat_at == "2026-05-13T00:03:00+00:00"


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


def test_list_returns_workspace_records_without_projection_or_summary_events(
    tmp_path: Path,
) -> None:
    repo = _FakeTaskRunRepository()
    first = _seed_record(
        repo,
        tmp_path,
        task_id="019e2200-0000-7000-8000-000000000111",
        goal="newest task",
        summary={"next_action": "stored next", "current_step": {"step_index": 2}},
        permission_profile={"name": "guarded"},
        updated_at="2026-05-13T00:03:00+00:00",
    )
    _seed_record(
        repo,
        tmp_path,
        task_id="019e2200-0000-7000-8000-000000000112",
        status="completed",
        goal="terminal task",
        updated_at="2026-05-13T00:02:00+00:00",
    )
    _seed_record(
        repo,
        tmp_path,
        task_id="019e2200-0000-7000-8000-000000000113",
        status="archived",
        goal="archived task",
        updated_at="2026-05-13T00:01:00+00:00",
    )
    _seed_record(
        repo,
        tmp_path / "other",
        task_id="019e2200-0000-7000-8000-000000000114",
        goal="other workspace",
    )
    projection = tmp_path / ".magipi" / "taskruns" / first.id / "summary.md"
    projection.parent.mkdir(parents=True)
    projection.write_text("manual edit", encoding="utf-8")
    event_count = len(repo.events)

    result = _service(repo).list(tmp_path)

    assert [item.task_run.id for item in result.items] == [
        "019e2200-0000-7000-8000-000000000111",
        "019e2200-0000-7000-8000-000000000112",
    ]
    assert result.items[0].permission_profile_name == "guarded"
    assert result.items[0].next_action == "stored next"
    assert result.items[0].current_step == {"step_index": 2}
    assert projection.read_text(encoding="utf-8") == "manual edit"
    assert len(repo.events) == event_count


def test_list_recovers_stale_running_before_reading(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        status="running",
        heartbeat_at="2026-05-13T00:00:00+00:00",
    )
    service = _service(repo, now=datetime(2026, 5, 13, 1, 0, tzinfo=UTC))

    result = service.list(tmp_path)

    assert result.items[0].task_run.id == record.id
    assert result.items[0].task_run.status == "blocked"
    assert [event.event_type for event in repo.list_events(record.id)] == [
        "task_run_blocked_stale"
    ]


def test_step_reason_and_counts_use_structured_priority() -> None:
    task_run_id = "019e2200-0000-7000-8000-000000000111"
    step_id = "019e2200-0000-7000-8000-000000000112"
    step = TaskStepRecord(
        id=step_id,
        task_run_id=task_run_id,
        step_index=1,
        title="Step 1",
        status="blocked",
        input={},
        output={"block_reason": "scope blocked", "permission_decision_count": 9},
        conclusion="fallback conclusion",
    )
    event = TaskEventRecord(
        id="019e2200-0000-7000-8000-000000000113",
        task_run_id=task_run_id,
        step_id=step_id,
        event_type="task_step_blocked",
        payload={"reason": "event reason"},
        occurred_at="2026-05-13T00:00:00+00:00",
    )
    decision = TaskPermissionDecisionRecord(
        id="019e2200-0000-7000-8000-000000000114",
        task_run_id=task_run_id,
        step_id=step_id,
        tool_execution_id=None,
        policy_request={},
        raw_decision={},
        resolved_decision={},
        profile_name="guarded",
        occurred_at="2026-05-13T00:00:00+00:00",
    )
    other_decision = replace(
        decision,
        id="019e2200-0000-7000-8000-000000000115",
        step_id=None,
    )

    assert step_reason(step, [event]) == "scope blocked"
    assert step_reason(replace(step, output={}), [event]) == "event reason"
    assert step_reason(replace(step, output={}), []) == "fallback conclusion"
    assert step_counts(step, [decision, other_decision]).permission_decision_count == 1
    assert step_counts(step, [other_decision]).permission_decision_count == 0
    assert step_counts(step, []).permission_decision_count == 9


def test_history_returns_step_timeline_key_events_and_reasons(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(
        repo,
        tmp_path,
        status="blocked",
        summary={"next_action": "stored stale action"},
    )
    blocked_step = TaskStepRecord(
        id="019e2200-0000-7000-8000-000000000333",
        task_run_id=record.id,
        step_index=1,
        title="Step 1",
        status="blocked",
        input={},
        output={"reason": "needs approval", "tool_count": 2},
    )
    repo.steps.append(blocked_step)
    repo.append_event(
        task_run_id=record.id,
        event_type="task_step_started",
        payload={},
        step_id=blocked_step.id,
        occurred_at="2026-05-13T00:00:00+00:00",
    )
    repo.append_event(
        task_run_id=record.id,
        event_type="task_step_blocked",
        payload={"reason": "needs approval"},
        step_id=blocked_step.id,
        occurred_at="2026-05-13T00:01:00+00:00",
    )
    repo.append_event(
        task_run_id=record.id,
        event_type="task_run_projection_rebuilt",
        payload={},
        occurred_at="2026-05-13T00:02:00+00:00",
    )
    repo.append_permission_decision(
        task_run_id=record.id,
        step_id=blocked_step.id,
        policy_request={},
        raw_decision={},
        resolved_decision={},
        profile_name="guarded",
    )
    event_count = len(repo.events)

    result = _service(repo).history(record.id[:12], tmp_path)

    assert result.steps[0].step.id == blocked_step.id
    assert result.steps[0].reason == "needs approval"
    assert result.steps[0].counts.tool_count == 2
    assert result.steps[0].counts.permission_decision_count == 1
    assert [event.event_type for event in result.key_events] == [
        "task_step_started",
        "task_step_blocked",
    ]
    assert len(repo.events) == event_count


def test_next_returns_pending_step_or_summary_without_creating_step(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path, status="pending")
    pending_later = TaskStepRecord(
        id="019e2200-0000-7000-8000-000000000333",
        task_run_id=record.id,
        step_index=2,
        title="Step 2",
        status="pending",
        input={},
        output={},
    )
    pending_first = replace(
        pending_later,
        id="019e2200-0000-7000-8000-000000000334",
        step_index=1,
        title="Step 1",
    )
    repo.steps.extend([pending_later, pending_first])
    step_count = len(repo.steps)
    event_count = len(repo.events)

    result = _service(repo).next(record.id, tmp_path)

    assert result.pending_step == pending_first
    assert result.current_step is None
    assert result.next_action.startswith("Run `magipi taskrun step")
    assert result.summary_snapshot["status"] == "pending"
    assert len(repo.steps) == step_count
    assert len(repo.events) == event_count


def test_next_exposes_blocked_reason_without_pending_step(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path, status="blocked")
    blocked_step = TaskStepRecord(
        id="019e2200-0000-7000-8000-000000000333",
        task_run_id=record.id,
        step_index=1,
        title="Step 1",
        status="failed",
        input={},
        output={"error_message": "provider failed"},
        conclusion="failed",
    )
    repo.steps.append(blocked_step)
    event_count = len(repo.events)

    result = _service(repo).next(record.id, tmp_path)

    assert result.pending_step is None
    assert result.last_attempt == blocked_step
    assert result.blocked_or_failed_reason == "provider failed"
    assert "provider failed" in result.next_action
    assert len(repo.events) == event_count


def test_terminal_taskrun_is_readable_by_explicit_id(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path, status="completed")
    repo.append_event(
        task_run_id=record.id,
        event_type="task_run_closed",
        payload={"final_status": "completed"},
        occurred_at="2026-05-13T00:01:00+00:00",
    )
    event_count = len(repo.events)
    service = _service(repo)

    history = service.history(record.id, tmp_path)
    next_view = service.next(record.id, tmp_path)
    events = service.events(record.id, tmp_path)

    assert history.task_run.id == record.id
    assert next_view.task_run.id == record.id
    assert next_view.next_action == "TaskRun is terminal; inspect summary or archive when ready."
    assert [event.event_type for event in events.events] == ["task_run_closed"]
    assert len(repo.events) == event_count


def test_events_returns_complete_ordered_stream_without_projection(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    late = repo.append_event(
        task_run_id=record.id,
        event_type="late",
        payload={"order": 2},
        occurred_at="2026-05-13T00:02:00+00:00",
    )
    early = repo.append_event(
        task_run_id=record.id,
        event_type="early",
        payload={"order": 1},
        occurred_at="2026-05-13T00:01:00+00:00",
    )
    event_count = len(repo.events)

    result = _service(repo).events(record.id, tmp_path)

    assert [event.id for event in result.events] == [early.id, late.id]
    assert len(repo.events) == event_count


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
