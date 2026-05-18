"""Tool execution loop for agent_core."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from ai_provider.tools import validate_tool_arguments
from ai_provider.types import AssistantMessage, ToolCall, ToolResultMessage

from .runtime_types import AgentEventSink, AgentLoopConfig, RuntimeAgentTool, maybe_await
from .types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class _PreparedToolCall:
    tool_call: ToolCall
    tool: RuntimeAgentTool
    args: Any


@dataclass(slots=True)
class _ImmediateToolCallOutcome:
    result: AgentToolResult
    is_error: bool


@dataclass(slots=True)
class _ExecutedToolCallOutcome:
    result: AgentToolResult
    is_error: bool


class _UpdateSentinel:
    __slots__ = ()


_UPDATE_SENTINEL = _UpdateSentinel()


@dataclass(slots=True)
class _FinalizedToolCallOutcome:
    tool_call: ToolCall
    result: AgentToolResult
    is_error: bool


async def execute_tool_calls(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> list[ToolResultMessage]:
    tool_calls = [block for block in assistant_message.content if block.type == "toolCall"]
    has_sequential_tool = any(
        _tool_requires_sequential(config.tools, tool_call)
        for tool_call in tool_calls
    )
    if config.tool_execution == "sequential" or has_sequential_tool:
        return await _execute_tool_calls_sequential(
            current_context,
            assistant_message,
            tool_calls,
            config,
            signal,
            emit,
        )
    return await _execute_tool_calls_parallel(
        current_context,
        assistant_message,
        tool_calls,
        config,
        signal,
        emit,
    )


async def _execute_tool_calls_sequential(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: list[ToolCall],
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> list[ToolResultMessage]:
    results: list[ToolResultMessage] = []
    for tool_call in tool_calls:
        if signal and signal.is_set():
            break
        await _emit_tool_execution_start(tool_call, emit)
        finalized = await _run_one_tool_call(
            current_context,
            assistant_message,
            tool_call,
            config,
            signal,
            emit,
        )
        await _emit_tool_execution_end(finalized, emit)
        tool_result_message = create_tool_result_message(finalized)
        await _emit_tool_result_message(tool_result_message, emit)
        results.append(tool_result_message)
    return results


async def _execute_tool_calls_parallel(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: list[ToolCall],
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> list[ToolResultMessage]:
    finalized_by_index: dict[int, _FinalizedToolCallOutcome] = {}
    tasks: list[asyncio.Task[tuple[int, _FinalizedToolCallOutcome]]] = []

    for index, tool_call in enumerate(tool_calls):
        if signal and signal.is_set():
            break
        await _emit_tool_execution_start(tool_call, emit)
        preparation = await prepare_tool_call(
            current_context,
            assistant_message,
            tool_call,
            config,
            signal,
        )
        if isinstance(preparation, _ImmediateToolCallOutcome):
            finalized = _FinalizedToolCallOutcome(
                tool_call=tool_call,
                result=preparation.result,
                is_error=preparation.is_error,
            )
            finalized_by_index[index] = finalized
            await _emit_tool_execution_end(finalized, emit)
            continue

        tasks.append(
            asyncio.create_task(
                _execute_and_finalize(index, current_context, assistant_message, preparation, config, signal, emit)
            )
        )

    for completed in asyncio.as_completed(tasks):
        index, finalized = await completed
        finalized_by_index[index] = finalized

    results: list[ToolResultMessage] = []
    for index in range(len(tool_calls)):
        finalized = finalized_by_index.get(index)
        if finalized is None:
            continue
        tool_result_message = create_tool_result_message(finalized)
        await _emit_tool_result_message(tool_result_message, emit)
        results.append(tool_result_message)
    return results


async def _execute_and_finalize(
    index: int,
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    preparation: _PreparedToolCall,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> tuple[int, _FinalizedToolCallOutcome]:
    executed = await execute_prepared_tool_call(preparation, signal, emit)
    finalized = await finalize_executed_tool_call(
        current_context,
        assistant_message,
        preparation,
        executed,
        config,
        signal,
    )
    await _emit_tool_execution_end(finalized, emit)
    return index, finalized


async def _run_one_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_call: ToolCall,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> _FinalizedToolCallOutcome:
    preparation = await prepare_tool_call(
        current_context,
        assistant_message,
        tool_call,
        config,
        signal,
    )
    if isinstance(preparation, _ImmediateToolCallOutcome):
        return _FinalizedToolCallOutcome(
            tool_call=tool_call,
            result=preparation.result,
            is_error=preparation.is_error,
        )

    executed = await execute_prepared_tool_call(preparation, signal, emit)
    return await finalize_executed_tool_call(
        current_context,
        assistant_message,
        preparation,
        executed,
        config,
        signal,
    )


async def prepare_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_call: ToolCall,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
) -> _PreparedToolCall | _ImmediateToolCallOutcome:
    tool = _find_tool(config.tools, tool_call.name)
    if tool is None:
        return _ImmediateToolCallOutcome(
            result=create_error_tool_result(f"Tool {tool_call.name} not found"),
            is_error=True,
        )

    try:
        args = tool.prepare_arguments(tool_call.arguments) if tool.prepare_arguments else tool_call.arguments
        if not isinstance(args, dict):
            raise TypeError("tool arguments must be an object")
        validate_tool_arguments(tool.to_provider_tool(), args)
        if config.before_tool_call is not None:
            before_result = await maybe_await(
                config.before_tool_call(
                    BeforeToolCallContext(
                        assistantMessage=assistant_message,
                        toolCall=tool_call.model_dump(by_alias=True, exclude_none=True),
                        args=args,
                        context=current_context,
                    ),
                    signal,
                )
            )
            if before_result is not None and not isinstance(before_result, BeforeToolCallResult):
                before_result = BeforeToolCallResult.model_validate(before_result)
            if before_result and before_result.block:
                return _ImmediateToolCallOutcome(
                    result=create_error_tool_result(before_result.reason or "Tool execution was blocked"),
                    is_error=True,
                )
        return _PreparedToolCall(tool_call=tool_call, tool=tool, args=args)
    except Exception as exc:
        return _ImmediateToolCallOutcome(
            result=create_error_tool_result(str(exc)),
            is_error=True,
        )


async def execute_prepared_tool_call(
    preparation: _PreparedToolCall,
    signal: asyncio.Event | None,
    emit: AgentEventSink,
) -> _ExecutedToolCallOutcome:
    if signal and signal.is_set():
        return _ExecutedToolCallOutcome(
            result=create_error_tool_result("Tool execution was aborted"),
            is_error=True,
        )

    update_queue: asyncio.Queue[AgentToolResult | _UpdateSentinel] = asyncio.Queue()
    consumer_task = asyncio.create_task(
        _drain_tool_update_queue(preparation.tool_call, update_queue, emit)
    )

    def on_update(partial_result: AgentToolResult) -> None:
        update_queue.put_nowait(partial_result)

    try:
        result = await maybe_await(
            preparation.tool.execute(
                preparation.tool_call.id,
                preparation.args,
                signal,
                on_update,
            )
        )
        if not isinstance(result, AgentToolResult):
            result = AgentToolResult.model_validate(result)
        if signal and signal.is_set():
            if result.is_error:
                return _ExecutedToolCallOutcome(result=result, is_error=True)
            return _ExecutedToolCallOutcome(
                result=create_error_tool_result("Tool execution was aborted"),
                is_error=True,
            )
        return _ExecutedToolCallOutcome(
            result=result,
            is_error=bool(result.is_error),
        )
    except Exception as exc:
        return _ExecutedToolCallOutcome(
            result=create_error_tool_result(str(exc)),
            is_error=True,
        )
    finally:
        await update_queue.put(_UPDATE_SENTINEL)
        await consumer_task


async def finalize_executed_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    preparation: _PreparedToolCall,
    executed: _ExecutedToolCallOutcome,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
) -> _FinalizedToolCallOutcome:
    result = executed.result
    is_error = executed.is_error

    if signal and signal.is_set():
        if executed.is_error:
            return _FinalizedToolCallOutcome(
                tool_call=preparation.tool_call,
                result=executed.result,
                is_error=True,
            )
        return _FinalizedToolCallOutcome(
            tool_call=preparation.tool_call,
            result=create_error_tool_result("Tool execution was aborted"),
            is_error=True,
        )

    if config.after_tool_call is not None:
        try:
            after_result = await maybe_await(
                config.after_tool_call(
                    AfterToolCallContext(
                        assistantMessage=assistant_message,
                        toolCall=preparation.tool_call.model_dump(by_alias=True, exclude_none=True),
                        args=preparation.args,
                        result=result,
                        isError=is_error,
                        context=current_context,
                    ),
                    signal,
                )
            )
            if after_result is not None and not isinstance(after_result, AfterToolCallResult):
                after_result = AfterToolCallResult.model_validate(after_result)
            if after_result is not None:
                result = AgentToolResult(
                    content=after_result.content if after_result.content is not None else result.content,
                    details=after_result.details if "details" in after_result.model_fields_set else result.details,
                )
                if after_result.is_error is not None:
                    is_error = after_result.is_error
        except Exception as exc:
            result = create_error_tool_result(str(exc))
            is_error = True

    return _FinalizedToolCallOutcome(
        tool_call=preparation.tool_call,
        result=result,
        is_error=is_error,
    )


def create_error_tool_result(message: str) -> AgentToolResult:
    return AgentToolResult(
        content=[{"type": "text", "text": message}],
        details={},
    )


def create_tool_result_message(finalized: _FinalizedToolCallOutcome) -> ToolResultMessage:
    return ToolResultMessage(
        role="toolResult",
        toolCallId=finalized.tool_call.id,
        toolName=finalized.tool_call.name,
        content=finalized.result.content,
        details=finalized.result.details,
        isError=finalized.is_error,
        timestamp=_now_ms(),
    )


async def _emit_tool_execution_start(tool_call: ToolCall, emit: AgentEventSink) -> None:
    await maybe_await(
        emit(
            ToolExecutionStartEvent(
                toolCallId=tool_call.id,
                toolName=tool_call.name,
                args=tool_call.arguments,
            )
        )
    )


async def _drain_tool_update_queue(
    tool_call: ToolCall,
    update_queue: asyncio.Queue[AgentToolResult | _UpdateSentinel],
    emit: AgentEventSink,
) -> None:
    while True:
        item = await update_queue.get()
        if isinstance(item, _UpdateSentinel):
            return
        await _emit_tool_execution_update(tool_call, item, emit)


async def _emit_tool_execution_update(tool_call: ToolCall, update: AgentToolResult, emit: AgentEventSink) -> None:
    await maybe_await(
        emit(
            ToolExecutionUpdateEvent(
                toolCallId=tool_call.id,
                toolName=tool_call.name,
                args=tool_call.arguments,
                partialResult=update,
            )
        )
    )


async def _emit_tool_execution_end(finalized: _FinalizedToolCallOutcome, emit: AgentEventSink) -> None:
    await maybe_await(
        emit(
            ToolExecutionEndEvent(
                toolCallId=finalized.tool_call.id,
                toolName=finalized.tool_call.name,
                result=finalized.result,
                isError=finalized.is_error,
            )
        )
    )


async def _emit_tool_result_message(tool_result_message: ToolResultMessage, emit: AgentEventSink) -> None:
    from .types import MessageEndEvent, MessageStartEvent

    await maybe_await(emit(MessageStartEvent(message=tool_result_message)))
    await maybe_await(emit(MessageEndEvent(message=tool_result_message)))


def _find_tool(tools: list[RuntimeAgentTool], name: str) -> RuntimeAgentTool | None:
    return next((tool for tool in tools if tool.name == name), None)


def _tool_requires_sequential(tools: list[RuntimeAgentTool], tool_call: ToolCall) -> bool:
    tool = _find_tool(tools, tool_call.name)
    return tool is not None and tool.execution_mode == "sequential"


__all__ = [
    "create_error_tool_result",
    "create_tool_result_message",
    "execute_prepared_tool_call",
    "execute_tool_calls",
    "finalize_executed_tool_call",
    "prepare_tool_call",
]
