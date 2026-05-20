from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agent_core import Agent, AgentOptions
from ai_provider.providers.faux import faux_assistant_message, faux_tool_call, stream_faux
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.types import Context, Model, TextContent, UserMessage
from cli.core.session_manager import SessionManager
from cli.core.taskrun_runner import TaskRunHeadlessRunner
from cli.core.taskrun_service import TaskRunRuntimeOptions, TaskRunStepContext
from cli.core.taskrun_step import TaskRunStepOutcome
from policy.permission_profiles import build_permission_profile_snapshot
from storage.in_memory_session_repository import InMemorySessionRepository
from storage.taskrun_repository import TaskRunRecord, TaskStepRecord


TASK_ID = "019e2200-0000-7000-8000-000000000001"
SESSION_ID = "019e2200-0000-7000-8000-000000000002"
STEP_ID = "019e2200-0000-7000-8000-000000000003"


class _FakeDecisionRecord:
    """Lightweight stand-in for ``TaskPermissionDecisionRecord`` used by
    the runner's finalize NULL-check (it reads attribute access)."""

    def __init__(self, **kwargs: Any) -> None:
        self.task_run_id = kwargs.get("task_run_id")
        self.step_id = kwargs.get("step_id")
        self.tool_execution_id = kwargs.get("tool_execution_id")
        self.policy_request = kwargs.get("policy_request") or {}
        self.raw_decision = kwargs.get("raw_decision") or {}
        self.resolved_decision = kwargs.get("resolved_decision") or {}
        self.profile_name = kwargs.get("profile_name", "")
        self.occurred_at = kwargs.get("occurred_at", "")
        self.id = kwargs.get("id", "")


class _TaskRepo:
    def __init__(self, sessions: InMemorySessionRepository) -> None:
        self.sessions = sessions
        self.permission_records: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def find_tool_execution_id(self, *, session_id: str, tool_call_id: str) -> str | None:
        for record in reversed(self.sessions.list_tool_executions(session_id)):
            if record.tool_call_id == tool_call_id:
                return record.id
        return None

    def append_permission_decision(self, **kwargs: Any) -> None:
        self.permission_records.append(dict(kwargs))

    def list_permission_decisions(
        self,
        task_run_id: str,
        *,
        step_id: str | None = None,
    ) -> list[Any]:
        out: list[Any] = []
        for record in self.permission_records:
            if record.get("task_run_id") != task_run_id:
                continue
            if step_id is not None and record.get("step_id") != step_id:
                continue
            out.append(_FakeDecisionRecord(**record))
        return out

    def append_event(self, **kwargs: Any) -> None:
        self.events.append(dict(kwargs))

    def backfill_permission_decision_tool_execution_id(
        self,
        *,
        task_run_id: str,
        step_id: str,
        tool_call_id: str,
        tool_execution_id: str,
    ) -> int:
        affected = 0
        for record in self.permission_records:
            if (
                record.get("task_run_id") == task_run_id
                and record.get("step_id") == step_id
                and record.get("tool_execution_id") is None
            ):
                source = (record.get("policy_request") or {}).get("source") or {}
                if source.get("tool_call_id") == tool_call_id:
                    record["tool_execution_id"] = tool_execution_id
                    affected += 1
        return affected


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


