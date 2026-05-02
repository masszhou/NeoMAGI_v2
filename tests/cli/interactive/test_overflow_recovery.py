from __future__ import annotations

import time
from pathlib import Path

from agent_core import Agent
from ai_provider.model_registry import get_model, register_model
from ai_provider.providers.faux import faux_assistant_message, stream_faux
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.streaming import AssistantMessageEventStream
from ai_provider.types import (
    AssistantMessage,
    Context,
    Model,
    StreamDone,
    TextContent,
    Usage,
    UsageCost,
    UserMessage,
)
from cli.core.session_manager import SessionManager
from cli.interactive.runtime import InteractiveAgentRuntime
from storage.in_memory_session_repository import InMemorySessionRepository

SUMMARY = """## Goal
Continue the task.
## Constraints & Preferences
Keep repo boundaries.
## Progress
### Done
Older context summarized.
### In Progress
Current work.
### Blocked
None.
## Key Decisions
Use durable session summaries.
## Next Steps
Continue.
## Critical Context
Important details.
<read-files>
</read-files>
<modified-files>
</modified-files>"""


class FakeSummaryGenerator:
    async def generate(self, _prompt: str, *, model) -> str:
        return SUMMARY


class LargeSummaryGenerator:
    async def generate(self, _prompt: str, *, model) -> str:
        return SUMMARY + "\n" + ("large summary " * 400)


def _drain_until_idle(runtime: InteractiveAgentRuntime, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    events = []
    while time.monotonic() < deadline:
        events.extend(runtime.drain_events())
        if not runtime.state.is_running and any(event.type == "agent_end" for event in events):
            events.extend(runtime.drain_events())
            return events
        time.sleep(0.01)
    raise AssertionError("runtime did not become idle")


def _assistant(
    model: Model,
    *,
    text: str,
    stop_reason: str = "stop",
    error_message: str | None = None,
    input_tokens: int = 0,
) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[TextContent(text=text)],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(
            input=input_tokens,
            output=1,
            cacheRead=0,
            cacheWrite=0,
            totalTokens=input_tokens + 1,
            cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
        ),
        stopReason=stop_reason,
        errorMessage=error_message,
        timestamp=1,
    )


def _done_stream(message: AssistantMessage) -> AssistantMessageEventStream:
    stream = AssistantMessageEventStream()
    stream.push(StreamDone(reason=message.stop_reason, message=message))
    return stream


def test_overflow_recovery_compacts_and_retries_without_persisting_first_error(
    tmp_path: Path,
) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="old")], timestamp=1),
    )
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="recent")], timestamp=2),
    )
    contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        contexts.append(context)
        if len(contexts) == 1:
            response = faux_assistant_message(
                "",
                model,
                stop_reason="error",
                error_message="prompt is too long",
            )
        else:
            response = "recovered"
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": response}),
        )

    def factory(options):
        options.stream_fn = stream_fn
        return Agent(options)

    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        tool_profile="none",
        summary_generator=FakeSummaryGenerator(),
        agent_factory=factory,
    )
    try:
        runtime.submit("current prompt")
        events = _drain_until_idle(runtime)
    finally:
        runtime.shutdown()

    entries = manager.repository.list_entries(session.id)
    assert [entry.entry_type for entry in entries] == [
        "message",
        "message",
        "message",
        "compaction",
        "message",
    ]
    assert entries[-1].payload.message.role == "assistant"
    assert entries[-1].payload.message.error_message is None
    assert entries[-1].payload.message.content[0].text == "recovered"
    assert len(contexts) == 2
    assert any(
        "<session-context type=\"compactionSummary\"" in message.content[0].text
        for message in contexts[1].messages
        if message.role == "user"
    )
    assert "auto_retry_start" in [event.type for event in events]
    assert "auto_retry_end" in [event.type for event in events]


def test_overflow_recovery_retry_still_overflow_compacts_once_and_fails_fast(
    tmp_path: Path,
) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="seed")], timestamp=1),
    )
    contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        contexts.append(context)
        response = faux_assistant_message(
            "",
            model,
            stop_reason="error",
            error_message="prompt is too long",
        )
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": response}),
        )

    def factory(options):
        options.stream_fn = stream_fn
        return Agent(options)

    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        tool_profile="none",
        summary_generator=FakeSummaryGenerator(),
        agent_factory=factory,
    )
    try:
        runtime.submit("current prompt")
        events = _drain_until_idle(runtime)
    finally:
        runtime.shutdown()

    assert len(contexts) == 2
    assert [entry.entry_type for entry in manager.repository.list_entries(session.id)].count(
        "compaction"
    ) == 1
    assert manager.repository.list_entries(session.id)[-1].payload.message.error_message
    assert [event.type for event in events].count("compaction_start") == 1
    assert any(
        event.type == "auto_retry_end" and event.success is False
        for event in events
    )


def test_non_overflow_error_does_not_trigger_compaction(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        contexts.append(context)
        response = faux_assistant_message(
            "",
            model,
            stop_reason="error",
            error_message="rate limit exceeded",
        )
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": response}),
        )

    def factory(options):
        options.stream_fn = stream_fn
        return Agent(options)

    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        tool_profile="none",
        summary_generator=FakeSummaryGenerator(),
        agent_factory=factory,
    )
    try:
        runtime.submit("current prompt")
        events = _drain_until_idle(runtime)
    finally:
        runtime.shutdown()

    assert len(contexts) == 1
    assert "compaction" not in [
        entry.entry_type for entry in manager.repository.list_entries(session.id)
    ]
    assert "compaction_start" not in [event.type for event in events]


