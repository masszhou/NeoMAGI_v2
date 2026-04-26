"""Anthropic Messages provider adapter."""

from __future__ import annotations

from urllib.parse import urlparse

from ai_provider.credentials import resolve_api_key
from ai_provider.prompt_cache import cache_enabled, resolve_cache_retention
from ai_provider.runtime_types import StreamOptions, ensure_stream_options
from ai_provider.streaming import AssistantMessageEventStream
from ai_provider.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    StreamDone,
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
    ToolResultMessage,
    UserMessage,
)
from ai_provider.usage import normalize_anthropic_usage

from ._shared import (
    clone_message,
    event_value,
    iterate_provider_stream,
    maybe_call_payload,
    maybe_call_response,
    parse_json_object,
    schedule_provider_task,
    start_stream,
)


def get_cache_control(base_url: str, retention: str) -> dict[str, str] | None:
    if not cache_enabled(retention):
        return None
    control = {"type": "ephemeral"}
    parsed = urlparse(base_url)
    if retention == "long" and parsed.netloc == "api.anthropic.com":
        control["ttl"] = "1h"
    return control


def build_anthropic_messages_params(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> dict[str, object]:
    options = ensure_stream_options(options)
    retention = resolve_cache_retention(options.cache_retention)
    cache_control = get_cache_control(model.base_url, retention)
    messages = [_convert_message(message) for message in context.messages]
    messages = [message for message in messages if message is not None]

    if cache_control:
        _mark_last_user_block(messages, cache_control)

    payload: dict[str, object] = {
        "model": model.id,
        "max_tokens": options.max_tokens or min(model.max_tokens, 32000),
        "messages": messages,
    }
    if options.temperature is not None:
        payload["temperature"] = options.temperature
    if options.metadata:
        payload["metadata"] = options.metadata
    if context.system_prompt is not None:
        system_block: dict[str, object] = {"type": "text", "text": context.system_prompt}
        if cache_control:
            system_block["cache_control"] = cache_control
        payload["system"] = [system_block]
    if context.tools:
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in context.tools
        ]
        if cache_control and tools:
            tools[-1]["cache_control"] = cache_control
        payload["tools"] = tools
    return payload


def stream_anthropic_messages(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    options = ensure_stream_options(options)
    stream, partial = start_stream(model)
    schedule_provider_task(stream, _run_anthropic(stream, partial, model, context, options))
    return stream


async def _run_anthropic(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    context: Context,
    options: StreamOptions,
) -> None:
    payload = build_anthropic_messages_params(model, context, options)
    payload = await maybe_call_payload(options, payload, model)
    try:
        source = await _call_anthropic_stream(model, options, payload)
        await maybe_call_response(options, model)
        await _parse_anthropic_events(stream, partial, model, source)
    except Exception as exc:
        if not stream.abort_event.is_set():
            stream.error(str(exc))


async def _call_anthropic_stream(model: Model, options: StreamOptions, payload: object) -> object:
    client = options.client
    if client is None:
        api_key = resolve_api_key(model, options)
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key, base_url=model.base_url)
    messages = getattr(client, "messages")
    if hasattr(messages, "stream"):
        return messages.stream(**payload)
    return messages.create(**payload)


async def _parse_anthropic_events(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    source: object,
) -> None:
    state = {"stop_reason": "stop", "tool_json": {}}
    handlers = {
        "message_start": _handle_message_start,
        "content_block_start": _handle_content_block_start,
        "content_block_delta": _handle_content_block_delta,
        "content_block_stop": _handle_content_block_stop,
        "message_delta": _handle_message_delta,
    }
    async for event in iterate_provider_stream(source):
        if stream.abort_event.is_set():
            stream.close()
            return
        event_type = event_value(event, "type")
        if event_type == "message_stop":
            _finish_anthropic_stream(stream, partial, state["stop_reason"])
            return
        handler = handlers.get(event_type)
        if handler:
            handler(stream, partial, model, event, state)

    _finish_anthropic_stream(stream, partial, state["stop_reason"])


