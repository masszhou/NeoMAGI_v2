"""OpenAI Responses provider adapter."""

from __future__ import annotations

import json
from urllib.parse import urlparse

from ai_provider.credentials import resolve_provider_auth
from ai_provider.oauth_github_copilot import GITHUB_COPILOT_PROVIDER_ID
from ai_provider.prompt_cache import cache_enabled, resolve_cache_retention, sanitize_cache_affinity_id
from ai_provider.runtime_types import (
    SimpleStreamOptions,
    StreamOptions,
    ensure_stream_options,
    stream_cancelled,
    stream_options_from_simple,
)
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
    copilot_dynamic_headers,
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
        "input": _convert_messages(context.messages),
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
    _apply_reasoning_options(payload, model, options)

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
    headers.update(copilot_dynamic_headers(model, context))
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


def stream_openai_responses_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessageEventStream:
    metadata: dict[str, object] = {}
    if model.reasoning:
        if options and options.reasoning:
            metadata["reasoning_effort"] = _map_reasoning_effort(options.reasoning)
            metadata["reasoning_summary"] = "auto"
        else:
            metadata["reasoning_disabled"] = True
    return stream_openai_responses(model, context, stream_options_from_simple(options, metadata=metadata))


async def _run_openai_responses(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    context: Context,
    options: StreamOptions,
) -> None:
    payload, headers = build_openai_responses_params(model, context, options)
    if stream_cancelled(stream, options):
        stream.close()
        return
    payload = await maybe_call_payload(options, payload, model)
    try:
        if stream_cancelled(stream, options):
            stream.close()
            return
        source = await _call_openai_responses_stream(model, options, payload, headers)
        if stream_cancelled(stream, options):
            stream.close()
            return
        await maybe_call_response(options, model, headers=headers)
        await _parse_response_events(stream, partial, model, source, options)
    except Exception as exc:
        if not stream_cancelled(stream, options):
            stream.error(str(exc))


async def _call_openai_responses_stream(
    model: Model,
    options: StreamOptions,
    payload: object,
    headers: dict[str, str],
) -> object:
    client = options.client
    if client is None:
        auth = resolve_provider_auth(model, options)
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=auth.api_key, base_url=auth.base_url, default_headers=headers or None
        )
        headers = {}
    return await call_stream_method(client.responses.create, payload, headers=headers)


async def _parse_response_events(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    source: object,
    options: StreamOptions,
) -> None:
    state: dict[str, object] = {
        "text_index": None,
        "text_item_id": None,
        "text_phase": None,
        "text_done": False,
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
        "response.function_call_arguments.done": _handle_response_tool_done,
        "response.output_item.done": _handle_response_output_item_done,
        "response.completed": _handle_response_completed,
    }

    async for event in iterate_provider_stream(source):
        if stream_cancelled(stream, options):
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
        state["text_phase"] = event_value(item, "phase")
    elif event_value(item, "type") == "function_call":
        _start_response_tool_call(stream, partial, item, state)


