"""Pi-compatible assistant event stream runtime."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from .types import (
    AssistantMessage,
    AssistantMessageEvent,
    StreamDone,
    StreamError,
)


def _clone_message(message: AssistantMessage) -> AssistantMessage:
    return message.model_copy(deep=True)


class AssistantMessageEventStream:
    """Async iterable with Pi-compatible terminal ``result()`` semantics."""

    def __init__(self, initial: AssistantMessage | None = None) -> None:
        self._queue: asyncio.Queue[AssistantMessageEvent | None] = asyncio.Queue()
        self._result: asyncio.Future[AssistantMessage] = asyncio.get_running_loop().create_future()
        self._partial: AssistantMessage | None = _clone_message(initial) if initial else None
        self._done = False
        self.abort_event = asyncio.Event()

    def push(self, event: AssistantMessageEvent) -> None:
        if self._done:
            return

        partial = getattr(event, "partial", None)
        if partial is not None:
            self._partial = _clone_message(partial)

        if isinstance(event, StreamDone):
            self._done = True
            self._resolve(_clone_message(event.message))
        elif isinstance(event, StreamError):
            self._done = True
            self._resolve(_clone_message(event.error))

        self._queue.put_nowait(event)

    def end(self, result: AssistantMessage | None = None) -> None:
        if self._done:
            return
        self._done = True
        if result is not None:
            self._resolve(_clone_message(result))
        self._queue.put_nowait(None)

    def error(self, message: str) -> None:
        if self._done:
            return
        if self._partial is None:
            raise RuntimeError("cannot synthesize stream error before start")
        error = _clone_message(self._partial)
        error.stop_reason = "error"
        error.error_message = message
        self.push(StreamError(reason="error", error=error))

    def close(self) -> None:
        self.abort_event.set()
        if self._done:
            return
        if self._partial is None:
            self.end()
            return
        aborted = _clone_message(self._partial)
        aborted.stop_reason = "aborted"
        aborted.error_message = "Request was aborted"
        self.push(StreamError(reason="aborted", error=aborted))

    async def result(self) -> AssistantMessage:
        return await self._result

    async def __aiter__(self) -> AsyncIterator[AssistantMessageEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event
            if isinstance(event, StreamDone | StreamError):
                return

    def _resolve(self, message: AssistantMessage) -> None:
        if not self._result.done():
            self._result.set_result(message)


def create_assistant_message_event_stream(
    initial: AssistantMessage | None = None,
) -> AssistantMessageEventStream:
    return AssistantMessageEventStream(initial=initial)


__all__ = [
    "AssistantMessageEventStream",
    "create_assistant_message_event_stream",
]
