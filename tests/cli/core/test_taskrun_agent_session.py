"""D13 TaskRunAgentSession behavior: listener queue, fail-closed, tool_call map."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from agent_core import Agent, RuntimeAgentTool
from agent_core.types import AgentToolResult
from ai_provider.model_registry import get_model
from ai_provider.providers.faux import faux_tool_call, stream_faux
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.types import Context, Model
from cli.core.session_manager import SessionManager
from cli.core.taskrun_agent_session import StepEventCollector, TaskRunAgentSession
from cli.core.session_types import AgentSessionEvent
from cli.interactive.session_writer import DurableSessionEventWriter
from storage.in_memory_session_repository import InMemorySessionRepository
from ai_provider.types import TextContent, UserMessage


SESSION_ID = "019e2300-0000-7000-8000-000000000001"


def _model() -> Model:
    return get_model("faux", "faux-1")


def _text_result(text: str) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": text}], details={"text": text})


def _tool(name: str, execute: Any) -> RuntimeAgentTool:
    return RuntimeAgentTool(
        name=name,
        label=name.title(),
        description=f"{name} test tool",
        parameters={"type": "object"},
        execute=execute,
    )


def _prompt() -> UserMessage:
    return UserMessage(role="user", content=[TextContent(text="hi")], timestamp=1)


def _stream_returning_tool_then_done(tool_name: str, tool_args: dict[str, Any] | None = None):
    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        has_tool_result = any(message.role == "toolResult" for message in context.messages)
        response = (
            "ok"
            if has_tool_result
            else [faux_tool_call(tool_name, tool_args or {})]
        )
        return stream_faux(model, context, SimpleStreamOptions(metadata={"response": response}))

    return stream_fn


def _stream_no_tool():
    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        return stream_faux(model, context, SimpleStreamOptions(metadata={"response": "ok"}))

    return stream_fn


def _build_writer(manager: SessionManager) -> DurableSessionEventWriter:
    return DurableSessionEventWriter(
        manager=manager,
        session_id_provider=lambda: SESSION_ID,
        runtime_session_id_provider=lambda: "rt-1",
        run_id_provider=lambda: None,
    )


def _session_manager() -> tuple[InMemorySessionRepository, SessionManager]:
    repo = InMemorySessionRepository()
    repo.create_session(cwd="/tmp/ws", session_id=SESSION_ID, source={"taskRunOwned": True})
    return repo, SessionManager(repo, include_taskrun_owned=True)


async def _quick_tool(_call_id: str, _args: dict[str, Any], _signal: Any, _on_update: Any) -> AgentToolResult:
    return _text_result("ok")


def test_listener_queue_preserves_order_with_awaiting_event_hook() -> None:
    async def run() -> None:
        _, manager = _session_manager()
        writer = _build_writer(manager)
        agent = Agent(
            model=_model(),
            stream_fn=_stream_returning_tool_then_done("noop"),
            tools=[_tool("noop", _quick_tool)],
        )
        recorded: list[str] = []

        async def event_hook(event: Any) -> None:
            # Force an await boundary inside the hook to exercise ordering.
            await asyncio.sleep(0)
            recorded.append(event.type)

        session = TaskRunAgentSession(
            agent=agent,
            durable_writer=writer,
            heartbeat=lambda: None,
            event_hook=event_hook,
        )
        outcome = await session.run(_prompt())
        assert outcome.status == "done"
        # The consumer is single-task, so even though the hook awaits inside
        # each handler, ordering is preserved end to end. The agent emits a
        # deterministic frame sequence for tool-then-done; we don't pin every
        # frame name (faux + tool_executor decide that), only the relative
        # order of the structural markers.
        positions = {name: recorded.index(name) for name in [
            "agent_start",
            "tool_execution_start",
            "tool_execution_end",
            "agent_end",
        ]}
        assert positions["agent_start"] < positions["tool_execution_start"]
        assert positions["tool_execution_start"] < positions["tool_execution_end"]
        assert positions["tool_execution_end"] < positions["agent_end"]

    asyncio.run(run())


def test_writer_failure_in_listener_marks_step_failed() -> None:
    async def run() -> None:
        _, manager = _session_manager()
        original_record = DurableSessionEventWriter.record
        call_count = {"n": 0}

        def fail_on_tool_start(self: DurableSessionEventWriter, event: AgentSessionEvent) -> None:
            if event.type == "tool_execution_start":
                call_count["n"] += 1
                raise RuntimeError("synthetic writer failure")
            original_record(self, event)

        writer = _build_writer(manager)
        writer.record = fail_on_tool_start.__get__(writer, DurableSessionEventWriter)

        agent = Agent(
            model=_model(),
            stream_fn=_stream_returning_tool_then_done("noop"),
            tools=[_tool("noop", _quick_tool)],
        )
        session = TaskRunAgentSession(
            agent=agent,
            durable_writer=writer,
            heartbeat=lambda: None,
        )
        outcome = await session.run(_prompt())
        assert call_count["n"] == 1
        assert outcome.status == "failed"
        assert outcome.error_message == "step finalization sink failed"
        sinks = [error.get("sink") for error in outcome.finalize_errors]
        assert "durable_session_writer" in sinks

    asyncio.run(run())


def test_event_hook_failure_marks_step_failed() -> None:
    async def run() -> None:
        _, manager = _session_manager()
        writer = _build_writer(manager)

        async def hook(event: Any) -> None:
            if event.type == "tool_execution_end":
                raise RuntimeError("synthetic hook failure")

        agent = Agent(
            model=_model(),
            stream_fn=_stream_returning_tool_then_done("noop"),
            tools=[_tool("noop", _quick_tool)],
        )
        session = TaskRunAgentSession(
            agent=agent,
            durable_writer=writer,
            heartbeat=lambda: None,
            event_hook=hook,
        )
        outcome = await session.run(_prompt())
        assert outcome.status == "failed"
        sinks = [error.get("sink") for error in outcome.finalize_errors]
        assert "event_hook" in sinks

    asyncio.run(run())


def test_heartbeat_failure_marks_step_failed_without_blocking_run() -> None:
    async def run() -> None:
        _, manager = _session_manager()
        writer = _build_writer(manager)
        agent = Agent(
            model=_model(),
            stream_fn=_stream_no_tool(),
        )

        def heartbeat() -> None:
            raise RuntimeError("heartbeat boom")

        session = TaskRunAgentSession(
            agent=agent,
            durable_writer=writer,
            heartbeat=heartbeat,
        )
        outcome = await session.run(_prompt())
        assert outcome.status == "failed"
        sinks = {error.get("sink") for error in outcome.finalize_errors}
        assert "heartbeat" in sinks

    asyncio.run(run())


def test_tool_call_state_map_populated_on_start_cleared_on_end() -> None:
    async def run() -> None:
        _, manager = _session_manager()
        writer = _build_writer(manager)
        captured: dict[str, Any] = {}

        async def execute(call_id: str, _args: dict[str, Any], _signal: Any, _on_update: Any) -> AgentToolResult:
            return _text_result(call_id)

        agent = Agent(
            model=_model(),
            stream_fn=_stream_returning_tool_then_done("inspect", {"x": 1}),
            tools=[_tool("inspect", execute)],
        )

        async def hook(event: Any) -> None:
            if event.type == "tool_execution_start":
                state = session.tool_call_state(event.tool_call_id)
                captured["on_start"] = (state.tool_name, state.args)
            if event.type == "tool_execution_end":
                state = session.tool_call_state(event.tool_call_id)
                captured["on_end_present"] = state is not None

        session = TaskRunAgentSession(
            agent=agent,
            durable_writer=writer,
            heartbeat=lambda: None,
            event_hook=hook,
        )
        outcome = await session.run(_prompt())
        assert outcome.status == "done"
        assert captured["on_start"] == ("inspect", {"x": 1})
        # State map still holds the entry when the end hook runs (so a D12
        # classifier hook can read tool_name+args from the end event).
        assert captured["on_end_present"] is True
        # After the consumer finishes processing end, state is cleared so a
        # missing entry for a fresh tool_call_id is a true lost-start signal.
        assert session.tool_call_state("nonexistent") is None

    asyncio.run(run())


def test_cancel_aborts_agent() -> None:
    async def run() -> None:
        _, manager = _session_manager()
        writer = _build_writer(manager)

        proceed = asyncio.Event()

        async def execute(_call_id: str, _args: dict[str, Any], signal: Any, _on_update: Any) -> AgentToolResult:
            await asyncio.wait_for(proceed.wait(), timeout=2.0)
            return _text_result("late")

        agent = Agent(
            model=_model(),
            stream_fn=_stream_returning_tool_then_done("waiter"),
            tools=[_tool("waiter", execute)],
        )
        session = TaskRunAgentSession(
            agent=agent,
            durable_writer=writer,
            heartbeat=lambda: None,
        )

        async def trigger_cancel() -> None:
            # Wait for the agent to start, then cancel.
            await asyncio.sleep(0.05)
            session.cancel()
            proceed.set()

        cancel_task = asyncio.create_task(trigger_cancel())
        await session.run(_prompt())
        await cancel_task
        # ``agent.abort`` set the signal; the agent's loop emits its abort
        # bookkeeping so the agent run terminates cleanly without raising.

    asyncio.run(run())


def test_cancel_requested_callback_marks_outcome_cancelled() -> None:
    async def run() -> None:
        _, manager = _session_manager()
        writer = _build_writer(manager)
        agent = Agent(
            model=_model(),
            stream_fn=_stream_no_tool(),
        )
        session = TaskRunAgentSession(
            agent=agent,
            durable_writer=writer,
            heartbeat=lambda: None,
            cancel_requested=lambda: True,
        )

        outcome = await session.run(_prompt())

        assert outcome.status == "cancelled"
        assert outcome.error_message == "cancelled by TaskRun cancel request"

    asyncio.run(run())


def test_cancel_requested_is_throttled_for_token_delta_events() -> None:
    checks = {"count": 0}
    aborts: list[bool] = []
    session = TaskRunAgentSession(
        agent=SimpleNamespace(active_run_id=None, abort=lambda: aborts.append(True)),
        durable_writer=SimpleNamespace(record=lambda _event: None),
        heartbeat=lambda: None,
        cancel_requested=lambda: checks.__setitem__("count", checks["count"] + 1) or False,
    )

    for _ in range(20):
        session._on_event(SimpleNamespace(type="message_update"), asyncio.Event())  # noqa: SLF001

    session._on_event(  # noqa: SLF001
        SimpleNamespace(type="tool_execution_start"),
        asyncio.Event(),
    )

    assert checks["count"] == 2
    assert aborts == []


def test_step_event_collector_finalize_errors_collapse_to_failed() -> None:
    collector = StepEventCollector()
    collector.listener_errors.append({"sink": "x", "exceptionClass": "RuntimeError", "message": "boom"})
    outcome = collector.outcome()
    assert outcome.status == "failed"
    assert outcome.error_message == "step finalization sink failed"
    assert outcome.finalize_errors == [
        {"sink": "x", "exceptionClass": "RuntimeError", "message": "boom"}
    ]
    # D12: verification_state must be attached even on the listener-error
    # path (no claim text + no observations → supported by default).
    assert outcome.verification_state is not None


def test_step_event_collector_inconsistent_when_tool_errors_under_claim() -> None:
    """R4 inconsistent path: assistant claims tests passed but the test
    tool errored. Status = failed AND verification_state = inconsistent
    must both be attached so downstream consumers see *why*."""

    from cli.core.evidence_classifier import EVIDENCE_TEST, EvidenceObservation

    collector = StepEventCollector(
        assistant_text="Ran the suite; tests passed.",
        tool_error="pytest exit 1",
    )
    collector.evidence_observations.append(
        EvidenceObservation(
            tool_call_id="call_test",
            tool_name="pytest",
            is_error=True,
            evidence_kind=EVIDENCE_TEST,
        )
    )
    outcome = collector.outcome()
    assert outcome.status == "failed"
    assert outcome.error_message == "pytest exit 1"
    assert outcome.verification_state == "inconsistent"


def test_step_event_collector_error_state_carries_through() -> None:
    """When the assistant terminal-errored, verification_state='error' is
    attached alongside the existing error_message status path."""

    collector = StepEventCollector(
        assistant_text="some text",
        error_message="provider hung up",
    )
    outcome = collector.outcome()
    assert outcome.status == "failed"
    assert outcome.error_message == "provider hung up"
    assert outcome.verification_state == "error"


def test_step_event_collector_block_path_keeps_verification_state() -> None:
    collector = StepEventCollector(
        assistant_text="",
        block_reason="policy blocked tool",
    )
    outcome = collector.outcome()
    assert outcome.status == "blocked"
    assert outcome.block_reason == "policy blocked tool"
    # No claim, no observations → supported is the natural fallback so
    # the field still carries a value (D12: complementary signal).
    assert outcome.verification_state == "supported"


def test_apply_verification_to_lifecycle_handles_every_state() -> None:
    """Defensive: every defined verification_state must have an explicit
    lifecycle mapping, so a future taxonomy addition fails loudly rather
    than mapping to ``done`` silently."""

    from cli.core.evidence_classifier import (
        VERIFICATION_ABANDONED,
        VERIFICATION_ERROR,
        VERIFICATION_INCONSISTENT,
        VERIFICATION_MISSING_EVIDENCE,
        VERIFICATION_SUPPORTED,
    )
    from cli.core.taskrun_agent_session import _apply_verification_to_lifecycle

    assert _apply_verification_to_lifecycle(VERIFICATION_SUPPORTED)[0] == "done"
    assert _apply_verification_to_lifecycle(VERIFICATION_MISSING_EVIDENCE)[0] == "blocked"
    assert _apply_verification_to_lifecycle(VERIFICATION_ABANDONED)[0] == "blocked"
    assert _apply_verification_to_lifecycle(VERIFICATION_INCONSISTENT)[0] == "failed"
    assert _apply_verification_to_lifecycle(VERIFICATION_ERROR)[0] == "failed"


def test_apply_verification_to_lifecycle_raises_on_unknown_state() -> None:
    """G3: an unrecognised verification_state must raise, not silently map
    to ``done``. A future taxonomy addition then fails its own tests
    rather than producing wrong-fact ``done`` steps."""

    import pytest as _pytest

    from cli.core.taskrun_agent_session import _apply_verification_to_lifecycle

    with _pytest.raises(ValueError, match="unrecognised verification_state"):
        _apply_verification_to_lifecycle("totally_unknown")


def test_outcome_carries_missing_and_inconsistent_kinds() -> None:
    """G2 transport: VerificationResult.missing_kinds / inconsistent_kinds
    must arrive on the outcome unchanged so the runner doesn't re-derive
    them (the re-derive path lost the inconsistent-vs-missing distinction)."""

    from cli.core.evidence_classifier import EVIDENCE_TEST, EvidenceObservation

    # Inconsistent case: claim says tests passed, evidence has only failing
    # test → outcome should report inconsistent_kinds=("test",) and
    # missing_kinds=() — NOT missing_kinds=("test",).
    collector = StepEventCollector(
        assistant_text="Ran the suite; tests passed.",
        tool_error="pytest exit 1",
    )
    collector.evidence_observations.append(
        EvidenceObservation(
            tool_call_id="call_t",
            tool_name="pytest",
            is_error=True,
            evidence_kind=EVIDENCE_TEST,
        )
    )
    outcome = collector.outcome()
    assert outcome.verification_state == "inconsistent"
    assert outcome.verification_inconsistent_kinds == ("test",)
    assert outcome.verification_missing_kinds == ()
