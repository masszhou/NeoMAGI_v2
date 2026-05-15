from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core import Agent, AgentOptions
from ai_provider.providers.faux import faux_tool_call, stream_faux
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.types import Context, Model
from cli.core.session_manager import SessionManager
from cli.core.taskrun_runner import TaskRunHeadlessRunner
from cli.core.taskrun_service import TaskRunRuntimeOptions, TaskRunStepContext
from policy.permission_profiles import build_permission_profile_snapshot
from storage.in_memory_session_repository import InMemorySessionRepository
from storage.taskrun_repository import TaskRunRecord, TaskStepRecord


TASK_ID = "019e2200-0000-7000-8000-000000000001"
SESSION_ID = "019e2200-0000-7000-8000-000000000002"
STEP_ID = "019e2200-0000-7000-8000-000000000003"


class _TaskRepo:
    def __init__(self, sessions: InMemorySessionRepository) -> None:
        self.sessions = sessions
        self.permission_records: list[dict[str, Any]] = []

    def find_tool_execution_id(self, *, session_id: str, tool_call_id: str) -> str | None:
        for record in reversed(self.sessions.list_tool_executions(session_id)):
            if record.tool_call_id == tool_call_id:
                return record.id
        return None

    def append_permission_decision(self, **kwargs: Any) -> None:
        self.permission_records.append(dict(kwargs))


def test_headless_runner_writes_taskrun_summary_context_and_messages(tmp_path: Path) -> None:
    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    repo = _TaskRepo(sessions)
    captured: dict[str, AgentOptions] = {}

    def agent_factory(options: AgentOptions) -> Agent:
        captured["options"] = options
        return Agent(options)

    runner = TaskRunHeadlessRunner(
        session_manager=manager,
        task_repository=repo,
        cwd=tmp_path,
        agent_factory=agent_factory,
    )

    outcome = runner.run(_context(tmp_path))

    assert outcome.status == "done"
    entries = sessions.list_entries(SESSION_ID)
    assert [entry.entry_type for entry in entries] == ["custom_message", "message", "message"]
    custom = entries[0].payload
    assert custom.type == "custom_message"
    assert custom.custom_type == "taskRunSummary"
    provider_text = captured["options"].messages[0].content[0].text
    assert "<taskrun-context type=\"taskRunSummary\"" in provider_text
    assert TASK_ID in provider_text


def test_headless_runner_records_permission_with_step_and_tool_execution_id(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    repo = _TaskRepo(sessions)
    calls = 0

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        nonlocal calls
        if context.messages and context.messages[-1].role == "toolResult":
            return stream_faux(model, context, SimpleStreamOptions(metadata={"response": "done"}))
        calls += 1
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(
                metadata={
                    "response": [
                        faux_tool_call("read", {"path": "a.txt"}, id=f"call_{calls}")
                    ]
                }
            ),
        )

    def agent_factory(options: AgentOptions) -> Agent:
        options.stream_fn = stream_fn
        return Agent(options)

    runner = TaskRunHeadlessRunner(
        session_manager=manager,
        task_repository=repo,
        cwd=tmp_path,
        agent_factory=agent_factory,
    )

    outcome = runner.run(_context(tmp_path))

    assert outcome.status == "done"
    assert outcome.tool_count == 1
    assert outcome.permission_decision_count == 1
    assert len(repo.permission_records) == 1
    record = repo.permission_records[0]
    assert record["step_id"] == STEP_ID
    assert record["tool_execution_id"]
    assert record["resolved_decision"]["effect"] == "allow"


def _context(tmp_path: Path) -> TaskRunStepContext:
    task_run = TaskRunRecord(
        id=TASK_ID,
        workspace_root=str(tmp_path),
        agent_session_id=SESSION_ID,
        goal="Analyze this repo",
        status="running",
        permission_profile=build_permission_profile_snapshot("guarded"),
        budget={},
        stop_conditions={},
        summary={},
        current_step_id=STEP_ID,
        heartbeat_at="2026-05-13T00:00:00+00:00",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T00:00:00+00:00",
    )
    step = TaskStepRecord(
        id=STEP_ID,
        task_run_id=TASK_ID,
        step_index=1,
        title="Step 1",
        status="running",
        input={},
        output={},
        started_at="2026-05-13T00:00:00+00:00",
    )
    return TaskRunStepContext(
        task_run=task_run,
        step=step,
        summary={"goal": "Analyze this repo", "next_action": "step"},
        runtime_options=TaskRunRuntimeOptions(model_ref="faux/local/faux-1"),
        workspace_root=str(tmp_path),
        heartbeat=lambda: None,
    )
