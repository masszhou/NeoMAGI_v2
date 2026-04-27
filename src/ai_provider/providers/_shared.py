"""Shared provider-adapter helpers that do not know any SDK types."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from ai_provider.runtime_types import ProviderResponse, StreamOptions
from ai_provider.streaming import AssistantMessageEventStream, create_assistant_message_event_stream
from ai_provider.types import (
    AssistantMessage,
    Model,
    StreamStart,
    Usage,
    UsageCost,
)


def now_ms() -> int:
    return int(time.time() * 1000)


def empty_usage() -> Usage:
    return Usage(
        input=0,
        output=0,
        cacheRead=0,
        cacheWrite=0,
        totalTokens=0,
        cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
    )


def initial_message(model: Model) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stopReason="stop",
        timestamp=now_ms(),
    )


def error_message(model: Model, message: str, *, stop_reason: str = "error") -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stopReason=stop_reason,
        errorMessage=message,
        timestamp=now_ms(),
    )


def clone_message(message: AssistantMessage) -> AssistantMessage:
    return message.model_copy(deep=True)


def start_stream(model: Model) -> tuple[AssistantMessageEventStream, AssistantMessage]:
    message = initial_message(model)
    stream = create_assistant_message_event_stream(initial=message)
    stream.push(StreamStart(partial=clone_message(message)))
    return stream, message


def schedule_provider_task(stream: AssistantMessageEventStream, coro: Awaitable[None]) -> None:
    async def runner() -> None:
        try:
            await coro
        except Exception as exc:  # pragma: no cover - defensive safety net
            if not stream.abort_event.is_set():
                try:
                    stream.error(str(exc))
                except RuntimeError:
                    stream.end()

    asyncio.create_task(runner())


async def maybe_call_payload(options: StreamOptions, payload: Any, model: Model) -> Any:
    if options.on_payload is None:
        return payload
    replacement = options.on_payload(payload, model)
    if inspect.isawaitable(replacement):
        replacement = await replacement
    return payload if replacement is None else replacement


async def maybe_call_response(
    options: StreamOptions,
    model: Model,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> None:
    if options.on_response is None:
        return
    result = options.on_response(ProviderResponse(status=status, headers=headers or {}), model)
    if inspect.isawaitable(result):
        await result


def event_value(event: Any, key: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def nested_value(event: Any, *keys: str, default: Any = None) -> Any:
    current = event
    for key in keys:
        current = event_value(current, key, default)
        if current is default:
            return default
    return current


def parse_json_object(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def iterate_provider_stream(source: Any) -> AsyncIterator[Any]:
    if inspect.isawaitable(source):
        source = await source
    if hasattr(source, "__aiter__"):
        async for event in source:
            yield event
        return
    if hasattr(source, "__aenter__"):
        async with source as entered:
            async for event in iterate_provider_stream(entered):
                yield event
        return
    if isinstance(source, list | tuple):
        for event in source:
            yield event
        return
    raise TypeError(f"provider stream object is not iterable: {type(source)!r}")


async def call_stream_method(
    factory: Callable[..., Any],
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    if headers:
        try:
            return factory(**payload, extra_headers=headers)
        except TypeError:
            return factory(**payload)
    return factory(**payload)


__all__ = [
    "call_stream_method",
    "clone_message",
    "empty_usage",
    "error_message",
    "event_value",
    "initial_message",
    "iterate_provider_stream",
    "maybe_call_payload",
    "maybe_call_response",
    "nested_value",
    "now_ms",
    "parse_json_object",
    "schedule_provider_task",
    "start_stream",
]
