"""Low-level Pi-compatible agent loop."""

from __future__ import annotations

import asyncio
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
            if first_turn:
                first_turn = False
            else:
                await _emit(emit, TurnStartEvent())

            if pending_messages:
                for message in pending_messages:
                    await _emit(emit, MessageStartEvent(message=message))
                    await _emit(emit, MessageEndEvent(message=message))
                    current_context.messages.append(message)
                    new_messages.append(message)
                pending_messages = []

            if signal and signal.is_set():
                await _emit(emit, AgentEndEvent(messages=list(new_messages)))
                return

            message = await stream_assistant_response(current_context, config, signal, emit)
            new_messages.append(message)

            if message.stop_reason in {"error", "aborted"}:
                await _emit(emit, TurnEndEvent(message=message, toolResults=[]))
                await _emit(emit, AgentEndEvent(messages=list(new_messages)))
                return

            tool_calls = [block for block in message.content if block.type == "toolCall"]
            has_more_tool_calls = bool(tool_calls)
            tool_results = []
            if has_more_tool_calls and not (signal and signal.is_set()):
                tool_results = await execute_tool_calls(
                    current_context,
                    message,
                    config,
                    signal,
                    emit,
                )
                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            await _emit(emit, TurnEndEvent(message=message, toolResults=tool_results))
            if signal and signal.is_set():
                await _emit(emit, AgentEndEvent(messages=list(new_messages)))
                return

            pending_messages = await _drain(config.get_steering_messages)

        follow_up_messages = await _drain(config.get_follow_up_messages)
        if follow_up_messages:
            pending_messages = follow_up_messages
            continue
        break

    await _emit(emit, AgentEndEvent(messages=list(new_messages)))


async def stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> AssistantMessage:
    messages: list[Any] = list(context.messages)
    if config.transform_context is not None:
        messages = list(await maybe_await(config.transform_context(messages, signal)))

    convert_to_llm = config.convert_to_llm or default_convert_to_llm
    llm_messages = list(await maybe_await(convert_to_llm(messages)))
    llm_context = Context(
        systemPrompt=context.system_prompt,
        messages=llm_messages,
        tools=[tool.to_provider_tool() for tool in config.tools] or None,
    )
    api_key = None
    if config.get_api_key is not None:
        api_key = await maybe_await(config.get_api_key(config.model.provider))

    stream_fn = config.stream_fn or stream_simple
    stream = await maybe_await(
        stream_fn(
            config.model,
            llm_context,
            SimpleStreamOptions(
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
            ),
        )
    )
    if config.on_stream_created is not None:
        config.on_stream_created(stream)

    partial_message: AssistantMessage | None = None
    added_partial = False
    async for event in stream:
        if event.type == "start":
            partial_message = event.partial
            context.messages.append(partial_message)
            added_partial = True
            await _emit(emit, MessageStartEvent(message=partial_message.model_copy(deep=True)))
            continue

        if _is_update_event(event):
            partial = getattr(event, "partial", None)
            if partial is not None:
                partial_message = partial
                if added_partial:
                    context.messages[-1] = partial_message
                await _emit(
                    emit,
                    MessageUpdateEvent(
                        message=partial_message.model_copy(deep=True),
                        assistantMessageEvent=event,
                    ),
                )
            continue

        if event.type in {"done", "error"}:
            final_message = await stream.result()
            if added_partial:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
                await _emit(emit, MessageStartEvent(message=final_message.model_copy(deep=True)))
            await _emit(emit, MessageEndEvent(message=final_message))
            return final_message

    final_message = await stream.result()
    if added_partial:
        context.messages[-1] = final_message
    else:
        context.messages.append(final_message)
        await _emit(emit, MessageStartEvent(message=final_message.model_copy(deep=True)))
    await _emit(emit, MessageEndEvent(message=final_message))
    return final_message


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
