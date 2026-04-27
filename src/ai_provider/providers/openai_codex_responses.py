"""OpenAI Codex Responses provider adapter.

This provider consumes the OpenAI Codex/ChatGPT OAuth access token produced by
``ai_provider.oauth``. It is intentionally separate from the direct
``openai-responses`` provider, which uses standard OpenAI API keys.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import platform
from collections.abc import AsyncIterator, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_provider.credentials import resolve_api_key
from ai_provider.oauth import extract_openai_account_id
from ai_provider.prompt_cache import cache_enabled, resolve_cache_retention, sanitize_cache_affinity_id
from ai_provider.runtime_types import SimpleStreamOptions, StreamOptions, ensure_stream_options, stream_options_from_simple
from ai_provider.streaming import AssistantMessageEventStream
from ai_provider.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from ._shared import call_stream_method, maybe_call_payload, maybe_call_response, schedule_provider_task, start_stream
from .openai_responses import (
    _convert_message,
    _convert_tool,
    _map_reasoning_effort,
    _parse_response_events,
)

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api"
CODEX_RESPONSES_BETA = "responses=experimental"


def build_openai_codex_responses_params(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    options = ensure_stream_options(options)
    payload: dict[str, object] = {
        "model": model.id,
        "store": False,
        "stream": True,
        "input": _convert_codex_messages(context),
        "tools": [_convert_tool(tool) for tool in context.tools or []],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "text": {"verbosity": options.metadata.get("text_verbosity", "medium")},
        "include": ["reasoning.encrypted_content"],
    }
    if context.system_prompt:
        payload["instructions"] = context.system_prompt
    if options.temperature is not None:
        payload["temperature"] = options.temperature
    if options.max_tokens is not None:
        payload["max_output_tokens"] = options.max_tokens
    _apply_codex_reasoning_options(payload, model, options)

    headers = build_openai_codex_headers(model, options)
    retention = resolve_cache_retention(options.cache_retention)
    affinity_id = sanitize_cache_affinity_id(options.session_id)
    if affinity_id and cache_enabled(retention):
        payload["prompt_cache_key"] = affinity_id
        headers["session_id"] = affinity_id
        headers["x-client-request-id"] = affinity_id
    return payload, headers


def build_openai_codex_headers(model: Model, options: StreamOptions) -> dict[str, str]:
    token = resolve_api_key(model, options)
    account_id = extract_openai_account_id(token)
    if account_id is None:
        raise RuntimeError("OpenAI Codex OAuth token is missing chatgpt_account_id")
    headers = {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": account_id,
        "originator": "pi",
        "User-Agent": _user_agent(),
        "OpenAI-Beta": CODEX_RESPONSES_BETA,
        "accept": "text/event-stream",
        "content-type": "application/json",
    }
    headers.update(model.headers or {})
    headers.update(options.headers or {})
    return headers


def stream_openai_codex_responses(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    options = ensure_stream_options(options)
    stream, partial = start_stream(model)
    schedule_provider_task(stream, _run_openai_codex_responses(stream, partial, model, context, options))
    return stream


def stream_openai_codex_responses_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessageEventStream:
    metadata: dict[str, object] = {}
    if options and options.reasoning:
        metadata["reasoning_effort"] = _map_codex_reasoning_effort(model.id, options.reasoning)
        metadata["reasoning_summary"] = "auto"
    return stream_openai_codex_responses(model, context, stream_options_from_simple(options, metadata=metadata))


async def _run_openai_codex_responses(
    stream: AssistantMessageEventStream,
    partial,
    model: Model,
    context: Context,
    options: StreamOptions,
) -> None:
    try:
        payload, headers = build_openai_codex_responses_params(model, context, options)
        payload = await maybe_call_payload(options, payload, model)
        _validate_codex_request_payload(payload)
        source = await _call_openai_codex_stream(model, options, payload, headers)
        await maybe_call_response(options, model, headers=_public_response_headers(headers))
        mapped = _map_codex_events(source)
        await _parse_response_events(stream, partial, model, mapped)
    except Exception as exc:
        if not stream.abort_event.is_set():
            stream.error(str(exc))


async def _call_openai_codex_stream(
    model: Model,
    options: StreamOptions,
    payload: object,
    headers: dict[str, str],
) -> object:
    client = options.client
    if client is not None:
        if callable(client):
            result = client(payload, headers)
            return await result if inspect.isawaitable(result) else result
        return await call_stream_method(client.responses.create, payload, headers=headers)
    return _openai_codex_sse_events(_resolve_codex_url(model.base_url), payload, headers)


async def _openai_codex_sse_events(
    url: str,
    payload: object,
    headers: Mapping[str, str],
) -> AsyncIterator[dict[str, object]]:
    queue: asyncio.Queue[dict[str, object] | Exception | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def put(item: dict[str, object] | Exception | None) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def worker() -> None:
        try:
            _read_sse_sync(url, payload, headers, put)
        except Exception as exc:  # pragma: no cover - exercised by real smoke
            put(exc)
        finally:
            put(None)

    asyncio.create_task(asyncio.to_thread(worker))
    while True:
        item = await queue.get()
        if item is None:
            return
        if isinstance(item, Exception):
            raise item
        yield item


def _read_sse_sync(
    url: str,
    payload: object,
    headers: Mapping[str, str],
    emit: Callable[[dict[str, object]], None],
) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=dict(headers), method="POST")  # noqa: S310
    try:
        with urlopen(request, timeout=120) as response:  # noqa: S310
            data_lines: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    _emit_sse_event(emit, data_lines)
                    data_lines = []
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            _emit_sse_event(emit, data_lines)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI Codex endpoint returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI Codex endpoint request failed: {exc}") from exc


def _emit_sse_event(emit: Callable[[dict[str, object]], None], data_lines: list[str]) -> None:
    if not data_lines:
        return
    data = "\n".join(data_lines).strip()
    if not data or data == "[DONE]":
        return
    parsed = json.loads(data)
    if isinstance(parsed, dict):
        emit(parsed)


async def _map_codex_events(source: object) -> AsyncIterator[object]:
    from ._shared import iterate_provider_stream

    async for event in iterate_provider_stream(source):
        event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
        if event_type == "error":
            raise RuntimeError(_codex_error_message(event))
        if event_type == "response.failed":
            raise RuntimeError(_codex_failed_message(event))
        if event_type in {"response.done", "response.completed", "response.incomplete"}:
            if isinstance(event, dict):
                event = dict(event)
                event["type"] = "response.completed"
            yield event
            return
        yield event


def _apply_codex_reasoning_options(payload: dict[str, object], model: Model, options: StreamOptions) -> None:
    if not model.reasoning:
        return
    effort = options.metadata.get("reasoning_effort")
    if effort:
        payload["reasoning"] = {
            "effort": _map_codex_reasoning_effort(model.id, str(effort)),
            "summary": options.metadata.get("reasoning_summary", "auto"),
        }


def _map_codex_reasoning_effort(model_id: str, effort: str) -> str:
    if model_id == "gpt-5.1-codex-mini" and effort in {"high", "xhigh"}:
        return "high"
    return _map_reasoning_effort(effort)


def _convert_codex_messages(context: Context) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    for index, message in enumerate(context.messages):
        if isinstance(message, UserMessage):
            converted.append(
                {"role": "user", "content": _convert_codex_user_content(message.content)}
            )
        elif isinstance(message, AssistantMessage):
            converted.extend(_convert_codex_assistant_content(message, index))
        elif isinstance(message, ToolResultMessage):
            converted.append(_convert_codex_tool_result(message))
        else:
            converted.append(_convert_message(message))
    return converted


def _convert_codex_user_content(content: object) -> list[dict[str, object]]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    return [_convert_codex_user_block(block) for block in content]


def _convert_codex_user_block(block: TextContent | ImageContent) -> dict[str, object]:
    if block.type == "text":
        return {"type": "input_text", "text": block.text}
    return {
        "type": "input_image",
        "detail": "auto",
        "image_url": f"data:{block.mime_type};base64,{block.data}",
    }


def _convert_codex_assistant_content(
    message: AssistantMessage,
    message_index: int,
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for block in message.content:
        if isinstance(block, TextContent):
            output.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": block.text,
                            "annotations": [],
                        }
                    ],
                    "status": "completed",
                    "id": _codex_text_message_id(block, message_index),
                }
            )
        elif isinstance(block, ThinkingContent):
            reasoning = _decode_codex_reasoning_item(block)
            if reasoning is not None:
                output.append(reasoning)
        elif isinstance(block, ToolCall):
            output.append(_convert_codex_tool_call(block))
    return output


def _convert_codex_tool_call(block: ToolCall) -> dict[str, object]:
    call_id, item_id = _split_codex_tool_call_id(block.id)
    converted: dict[str, object] = {
        "type": "function_call",
        "call_id": call_id,
        "name": block.name,
        "arguments": json.dumps(block.arguments, sort_keys=True),
    }
    if item_id:
        converted["id"] = item_id
    return converted


def _convert_codex_tool_result(message: ToolResultMessage) -> dict[str, object]:
    text = "\n".join(block.text for block in message.content if block.type == "text")
    return {
        "type": "function_call_output",
        "call_id": _split_codex_tool_call_id(message.tool_call_id)[0],
        "output": text,
    }


def _codex_text_message_id(block: TextContent, message_index: int) -> str:
    if block.text_signature:
        try:
            parsed = json.loads(block.text_signature)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("id"), str):
            return _trim_codex_id(parsed["id"], f"msg_{message_index}")
        return _trim_codex_id(block.text_signature, f"msg_{message_index}")
    return f"msg_{message_index}"


def _decode_codex_reasoning_item(block: ThinkingContent) -> dict[str, object] | None:
    if not block.thinking_signature:
        return None
    try:
        parsed = json.loads(block.thinking_signature)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _split_codex_tool_call_id(raw_id: str) -> tuple[str, str | None]:
    if "|" not in raw_id:
        return raw_id, None
    call_id, item_id = raw_id.split("|", 1)
    return call_id, item_id or None


def _trim_codex_id(value: str, fallback: str) -> str:
    if not value:
        return fallback
    return value[:64]


def _validate_codex_request_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    instructions = payload.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise RuntimeError(
            "OpenAI Codex Responses requires Context.systemPrompt; "
            "pi sends it as top-level instructions, not as an input message"
        )


def _resolve_codex_url(base_url: str | None) -> str:
    raw = base_url.strip() if base_url else DEFAULT_CODEX_BASE_URL
    normalized = raw.rstrip("/")
    if normalized.endswith("/codex/responses"):
        return normalized
    if normalized.endswith("/codex"):
        return f"{normalized}/responses"
    return f"{normalized}/codex/responses"


def _codex_error_message(event: object) -> str:
    if isinstance(event, dict):
        return str(event.get("message") or event.get("code") or event)
    return str(event)


def _codex_failed_message(event: object) -> str:
    if isinstance(event, dict):
        response = event.get("response")
        if isinstance(response, dict):
            error = response.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
    return "Codex response failed"


def _public_response_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() != "authorization"}


def _user_agent() -> str:
    return f"neomagi ({platform.system()} {platform.release()}; {platform.machine()})"


__all__ = [
    "build_openai_codex_headers",
    "build_openai_codex_responses_params",
    "stream_openai_codex_responses",
    "stream_openai_codex_responses_simple",
]
