"""OpenAI Chat Completions and OpenAI-compatible provider adapter."""

from __future__ import annotations

import json
from urllib.parse import urlparse

from ai_provider.credentials import resolve_api_key
from ai_provider.models import parse_openai_completions_compat
from ai_provider.prompt_cache import cache_enabled, resolve_cache_retention, sanitize_cache_affinity_id
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
from ai_provider.usage import normalize_openai_completions_usage

from ._shared import (
    call_stream_method,
    clone_message,
    event_value,
    iterate_provider_stream,
    maybe_call_payload,
    maybe_call_response,
    parse_json_object,
    schedule_provider_task,
    start_stream,
)


def build_openai_completions_params(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    options = ensure_stream_options(options)
    compat = parse_openai_completions_compat(model.compat)
    payload: dict[str, object] = {
        "model": model.id,
        "messages": _convert_messages(context, model),
        "tools": [_convert_tool(tool) for tool in context.tools or []],
        "stream": True,
    }
    max_tokens_field = compat.max_tokens_field or "max_completion_tokens"
    if options.max_tokens is not None:
        payload[max_tokens_field] = options.max_tokens
    if options.temperature is not None:
        payload["temperature"] = options.temperature
    if compat.supports_usage_in_streaming is not False:
        payload["stream_options"] = {"include_usage": True}
    if compat.supports_store is not False and _is_direct_openai(model.base_url):
        payload["store"] = False

    headers: dict[str, str] = {}
    retention = resolve_cache_retention(options.cache_retention)
    affinity_id = sanitize_cache_affinity_id(options.session_id)
    if affinity_id and cache_enabled(retention):
        if _is_direct_openai(model.base_url):
            payload["prompt_cache_key"] = affinity_id
            if retention == "long":
                payload["prompt_cache_retention"] = "24h"
        if compat.send_session_affinity_headers:
            headers["session_id"] = affinity_id
            headers["x-client-request-id"] = affinity_id
            headers["x-session-affinity"] = affinity_id

    headers.update(model.headers or {})
    headers.update(options.headers or {})
    return payload, headers


def stream_openai_completions(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    options = ensure_stream_options(options)
    stream, partial = start_stream(model)
    schedule_provider_task(stream, _run_openai_completions(stream, partial, model, context, options))
    return stream


async def _run_openai_completions(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    context: Context,
    options: StreamOptions,
) -> None:
    payload, headers = build_openai_completions_params(model, context, options)
    payload = await maybe_call_payload(options, payload, model)
    try:
        source = await _call_openai_completions_stream(model, options, payload, headers)
        await maybe_call_response(options, model, headers=headers)
        await _parse_completion_chunks(stream, partial, model, source)
    except Exception as exc:
        if not stream.abort_event.is_set():
            stream.error(str(exc))


async def _call_openai_completions_stream(
    model: Model,
    options: StreamOptions,
    payload: object,
    headers: dict[str, str],
) -> object:
    client = options.client
    if client is None:
        api_key = resolve_api_key(model, options)
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=model.base_url, default_headers=headers or None)
        headers = {}
    return await call_stream_method(client.chat.completions.create, payload, headers=headers)


async def _parse_completion_chunks(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    source: object,
) -> None:
    state: dict[str, object] = {
        "text_index": None,
        "thinking_index": None,
        "tool_indexes": {},
        "tool_json": {},
        "finish_reason": "stop",
    }

    async for chunk in iterate_provider_stream(source):
        if stream.abort_event.is_set():
            stream.close()
            return
        usage = event_value(chunk, "usage")
        if usage:
            partial.usage = normalize_openai_completions_usage(usage, model)
        choice = _first_choice(chunk)
        if choice is None:
            continue
        state["finish_reason"] = event_value(choice, "finish_reason", state["finish_reason"]) or state[
            "finish_reason"
        ]
        _apply_completion_delta(stream, partial, event_value(choice, "delta", {}), state)

    _finish_completion_blocks(stream, partial, state)
    final_reason = _map_finish_reason(state["finish_reason"], partial)
    partial.stop_reason = final_reason
    stream.push(StreamDone(reason=final_reason, message=clone_message(partial)))


def _apply_completion_delta(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    delta: object,
    state: dict[str, object],
) -> None:
    text = event_value(delta, "content")
    if text:
        _append_completion_text(stream, partial, text, state)
    thinking_field, thinking_text = _first_thinking_delta(delta)
    if thinking_text:
        _append_completion_thinking(stream, partial, thinking_field, thinking_text, state)
    for tool_delta in event_value(delta, "tool_calls", []) or []:
        _apply_tool_delta(stream, partial, state, tool_delta)


def _append_completion_text(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    text: str,
    state: dict[str, object],
) -> None:
    if state["text_index"] is None:
        state["text_index"] = len(partial.content)
        partial.content.append(TextContent(text=""))
        stream.push(StreamTextStart(contentIndex=state["text_index"], partial=clone_message(partial)))
    text_index = state["text_index"]
    partial.content[text_index].text += text
    stream.push(StreamTextDelta(contentIndex=text_index, delta=text, partial=clone_message(partial)))


def _append_completion_thinking(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    field: str | None,
    text: str,
    state: dict[str, object],
) -> None:
    if state["thinking_index"] is None:
        state["thinking_index"] = len(partial.content)
        partial.content.append(ThinkingContent(thinking="", thinkingSignature=field))
        stream.push(StreamThinkingStart(contentIndex=state["thinking_index"], partial=clone_message(partial)))
    thinking_index = state["thinking_index"]
    partial.content[thinking_index].thinking += text
    stream.push(StreamThinkingDelta(contentIndex=thinking_index, delta=text, partial=clone_message(partial)))


def _finish_completion_blocks(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    state: dict[str, object],
) -> None:
    thinking_index = state["thinking_index"]
    if thinking_index is not None:
        thinking = partial.content[thinking_index].thinking
        stream.push(StreamThinkingEnd(contentIndex=thinking_index, content=thinking, partial=clone_message(partial)))
    text_index = state["text_index"]
    if text_index is not None:
        text = partial.content[text_index].text
        stream.push(StreamTextEnd(contentIndex=text_index, content=text, partial=clone_message(partial)))


def _apply_tool_delta(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    state: dict[str, object],
    tool_delta: object,
) -> None:
    tool_delta_index = int(event_value(tool_delta, "index", 0))
    function = event_value(tool_delta, "function", {})
    tool_indexes = state["tool_indexes"]
    tool_json = state["tool_json"]
    if tool_delta_index not in tool_indexes:
        content_index = len(partial.content)
        tool_indexes[tool_delta_index] = content_index
        tool_json[tool_delta_index] = ""
        partial.content.append(
            ToolCall(
                id=event_value(tool_delta, "id", f"call_{tool_delta_index}"),
                name=event_value(function, "name", "") or "",
                arguments={},
            )
        )
        stream.push(StreamToolCallStart(contentIndex=content_index, partial=clone_message(partial)))

    content_index = tool_indexes[tool_delta_index]
    block = partial.content[content_index]
    if event_value(tool_delta, "id"):
        block.id = event_value(tool_delta, "id")
    if event_value(function, "name"):
        block.name = event_value(function, "name")
    arguments_delta = event_value(function, "arguments", "") or ""
    if arguments_delta:
        tool_json[tool_delta_index] += arguments_delta
        stream.push(
            StreamToolCallDelta(
                contentIndex=content_index,
                delta=arguments_delta,
                partial=clone_message(partial),
            )
        )
    parsed = parse_json_object(tool_json[tool_delta_index])
    if parsed:
        block.arguments = parsed
        stream.push(StreamToolCallEnd(contentIndex=content_index, toolCall=block, partial=clone_message(partial)))


def _first_choice(chunk: object) -> object | None:
    choices = event_value(chunk, "choices", []) or []
    if not choices:
        return None
    return choices[0]


def _first_thinking_delta(delta: object) -> tuple[str | None, str | None]:
    for field in ("reasoning_content", "reasoning", "reasoning_text"):
        value = event_value(delta, field)
        if isinstance(value, str) and value:
            return field, value
    return None, None


def _map_finish_reason(reason: str, partial: AssistantMessage) -> str:
    if reason == "length":
        return "length"
    if reason in {"tool_calls", "function_call"}:
        return "toolUse"
    if any(block.type == "toolCall" for block in partial.content):
        return "toolUse"
    return "stop"


def _is_direct_openai(base_url: str) -> bool:
    return urlparse(base_url).netloc == "api.openai.com"


def _convert_messages(context: Context, model: Model) -> list[dict[str, object]]:
    compat = parse_openai_completions_compat(model.compat)
    messages: list[dict[str, object]] = []
    if context.system_prompt:
        role = "developer" if compat.supports_developer_role is not False and _is_direct_openai(model.base_url) else "system"
        messages.append({"role": role, "content": context.system_prompt})
    for message in context.messages:
        messages.append(_convert_message(message))
    return messages


def _convert_message(message: object) -> dict[str, object]:
    if isinstance(message, UserMessage):
        return {"role": "user", "content": _convert_user_content(message.content)}
    if isinstance(message, AssistantMessage):
        converted: dict[str, object] = {"role": "assistant", "content": _assistant_text(message)}
        tool_calls = [_convert_assistant_tool_call(block) for block in message.content if block.type == "toolCall"]
        if tool_calls:
            converted["tool_calls"] = tool_calls
        return converted
    if isinstance(message, ToolResultMessage):
        result: dict[str, object] = {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": "\n".join(block.text for block in message.content if block.type == "text"),
        }
        if message.tool_name:
            result["name"] = message.tool_name
        return result
    raise TypeError(f"unsupported message type for OpenAI Completions: {type(message)!r}")


def _convert_user_content(content: object) -> object:
    if isinstance(content, str):
        return content
    return [_convert_user_block(block) for block in content]


def _convert_user_block(block: TextContent | ImageContent) -> dict[str, object]:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    return {"type": "image_url", "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"}}


def _assistant_text(message: AssistantMessage) -> str:
    return "\n".join(
        block.text if block.type == "text" else block.thinking
        for block in message.content
        if block.type in {"text", "thinking"}
    )


def _convert_assistant_tool_call(block: ToolCall) -> dict[str, object]:
    return {
        "id": block.id,
        "type": "function",
        "function": {
            "name": block.name,
            "arguments": json.dumps(block.arguments, sort_keys=True),
        },
    }


def _convert_tool(tool: object) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


__all__ = [
    "build_openai_completions_params",
    "stream_openai_completions",
]