def _start_response_tool_call(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    item: object,
    state: dict[str, object],
) -> None:
    item_id = str(event_value(item, "id") or "")
    call_id = str(event_value(item, "call_id") or item_id)
    index = len(partial.content)
    arguments = event_value(item, "arguments", "") or ""
    for alias in {call_id, item_id}:
        if alias:
            state["tool_indexes"][alias] = index
            state["tool_json"][alias] = arguments
    partial.content.append(
        ToolCall(
            id=call_id,
            name=event_value(item, "name", ""),
            arguments=parse_json_object(arguments),
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
        partial.content[text_index].text_signature = encode_text_signature(
            str(state["text_item_id"]),
            _text_phase(state),
        )
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
    index = state["tool_indexes"][call_id]
    for alias in _tool_aliases_for_index(state, index):
        state["tool_json"][alias] = state["tool_json"].get(alias, "") + text
    stream.push(
        StreamToolCallDelta(
            contentIndex=index,
            delta=text,
            partial=clone_message(partial),
        )
    )


def _handle_response_tool_done(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    model: Model,
    event: object,
    state: dict[str, object],
) -> None:
    call_id = str(event_value(event, "call_id") or event_value(event, "item_id"))
    if call_id not in state["tool_indexes"]:
        _start_response_tool_call(stream, partial, {"id": call_id, "call_id": call_id}, state)
    arguments = event_value(event, "arguments", "") or ""
    index = state["tool_indexes"][call_id]
    previous = state["tool_json"].get(call_id, "")
    for alias in _tool_aliases_for_index(state, index):
        state["tool_json"][alias] = arguments
    block = partial.content[index]
    block.arguments = parse_json_object(arguments)
    if isinstance(arguments, str) and isinstance(previous, str) and arguments.startswith(previous):
        delta = arguments[len(previous) :]
        if delta:
            stream.push(
                StreamToolCallDelta(
                    contentIndex=index,
                    delta=delta,
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
    item_type = event_value(item, "type")
    if item_type == "message":
        _finish_response_text_item(stream, partial, item, state)
        return
    if item_type != "function_call":
        return
    call_id = str(event_value(item, "call_id") or event_value(item, "id"))
    index = state["tool_indexes"].get(call_id)
    if index is None:
        return
    block = partial.content[index]
    block.name = event_value(item, "name", block.name)
    block.arguments = parse_json_object(event_value(item, "arguments", state["tool_json"].get(call_id, "")))
    stream.push(StreamToolCallEnd(contentIndex=index, toolCall=block, partial=clone_message(partial)))


def _finish_response_text_item(
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
    item: object,
    state: dict[str, object],
) -> None:
    item_id = event_value(item, "id") or state.get("text_item_id")
    if item_id:
        state["text_item_id"] = item_id
    phase = event_value(item, "phase")
    if isinstance(phase, str):
        state["text_phase"] = phase
    item_text = _response_message_text(item)
    text_index = state["text_index"]
    if text_index is None and item_text is not None:
        _start_response_text(stream, partial, state)
        text_index = state["text_index"]
    if text_index is None or partial.content[text_index].type != "text":
        return
    block = partial.content[text_index]
    if item_text is not None:
        block.text = item_text
    if item_id:
        block.text_signature = encode_text_signature(str(item_id), _text_phase(state, item))
    stream.push(StreamTextEnd(contentIndex=text_index, content=block.text, partial=clone_message(partial)))
    state["text_done"] = True


def _text_phase(state: dict[str, object], item: object | None = None) -> str | None:
    phase = event_value(item, "phase") if item is not None else state.get("text_phase")
    if phase is None:
        phase = state.get("text_phase")
    return phase if isinstance(phase, str) else None


def _response_message_text(item: object) -> str | None:
    content = event_value(item, "content")
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for part in content:
        part_type = event_value(part, "type")
        if part_type == "output_text":
            parts.append(event_value(part, "text", "") or "")
        elif part_type == "refusal":
            parts.append(event_value(part, "refusal", "") or "")
    return "".join(parts)


def _tool_aliases_for_index(state: dict[str, object], index: int) -> list[str]:
    return [alias for alias, value in state["tool_indexes"].items() if value == index]


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
    if text_index is not None and not state["text_done"] and partial.content[text_index].type == "text":
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


def _apply_reasoning_options(payload: dict[str, object], model: Model, options: StreamOptions) -> None:
    if not model.reasoning:
        return
    if options.metadata.get("reasoning_effort"):
        payload["reasoning"] = {
            "effort": options.metadata["reasoning_effort"],
            "summary": options.metadata.get("reasoning_summary", "auto"),
        }
        payload["include"] = ["reasoning.encrypted_content"]
    elif options.metadata.get("reasoning_disabled") and model.provider != GITHUB_COPILOT_PROVIDER_ID:
        # GitHub Copilot's responses endpoint rejects an explicit
        # ``reasoning.effort = "none"`` for its reasoning models; pi-mono skips
        # the field for this provider, so we mirror that.
        payload["reasoning"] = {"effort": "none"}


def _map_reasoning_effort(reasoning: str) -> str:
    return "high" if reasoning == "xhigh" else reasoning


def _is_direct_openai(base_url: str) -> bool:
    return urlparse(base_url).netloc == "api.openai.com"


def _convert_tool(tool: object) -> dict[str, object]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }


def _convert_messages(messages: list[object]) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    for message in messages:
        converted.extend(_convert_message(message))
    return converted


def _convert_message(message: object) -> list[dict[str, object]]:
    if isinstance(message, UserMessage):
        return [{"role": "user", "content": _convert_user_content(message.content)}]
    if isinstance(message, AssistantMessage):
        return _convert_assistant_message(message)
    if isinstance(message, ToolResultMessage):
        text = "\n".join(block.text for block in message.content if block.type == "text")
        return [{"type": "function_call_output", "call_id": message.tool_call_id, "output": text}]
    raise TypeError(f"unsupported message type for OpenAI Responses: {type(message)!r}")


def _convert_assistant_message(message: AssistantMessage) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    message_content: list[dict[str, object]] = []
    for block in message.content:
        if isinstance(block, ToolCall):
            if message_content:
                converted.append({"role": "assistant", "content": message_content})
                message_content = []
            converted.append(_convert_tool_call(block))
        else:
            message_content.append(_convert_assistant_block(block))
    if message_content:
        converted.append({"role": "assistant", "content": message_content})
    return converted


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
    return _convert_tool_call(block)


def _convert_tool_call(block: ToolCall) -> dict[str, object]:
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
    "stream_openai_responses_simple",
]
