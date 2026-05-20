from __future__ import annotations

import asyncio
from typing import Any

from agent_core import Agent, RuntimeAgentTool
from agent_core.cache_affinity import derive_provider_cache_affinity_id, mint_provider_cache_affinity_id
from agent_core.types import AgentEventAdapter, AgentToolResult
from ai_provider.model_registry import get_model
from ai_provider.providers.faux import faux_assistant_message, faux_tool_call, stream_faux
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.streaming import create_assistant_message_event_stream
from ai_provider.types import Context, Model, TextContent, ToolResultMessage, UserMessage
from ai_provider.types import StreamDone


def _model() -> Model:
    return get_model("faux", "faux-1")


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)], timestamp=1)


def _text_result(text: str) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": text}], details={"text": text})


class _FakeResponses:
    def __init__(self, event_batches: list[list[dict[str, Any]]]) -> None:
        self._event_batches = list(event_batches)
        self.payloads: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> list[dict[str, Any]]:
        kwargs.pop("extra_headers", None)
        self.payloads.append(kwargs)
        return self._event_batches.pop(0)


class _FakeOpenAIClient:
    def __init__(self, event_batches: list[list[dict[str, Any]]]) -> None:
        self.responses = _FakeResponses(event_batches)


OPENAI_TOOL_CALL_RESPONSE = [
    {
        "type": "response.output_item.added",
        "item": {"type": "function_call", "id": "fc_read_1", "call_id": "call_read_1", "name": "read_file"},
    },
    {"type": "response.function_call_arguments.done", "item_id": "fc_read_1", "arguments": '{"path":"README.md"}'},
    {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "id": "fc_read_1",
            "call_id": "call_read_1",
            "name": "read_file",
            "arguments": '{"path":"README.md"}',
        },
    },
    {"type": "response.completed", "response": {"id": "resp_1"}},
]

OPENAI_FINAL_TEXT_RESPONSE = [
    {"type": "response.output_item.added", "item": {"type": "message", "id": "msg_1"}},
    {"type": "response.output_text.delta", "delta": "summary done"},
    {"type": "response.completed", "response": {"id": "resp_2"}},
]

ECHO_PARAMETERS = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _runtime_tool(
    name: str,
    execute: Any,
    *,
    parameters: dict[str, Any] | None = None,
) -> RuntimeAgentTool:
    return RuntimeAgentTool(
        name=name,
        label=name.replace("_", " ").title(),
        description=f"{name} test tool",
        parameters=parameters or {"type": "object"},
        execute=execute,
    )


def _assert_session_options(
    options_seen: list[SimpleStreamOptions],
    *,
    session_id: str,
    cache_retention: str,
    api_key: str,
) -> None:
    for options in options_seen:
        assert options.session_id == session_id
        assert options.cache_retention == cache_retention
        assert options.api_key == api_key


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