def test_headless_runner_compaction_failure_does_not_append_taskrun_summary(
    tmp_path: Path,
) -> None:
    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    repo = _TaskRepo(sessions)

    class FailingCompactionRunner(TaskRunHeadlessRunner):
        def _auto_compact_before_prompt(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("compaction failed")

    runner = FailingCompactionRunner(
        session_manager=manager,
        task_repository=repo,
        cwd=tmp_path,
    )

    with pytest.raises(RuntimeError, match="compaction failed"):
        runner.run(_context(tmp_path))

    assert sessions.list_entries(SESSION_ID) == []


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
    # D11: hook wrote with NULL, consumer back-filled via the new repository API.
    assert record["tool_execution_id"]
    assert record["resolved_decision"]["effect"] == "allow"
    # D10: a derived policy event is appended; tool-detail event stays Tier 1.
    policy_events = [event for event in repo.events if event["event_type"].startswith("task_tool_policy_")]
    assert len(policy_events) == 1
    assert policy_events[0]["event_type"] == "task_tool_policy_resolved"
    assert policy_events[0]["payload"]["payload_version"] == 1
    assert policy_events[0]["payload"]["effect"] == "allow"


def test_headless_runner_blocks_step_when_claim_lacks_evidence(tmp_path: Path) -> None:
    """R4 counter-example: model claims it ran tests but no test tool fired.
    Step must land blocked + verification_state=missing_evidence (D12)."""

    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    repo = _TaskRepo(sessions)

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": "Ran the suite; tests passed."}),
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

    assert outcome.status == "blocked"
    assert outcome.verification_state == "missing_evidence"
    block_reason = outcome.block_reason or ""
    assert block_reason  # populated by the D12 lifecycle mapping

    event_types = {event["event_type"] for event in repo.events}
    assert "task_step_evidence_missing" in event_types
    assert "task_step_outcome_unsupported" in event_types
    assert "task_step_outcome_supported" not in event_types


def _build_read_tool_step_runner(tmp_path: Path) -> tuple[TaskRunHeadlessRunner, "_TaskRepo"]:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    repo = _TaskRepo(sessions)
    calls = 0

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        nonlocal calls
        if context.messages and context.messages[-1].role == "toolResult":
            return stream_faux(model, context, SimpleStreamOptions(metadata={"response": "Done."}))
        calls += 1
        return stream_faux(
            model, context,
            SimpleStreamOptions(metadata={
                "response": [faux_tool_call("read", {"path": "a.txt"}, id=f"call_{calls}")]
            }),
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
    return runner, repo


def test_end_to_end_emits_derived_event_chain(tmp_path: Path) -> None:
    """One step with a real tool call exercises the full D10-D12 event chain:
    task_tool_policy_resolved → task_tool_observed → task_step_evidence_*
    → task_step_outcome_* → task_step_resume_context_generated."""

    runner, repo = _build_read_tool_step_runner(tmp_path)
    outcome = runner.run(_context(tmp_path))

    assert outcome.status == "done"
    types = [event["event_type"] for event in repo.events]
    expected_chain = [
        "task_tool_policy_resolved",
        "task_tool_observed",
        "task_step_evidence_recorded",
        "task_step_outcome_supported",
        "task_step_resume_context_generated",
    ]
    for event_type in expected_chain:
        assert event_type in types
    positions = [types.index(event_type) for event_type in expected_chain]
    assert positions == sorted(positions), f"unexpected order: {types}"

    resume = next(event for event in repo.events if event["event_type"] == "task_step_resume_context_generated")
    assert resume["payload"]["payload_version"] == 1
    observed = next(event for event in repo.events if event["event_type"] == "task_tool_observed")
    assert observed["payload"]["payload_version"] == 1
    assert observed["payload"]["tool_name"] == "read"
    assert observed["payload"]["evidence_kind"] == "read"
    assert observed["payload"]["is_error"] is False
    policy_resolved = next(event for event in repo.events if event["event_type"] == "task_tool_policy_resolved")
    assert policy_resolved["payload"]["effect"] == "allow"


def test_back_fill_failure_marks_step_failed(tmp_path: Path) -> None:
    """When the consumer's back-fill update fails (e.g., schema invariant
    breakage), the session error sink flips the step to failed."""

    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    repo = _TaskRepo(sessions)

    def bad_backfill(**_kwargs: Any) -> int:
        return 0  # Hook never matched → fail-closed contract requires == 1.

    repo.backfill_permission_decision_tool_execution_id = bad_backfill  # type: ignore[assignment]
    calls = 0

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        nonlocal calls
        if context.messages and context.messages[-1].role == "toolResult":
            return stream_faux(model, context, SimpleStreamOptions(metadata={"response": "Done."}))
        calls += 1
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(
                metadata={
                    "response": [faux_tool_call("read", {"path": "a.txt"}, id=f"call_{calls}")]
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

    assert outcome.status == "failed"
    assert outcome.error_message == "step finalization sink failed"
    sinks = {error.get("sink") for error in outcome.finalize_errors}
    assert "event_hook" in sinks


def test_emit_step_outcome_events_routes_error_to_unsupported(tmp_path: Path) -> None:
    """G1: verification_state=='error' must emit outcome_unsupported
    (never outcome_supported). The previous dispatch only treated
    blocking states as unsupported and let ``error`` slip through."""

    from cli.core.taskrun_agent_session import StepEventCollector

    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    repo = _TaskRepo(sessions)
    runner = TaskRunHeadlessRunner(
        session_manager=SessionManager(sessions, include_taskrun_owned=True),
        task_repository=repo,
        cwd=tmp_path,
    )

    outcome = TaskRunStepOutcome(
        status="failed",
        assistant_text="boom",
        error_message="provider hung up",
        verification_state="error",
        verification_reason="terminal assistant error",
    )
    collector = StepEventCollector()
    runner._emit_step_outcome_events(_context(tmp_path), outcome, collector)

    types = [event["event_type"] for event in repo.events]
    assert "task_step_outcome_supported" not in types
    assert "task_step_outcome_unsupported" in types
    unsupported = next(e for e in repo.events if e["event_type"] == "task_step_outcome_unsupported")
    assert unsupported["payload"]["verification_state"] == "error"


def test_hook_block_lands_blocked_step_with_permission_counted(tmp_path: Path) -> None:
    """H2: hook-side ``before_tool_call`` block must surface as:
    - ``outcome.status == "blocked"`` (NOT failed),
    - ``permission_decision_count == 1`` (the hook wrote one row),
    - no ``task_step_outcome_supported`` event (lifecycle ≠ done),
    - ``task_tool_policy_blocked`` event present.

    Uses ``guarded`` profile (commands.allow=[] by default → bash blocked
    at the resolver). The agent_core error result has no
    ``policyDecision`` details, so the collector relies on the
    PolicyResolutionStore side-channel to distinguish hook-block from
    a regular tool error."""

    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    repo = _TaskRepo(sessions)

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        if context.messages and context.messages[-1].role == "toolResult":
            return stream_faux(
                model, context,
                SimpleStreamOptions(metadata={"response": "Cannot proceed."}),
            )
        return stream_faux(
            model, context,
            SimpleStreamOptions(metadata={
                "response": [faux_tool_call("bash", {"command": "ls"}, id="call_bash_1")]
            }),
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

    assert outcome.status == "blocked"
    assert outcome.block_reason
    assert outcome.permission_decision_count == 1
    types = [event["event_type"] for event in repo.events]
    assert "task_tool_policy_blocked" in types
    assert "task_step_outcome_supported" not in types
    blocked = next(e for e in repo.events if e["event_type"] == "task_tool_policy_blocked")
    assert blocked["payload"]["effect"] == "block"


def test_emit_step_outcome_events_failed_step_with_supported_verification_emits_unsupported(
    tmp_path: Path,
) -> None:
    """H1: a tool error / listener failure / policy block can leave
    ``verification_state="supported"`` (no claim → no evidence required)
    on a step whose lifecycle status is ``failed`` / ``blocked``.
    Writing outcome_supported for those is wrong fact — dispatch must
    require BOTH ``status == "done"`` AND ``verification == supported``."""

    from cli.core.taskrun_agent_session import StepEventCollector

    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    repo = _TaskRepo(sessions)
    runner = TaskRunHeadlessRunner(
        session_manager=SessionManager(sessions, include_taskrun_owned=True),
        task_repository=repo,
        cwd=tmp_path,
    )

    outcome = TaskRunStepOutcome(
        status="failed",
        assistant_text="Working on it.",
        error_message="bash tool errored",
        verification_state="supported",
    )
    collector = StepEventCollector()
    runner._emit_step_outcome_events(_context(tmp_path), outcome, collector)

    types = [event["event_type"] for event in repo.events]
    assert "task_step_outcome_supported" not in types
    assert "task_step_outcome_unsupported" in types


def test_emit_step_outcome_events_inconsistent_does_not_write_evidence_missing(
    tmp_path: Path,
) -> None:
    """G2: an inconsistent outcome carries inconsistent_kinds, not
    missing_kinds. Runner must read the outcome fields directly so it
    no longer turns failing-pytest into a spurious evidence_missing(test)."""

    from cli.core.taskrun_agent_session import StepEventCollector

    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    repo = _TaskRepo(sessions)
    runner = TaskRunHeadlessRunner(
        session_manager=SessionManager(sessions, include_taskrun_owned=True),
        task_repository=repo,
        cwd=tmp_path,
    )

    outcome = TaskRunStepOutcome(
        status="failed",
        assistant_text="Ran the suite; tests passed.",
        error_message="pytest exit 1",
        verification_state="inconsistent",
        verification_reason="claim contradicts observed tool errors",
        verification_inconsistent_kinds=("test",),
        verification_missing_kinds=(),
    )
    collector = StepEventCollector()
    runner._emit_step_outcome_events(_context(tmp_path), outcome, collector)

    types = [event["event_type"] for event in repo.events]
    assert "task_step_evidence_missing" not in types
    assert "task_step_outcome_unsupported" in types
    unsupported = next(e for e in repo.events if e["event_type"] == "task_step_outcome_unsupported")
    assert unsupported["payload"]["verification_state"] == "inconsistent"


def test_finalize_invariant_writes_blocker_when_permission_decision_null(
    tmp_path: Path,
) -> None:
    """W4 spec: a step whose permission_decisions row survived without
    back-fill (e.g., abort / lost event) must demote to blocked AND emit
    task_step_blocker_detected so the failure is visible in the history view."""

    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    repo = _TaskRepo(sessions)
    # Inject a hook-written but never-back-filled row directly into the
    # fixture, simulating a consumer that exited before processing the
    # matching tool_execution_start.
    repo.permission_records.append(
        {
            "task_run_id": TASK_ID,
            "step_id": STEP_ID,
            "tool_execution_id": None,
            "policy_request": {"source": {"tool_call_id": "orphan_call"}},
            "raw_decision": {"effect": "allow"},
            "resolved_decision": {"effect": "allow"},
            "profile_name": "guarded",
            "occurred_at": "2026-05-18T00:00:00+00:00",
        }
    )

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        return stream_faux(
            model, context,
            SimpleStreamOptions(metadata={"response": "Status update."}),
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

    assert outcome.status == "blocked"
    assert outcome.block_reason == "permission decisions missing tool_execution_id back-fill"
    sinks = {error.get("sink") for error in outcome.finalize_errors}
    assert "permission_decision_back_fill" in sinks
    blocker_events = [
        event for event in repo.events
        if event["event_type"] == "task_step_blocker_detected"
    ]
    assert len(blocker_events) == 1
    payload = blocker_events[0]["payload"]
    assert payload["payload_version"] == 1
    assert payload["reason"] == "permission decisions missing tool_execution_id back-fill"
    assert payload["detail"]["tool_call_ids"] == ["orphan_call"]


def test_finalize_invariant_ignores_host_command_permission_decisions(
    tmp_path: Path,
) -> None:
    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    repo = _TaskRepo(sessions)
    repo.permission_records.append(
        {
            "task_run_id": TASK_ID,
            "step_id": STEP_ID,
            "tool_execution_id": None,
            "policy_request": {
                "source": {
                    "host": "task_run",
                    "decision_subject": "host_command",
                    "phase": "baseline",
                }
            },
            "raw_decision": {"effect": "allow"},
            "resolved_decision": {"effect": "allow"},
            "profile_name": "guarded",
            "occurred_at": "2026-05-18T00:00:00+00:00",
        }
    )

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": "Status update."}),
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
    assert not any(
        event["event_type"] == "task_step_blocker_detected" for event in repo.events
    )


def test_headless_runner_marks_step_done_when_no_claim(tmp_path: Path) -> None:
    """Sanity: bland assistant text → no claim → verification supported."""

    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    repo = _TaskRepo(sessions)

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": "Here is a status update."}),
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
    assert outcome.verification_state == "supported"
    event_types = {event["event_type"] for event in repo.events}
    assert "task_step_outcome_supported" in event_types
    assert "task_step_outcome_unsupported" not in event_types


def test_headless_runner_records_overflow_compaction_and_auto_retry(
    tmp_path: Path,
) -> None:
    sessions = InMemorySessionRepository()
    sessions.create_session(cwd=str(tmp_path), session_id=SESSION_ID, source={"taskRunOwned": True})
    manager = SessionManager(sessions, include_taskrun_owned=True)
    manager.append_message(
        SESSION_ID,
        UserMessage(content=[TextContent(text="old context")], timestamp=1),
    )
    manager.append_message(
        SESSION_ID,
        UserMessage(content=[TextContent(text="recent context")], timestamp=2),
    )
    repo = _TaskRepo(sessions)
    calls = {"n": 0}

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        del options
        calls["n"] += 1
        if calls["n"] == 1:
            overflow = faux_assistant_message("", model)
            overflow.stop_reason = "error"
            overflow.error_message = "prompt is too long"
            return stream_faux(
                model,
                context,
                SimpleStreamOptions(metadata={"response": overflow}),
            )
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": "Recovered after compaction."}),
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

    assert calls["n"] == 2
    assert outcome.status == "done"
    event_types = [event["event_type"] for event in repo.events]
    assert "task_runtime_compaction_observed" in event_types
    assert "task_runtime_auto_retry_observed" in event_types
    retry_event = next(
        event for event in repo.events
        if event["event_type"] == "task_runtime_auto_retry_observed"
    )
    assert retry_event["payload"]["success"] is True


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
