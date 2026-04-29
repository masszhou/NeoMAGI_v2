"""Interactive TUI bridge for the real ``agent_core.Agent`` runtime."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from agent_core import Agent, AgentOptions
from agent_core.cache_affinity import derive_provider_cache_affinity_id
from ai_provider.credentials import resolve_api_key
from ai_provider.model_registry import resolve_model, validate_thinking_level_for_model
from ai_provider.types import (
    AssistantMessage,
    CacheRetention,
    TextContent,
    ThinkingLevel,
    Usage,
    UsageCost,
    UserMessage,
)
from cli.core.session_types import (
    AgentEndEvent,
    AgentSessionEvent,
    MessageEndEvent,
    MessageStartEvent,
    QueueUpdateEvent,
)

from .runtime_events import agent_event_to_session_event


@dataclass(frozen=True, slots=True)
class RuntimeState:
    is_running: bool
    queued_steering: tuple[str, ...]
    queued_follow_up: tuple[str, ...]
    model_ref: str
    runtime_session_id: str
    provider_cache_affinity_id: str | None


_DEFAULT_SYSTEM_PROMPT = "You are a helpful coding assistant."


def _now_ms() -> int:
    return int(time.time() * 1000)


def _empty_usage() -> Usage:
    return Usage(
        input=0,
        output=0,
        cacheRead=0,
        cacheWrite=0,
        totalTokens=0,
        cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
    )


class InteractiveAgentRuntime:
    """Owns one ``Agent`` plus a background asyncio loop.

    The runtime never mutates TUI components directly. Agent listeners adapt
    events and enqueue them for the controller to drain on the UI loop.
    """

    def __init__(
        self,
        *,
        model_ref: str = "faux/faux-1",
        thinking_level: ThinkingLevel = "off",
        cache_retention: CacheRetention | None = None,
        agent_factory: Callable[[AgentOptions], Agent] = Agent,
    ) -> None:
        self._model_ref = model_ref
        self._model = resolve_model(model_ref)
        self._thinking_level = validate_thinking_level_for_model(
            self._model,
            thinking_level,
        )
        self._cache_retention = cache_retention
        self._agent_factory = agent_factory

        self._events: queue.Queue[AgentSessionEvent] = queue.Queue()
        self._lock = threading.RLock()
        self._wake: Callable[[], None] | None = None
        self._queued_steering: list[str] = []
        self._queued_follow_up: list[str] = []
        self._active_future: Future[Any] | None = None
        self._closed = False
        self._generation = 0

        self._runtime_session_id = self._mint_runtime_session_id()
        self._provider_cache_affinity_id = derive_provider_cache_affinity_id(
            self._runtime_session_id
        )
        self._agent = self._build_agent(self._generation)

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="neomagi-agent-runtime",
            daemon=True,
        )
        self._thread.start()

    @property
    def state(self) -> RuntimeState:
        with self._lock:
            return RuntimeState(
                is_running=self._is_running_locked(),
                queued_steering=tuple(self._queued_steering),
                queued_follow_up=tuple(self._queued_follow_up),
                model_ref=self._model_ref,
                runtime_session_id=self._runtime_session_id,
                provider_cache_affinity_id=self._provider_cache_affinity_id,
            )

    @property
    def footer_summary(self) -> str:
        cache = self._cache_retention or "default"
        return f"runtime: {self._model_ref}  thinking={self._thinking_level}  cache={cache}"

    def set_event_wake(self, wake: Callable[[], None] | None) -> None:
        self._wake = wake

    def submit(self, text: str) -> None:
        prompt = text if text.strip() else ""
        if not prompt:
            return
        with self._lock:
            self._ensure_open()
            if self._is_running_locked():
                raise RuntimeError("runtime is already processing; use steer() or follow_up()")
            self._queued_steering.clear()
            self._queued_follow_up.clear()
            self._enqueue_queue_update_locked()
            self._active_future = asyncio.run_coroutine_threadsafe(
                self._run_prompt(prompt, self._generation),
                self._loop,
            )

    def steer(self, text: str) -> None:
        prompt = text if text.strip() else ""
        if not prompt:
            return
        with self._lock:
            if not self._is_running_locked():
                self.submit(prompt)
                return
            self._queued_steering.append(prompt)
            self._loop.call_soon_threadsafe(self._agent.steer, self._user_message(prompt))
            self._enqueue_queue_update_locked()

    def follow_up(self, text: str) -> None:
        prompt = text if text.strip() else ""
        if not prompt:
            return
        with self._lock:
            if not self._is_running_locked():
                self.submit(prompt)
                return
            self._queued_follow_up.append(prompt)
            self._loop.call_soon_threadsafe(self._agent.follow_up, self._user_message(prompt))
            self._enqueue_queue_update_locked()

    def abort(self) -> bool:
        with self._lock:
            active = self._is_running_locked()
            if active:
                self._loop.call_soon_threadsafe(self._agent.abort)
            else:
                self._queued_steering.clear()
                self._queued_follow_up.clear()
                self._enqueue_queue_update_locked()
            return active

    def reset(self, *, wait_timeout: float = 1.0) -> None:
        future: Future[Any] | None
        with self._lock:
            future = self._active_future if self._is_running_locked() else None
            if future is not None:
                self._loop.call_soon_threadsafe(self._agent.abort)

        if future is not None:
            try:
                future.result(timeout=wait_timeout)
            except (FutureTimeoutError, Exception):
                pass

        with self._lock:
            self._generation += 1
            self._runtime_session_id = self._mint_runtime_session_id()
            self._provider_cache_affinity_id = derive_provider_cache_affinity_id(
                self._runtime_session_id
            )
            self._queued_steering.clear()
            self._queued_follow_up.clear()
            self._clear_event_queue_locked()
            self._agent = self._build_agent(self._generation)
            self._active_future = None
            self._enqueue_queue_update_locked()

    def drain_events(self) -> list[AgentSessionEvent]:
        events: list[AgentSessionEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def shutdown(self, *, timeout: float = 2.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            future = self._active_future if self._is_running_locked() else None
            if future is not None:
                self._loop.call_soon_threadsafe(self._agent.abort)

        if future is not None:
            try:
                future.result(timeout=timeout)
            except (FutureTimeoutError, Exception):
                pass

        if self._thread.is_alive():
            cancel_future = asyncio.run_coroutine_threadsafe(
                self._cancel_pending_tasks(),
                self._loop,
            )
            try:
                cancel_future.result(timeout=timeout)
            except (FutureTimeoutError, Exception):
                pass

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=timeout)
        if not self._thread.is_alive():
            self._loop.close()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _run_prompt(self, text: str, generation: int) -> None:
        try:
            await self._agent.prompt(text)
        except Exception as exc:
            self._enqueue_error(str(exc), generation)
        finally:
            with self._lock:
                if generation == self._generation:
                    self._queued_steering.clear()
                    self._queued_follow_up.clear()
                    self._enqueue_queue_update_locked()
                    self._active_future = None

    async def _cancel_pending_tasks(self) -> None:
        current = asyncio.current_task()
        tasks = [
            task
            for task in asyncio.all_tasks(self._loop)
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _build_agent(self, generation: int) -> Agent:
        agent = self._agent_factory(
            AgentOptions(
                model=self._model,
                system_prompt=_DEFAULT_SYSTEM_PROMPT,
                thinking_level=self._thinking_level,
                cache_retention=self._cache_retention,
                session_id=self._provider_cache_affinity_id,
                get_api_key=self._get_api_key,
                tools=[],
            )
        )

        def listener(event: Any, _signal: asyncio.Event) -> None:
            if generation != self._generation:
                return
            self._events.put(agent_event_to_session_event(event))
            self._notify_wake()

        agent.subscribe(listener)
        return agent

    def _get_api_key(self, provider: str) -> str | None:
        if provider == "faux":
            return None
        return resolve_api_key(self._model)

    def _enqueue_error(self, message: str, generation: int) -> None:
        if generation != self._generation:
            return
        failure = AssistantMessage(
            role="assistant",
            content=[TextContent(text="")],
            api=self._model.api,
            provider=self._model.provider,
            model=self._model.id,
            usage=_empty_usage(),
            stopReason="error",
            errorMessage=message,
            timestamp=_now_ms(),
        )
        for event in (
            MessageStartEvent(message=failure),
            MessageEndEvent(message=failure),
            AgentEndEvent(messages=[failure]),
        ):
            self._events.put(event)
        self._notify_wake()

    def _enqueue_queue_update_locked(self) -> None:
        self._events.put(
            QueueUpdateEvent(
                steering=list(self._queued_steering),
                followUp=list(self._queued_follow_up),
            )
        )
        self._notify_wake()

    def _clear_event_queue_locked(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                return

    def _is_running_locked(self) -> bool:
        return self._active_future is not None and not self._active_future.done()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("runtime is closed")

    def _notify_wake(self) -> None:
        wake = self._wake
        if wake is not None:
            wake()

    @staticmethod
    def _mint_runtime_session_id() -> str:
        return f"runtime-{uuid.uuid4()}"

    @staticmethod
    def _user_message(text: str) -> UserMessage:
        return UserMessage(
            role="user",
            content=[TextContent(text=text)],
            timestamp=_now_ms(),
        )


__all__ = ["InteractiveAgentRuntime", "RuntimeState"]
