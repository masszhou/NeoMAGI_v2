from __future__ import annotations

from pathlib import Path

import pytest

import cli.__main__ as cli_main
import cli.taskrun_commands as taskrun_commands
from cli.core.taskrun_autorun import TaskRunAutoRunIteration, TaskRunAutoRunResult
from cli.core.taskrun_projection import TaskRunProjectionResult
from cli.core.taskrun_service import TaskRunResult
from cli.core.taskrun_views import (
    TaskRunEventsResult,
    TaskRunHistoryResult,
    TaskRunHistoryStep,
    TaskRunListItem,
    TaskRunListResult,
    TaskRunNextResult,
    TaskStepCounts,
)
from storage.taskrun_repository import TaskEventRecord, TaskRunRecord, TaskStepRecord


class _Conn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeService:
    def __init__(self, result: TaskRunResult) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def start(
        self,
        goal: str,
        cwd: Path,
        *,
        permission_profile: object | None = None,
    ) -> TaskRunResult:
        self.calls.append(("start", goal, cwd, permission_profile))
        return self.result

    def status(self, task_id: str | None, cwd: Path) -> TaskRunResult:
        self.calls.append(("status", task_id, cwd))
        return self.result

    def summary(self, task_id: str | None, cwd: Path) -> TaskRunResult:
        self.calls.append(("summary", task_id, cwd))
        return self.result

    def list(self, cwd: Path) -> TaskRunListResult:
        self.calls.append(("list", cwd))
        return TaskRunListResult(
            [
                TaskRunListItem(
                    task_run=self.result.task_run,
                    current_step=None,
                    permission_profile_name="interactive",
                    next_action=str(self.result.summary.get("next_action") or ""),
                )
            ]
        )

    def history(self, task_id: str | None, cwd: Path) -> TaskRunHistoryResult:
        self.calls.append(("history", task_id, cwd))
        step = self.result.steps[0] if self.result.steps else None
        return TaskRunHistoryResult(
            task_run=self.result.task_run,
            steps=[
                TaskRunHistoryStep(
                    step=step,
                    reason="needs approval",
                    counts=TaskStepCounts(tool_count=1, permission_decision_count=2),
                )
            ]
            if step is not None
            else [],
            key_events=[
                TaskEventRecord(
                    id="019e2200-0000-7000-8000-000000000004",
                    task_run_id=self.result.task_run.id,
                    step_id=step.id if step else None,
                    event_type="task_step_blocked",
                    payload={"reason": "needs approval"},
                    occurred_at="2026-05-13T00:02:00+00:00",
                )
            ],
            next_action=str(self.result.summary.get("next_action") or ""),
        )

    def next(self, task_id: str | None, cwd: Path) -> TaskRunNextResult:
        self.calls.append(("next", task_id, cwd))
        step = self.result.steps[0] if self.result.steps else None
        return TaskRunNextResult(
            task_run=self.result.task_run,
            pending_step=None,
            current_step=None,
            last_attempt=step,
            next_action=str(self.result.summary.get("next_action") or ""),
            blocked_or_failed_reason="needs approval",
            permission_profile=self.result.task_run.permission_profile,
            summary_snapshot=self.result.summary,
        )

    def events(self, task_id: str | None, cwd: Path) -> TaskRunEventsResult:
        self.calls.append(("events", task_id, cwd))
        return TaskRunEventsResult(
            task_run=self.result.task_run,
            events=[
                TaskEventRecord(
                    id="019e2200-0000-7000-8000-000000000004",
                    task_run_id=self.result.task_run.id,
                    step_id=None,
                    event_type="task_run_started",
                    payload={"goal": "Analyze this repo"},
                    occurred_at="2026-05-13T00:00:00+00:00",
                )
            ],
        )

    def close(self, task_id: str | None, cwd: Path) -> TaskRunResult:
        self.calls.append(("close", task_id, cwd))
        return self.result

    def step(
        self,
        task_id: str | None,
        cwd: Path,
        *,
        runtime_options: object | None = None,
        runner: object | None = None,
    ) -> TaskRunResult:
        self.calls.append(("step", task_id, cwd, runtime_options, runner))
        return self.result

    def run(
        self,
        task_id: str | None,
        cwd: Path,
        *,
        options: object,
        runner: object | None = None,
        permission_profile: object | None = None,
    ) -> TaskRunAutoRunResult:
        self.calls.append(("run", task_id, cwd, options, runner, permission_profile))
        step = self.result.steps[0] if self.result.steps else None
        iterations = (
            [
                TaskRunAutoRunIteration(
                    step=step,
                    task_run_status=self.result.task_run.status,
                    stop_candidate="max_steps_reached",
                )
            ]
            if step is not None
            else []
        )
        return TaskRunAutoRunResult(
            task_run=self.result.task_run,
            iterations=iterations,
            stop_reason="max_steps_reached",
            projection=self.result.projection,
            events=self.result.events,
            exit_code=self.result.exit_code,
        )


