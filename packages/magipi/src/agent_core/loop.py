"""Low-level Pi-compatible agent loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from ai_provider.api_registry import stream_simple
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.types import AssistantMessage, AssistantMessageEvent, Context, Message

from .runtime_types import AgentEventSink, AgentLoopConfig, maybe_await
from .tool_executor import execute_tool_calls
from .types import (
    AgentContext,
    AgentEndEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
    AgentStartEvent,
)


@dataclass(slots=True)
class _AssistantStreamState:
    partial_message: AssistantMessage | None = None
    added_partial: bool = False


def default_convert_to_llm(messages: list[Any]) -> list[Message]:
    return [
        message
        for message in messages
        if getattr(message, "role", None) in {"user", "assistant", "toolResult"}
    ]


async def run_agent_loop(
    prompts: list[Any],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: asyncio.Event | None = None,
) -> list[Any]:
    new_messages = list(prompts)
    current_context = AgentContext(
        systemPrompt=context.system_prompt,
        messages=[*context.messages, *prompts],
        tools=[tool.to_agent_tool_spec() for tool in config.tools],
    )

    await _emit(emit, AgentStartEvent())
    await _emit(emit, TurnStartEvent())
    for prompt in prompts:
        await _emit(emit, MessageStartEvent(message=prompt))
        await _emit(emit, MessageEndEvent(message=prompt))

    await _run_loop(current_context, new_messages, config, signal, emit)
    return new_messages


async def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: asyncio.Event | None = None,
) -> list[Any]:
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")
    if getattr(context.messages[-1], "role", None) == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    new_messages: list[Any] = []
    current_context = AgentContext(
        systemPrompt=context.system_prompt,
        messages=list(context.messages),
        tools=[tool.to_agent_tool_spec() for tool in config.tools],
    )

    await _emit(emit, AgentStartEvent())
    await _emit(emit, TurnStartEvent())
    await _run_loop(current_context, new_messages, config, signal, emit)
    return new_messages


async def _run_loop(
    current_context: AgentContext,
    new_messages: list[Any],
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> None:
    first_turn = True
    pending_messages = await _drain(config.get_steering_messages)

    while True:
        has_more_tool_calls = True
        while has_more_tool_calls or pending_messages:
            first_turn, has_more_tool_calls, should_stop = await _run_one_turn(
                current_context,
                new_messages,
                config,
                signal,
                emit,
                first_turn,
                pending_messages,
            )
            if should_stop:
                return
            pending_messages = []
            pending_messages = await _drain(config.get_steering_messages)

        follow_up_messages = await _drain(config.get_follow_up_messages)
        if follow_up_messages:
            pending_messages = follow_up_messages
            continue
        break

    await _emit(emit, AgentEndEvent(messages=list(new_messages)))


async def _run_one_turn(
    current_context: AgentContext,
    new_messages: list[Any],
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
    first_turn: bool,
    pending_messages: list[Any],
) -> tuple[bool, bool, bool]:
    first_turn = await _start_turn_if_needed(first_turn, emit)
    await _emit_pending_messages(pending_messages, current_context, new_messages, emit)
    if _signal_is_set(signal):
        await _emit_agent_end(new_messages, emit)
        return first_turn, False, True

    message = await stream_assistant_response(current_context, config, signal, emit)
    new_messages.append(message)
    if message.stop_reason in {"error", "aborted"}:
        await _emit(emit, TurnEndEvent(message=message, toolResults=[]))
        await _emit_agent_end(new_messages, emit)
        return first_turn, False, True

    has_more_tool_calls, tool_results = await _execute_message_tools(
        current_context,
        new_messages,
        message,
        config,
        signal,
        emit,
    )
    await _emit(emit, TurnEndEvent(message=message, toolResults=tool_results))
    if _signal_is_set(signal):
        await _emit_agent_end(new_messages, emit)
        return first_turn, has_more_tool_calls, True
    return first_turn, has_more_tool_calls, False


async def _start_turn_if_needed(first_turn: bool, emit: AgentEventSink) -> bool:
    if first_turn:
        return False
    await _emit(emit, TurnStartEvent())
    return False


async def _emit_pending_messages(
    pending_messages: list[Any],
    current_context: AgentContext,
    new_messages: list[Any],
    emit: AgentEventSink,
) -> None:
    for message in pending_messages:
        await _emit(emit, MessageStartEvent(message=message))
        await _emit(emit, MessageEndEvent(message=message))
        current_context.messages.append(message)
        new_messages.append(message)


async def _execute_message_tools(
    current_context: AgentContext,
    new_messages: list[Any],
    message: AssistantMessage,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> tuple[bool, list[Any]]:
    tool_calls = [block for block in message.content if block.type == "toolCall"]
    has_more_tool_calls = bool(tool_calls)
    tool_results: list[Any] = []
    if has_more_tool_calls and not _signal_is_set(signal):
        tool_results = await execute_tool_calls(current_context, message, config, signal, emit)
        current_context.messages.extend(tool_results)
        new_messages.extend(tool_results)
    return has_more_tool_calls, tool_results


async def stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> AssistantMessage:
    llm_context = await _build_llm_context(context, config, signal)
    if config.recover_assistant_response is not None:
        return await _stream_assistant_response_with_recovery(
            context,
            llm_context,
            config,
            signal,
            emit,
        )
    stream = await _open_assistant_stream(llm_context, config, signal)
    if config.on_stream_created is not None:
        config.on_stream_created(stream)
    return await _consume_assistant_stream(context, stream, emit)


async def _stream_assistant_response_with_recovery(
    context: AgentContext,
    llm_context: Context,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> AssistantMessage:
    attempt = 1
    max_attempts = 2
    while True:
        stream = await _open_assistant_stream(llm_context, config, signal)
        if config.on_stream_created is not None:
            config.on_stream_created(stream)
        events, message = await _consume_assistant_stream_buffered(stream)
        retry_context = await maybe_await(
            config.recover_assistant_response(
                message,
                llm_context,
                attempt,
                max_attempts,
                signal,
            )
        )
        if retry_context is None or attempt >= max_attempts:
            await _replay_buffered_events(context, events, emit)
            return message
        llm_context = retry_context
        attempt += 1


async def _build_llm_context(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
) -> Context:
    messages = list(context.messages)
    if config.transform_context is not None:
        messages = list(await maybe_await(config.transform_context(messages, signal)))
    convert_to_llm = config.convert_to_llm or default_convert_to_llm
    llm_messages = list(await maybe_await(convert_to_llm(messages)))
    return Context(
        systemPrompt=context.system_prompt,
        messages=llm_messages,
        tools=[tool.to_provider_tool() for tool in config.tools] or None,
    )


async def _open_assistant_stream(
    llm_context: Context,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
) -> Any:
    stream_fn = config.stream_fn or stream_simple
    return await maybe_await(
        stream_fn(
            config.model,
            llm_context,
            await _stream_options(config, signal),
        )
    )


async def _stream_options(
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
) -> SimpleStreamOptions:
    api_key = None
    if config.get_api_key is not None:
        api_key = await maybe_await(config.get_api_key(config.model.provider))
    return SimpleStreamOptions(
        signal=signal,
        api_key=api_key,
        transport=config.transport,
        cache_retention=config.cache_retention,
        session_id=config.session_id,
        on_payload=config.on_payload,
        on_response=config.on_response,
        max_retry_delay_ms=config.max_retry_delay_ms,
        metadata=dict(config.metadata),
        client=config.client,
        reasoning=None if config.thinking_level == "off" else config.thinking_level,
        thinking_budgets=dict(config.thinking_budgets),
    )


async def _consume_assistant_stream(
    context: AgentContext,
    stream: Any,
    emit: AgentEventSink,
) -> AssistantMessage:
    state = _AssistantStreamState()
    async for event in stream:
        if event.type == "start":
            await _handle_stream_start(context, state, event.partial, emit)
            continue
        if _is_update_event(event):
            await _handle_stream_update(context, state, event, emit)
            continue
        if event.type in {"done", "error"}:
            return await _finish_stream_message(context, state, stream, emit)
    return await _finish_stream_message(context, state, stream, emit)


async def _consume_assistant_stream_buffered(stream: Any) -> tuple[list[Any], AssistantMessage]:
    state = _AssistantStreamState()
    events: list[Any] = []
    async for event in stream:
        events.append(event)
        if event.type == "start":
            state.partial_message = event.partial
            state.added_partial = True
            continue
        if _is_update_event(event):
            partial_message = getattr(event, "partial", None)
            if partial_message is not None:
                state.partial_message = partial_message
            continue
        if event.type in {"done", "error"}:
            return events, await stream.result()
    return events, await stream.result()


async def _replay_buffered_events(
    context: AgentContext,
    events: list[Any],
    emit: AgentEventSink,
) -> None:
    state = _AssistantStreamState()
    for event in events:
        if event.type == "start":
            await _handle_stream_start(context, state, event.partial, emit)
            continue
        if _is_update_event(event):
            await _handle_stream_update(context, state, event, emit)
            continue
        if event.type == "done":
            await _finish_buffered_message(context, state, event.message, emit)
            continue
        if event.type == "error":
            await _finish_buffered_message(context, state, event.error, emit)
            continue


async def _finish_buffered_message(
    context: AgentContext,
    state: _AssistantStreamState,
    final_message: AssistantMessage,
    emit: AgentEventSink,
) -> None:
    if state.added_partial:
        context.messages[-1] = final_message
    else:
        context.messages.append(final_message)
        await _emit(emit, MessageStartEvent(message=final_message.model_copy(deep=True)))
    await _emit(emit, MessageEndEvent(message=final_message))


async def _handle_stream_start(
    context: AgentContext,
    state: _AssistantStreamState,
    partial_message: AssistantMessage,
    emit: AgentEventSink,
) -> None:
    state.partial_message = partial_message
    state.added_partial = True
    context.messages.append(partial_message)
    await _emit(emit, MessageStartEvent(message=partial_message.model_copy(deep=True)))


async def _handle_stream_update(
    context: AgentContext,
    state: _AssistantStreamState,
    event: Any,
    emit: AgentEventSink,
) -> None:
    partial_message = getattr(event, "partial", None)
    if partial_message is None:
        return
    state.partial_message = partial_message
    if state.added_partial:
        context.messages[-1] = partial_message
    await _emit(
        emit,
        MessageUpdateEvent(
            message=partial_message.model_copy(deep=True),
            assistantMessageEvent=event,
        ),
    )


async def _finish_stream_message(
    context: AgentContext,
    state: _AssistantStreamState,
    stream: Any,
    emit: AgentEventSink,
) -> AssistantMessage:
    final_message = await stream.result()
    if state.added_partial:
        context.messages[-1] = final_message
    else:
        context.messages.append(final_message)
        await _emit(emit, MessageStartEvent(message=final_message.model_copy(deep=True)))
    await _emit(emit, MessageEndEvent(message=final_message))
    return final_message


async def _emit_agent_end(new_messages: list[Any], emit: AgentEventSink) -> None:
    await _emit(emit, AgentEndEvent(messages=list(new_messages)))


def _signal_is_set(signal: asyncio.Event | None) -> bool:
    return signal is not None and signal.is_set()


async def _drain(drain: Any) -> list[Any]:
    if drain is None:
        return []
    messages = await maybe_await(drain())
    return list(messages or [])


async def _emit(emit: AgentEventSink, event: Any) -> None:
    await maybe_await(emit(event))


def _is_update_event(event: AssistantMessageEvent) -> bool:
    return event.type in {
        "text_start",
        "text_delta",
        "text_end",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
    }


__all__ = [
    "default_convert_to_llm",
    "run_agent_loop",
    "run_agent_loop_continue",
    "stream_assistant_response",
]
