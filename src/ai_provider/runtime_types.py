"""Runtime-only provider API types.

These dataclasses intentionally sit beside, not inside, the pydantic wire
models in :mod:`ai_provider.types`. They describe call-time provider behavior
and never cross the durable Pi-compatible message/session boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .types import CacheRetention, Context, Model, ThinkingLevel, Transport

if TYPE_CHECKING:
    from .streaming import AssistantMessageEventStream


@dataclass(slots=True)
class ProviderResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)


PayloadCallback = Callable[[Any, Model], Any | Awaitable[Any | None] | None]
ResponseCallback = Callable[[ProviderResponse, Model], Awaitable[None] | None]


@dataclass(slots=True)
class StreamOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    signal: asyncio.Event | None = None
    api_key: str | None = None
    transport: Transport | None = None
    cache_retention: CacheRetention | None = None
    session_id: str | None = None
    on_payload: PayloadCallback | None = None
    on_response: ResponseCallback | None = None
    headers: dict[str, str] | None = None
    max_retry_delay_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    client: object | None = None


@dataclass(slots=True)
class SimpleStreamOptions(StreamOptions):
    reasoning: ThinkingLevel | None = None
    thinking_budgets: dict[str, int] = field(default_factory=dict)


class StreamFunction(Protocol):
    def __call__(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> "AssistantMessageEventStream": ...


class SimpleStreamFunction(Protocol):
    def __call__(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> "AssistantMessageEventStream": ...


class ProviderAdapter(Protocol):
    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> "AssistantMessageEventStream": ...

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> "AssistantMessageEventStream": ...


def ensure_stream_options(options: StreamOptions | None = None) -> StreamOptions:
    return options if options is not None else StreamOptions()


def stream_options_from_simple(
    options: SimpleStreamOptions | None,
    *,
    metadata: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> StreamOptions:
    if options is None:
        return StreamOptions(metadata=metadata or {})
    merged_metadata = dict(options.metadata)
    if metadata:
        merged_metadata.update(metadata)
    return StreamOptions(
        temperature=options.temperature,
        max_tokens=options.max_tokens if max_tokens is None else max_tokens,
        signal=options.signal,
        api_key=options.api_key,
        transport=options.transport,
        cache_retention=options.cache_retention,
        session_id=options.session_id,
        on_payload=options.on_payload,
        on_response=options.on_response,
        headers=options.headers,
        max_retry_delay_ms=options.max_retry_delay_ms,
        metadata=merged_metadata,
        client=options.client,
    )


__all__ = [
    "PayloadCallback",
    "ProviderAdapter",
    "ProviderResponse",
    "ResponseCallback",
    "SimpleStreamFunction",
    "SimpleStreamOptions",
    "StreamFunction",
    "StreamOptions",
    "ensure_stream_options",
    "stream_options_from_simple",
]