def _result(tmp_path: Path) -> TaskRunResult:
    record = TaskRunRecord(
        id="019e2200-0000-7000-8000-000000000001",
        workspace_root=str(tmp_path),
        agent_session_id="019e2200-0000-7000-8000-000000000002",
        goal="Analyze this repo",
        status="pending",
        permission_profile={"name": "interactive"},
        budget={},
        stop_conditions={},
        summary={
            "goal": "Analyze this repo",
            "status": "pending",
            "next_action": "step execution is not implemented until P2-M3",
        },
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T00:00:00+00:00",
    )
    projection = TaskRunProjectionResult(
        path=tmp_path / ".magipi" / "taskruns" / record.id,
        state_path=tmp_path / "state.json",
        events_path=tmp_path / "events.jsonl",
        summary_path=tmp_path / "summary.md",
    )
    return TaskRunResult(task_run=record, projection=projection, events=[], steps=[])


def _step_result(tmp_path: Path) -> TaskRunResult:
    result = _result(tmp_path)
    step = TaskStepRecord(
        id="019e2200-0000-7000-8000-000000000003",
        task_run_id=result.task_run.id,
        step_index=1,
        title="Step 1",
        status="done",
        input={},
        output={"next_action": "continue"},
        conclusion="done",
        started_at="2026-05-13T00:00:00+00:00",
        ended_at="2026-05-13T00:01:00+00:00",
    )
    return TaskRunResult(
        task_run=result.task_run,
        projection=result.projection,
        events=[],
        steps=[step],
        step=step,
    )


def _stub_runtime(monkeypatch, service: _FakeService) -> _Conn:
    conn = _Conn()
    monkeypatch.setattr(taskrun_commands, "load_database_config", lambda **_kwargs: object())
    monkeypatch.setattr(taskrun_commands, "connect_database", lambda _config: conn)
    monkeypatch.setattr(taskrun_commands, "ensure_schema", lambda _conn, _config: None)
    monkeypatch.setattr(
        taskrun_commands,
        "_load_permission_profile_snapshot",
        lambda name, _cwd: {
            "name": name,
            "nonInteractive": name != "interactive",
            "scope": {},
            "sources": ["builtin"],
            "explicitScope": False,
            "explicitScopeKeys": [],
        },
    )
    monkeypatch.setattr(
        taskrun_commands,
        "PostgresTaskRunRepository",
        lambda _conn, _config: object(),
    )
    monkeypatch.setattr(
        taskrun_commands,
        "PostgresSessionRepository",
        lambda _conn, _config: object(),
    )
    monkeypatch.setattr(taskrun_commands, "TaskRunHeadlessRunner", lambda **_kwargs: object())
    monkeypatch.setattr(taskrun_commands, "TaskRunService", lambda _repo: service)
    return conn


