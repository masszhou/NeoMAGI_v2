from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_core import Agent
from agent_core.types import (
    AgentEventAdapter,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from ai_provider.model_registry import get_model
from cli.core.session_types import AgentSessionEventAdapter
from cli.interactive.runtime_events import agent_event_to_session_event


def test_agent_prompt_events_map_to_valid_session_events() -> None:
    async def run() -> None:
        core_events: list[Any] = []
        agent = Agent(model=get_model("faux", "faux-1"), metadata={"response": "ok"})
        agent.subscribe(lambda event, _signal: core_events.append(event))

        await agent.prompt("hello")

        assert core_events
        mapped = [agent_event_to_session_event(event) for event in core_events]
        assert [event.type for event in mapped] == [event.type for event in core_events]
        for event in mapped:
            raw = event.model_dump(by_alias=True, exclude_none=True)
            reparsed = AgentSessionEventAdapter.validate_python(raw)
            assert reparsed.type == event.type

    asyncio.run(run())


def test_adapter_preserves_pi_aliases_for_message_and_tool_events() -> None:
    async def run() -> None:
        core_events: list[Any] = []
        agent = Agent(model=get_model("faux", "faux-1"), metadata={"response": "ok"})
        agent.subscribe(lambda event, _signal: core_events.append(event))
        await agent.prompt("hello")

        update = next(event for event in core_events if event.type == "message_update")
        raw_update = agent_event_to_session_event(update).model_dump(
            by_alias=True,
            exclude_none=True,
        )
        assert "assistantMessageEvent" in raw_update

    asyncio.run(run())

    tool_events = [
        ToolExecutionStartEvent(toolCallId="call_1", toolName="read", args={"path": "x"}),
        ToolExecutionUpdateEvent(
            toolCallId="call_1",
            toolName="read",
            args={"path": "x"},
            partialResult={"bytes": 1},
        ),
        ToolExecutionEndEvent(
            toolCallId="call_1",
            toolName="read",
            result={"ok": True},
            isError=False,
        ),
    ]
    for event in tool_events:
        raw = agent_event_to_session_event(event).model_dump(
            by_alias=True,
            exclude_none=True,
        )
        assert "toolCallId" in raw
        assert "toolName" in raw
        AgentSessionEventAdapter.validate_python(raw)


def test_adapter_does_not_mutate_original_event() -> None:
    async def run() -> None:
        events: list[Any] = []
        agent = Agent(model=get_model("faux", "faux-1"), metadata={"response": "ok"})
        agent.subscribe(lambda event, _signal: events.append(event))
        await agent.prompt("hello")

        event = next(item for item in events if item.type == "message_update")
        before = event.model_dump(by_alias=True, exclude_none=True)
        agent_event_to_session_event(event)
        after = event.model_dump(by_alias=True, exclude_none=True)
        assert before == after
        AgentEventAdapter.validate_python(after)

    asyncio.run(run())


def test_adapter_rejects_unsupported_event_object() -> None:
    class Unknown:
        type = "unknown"

    with pytest.raises(TypeError, match="unsupported agent event type"):
        agent_event_to_session_event(Unknown())  # type: ignore[arg-type]
