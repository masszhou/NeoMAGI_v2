from __future__ import annotations

from pathlib import Path

import pytest

import cli.__main__ as cli_main
import cli.taskrun_commands as taskrun_commands
from cli.core.taskrun_projection import TaskRunProjectionResult
from cli.core.taskrun_service import TaskRunResult
from storage.taskrun_repository import TaskRunRecord


class _Conn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeService:
    def __init__(self, result: TaskRunResult) -> None:
        self.result = result
        self.calls: list[tuple[str, object, Path]] = []

    def start(self, goal: str, cwd: Path) -> TaskRunResult:
        self.calls.append(("start", goal, cwd))
        return self.result

    def status(self, task_id: str | None, cwd: Path) -> TaskRunResult:
        self.calls.append(("status", task_id, cwd))
        return self.result

    def summary(self, task_id: str | None, cwd: Path) -> TaskRunResult:
        self.calls.append(("summary", task_id, cwd))
        return self.result

    def close(self, task_id: str | None, cwd: Path) -> TaskRunResult:
        self.calls.append(("close", task_id, cwd))
        return self.result


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


def _stub_runtime(monkeypatch, service: _FakeService) -> _Conn:
    conn = _Conn()
    monkeypatch.setattr(taskrun_commands, "load_database_config", lambda **_kwargs: object())
    monkeypatch.setattr(taskrun_commands, "connect_database", lambda _config: conn)
    monkeypatch.setattr(taskrun_commands, "ensure_schema", lambda _conn, _config: None)
    monkeypatch.setattr(
        taskrun_commands,
        "PostgresTaskRunRepository",
        lambda _conn, _config: object(),
    )
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