def _handle_message_start(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    message = event_value(event, "message", {})
    partial.response_id = event_value(message, "id")
    _update_usage(partial, model, event_value(message, "usage"))


def _handle_content_block_start(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    index = int(event_value(event, "index", len(partial.content)))
    block = event_value(event, "content_block", {})
    block_type = event_value(block, "type")
    if block_type == "text":
        partial.content.append(TextContent(text=event_value(block, "text", "") or ""))
        stream.push(StreamTextStart(contentIndex=index, partial=clone_message(partial)))
    elif block_type in {"thinking", "redacted_thinking"}:
        _start_thinking_block(stream, partial, block, block_type, index)
    elif block_type == "tool_use":
        partial.content.append(
            ToolCall(
                id=event_value(block, "id", f"tool_{index}"),
                name=event_value(block, "name", ""),
                arguments=event_value(block, "input", {}) or {},
            )
        )
        state["tool_json"][index] = ""
        stream.push(StreamToolCallStart(contentIndex=index, partial=clone_message(partial)))


def _start_thinking_block(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    block: object,
    block_type: object,
    index: int,
) -> None:
    partial.content.append(
        ThinkingContent(
            thinking=event_value(block, "thinking", "") or "",
            thinkingSignature=event_value(block, "signature") or event_value(block, "data"),
            redacted=block_type == "redacted_thinking",
        )
    )
    stream.push(StreamThinkingStart(contentIndex=index, partial=clone_message(partial)))


def _handle_content_block_delta(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    index = int(event_value(event, "index", 0))
    delta = event_value(event, "delta", {})
    delta_type = event_value(delta, "type")
    if delta_type == "text_delta":
        _append_text_delta(stream, partial, index, event_value(delta, "text", "") or "")
    elif delta_type == "thinking_delta":
        _append_thinking_delta(stream, partial, index, event_value(delta, "thinking", "") or "")
    elif delta_type == "signature_delta":
        partial.content[index].thinking_signature = event_value(delta, "signature")
    elif delta_type == "input_json_delta":
        text = event_value(delta, "partial_json", "") or ""
        state["tool_json"][index] = state["tool_json"].get(index, "") + text
        stream.push(StreamToolCallDelta(contentIndex=index, delta=text, partial=clone_message(partial)))


def _append_text_delta(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    index: int,
    text: str,
) -> None:
    partial.content[index].text += text
    stream.push(StreamTextDelta(contentIndex=index, delta=text, partial=clone_message(partial)))


def _append_thinking_delta(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    index: int,
    text: str,
) -> None:
    partial.content[index].thinking += text
    stream.push(StreamThinkingDelta(contentIndex=index, delta=text, partial=clone_message(partial)))


def _handle_content_block_stop(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    index = int(event_value(event, "index", 0))
    block = partial.content[index]
    if block.type == "text":
        stream.push(StreamTextEnd(contentIndex=index, content=block.text, partial=clone_message(partial)))
    elif block.type == "thinking":
        stream.push(StreamThinkingEnd(contentIndex=index, content=block.thinking, partial=clone_message(partial)))
    else:
        parsed = parse_json_object(state["tool_json"].get(index, ""))
        if parsed:
            block.arguments = parsed
        stream.push(StreamToolCallEnd(contentIndex=index, toolCall=block, partial=clone_message(partial)))


def _handle_message_delta(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    delta = event_value(event, "delta", {})
    state["stop_reason"] = _map_stop_reason(event_value(delta, "stop_reason")) or state["stop_reason"]
    _update_usage(partial, model, event_value(event, "usage"))


def _finish_anthropic_stream(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    stop_reason: object,
) -> None:
    final_reason = "toolUse" if any(block.type == "toolCall" for block in partial.content) else stop_reason
    partial.stop_reason = final_reason
    stream.push(StreamDone(reason=final_reason, message=clone_message(partial)))


def _update_usage(partial: AssistantMessage, model: Model, raw_usage: object | None) -> None:
    if raw_usage:
        partial.usage = normalize_anthropic_usage(raw_usage, model)


def _map_stop_reason(reason: object) -> str | None:
    if reason in {None, ""}:
        return None
    if reason == "max_tokens":
        return "length"
    if reason == "tool_use":
        return "toolUse"
    return "stop"


def _convert_message(message: object) -> dict[str, object] | None:
    if isinstance(message, UserMessage):
        return {"role": "user", "content": _convert_user_content(message.content)}
    if isinstance(message, AssistantMessage):
        return {"role": "assistant", "content": [_convert_assistant_block(block) for block in message.content]}
    if isinstance(message, ToolResultMessage):
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": [_convert_tool_result_block(block) for block in message.content],
                    "is_error": message.is_error,
                }
            ],
        }
    return None


def _convert_user_content(content: object) -> object:
    if isinstance(content, str):
        return content
    return [_convert_user_block(block) for block in content]


def _convert_user_block(block: TextContent | ImageContent) -> dict[str, object]:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": block.mime_type,
            "data": block.data,
        },
    }


def _convert_tool_result_block(block: TextContent | ImageContent) -> dict[str, object]:
    return _convert_user_block(block)


def _convert_assistant_block(block: TextContent | ThinkingContent | ToolCall) -> dict[str, object]:
    if block.type == "text":
        result: dict[str, object] = {"type": "text", "text": block.text}
        if block.text_signature:
            result["textSignature"] = block.text_signature
        return result
    if block.type == "thinking":
        if block.redacted:
            return {"type": "redacted_thinking", "data": block.thinking_signature}
        result = {"type": "thinking", "thinking": block.thinking}
        if block.thinking_signature:
            result["signature"] = block.thinking_signature
        return result
    return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.arguments}


def _mark_last_user_block(messages: list[dict[str, object]], cache_control: dict[str, str]) -> None:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = [{"type": "text", "text": content, "cache_control": cache_control}]
            return
        if isinstance(content, list) and content:
            for block in reversed(content):
                if isinstance(block, dict):
                    block["cache_control"] = cache_control
                    return


__all__ = [
    "build_anthropic_messages_params",
    "get_cache_control",
    "stream_anthropic_messages",
]
