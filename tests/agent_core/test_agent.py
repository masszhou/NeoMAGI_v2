from __future__ import annotations

import asyncio
from typing import Any

from agent_core import Agent, RuntimeAgentTool
from agent_core.cache_affinity import derive_provider_cache_affinity_id, mint_provider_cache_affinity_id
from agent_core.types import AgentEventAdapter, AgentToolResult
from ai_provider.model_registry import get_model
from ai_provider.providers.faux import faux_tool_call, stream_faux
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.types import Context, Model, TextContent, ToolResultMessage, UserMessage


def _model() -> Model:
    return get_model("faux", "faux-1")


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)], timestamp=1)


def _text_result(text: str) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": text}], details={"text": text})


def test_prompt_no_tool_turn_emits_core_events_and_updates_state() -> None:
    async def run() -> None:
        events: list[Any] = []
        agent = Agent(model=_model(), metadata={"response": "ok"})
        agent.subscribe(lambda event, _signal: events.append(event))

        await agent.prompt("hello")

        event_types = [event.type for event in events]
        assert event_types[:4] == ["agent_start", "turn_start", "message_start", "message_end"]
        assert "message_update" in event_types
        assert event_types[-2:] == ["turn_end", "agent_end"]
        assert agent.state.is_streaming is False
        assert [message.role for message in agent.state.messages] == ["user", "assistant"]
        assert events[-1].messages == agent.state.messages
        for event in events:
            AgentEventAdapter.validate_python(event.model_dump(by_alias=True, exclude_none=True))

    asyncio.run(run())


