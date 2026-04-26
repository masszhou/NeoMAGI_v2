"""OpenAI Responses provider adapter."""

from __future__ import annotations

import json
from urllib.parse import urlparse

from ai_provider.credentials import resolve_api_key
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
    StreamToolCallDelta,
    StreamToolCallEnd,
    StreamToolCallStart,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from ai_provider.usage import normalize_openai_responses_usage

from ._shared import (
    call_stream_method,
    clone_message,
    event_value,
    iterate_provider_stream,
    maybe_call_payload,
    maybe_call_response,
    nested_value,
    parse_json_object,
    schedule_provider_task,
    start_stream,
)


def encode_text_signature(item_id: str, phase: str | None = None) -> str:
    payload: dict[str, object] = {"v": 1, "id": item_id}
    if phase:
        payload["phase"] = phase
    return json.dumps(payload, separators=(",", ":"))


def decode_text_signature(signature: str) -> dict[str, object] | None:
    try:
        decoded = json.loads(signature)
    except json.JSONDecodeError:
        return None
    if isinstance(decoded, dict) and decoded.get("v") == 1 and isinstance(decoded.get("id"), str):
        return decoded
    return None


def build_openai_responses_params(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    options = ensure_stream_options(options)
    payload: dict[str, object] = {
        "model": model.id,
        "input": [_convert_message(message) for message in context.messages],
        "tools": [_convert_tool(tool) for tool in context.tools or []],
        "store": False,
        "stream": True,
    }
    if context.system_prompt:
        payload["instructions"] = context.system_prompt
    if options.temperature is not None:
        payload["temperature"] = options.temperature
    if options.max_tokens is not None:
        payload["max_output_tokens"] = options.max_tokens

    headers: dict[str, str] = {}
    retention = resolve_cache_retention(options.cache_retention)
    affinity_id = sanitize_cache_affinity_id(options.session_id)
    if affinity_id and cache_enabled(retention):
        payload["prompt_cache_key"] = affinity_id
        headers["session_id"] = affinity_id
        headers["x-client-request-id"] = affinity_id
        if retention == "long" and _is_direct_openai(model.base_url):
            payload["prompt_cache_retention"] = "24h"
    headers.update(model.headers or {})
    headers.update(options.headers or {})
    return payload, headers


def stream_openai_responses(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    options = ensure_stream_options(options)
    stream, partial = start_stream(model)
    schedule_provider_task(stream, _run_openai_responses(stream, partial, model, context, options))
    return stream


async def _run_openai_responses(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    context: Context,
    options: StreamOptions,
) -> None:
    payload, headers = build_openai_responses_params(model, context, options)
    payload = await maybe_call_payload(options, payload, model)
    try:
        source = await _call_openai_responses_stream(model, options, payload, headers)
        await maybe_call_response(options, model, headers=headers)
        await _parse_response_events(stream, partial, model, source)
    except Exception as exc:
        if not stream.abort_event.is_set():
            stream.error(str(exc))


async def _call_openai_responses_stream(
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
    return await call_stream_method(client.responses.create, payload, headers=headers)


async def _parse_response_events(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    source: object,
) -> None:
    state: dict[str, object] = {
        "text_index": None,
        "text_item_id": None,
        "tool_indexes": {},
        "tool_json": {},
        "stop_reason": "stop",
    }
    handlers = {
        "response.created": _handle_response_created,
        "response.output_item.added": _handle_response_output_item_added,
        "response.output_text.delta": _handle_response_text_delta,
        "response.refusal.delta": _handle_response_text_delta,
        "response.function_call_arguments.delta": _handle_response_tool_delta,
        "response.output_item.done": _handle_response_output_item_done,
        "response.completed": _handle_response_completed,
    }

    async for event in iterate_provider_stream(source):
        if stream.abort_event.is_set():
            stream.close()
            return
        handler = handlers.get(event_value(event, "type"))
        if handler:
            handler(stream, partial, model, event, state)

    _finish_response_stream(stream, partial, state)


def _handle_response_created(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    partial.response_id = nested_value(event, "response", "id")


def _handle_response_output_item_added(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    item = event_value(event, "item", {})
    if event_value(item, "type") == "message":
        state["text_item_id"] = event_value(item, "id")
    elif event_value(item, "type") == "function_call":
        _start_response_tool_call(stream, partial, item, state)


def _start_response_tool_call(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    item: object,
    state: dict[str, object],
) -> None:
    call_id = str(event_value(item, "call_id") or event_value(item, "id"))
    index = len(partial.content)
    state["tool_indexes"][call_id] = index
    state["tool_json"][call_id] = event_value(item, "arguments", "") or ""
    partial.content.append(
        ToolCall(
            id=call_id,
            name=event_value(item, "name", ""),
            arguments=parse_json_object(state["tool_json"][call_id]),
        )
    )
    stream.push(StreamToolCallStart(contentIndex=index, partial=clone_message(partial)))


def _handle_response_text_delta(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    text = event_value(event, "delta", "") or ""
    if state["text_index"] is None:
        _start_response_text(stream, partial, state)
    text_index = state["text_index"]
    partial.content[text_index].text += text
    stream.push(StreamTextDelta(contentIndex=text_index, delta=text, partial=clone_message(partial)))


def _start_response_text(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    state: dict[str, object],
) -> None:
    text_index = len(partial.content)
    state["text_index"] = text_index
    partial.content.append(TextContent(text=""))
    if state["text_item_id"]:
        partial.content[text_index].text_signature = encode_text_signature(state["text_item_id"])
    stream.push(StreamTextStart(contentIndex=text_index, partial=clone_message(partial)))


def _handle_response_tool_delta(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    call_id = str(event_value(event, "call_id") or event_value(event, "item_id"))
    if call_id not in state["tool_indexes"]:
        _start_response_tool_call(stream, partial, {"id": call_id, "call_id": call_id}, state)
    text = event_value(event, "delta", "") or ""
    state["tool_json"][call_id] += text
    stream.push(
        StreamToolCallDelta(
            contentIndex=state["tool_indexes"][call_id],
            delta=text,
            partial=clone_message(partial),
        )
    )


def _handle_response_output_item_done(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    item = event_value(event, "item", {})
    if event_value(item, "type") != "function_call":
        return
    call_id = str(event_value(item, "call_id") or event_value(item, "id"))
    index = state["tool_indexes"].get(call_id)
    if index is None:
        return
    block = partial.content[index]
    block.name = event_value(item, "name", block.name)
    block.arguments = parse_json_object(event_value(item, "arguments", state["tool_json"].get(call_id, "")))
    stream.push(StreamToolCallEnd(contentIndex=index, toolCall=block, partial=clone_message(partial)))


def _handle_response_completed(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    response = event_value(event, "response", {})
    partial.response_id = event_value(response, "id", partial.response_id)
    usage = event_value(response, "usage")
    if usage:
        partial.usage = normalize_openai_responses_usage(usage, model)
    state["stop_reason"] = _response_stop_reason(response, partial)


def _finish_response_stream(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    state: dict[str, object],
) -> None:
    text_index = state["text_index"]
    if text_index is not None and partial.content[text_index].type == "text":
        text = partial.content[text_index].text
        stream.push(StreamTextEnd(contentIndex=text_index, content=text, partial=clone_message(partial)))
    partial.stop_reason = state["stop_reason"]
    stream.push(StreamDone(reason=state["stop_reason"], message=clone_message(partial)))


def _response_stop_reason(response: object, partial: AssistantMessage) -> str:
    reason = nested_value(response, "incomplete_details", "reason")
    if reason == "max_output_tokens":
        return "length"
    if any(block.type == "toolCall" for block in partial.content):
        return "toolUse"
    return "stop"


def _is_direct_openai(base_url: str) -> bool:
    return urlparse(base_url).netloc == "api.openai.com"


def _convert_tool(tool: object) -> dict[str, object]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }


def _convert_message(message: object) -> dict[str, object]:
    if isinstance(message, UserMessage):
        return {"role": "user", "content": _convert_user_content(message.content)}
    if isinstance(message, AssistantMessage):
        return {
            "role": "assistant",
            "content": [_convert_assistant_block(block) for block in message.content],
        }
    if isinstance(message, ToolResultMessage):
        text = "\n".join(block.text for block in message.content if block.type == "text")
        return {"type": "function_call_output", "call_id": message.tool_call_id, "output": text}
    raise TypeError(f"unsupported message type for OpenAI Responses: {type(message)!r}")


def _convert_user_content(content: object) -> object:
    if isinstance(content, str):
        return content
    return [_convert_user_block(block) for block in content]


def _convert_user_block(block: TextContent | ImageContent) -> dict[str, object]:
    if block.type == "text":
        return {"type": "input_text", "text": block.text}
    return {"type": "input_image", "image_url": f"data:{block.mime_type};base64,{block.data}"}


def _convert_assistant_block(block: TextContent | ThinkingContent | ToolCall) -> dict[str, object]:
    if block.type == "text":
        return {"type": "output_text", "text": block.text}
    if block.type == "thinking":
        return {
            "type": "reasoning",
            "text": block.thinking,
            "encrypted_content": block.thinking_signature,
        }
    return {
        "type": "function_call",
        "call_id": block.id,
        "name": block.name,
        "arguments": json.dumps(block.arguments, sort_keys=True),
    }


__all__ = [
    "build_openai_responses_params",
    "decode_text_signature",
    "encode_text_signature",
    "stream_openai_responses",
]