def test_silent_overflow_triggers_compaction_and_retry(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="seed")], timestamp=1),
    )
    contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        contexts.append(context)
        if len(contexts) == 1:
            return _done_stream(
                _assistant(
                    model,
                    text="silent overflow",
                    stop_reason="stop",
                    input_tokens=model.context_window + 1,
                )
            )
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": "recovered"}),
        )

    def factory(options):
        options.stream_fn = stream_fn
        return Agent(options)

    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        tool_profile="none",
        summary_generator=FakeSummaryGenerator(),
        agent_factory=factory,
    )
    try:
        runtime.submit("current prompt")
        events = _drain_until_idle(runtime)
    finally:
        runtime.shutdown()

    assert len(contexts) == 2
    assert "compaction" in [
        entry.entry_type for entry in manager.repository.list_entries(session.id)
    ]
    assert "auto_retry_start" in [event.type for event in events]


def test_high_usage_non_stop_reasons_do_not_trigger_silent_overflow(
    tmp_path: Path,
) -> None:
    for stop_reason in ("length", "toolUse"):
        manager = SessionManager(InMemorySessionRepository())
        session = manager.new_session(tmp_path / stop_reason)
        contexts: list[Context] = []

        def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
            contexts.append(context)
            return _done_stream(
                _assistant(
                    model,
                    text=f"{stop_reason} response",
                    stop_reason=stop_reason,
                    input_tokens=model.context_window + 1,
                )
            )

        def factory(options):
            options.stream_fn = stream_fn
            return Agent(options)

        runtime = InteractiveAgentRuntime(
            cwd=tmp_path / stop_reason,
            session_manager=manager,
            tool_profile="none",
            summary_generator=FakeSummaryGenerator(),
            agent_factory=factory,
        )
        try:
            runtime.submit("current prompt")
            events = _drain_until_idle(runtime)
        finally:
            runtime.shutdown()

        assert len(contexts) == 1
        assert "compaction" not in [
            entry.entry_type for entry in manager.repository.list_entries(session.id)
        ]
        assert "compaction_start" not in [event.type for event in events]


def test_overflow_recovery_does_not_open_retry_stream_when_compaction_stays_over_budget(
    tmp_path: Path,
) -> None:
    constrained = get_model("faux", "faux-1").model_copy(
        update={"id": "faux-overflow-over-budget-test", "context_window": 16_784}
    )
    register_model(constrained)
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="seed")], timestamp=1),
    )
    contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        contexts.append(context)
        response = faux_assistant_message(
            "",
            model,
            stop_reason="error",
            error_message="prompt is too long",
        )
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": response}),
        )

    def factory(options):
        options.stream_fn = stream_fn
        return Agent(options)

    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        model_ref="faux/faux-overflow-over-budget-test",
        session_manager=manager,
        tool_profile="none",
        summary_generator=LargeSummaryGenerator(),
        agent_factory=factory,
    )
    try:
        runtime.submit("current prompt")
        events = _drain_until_idle(runtime)
    finally:
        runtime.shutdown()

    assert len(contexts) == 1
    assert "compaction" not in [
        entry.entry_type for entry in manager.repository.list_entries(session.id)
    ]
    assert any(
        event.type == "compaction_end" and event.aborted is True
        for event in events
    )


def test_auto_compaction_runs_before_provider_call_without_dropping_prompt(
    tmp_path: Path,
) -> None:
    tiny = get_model("faux", "faux-1").model_copy(
        update={"id": "faux-auto-compact-test", "context_window": 17_000}
    )
    register_model(tiny)
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="old " * 3000)], timestamp=1),
    )
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="recent")], timestamp=2),
    )
    contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        contexts.append(context)
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": "ok"}),
        )

    def factory(options):
        options.stream_fn = stream_fn
        return Agent(options)

    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        model_ref="faux/faux-auto-compact-test",
        session_manager=manager,
        tool_profile="none",
        summary_generator=FakeSummaryGenerator(),
        agent_factory=factory,
    )
    before_affinity = runtime.state.provider_cache_affinity_id
    try:
        runtime.submit("current prompt")
        events = _drain_until_idle(runtime)
        after_affinity = runtime.state.provider_cache_affinity_id
    finally:
        runtime.shutdown()

    assert before_affinity == after_affinity
    entries = manager.repository.list_entries(session.id)
    assert [entry.entry_type for entry in entries] == [
        "message",
        "message",
        "compaction",
        "message",
        "message",
    ]
    assert any(
        message.role == "user" and message.content[0].text == "current prompt"
        for message in contexts[0].messages
    )
    assert any(
        "<session-context type=\"compactionSummary\"" in message.content[0].text
        for message in contexts[0].messages
        if message.role == "user"
    )
    assert "compaction_start" in [event.type for event in events]


def test_small_context_does_not_auto_compact(
    tmp_path: Path,
) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        contexts.append(context)
        return stream_faux(
            model,
            context,
            SimpleStreamOptions(metadata={"response": "ok"}),
        )

    def factory(options):
        options.stream_fn = stream_fn
        return Agent(options)

    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        tool_profile="none",
        summary_generator=FakeSummaryGenerator(),
        agent_factory=factory,
    )
    try:
        runtime.submit("current prompt")
        events = _drain_until_idle(runtime)
    finally:
        runtime.shutdown()

    assert len(contexts) == 1
    assert "compaction" not in [
        entry.entry_type for entry in manager.repository.list_entries(session.id)
    ]
    assert "compaction_start" not in [event.type for event in events]