def test_openai_responses_agent_tool_loop_flattens_tool_history() -> None:
    async def run() -> None:
        fake = _FakeOpenAIClient([OPENAI_TOOL_CALL_RESPONSE, OPENAI_FINAL_TEXT_RESPONSE])

        async def read_file(*_args: Any) -> AgentToolResult:
            return _text_result("README.md says this is NeoMAGI_v2.")

        agent = Agent(
            model=get_model("openai", "gpt-4o-mini"),
            client=fake,
            cache_retention="none",
            tools=[
                _runtime_tool(
                    "read_file",
                    read_file,
                    parameters={
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                )
            ],
        )

        await agent.prompt("Use read_file with path README.md.")

        assert [message.role for message in agent.state.messages] == [
            "user",
            "assistant",
            "toolResult",
            "assistant",
        ]
        assert agent.state.messages[-1].stop_reason == "stop"
        assert len(fake.responses.payloads) == 2
        second_input = fake.responses.payloads[1]["input"]
        assert second_input[1]["type"] == "function_call"
        assert second_input[2]["type"] == "function_call_output"

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


def test_active_run_id_is_visible_only_during_one_run() -> None:
    async def run() -> None:
        seen: list[str | None] = []
        agent = Agent(model=_model(), metadata={"response": "ok"})
        agent.subscribe(lambda _event, _signal: seen.append(agent.active_run_id))

        await agent.prompt("first")
        first_ids = {item for item in seen if item is not None}
        assert len(first_ids) == 1
        first_id = first_ids.pop()
        assert first_id.startswith("run-")
        assert agent.active_run_id is None

        seen.clear()
        await agent.prompt("second")
        second_ids = {item for item in seen if item is not None}
        assert len(second_ids) == 1
        second_id = second_ids.pop()
        assert second_id.startswith("run-")
        assert second_id != first_id
        assert agent.active_run_id is None

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

        async def second_listener(event: Any, _signal: asyncio.Event) -> None:
            if event.type == "agent_end":
                raise ValueError("second listener failed")

        agent.subscribe(listener)
        agent.subscribe(second_listener)
        await agent.prompt("hello")
        await agent.wait_for_idle()

        assert saw_streaming_at_agent_end == [True]
        assert agent.state.is_streaming is False
        assert agent.state.error_message == "second listener failed"
        assert agent._listener_errors == [  # noqa: SLF001
            {
                "eventType": "agent_end",
                "errorType": "RuntimeError",
                "message": "listener failed",
            },
            {
                "eventType": "agent_end",
                "errorType": "ValueError",
                "message": "second listener failed",
            },
        ]

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


def test_abort_before_stream_registration_closes_new_stream() -> None:
    async def run() -> None:
        stream_fn_entered = asyncio.Event()
        allow_stream_return = asyncio.Event()

        async def stream_fn(
            model: Model,
            context: Context,
            options: SimpleStreamOptions | None = None,
        ):
            del context, options
            stream_fn_entered.set()
            await allow_stream_return.wait()
            stream = create_assistant_message_event_stream(
                initial=faux_assistant_message("", model)
            )

            async def finish_late() -> None:
                await asyncio.sleep(0)
                stream.push(
                    StreamDone(
                        reason="stop",
                        message=faux_assistant_message("late success", model),
                    )
                )

            asyncio.create_task(finish_late())
            return stream

        agent = Agent(model=_model(), stream_fn=stream_fn)
        prompt_task = asyncio.create_task(agent.prompt("hello"))
        await asyncio.wait_for(stream_fn_entered.wait(), timeout=1.0)
        agent.abort()
        allow_stream_return.set()
        await prompt_task

        assistant = agent.state.messages[-1]
        assert assistant.role == "assistant"
        assert assistant.stop_reason == "aborted"
        assert assistant.error_message == "Request was aborted"
        assert agent.state.is_streaming is False
        assert agent.active_run_id is None

    asyncio.run(run())


def test_abort_during_tool_preserves_structured_tool_error_result() -> None:
    async def run() -> None:
        def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
            return stream_faux(
                model,
                context,
                SimpleStreamOptions(metadata={"response": [faux_tool_call("cancelled_tool", {})]}),
            )

        async def execute(
            _tool_call_id: str,
            _params: dict[str, Any],
            signal: asyncio.Event | None,
            _on_update: Any,
        ) -> AgentToolResult:
            if signal is not None:
                signal.set()
            return AgentToolResult(
                content=[{"type": "text", "text": "cancelled with partial output"}],
                details={"cancelled": True, "truncation": {"truncated": False}},
                isError=True,
            )

        events: list[Any] = []
        agent = Agent(
            model=_model(),
            stream_fn=stream_fn,
            tools=[_runtime_tool("cancelled_tool", execute)],
        )
        agent.subscribe(lambda event, _signal: events.append(event))

        await agent.prompt("hello")

        tool_results = [message for message in agent.state.messages if isinstance(message, ToolResultMessage)]
        assert len(tool_results) == 1
        assert tool_results[0].is_error is True
        assert tool_results[0].content[0].text == "cancelled with partial output"
        assert tool_results[0].details["cancelled"] is True
        end_events = [event for event in events if event.type == "tool_execution_end"]
        assert end_events[0].result.details["cancelled"] is True

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
                _runtime_tool("echo", execute, parameters=ECHO_PARAMETERS)
            ],
        )
        events: list[str] = []
        agent.subscribe(lambda event, _signal: events.append(event.type))

        await agent.prompt("hello")

        assert events.count("turn_start") == 2
        assert "tool_execution_update" in events
        assert [message.role for message in agent.state.messages] == ["user", "assistant", "toolResult", "assistant"]
        assert agent.state.messages[-1].content[0].text == "final"
        assert len(contexts) == 2
        assert len(options_seen) == 2
        _assert_session_options(
            options_seen,
            session_id="session-1",
            cache_retention="none",
            api_key="key-for-faux",
        )

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