def test_taskrun_start_prints_full_id_and_status(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    service = _FakeService(_result(tmp_path))
    conn = _stub_runtime(monkeypatch, service)

    rc = taskrun_commands.run_taskrun_command(
        ["start", "Analyze", "this", "repo"],
        prog="magipi",
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "id: 019e2200-0000-7000-8000-000000000001" in captured.out
    assert "status: pending" in captured.out
    assert service.calls[0][0] == "start"
    assert service.calls[0][1] == "Analyze this repo"
    assert conn.closed is True


def test_taskrun_start_passes_permission_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _FakeService(_result(tmp_path))
    _stub_runtime(monkeypatch, service)

    rc = taskrun_commands.run_taskrun_command(
        ["start", "--permission", "guarded", "Analyze", "this", "repo"],
        prog="magipi",
    )

    assert rc == 0
    assert service.calls[0][0] == "start"
    assert service.calls[0][3]["name"] == "guarded"


def test_taskrun_status_passes_optional_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _FakeService(_result(tmp_path))
    _stub_runtime(monkeypatch, service)

    rc = taskrun_commands.run_taskrun_command(
        ["status", "019e2200"],
        prog="magipi",
    )

    assert rc == 0
    assert service.calls[0][0] == "status"
    assert service.calls[0][1] == "019e2200"


def test_taskrun_summary_prints_structured_summary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    service = _FakeService(_result(tmp_path))
    _stub_runtime(monkeypatch, service)

    rc = taskrun_commands.run_taskrun_command(["summary"], prog="magipi")

    captured = capsys.readouterr()
    assert rc == 0
    assert "summary:" in captured.out
    assert '"next_action"' in captured.out


def test_taskrun_list_routes_and_prints_workspace_view(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    service = _FakeService(_result(tmp_path))
    _stub_runtime(monkeypatch, service)

    rc = taskrun_commands.run_taskrun_command(["list"], prog="magipi")

    captured = capsys.readouterr()
    assert rc == 0
    assert service.calls[0][0] == "list"
    assert "id: 019e2200-0000-7000-8000-000000000001" in captured.out
    assert "permission_profile: interactive" in captured.out
    assert "goal: Analyze this repo" in captured.out
    assert "next_action:" in captured.out


def test_taskrun_history_routes_and_prints_reason(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    service = _FakeService(_step_result(tmp_path))
    _stub_runtime(monkeypatch, service)

    rc = taskrun_commands.run_taskrun_command(["history", "019e2200"], prog="magipi")

    captured = capsys.readouterr()
    assert rc == 0
    assert service.calls[0][0] == "history"
    assert service.calls[0][1] == "019e2200"
    assert "step_status: done" in captured.out
    assert "reason: needs approval" in captured.out
    assert "permission_decision_count: 2" in captured.out
    assert "task_step_blocked" in captured.out


def test_taskrun_next_routes_and_prints_deterministic_view(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    service = _FakeService(_step_result(tmp_path))
    _stub_runtime(monkeypatch, service)

    rc = taskrun_commands.run_taskrun_command(["next", "019e2200"], prog="magipi")

    captured = capsys.readouterr()
    assert rc == 0
    assert service.calls[0][0] == "next"
    assert service.calls[0][1] == "019e2200"
    assert "pending_step: none" in captured.out
    assert "last_attempt: #1 done" in captured.out
    assert "blocked_or_failed_reason: needs approval" in captured.out
    assert "summary_snapshot:" in captured.out


def test_taskrun_events_routes_and_prints_jsonl(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    service = _FakeService(_result(tmp_path))
    _stub_runtime(monkeypatch, service)

    rc = taskrun_commands.run_taskrun_command(["events", "019e2200"], prog="magipi")

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert rc == 0
    assert service.calls[0][0] == "events"
    assert service.calls[0][1] == "019e2200"
    assert len(lines) == 1
    assert '"event_type": "task_run_started"' in lines[0]
    assert '"payload": {"goal": "Analyze this repo"}' in lines[0]


def test_taskrun_step_passes_runtime_options_and_prints_step(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    service = _FakeService(_step_result(tmp_path))
    _stub_runtime(monkeypatch, service)

    rc = taskrun_commands.run_taskrun_command(
        [
            "step",
            "019e2200",
            "--model",
            "faux/local/faux-1",
            "--thinking-level",
            "off",
            "--cache-retention",
            "none",
        ],
        prog="magipi",
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert service.calls[0][0] == "step"
    assert service.calls[0][1] == "019e2200"
    assert service.calls[0][3].model_ref == "faux/local/faux-1"
    assert service.calls[0][3].cache_retention == "none"
    assert service.calls[0][4] is not None
    assert "step_id: 019e2200-0000-7000-8000-000000000003" in captured.out
    assert "step_status: done" in captured.out
    assert "conclusion: done" in captured.out


def test_taskrun_run_passes_bounded_options_permission_and_prints_iterations(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    service = _FakeService(_step_result(tmp_path))
    _stub_runtime(monkeypatch, service)

    rc = taskrun_commands.run_taskrun_command(
        [
            "run",
            "019e2200",
            "--max-steps",
            "2",
            "--permission",
            "guarded",
            "--model",
            "faux/local/faux-1",
            "--thinking-level",
            "off",
            "--cache-retention",
            "none",
        ],
        prog="magipi",
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert service.calls[0][0] == "run"
    assert service.calls[0][1] == "019e2200"
    assert service.calls[0][3].max_steps == 2
    assert service.calls[0][3].runtime_options.model_ref == "faux/local/faux-1"
    assert service.calls[0][3].runtime_options.cache_retention == "none"
    assert service.calls[0][4] is not None
    assert service.calls[0][5]["name"] == "guarded"
    assert "iterations:" in captured.out
    assert "step_status: done" in captured.out
    assert "stop_reason: max_steps_reached" in captured.out
    assert "steps_run: 1" in captured.out


def test_taskrun_run_rejects_interactive_permission_before_db(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        taskrun_commands,
        "load_database_config",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("db should not load")),
    )

    rc = taskrun_commands.run_taskrun_command(
        ["run", "--max-steps", "1", "--permission", "interactive"],
        prog="magipi",
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "interactive permission profile" in captured.err


@pytest.mark.parametrize("value", ["0", "-1", "51"])
def test_taskrun_run_rejects_invalid_max_steps_before_db(
    value: str,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        taskrun_commands,
        "load_database_config",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("db should not load")),
    )

    with pytest.raises(SystemExit) as exc:
        taskrun_commands.run_taskrun_command(
            ["run", "--max-steps", value],
            prog="magipi",
        )

    assert exc.value.code == 2


def test_taskrun_db_failure_returns_2_without_service(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        taskrun_commands,
        "load_database_config",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("missing db")),
    )

    rc = taskrun_commands.run_taskrun_command(["status"], prog="magipi")

    captured = capsys.readouterr()
    assert rc == 2
    assert "durable task storage unavailable" in captured.err
    assert "missing db" in captured.err


def test_taskrun_unknown_subcommand_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        taskrun_commands.run_taskrun_command(["unknown"], prog="magipi")

    assert exc.value.code == 2


def test_taskrun_unknown_permission_profile_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        taskrun_commands.run_taskrun_command(
            ["start", "--permission", "custom", "Analyze"],
            prog="magipi",
        )

    assert exc.value.code == 2


def test_taskrun_full_without_explicit_scope_fails_before_db(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        taskrun_commands,
        "load_database_config",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("db should not load")),
    )

    rc = taskrun_commands.run_taskrun_command(
        ["start", "--permission", "full", "Analyze"],
        prog="magipi",
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "requires explicit" in captured.err


def test_taskrun_start_does_not_swallow_late_env_file_flag() -> None:
    with pytest.raises(SystemExit) as exc:
        taskrun_commands.run_taskrun_command(
            ["start", "Analyze", "--env-file", "database.env"],
            prog="magipi",
        )

    assert exc.value.code == 2


def test_main_routes_taskrun_before_interactive_parser(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], *, prog: str) -> int:
        seen["argv"] = argv
        seen["prog"] = prog
        return 17

    monkeypatch.setattr(taskrun_commands, "run_taskrun_command", fake_run)

    assert cli_main.main(["taskrun", "status"]) == 17
    assert seen["argv"] == ["status"]
