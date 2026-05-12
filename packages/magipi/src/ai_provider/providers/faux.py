"""Deterministic offline provider for tests and local agent loops."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ai_provider.prompt_cache import cache_enabled, resolve_cache_retention, sanitize_cache_affinity_id
from ai_provider.runtime_types import StreamOptions, ensure_stream_options
from ai_provider.streaming import AssistantMessageEventStream
from ai_provider.types import (
    AssistantMessage,
    AssistantContentItem,
    Context,
    Model,
    StreamDone,
    StreamError,
    StreamTextDelta,
    StreamTextEnd,
    StreamTextStart,
    StreamThinkingDelta,
    StreamThinkingEnd,
    StreamThinkingStart,
    StreamToolCallDelta,
    StreamToolCallEnd,
    StreamToolCallStart,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
)
from ai_provider.usage import calculate_cost

from ._shared import (
    clone_message,
    maybe_call_payload,
    maybe_call_response,
    now_ms,
    schedule_provider_task,
    start_stream,
)

_PROMPT_CACHE: dict[str, str] = {}


def faux_text(text: str) -> TextContent:
    return TextContent(text=text)


def faux_thinking(thinking: str) -> ThinkingContent:
    return ThinkingContent(thinking=thinking)


def faux_tool_call(name: str, arguments: dict[str, Any], *, id: str = "call_faux_1") -> ToolCall:
    return ToolCall(id=id, name=name, arguments=arguments)


def faux_assistant_message(
    content: str | AssistantContentItem | list[AssistantContentItem],
    model: Model,
    *,
    stop_reason: str = "stop",
    error_message: str | None = None,
) -> AssistantMessage:
    if isinstance(content, str):
        blocks: list[AssistantContentItem] = [faux_text(content)]
    elif isinstance(content, list):
        blocks = content
    else:
        blocks = [content]
    return AssistantMessage(
        role="assistant",
        content=blocks,
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(input=0, output=0, cacheRead=0, cacheWrite=0, totalTokens=0),
        stopReason=stop_reason,
        errorMessage=error_message,
        timestamp=now_ms(),
    )


def stream_faux(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    options = ensure_stream_options(options)
    stream, partial = start_stream(model)
    schedule_provider_task(stream, _run_faux(stream, partial, model, context, options))
    return stream


async def _run_faux(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    context: Context,
    options: StreamOptions,
) -> None:
    await maybe_call_response(options, model)
    payload = await maybe_call_payload(options, {"provider": "faux", "context": context}, model)
    if options.metadata.get("abort"):
        aborted = clone_message(partial)
        aborted.stop_reason = "aborted"
        aborted.error_message = "Request was aborted"
        stream.push(StreamError(reason="aborted", error=aborted))
        return
    if message := options.metadata.get("error"):
        stream.error(str(message))
        return

    response = _coerce_response(options.metadata.get("response"), model)
    response.usage = _estimate_usage(model, context, response, options)
    if isinstance(payload, dict) and payload.get("empty"):
        response.content = []

    await _stream_content(stream, partial, response, options)


def _coerce_response(raw: Any, model: Model) -> AssistantMessage:
    if raw is None:
        return faux_assistant_message("Hello from faux.", model)
    if isinstance(raw, AssistantMessage):
        return raw.model_copy(deep=True)
    if isinstance(raw, str):
        return faux_assistant_message(raw, model)
    if isinstance(raw, dict):
        return AssistantMessage.model_validate(raw)
    if isinstance(raw, list):
        return faux_assistant_message(raw, model)
    return faux_assistant_message(str(raw), model)


def _text_for_context(context: Context) -> str:
    parts: list[str] = []
    if context.system_prompt:
        parts.append(f"system:{context.system_prompt}")
    for message in context.messages:
        parts.append(message.model_dump_json(by_alias=True, exclude_none=True))
    if context.tools:
        parts.append(json.dumps([tool.model_dump(by_alias=True) for tool in context.tools], sort_keys=True))
    return "\n\n".join(parts)


def _content_text(message: AssistantMessage) -> str:
    parts: list[str] = []
    for block in message.content:
        if block.type == "text":
            parts.append(block.text)
        elif block.type == "thinking":
            parts.append(block.thinking)
        else:
            parts.append(f"{block.name}:{json.dumps(block.arguments, sort_keys=True)}")
    return "\n".join(parts)


def _estimate_tokens(text: str) -> int:
    return max(0, (len(text) + 3) // 4)


def _estimate_usage(
    model: Model,
    context: Context,
    response: AssistantMessage,
    options: StreamOptions,
) -> Usage:
    prompt_text = _text_for_context(context)
    prompt_tokens = _estimate_tokens(prompt_text)
    output_tokens = _estimate_tokens(_content_text(response))
    input_tokens = prompt_tokens
    cache_read = 0
    cache_write = 0

    retention = resolve_cache_retention(options.cache_retention)
    affinity_id = sanitize_cache_affinity_id(options.session_id)
    if affinity_id and cache_enabled(retention):
        previous = _PROMPT_CACHE.get(affinity_id)
        if previous:
            common = 0
            max_common = min(len(previous), len(prompt_text))
            while common < max_common and previous[common] == prompt_text[common]:
                common += 1
            cache_read = _estimate_tokens(previous[:common])
            cache_write = _estimate_tokens(prompt_text[common:])
            input_tokens = max(prompt_tokens - cache_read, 0)
        else:
            cache_write = prompt_tokens
        _PROMPT_CACHE[affinity_id] = prompt_text

    usage = Usage(
        input=input_tokens,
        output=output_tokens,
        cacheRead=cache_read,
        cacheWrite=cache_write,
        totalTokens=input_tokens + output_tokens + cache_read + cache_write,
    )
    usage.cost = calculate_cost(model, usage)
    return usage


async def _stream_content(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    response: AssistantMessage,
    options: StreamOptions,
) -> None:
    for index, block in enumerate(response.content):
        if _is_aborted(stream, options):
            _emit_abort(stream, partial)
            return
        if block.type == "thinking":
            if not await _stream_thinking_block(stream, partial, block, index, options):
                return
        elif block.type == "text":
            if not await _stream_text_block(stream, partial, block, index, options):
                return
        else:
            if not await _stream_tool_block(stream, partial, block, index, options):
                return

    final = response.model_copy(deep=True)
    final.content = [block.model_copy(deep=True) for block in partial.content]
    final.timestamp = now_ms()
    if final.stop_reason in {"error", "aborted"}:
        stream.push(StreamError(reason=final.stop_reason, error=final))
    else:
        reason = "toolUse" if any(block.type == "toolCall" for block in final.content) else final.stop_reason
        final.stop_reason = reason
        stream.push(StreamDone(reason=reason, message=final))


async def _stream_thinking_block(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    block: ThinkingContent,
    index: int,
    options: StreamOptions,
) -> bool:
    partial.content.append(ThinkingContent(thinking=""))
    stream.push(StreamThinkingStart(contentIndex=index, partial=clone_message(partial)))
    for chunk in _chunks(block.thinking):
        await asyncio.sleep(0)
        if _is_aborted(stream, options):
            _emit_abort(stream, partial)
            return False
        partial.content[index].thinking += chunk
        stream.push(StreamThinkingDelta(contentIndex=index, delta=chunk, partial=clone_message(partial)))
    stream.push(StreamThinkingEnd(contentIndex=index, content=block.thinking, partial=clone_message(partial)))
    return True


async def _stream_text_block(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    block: TextContent,
    index: int,
    options: StreamOptions,
) -> bool:
    partial.content.append(TextContent(text=""))
    stream.push(StreamTextStart(contentIndex=index, partial=clone_message(partial)))
    for chunk in _chunks(block.text):
        await asyncio.sleep(0)
        if _is_aborted(stream, options):
            _emit_abort(stream, partial)
            return False
        partial.content[index].text += chunk
        stream.push(StreamTextDelta(contentIndex=index, delta=chunk, partial=clone_message(partial)))
    stream.push(StreamTextEnd(contentIndex=index, content=block.text, partial=clone_message(partial)))
    return True


async def _stream_tool_block(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    block: ToolCall,
    index: int,
    options: StreamOptions,
) -> bool:
    partial.content.append(ToolCall(id=block.id, name=block.name, arguments={}))
    stream.push(StreamToolCallStart(contentIndex=index, partial=clone_message(partial)))
    for chunk in _chunks(json.dumps(block.arguments, sort_keys=True)):
        await asyncio.sleep(0)
        if _is_aborted(stream, options):
            _emit_abort(stream, partial)
            return False
        stream.push(StreamToolCallDelta(contentIndex=index, delta=chunk, partial=clone_message(partial)))
    partial.content[index].arguments = block.arguments
    stream.push(StreamToolCallEnd(contentIndex=index, toolCall=block, partial=clone_message(partial)))
    return True


def _chunks(text: str, size: int = 6) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def _is_aborted(stream: AssistantMessageEventStream, options: StreamOptions) -> bool:
    return stream.abort_event.is_set() or bool(options.signal and options.signal.is_set())


def _emit_abort(stream: AssistantMessageEventStream, partial: AssistantMessage) -> None:
    aborted = clone_message(partial)
    aborted.stop_reason = "aborted"
    aborted.error_message = "Request was aborted"
    stream.push(StreamError(reason="aborted", error=aborted))


__all__ = [
    "faux_assistant_message",
    "faux_text",
    "faux_thinking",
    "faux_tool_call",
    "stream_faux",
]