def test_agent_end_messages_are_run_local_after_existing_transcript() -> None:
    async def run() -> None:
        agent_end_messages: list[list[Any]] = []
        agent = Agent(model=_model(), metadata={"response": "ok"})

        def listener(event: Any, _signal: asyncio.Event) -> None:
            if event.type == "agent_end":
                agent_end_messages.append(event.messages)

        agent.subscribe(listener)

        await agent.prompt("first")
        await agent.prompt("second")

        assert [message.role for message in agent.state.messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert len(agent_end_messages) == 2
        assert [message.role for message in agent_end_messages[0]] == ["user", "assistant"]
        assert [message.role for message in agent_end_messages[1]] == ["user", "assistant"]
        assert agent_end_messages[1] is not agent.state.messages
        assert agent_end_messages[1] != agent.state.messages

    asyncio.run(run())


def test_wait_for_idle_waits_for_agent_end_listener_and_listener_errors_settle() -> None:
    async def run() -> None:
        saw_streaming_at_agent_end: list[bool] = []
        agent = Agent(model=_model(), metadata={"response": "ok"})

        async def listener(event: Any, _signal: asyncio.Event) -> None:
            if event.type == "agent_end":
                saw_streaming_at_agent_end.append(agent.state.is_streaming)
                await asyncio.sleep(0.01)
                raise RuntimeError("listener failed")

        agent.subscribe(listener)
        await agent.prompt("hello")
        await agent.wait_for_idle()

        assert saw_streaming_at_agent_end == [True]
        assert agent.state.is_streaming is False
        assert agent.state.error_message == "listener failed"

    asyncio.run(run())


def test_abort_during_stream_preserves_partial_and_returns_to_idle() -> None:
    async def run() -> None:
        agent = Agent(
            model=_model(),
            metadata={"response": "abcdefghijklmnopqrstuvwxyz"},
        )

        def listener(event: Any, _signal: asyncio.Event) -> None:
            if event.type == "message_update" and event.assistant_message_event.type == "text_delta":
                agent.abort()

        agent.subscribe(listener)
        await agent.prompt("hello")

        assistant = agent.state.messages[-1]
        assert assistant.role == "assistant"
        assert assistant.stop_reason == "aborted"
        assert assistant.content[0].text
        assert agent.state.pending_tool_calls == []
        assert agent.state.is_streaming is False

    asyncio.run(run())


def test_tool_call_result_is_fed_back_until_model_stops() -> None:
    async def run() -> None:
        contexts: list[Context] = []
        options_seen: list[SimpleStreamOptions] = []

        def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
            assert options is not None
            contexts.append(context)
            options_seen.append(options)
            has_tool_result = any(message.role == "toolResult" for message in context.messages)
            response = "final" if has_tool_result else [faux_tool_call("echo", {"text": "hi"})]
            return stream_faux(model, context, SimpleStreamOptions(metadata={"response": response}))

        async def execute(
            _tool_call_id: str,
            params: dict[str, Any],
            _signal: asyncio.Event | None,
            on_update: Any,
        ) -> AgentToolResult:
            on_update(_text_result("partial"))
            return _text_result(params["text"])

        agent = Agent(
            model=_model(),
            stream_fn=stream_fn,
            session_id="session-1",
            cache_retention="none",
            get_api_key=lambda provider: f"key-for-{provider}",
            tools=[
                RuntimeAgentTool(
                    name="echo",
                    label="Echo",
                    description="Echo text",
                    parameters={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                    execute=execute,
                )
            ],
        )
        events: list[str] = []
        agent.subscribe(lambda event, _signal: events.append(event.type))

        await agent.prompt("hello")

        assert events.count("turn_start") == 2
        assert "tool_execution_update" in events
        assert [message.role for message in agent.state.messages] == [
            "user",
            "assistant",
            "toolResult",
            "assistant",
        ]
        assert agent.state.messages[-1].content[0].text == "final"
        assert len(contexts) == 2
        assert len(options_seen) == 2
        for options in options_seen:
            assert options.session_id == "session-1"
            assert options.cache_retention == "none"
            assert options.api_key == "key-for-faux"

    asyncio.run(run())


def test_tool_errors_become_tool_results_and_model_can_recover() -> None:
    async def run() -> None:
        def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
            has_tool_result = any(message.role == "toolResult" for message in context.messages)
            response = "recovered" if has_tool_result else [faux_tool_call("missing", {"path": 1})]
            return stream_faux(model, context, SimpleStreamOptions(metadata={"response": response}))

        agent = Agent(model=_model(), stream_fn=stream_fn)
        await agent.prompt("hello")

        tool_results = [message for message in agent.state.messages if isinstance(message, ToolResultMessage)]
        assert len(tool_results) == 1
        assert tool_results[0].is_error is True
        assert "not found" in tool_results[0].content[0].text
        assert agent.state.messages[-1].content[0].text == "recovered"

    asyncio.run(run())


def test_parallel_tools_emit_completion_order_but_persist_source_order() -> None:
    async def run() -> None:
        def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
            has_tool_result = any(message.role == "toolResult" for message in context.messages)
            response = (
                "done"
                if has_tool_result
                else [
                    faux_tool_call("slow", {}, id="call_slow"),
                    faux_tool_call("fast", {}, id="call_fast"),
                ]
            )
            return stream_faux(model, context, SimpleStreamOptions(metadata={"response": response}))

        async def slow(*_args: Any) -> AgentToolResult:
            await asyncio.sleep(0.02)
            return _text_result("slow")

        async def fast(*_args: Any) -> AgentToolResult:
            await asyncio.sleep(0)
            return _text_result("fast")

        agent = Agent(
            model=_model(),
            stream_fn=stream_fn,
            tools=[
                RuntimeAgentTool(name="slow", label="Slow", description="Slow", parameters={"type": "object"}, execute=slow),
                RuntimeAgentTool(name="fast", label="Fast", description="Fast", parameters={"type": "object"}, execute=fast),
            ],
        )
        end_order: list[str] = []

        def listener(event: Any, _signal: asyncio.Event) -> None:
            if event.type == "tool_execution_end":
                end_order.append(event.tool_name)

        agent.subscribe(listener)
        await agent.prompt("hello")

        tool_results = [message.tool_name for message in agent.state.messages if isinstance(message, ToolResultMessage)]
        assert end_order == ["fast", "slow"]
        assert tool_results == ["slow", "fast"]

    asyncio.run(run())


def test_before_after_hooks_and_validation_failures_are_error_results() -> None:
    async def run() -> None:
        responses = [
            [faux_tool_call("needs_string", {"value": 1})],
            [faux_tool_call("blocked", {})],
            [faux_tool_call("override", {})],
            "done",
        ]

        def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
            tool_results = [message for message in context.messages if message.role == "toolResult"]
            response = responses[min(len(tool_results), len(responses) - 1)]
            return stream_faux(model, context, SimpleStreamOptions(metadata={"response": response}))

        async def before(context: Any, _signal: asyncio.Event | None):
            if context.tool_call["name"] == "blocked":
                return {"block": True, "reason": "blocked by hook"}
            return None

        async def after(context: Any, _signal: asyncio.Event | None):
            if context.tool_call["name"] == "override":
                return {"content": [{"type": "text", "text": "overridden"}], "isError": True}
            return None

        async def execute(*_args: Any) -> AgentToolResult:
            return _text_result("ok")

        agent = Agent(
            model=_model(),
            stream_fn=stream_fn,
            before_tool_call=before,
            after_tool_call=after,
            tools=[
                RuntimeAgentTool(
                    name="needs_string",
                    label="Needs String",
                    description="Needs string",
                    parameters={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                    execute=execute,
                ),
                RuntimeAgentTool(name="blocked", label="Blocked", description="Blocked", parameters={"type": "object"}, execute=execute),
                RuntimeAgentTool(name="override", label="Override", description="Override", parameters={"type": "object"}, execute=execute),
            ],
        )

        await agent.prompt("hello")

        tool_results = [message for message in agent.state.messages if isinstance(message, ToolResultMessage)]
        assert [result.is_error for result in tool_results] == [True, True, True]
        assert "arguments invalid" in tool_results[0].content[0].text
        assert tool_results[1].content[0].text == "blocked by hook"
        assert tool_results[2].content[0].text == "overridden"

    asyncio.run(run())


def test_steering_follow_up_and_continue_from_assistant_are_deterministic() -> None:
    async def run() -> None:
        payloads: list[list[str]] = []

        def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
            payloads.append(
                [
                    block.text
                    for message in context.messages
                    if message.role == "user"
                    for block in (message.content if isinstance(message.content, list) else [TextContent(text=message.content)])
                    if block.type == "text"
                ]
            )
            return stream_faux(model, context, SimpleStreamOptions(metadata={"response": "ok"}))

        agent = Agent(model=_model(), stream_fn=stream_fn)
        agent.follow_up(_user("follow"))
        await agent.prompt("first")
        assert payloads == [["first"], ["first", "follow"]]

        agent.steer(_user("steer"))
        await agent.continue_()
        assert payloads[-1] == ["first", "follow", "steer"]

    asyncio.run(run())


def test_continue_from_tool_result_uses_existing_context() -> None:
    async def run() -> None:
        contexts: list[Context] = []

        def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
            contexts.append(context)
            return stream_faux(model, context, SimpleStreamOptions(metadata={"response": "continued"}))

        tool_result = ToolResultMessage(
            toolCallId="call_echo",
            toolName="echo",
            content=[TextContent(text="tool output")],
            details={"text": "tool output"},
            isError=False,
            timestamp=2,
        )
        agent = Agent(
            model=_model(),
            stream_fn=stream_fn,
            messages=[_user("hello"), tool_result],
        )

        await agent.continue_()

        assert len(contexts) == 1
        assert [message.role for message in contexts[0].messages] == ["user", "toolResult"]
        assert agent.state.messages[-1].role == "assistant"
        assert agent.state.messages[-1].content[0].text == "continued"

    asyncio.run(run())


def test_cache_affinity_helpers_and_passthrough_contract() -> None:
    assert derive_provider_cache_affinity_id("session-1") == "session-1"
    hashed = derive_provider_cache_affinity_id("bad value with spaces")
    assert hashed is not None
    assert hashed.startswith("neomagi-")
    assert len(hashed) == len("neomagi-") + 32
    assert mint_provider_cache_affinity_id()
