"""D15: tool_execution_update real-time emission via asyncio.Queue bridge."""

from __future__ import annotations

import asyncio
from typing import Any

from agent_core import Agent, RuntimeAgentTool
from agent_core.types import AgentToolResult
from ai_provider.model_registry import get_model
from ai_provider.providers.faux import faux_tool_call, stream_faux
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.types import Context, Model


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


def _faux_then_done(name: str):
    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        has_tool_result = any(message.role == "toolResult" for message in context.messages)
        response = "done" if has_tool_result else [faux_tool_call(name, {})]
        return stream_faux(model, context, SimpleStreamOptions(metadata={"response": response}))

    return stream_fn


def test_async_tool_update_visible_before_tool_returns() -> None:
    """Happy path: async tool emits update, then waits on a signal set by the
    subscriber when the update arrives. If updates were buffered until tool
    return, the signal would never be set and the test would deadlock."""

    async def run() -> None:
        midway = asyncio.Event()

        async def execute(_call_id: str, _args: dict[str, Any], _signal: Any, on_update: Any) -> AgentToolResult:
            on_update(_text_result("partial-1"))
            await asyncio.wait_for(midway.wait(), timeout=2.0)
            return _text_result("final")

        agent = Agent(
            model=_model(),
            stream_fn=_faux_then_done("watcher"),
            tools=[_tool("watcher", execute)],
        )

        def listener(event: Any, _signal: Any) -> None:
            if event.type == "tool_execution_update":
                midway.set()

        agent.subscribe(listener)
        await asyncio.wait_for(agent.prompt("hi"), timeout=5.0)

    asyncio.run(run())


def test_async_tool_updates_arrive_in_fifo_order() -> None:
    async def run() -> None:
        captured: list[str] = []
        gates = [asyncio.Event() for _ in range(3)]

        async def execute(_call_id: str, _args: dict[str, Any], _signal: Any, on_update: Any) -> AgentToolResult:
            on_update(_text_result("p1"))
            await asyncio.wait_for(gates[0].wait(), timeout=2.0)
            on_update(_text_result("p2"))
            await asyncio.wait_for(gates[1].wait(), timeout=2.0)
            on_update(_text_result("p3"))
            await asyncio.wait_for(gates[2].wait(), timeout=2.0)
            return _text_result("final")

        seen = 0

        def listener(event: Any, _signal: Any) -> None:
            nonlocal seen
            if event.type == "tool_execution_update":
                captured.append(event.partial_result.content[0]["text"])
                gates[seen].set()
                seen += 1

        agent = Agent(
            model=_model(),
            stream_fn=_faux_then_done("fifo"),
            tools=[_tool("fifo", execute)],
        )
        agent.subscribe(listener)
        await asyncio.wait_for(agent.prompt("hi"), timeout=5.0)

        assert captured == ["p1", "p2", "p3"]

    asyncio.run(run())


def test_blocking_tool_body_defers_updates_until_after_return() -> None:
    """Negative path documenting D15 yielding requirement: if the tool body
    never yields (no ``await``), the consumer task cannot drain mid-execution,
    so updates emit *after* the tool's own work completes — never during.
    The queue still preserves ordering and ensures every update reaches the
    subscriber before ``tool_execution_end``."""

    async def run() -> None:
        order: list[str] = []

        async def execute(_call_id: str, _args: dict[str, Any], _signal: Any, on_update: Any) -> AgentToolResult:
            on_update(_text_result("p1"))
            order.append("after-emit-1")
            on_update(_text_result("p2"))
            order.append("after-emit-2")
            order.append("tool-returning")
            return _text_result("final")

        def listener(event: Any, _signal: Any) -> None:
            if event.type == "tool_execution_update":
                order.append(f"update:{event.partial_result.content[0]['text']}")
            elif event.type == "tool_execution_end":
                order.append("end")

        agent = Agent(
            model=_model(),
            stream_fn=_faux_then_done("blocker"),
            tools=[_tool("blocker", execute)],
        )
        agent.subscribe(listener)
        await asyncio.wait_for(agent.prompt("hi"), timeout=5.0)

        # Tool's local body completes before any update is dispatched
        # (because the consumer task can only run on an await boundary).
        assert "tool-returning" in order
        assert "update:p1" in order, f"order trace: {order}"
        assert order.index("tool-returning") < order.index("update:p1")
        # FIFO order is preserved across the queue.
        assert order.index("update:p1") < order.index("update:p2")
        # All updates flushed before end event.
        assert order.index("update:p2") < order.index("end")

    asyncio.run(run())


def test_aborted_tool_before_execute_emits_no_update() -> None:
    async def run() -> None:
        events_by_type: list[str] = []

        async def execute(_call_id: str, _args: dict[str, Any], _signal: Any, on_update: Any) -> AgentToolResult:
            on_update(_text_result("late"))
            return _text_result("never")

        agent = Agent(
            model=_model(),
            stream_fn=_faux_then_done("post_abort"),
            tools=[_tool("post_abort", execute)],
        )

        def listener(event: Any, _signal: asyncio.Event) -> None:
            events_by_type.append(event.type)
            if event.type == "tool_execution_start":
                _signal.set()

        agent.subscribe(listener)
        await asyncio.wait_for(agent.prompt("hi"), timeout=5.0)

        assert "tool_execution_update" not in events_by_type
        assert "tool_execution_end" in events_by_type

    asyncio.run(run())
