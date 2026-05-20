"""Stateful Agent wrapper around the low-level agent loop."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from ai_provider.model_registry import get_model
from ai_provider.types import AssistantMessage, ImageContent, TextContent, Usage, UsageCost, UserMessage

from .loop import default_convert_to_llm, run_agent_loop, run_agent_loop_continue
from .runtime_types import ActiveRun, AgentOptions, QueueMode, RuntimeAgentTool, user_content_from_text_and_images
from .types import (
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentState,
    AgentTool,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
)

Listener = Callable[[AgentEvent, asyncio.Event], Awaitable[None] | None]

_logger = logging.getLogger("magipi.agent")


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


class _PendingMessageQueue:
    def __init__(self, mode: QueueMode) -> None:
        self.mode = mode
        self._messages: list[Any] = []

    def enqueue(self, message: Any) -> None:
        self._messages.append(message)

    def has_items(self) -> bool:
        return bool(self._messages)

    def drain(self) -> list[Any]:
        if self.mode == "all":
            messages = list(self._messages)
            self._messages.clear()
            return messages
        if not self._messages:
            return []
        message = self._messages.pop(0)
        return [message]

    def clear(self) -> None:
        self._messages.clear()


class Agent:
    def __init__(self, options: AgentOptions | None = None, **kwargs: Any) -> None:
        if options is None:
            if "model" not in kwargs:
                kwargs["model"] = get_model("faux", "faux-1")
            options = AgentOptions(**kwargs)
        elif kwargs:
            raise TypeError("pass either AgentOptions or keyword options, not both")

        self._runtime_tools = list(options.tools)
        self._state = AgentState(
            systemPrompt=options.system_prompt,
            model=options.model,
            thinkingLevel=options.thinking_level,
            tools=self._tool_specs(),
            messages=list(options.messages),
            isStreaming=False,
            streamingMessage=None,
            pendingToolCalls=[],
            errorMessage=None,
        )
        self.convert_to_llm = options.convert_to_llm or default_convert_to_llm
        self.transform_context = options.transform_context
        self.recover_assistant_response = options.recover_assistant_response
        self.stream_fn = options.stream_fn
        self.get_api_key = options.get_api_key
        self.cache_retention = options.cache_retention
        self.session_id = options.session_id
        self.transport = options.transport
        self.thinking_budgets = dict(options.thinking_budgets)
        self.max_retry_delay_ms = options.max_retry_delay_ms
        self.on_payload = options.on_payload
        self.on_response = options.on_response
        self.before_tool_call = options.before_tool_call
        self.after_tool_call = options.after_tool_call
        self.metadata = dict(options.metadata)
        self.client = options.client
        self.tool_execution = options.tool_execution
        self._steering_queue = _PendingMessageQueue(options.steering_mode)
        self._follow_up_queue = _PendingMessageQueue(options.follow_up_mode)
        self._listeners: list[Listener] = []
        self._listener_errors: list[dict[str, str]] = []
        self._active_run: ActiveRun | None = None

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def tools(self) -> list[RuntimeAgentTool]:
        return list(self._runtime_tools)

    @tools.setter
    def tools(self, tools: list[RuntimeAgentTool]) -> None:
        self._runtime_tools = list(tools)
        self._state.tools = self._tool_specs()

    @property
    def steering_mode(self) -> QueueMode:
        return self._steering_queue.mode

    @steering_mode.setter
    def steering_mode(self, mode: QueueMode) -> None:
        self._steering_queue.mode = mode

    @property
    def follow_up_mode(self) -> QueueMode:
        return self._follow_up_queue.mode

    @follow_up_mode.setter
    def follow_up_mode(self, mode: QueueMode) -> None:
        self._follow_up_queue.mode = mode

    @property
    def signal(self) -> asyncio.Event | None:
        return self._active_run.signal if self._active_run else None

    @property
    def active_run_id(self) -> str | None:
        return self._active_run.id if self._active_run else None

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return unsubscribe

    async def prompt(
        self,
        input: str | Any | list[Any],
        images: list[ImageContent] | None = None,
    ) -> None:
        if self._active_run is not None:
            raise RuntimeError(
                "Agent is already processing a prompt. Use steer() or follow_up() to queue messages."
            )
        messages = self._normalize_prompt_input(input, images)
        await self._run_prompt_messages(messages)

    async def continue_(self) -> None:
        if self._active_run is not None:
            raise RuntimeError("Agent is already processing. Wait for completion before continuing.")
        if not self._state.messages:
            raise RuntimeError("No messages to continue from")

        last_message = self._state.messages[-1]
        if getattr(last_message, "role", None) == "assistant":
            queued_steering = self._steering_queue.drain()
            if queued_steering:
                await self._run_prompt_messages(queued_steering, skip_initial_steering_poll=True)
                return

            queued_follow_up = self._follow_up_queue.drain()
            if queued_follow_up:
                await self._run_prompt_messages(queued_follow_up)
                return

            raise RuntimeError("Cannot continue from message role: assistant")

        await self._run_continuation()

    def steer(self, message: Any) -> None:
        self._steering_queue.enqueue(message)

    def follow_up(self, message: Any) -> None:
        self._follow_up_queue.enqueue(message)

    def clear_steering_queue(self) -> None:
        self._steering_queue.clear()

    def clear_follow_up_queue(self) -> None:
        self._follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        self.clear_steering_queue()
        self.clear_follow_up_queue()

    def has_queued_messages(self) -> bool:
        return self._steering_queue.has_items() or self._follow_up_queue.has_items()

    def abort(self) -> None:
        if self._active_run is None:
            _logger.debug("agent abort ignored because no run is active")
            return
        _logger.debug(
            "agent abort requested",
            extra={
                "run_id": self._active_run.id,
                "stream_registered": self._active_run.stream is not None,
            },
        )
        self._active_run.signal.set()
        if self._active_run.stream is not None:
            self._active_run.stream.close()

    async def wait_for_idle(self) -> None:
        if self._active_run is None:
            return
        await self._active_run.settlement

    def reset(self) -> None:
        self._state.messages = []
        self._state.streaming_message = None
        self._state.pending_tool_calls = []
        self._state.error_message = None
        self._state.is_streaming = False
        self.clear_all_queues()

    async def _run_prompt_messages(
        self,
        messages: list[Any],
        *,
        skip_initial_steering_poll: bool = False,
    ) -> None:
        await self._run_with_lifecycle(
            lambda signal: run_agent_loop(
                messages,
                self._create_context_snapshot(),
                self._create_loop_config(skip_initial_steering_poll=skip_initial_steering_poll),
                self._process_event,
                signal,
            )
        )

    async def _run_continuation(self) -> None:
        await self._run_with_lifecycle(
            lambda signal: run_agent_loop_continue(
                self._create_context_snapshot(),
                self._create_loop_config(),
                self._process_event,
                signal,
            )
        )

    async def _run_with_lifecycle(self, executor: Callable[[asyncio.Event], Awaitable[Any]]) -> None:
        if self._active_run is not None:
            raise RuntimeError("Agent is already processing.")

        signal = asyncio.Event()
        settlement = asyncio.get_running_loop().create_future()
        active_run = ActiveRun(
            id=self._mint_run_id(),
            signal=signal,
            settlement=settlement,
        )
        self._active_run = active_run
        self._state.is_streaming = True
        self._state.streaming_message = None
        self._state.error_message = None
        run_start_index = len(self._state.messages)

        try:
            await executor(signal)
        except Exception as exc:
            await self._handle_run_failure(exc, signal.is_set(), run_start_index)
        finally:
            self._finish_run(active_run)

    async def _handle_run_failure(
        self,
        error: Exception,
        aborted: bool,
        run_start_index: int,
    ) -> None:
        failure = AssistantMessage(
            role="assistant",
            content=[TextContent(text="")],
            api=self._state.model.api,
            provider=self._state.model.provider,
            model=self._state.model.id,
            usage=_empty_usage(),
            stopReason="aborted" if aborted else "error",
            errorMessage=str(error),
            timestamp=_now_ms(),
        )
        self._state.error_message = failure.error_message
        await self._process_event(MessageStartEvent(message=failure))
        await self._process_event(MessageEndEvent(message=failure))
        await self._process_event(TurnEndEvent(message=failure, toolResults=[]))
        await self._process_event(AgentEndEvent(messages=self._state.messages[run_start_index:]))

    def _finish_run(self, active_run: ActiveRun) -> None:
        self._state.is_streaming = False
        self._state.streaming_message = None
        self._state.pending_tool_calls = []
        if not active_run.settlement.done():
            active_run.settlement.set_result(None)
        if self._active_run is active_run:
            self._active_run = None

    async def _process_event(self, event: AgentEvent) -> None:
        self._reduce_state(event)
        if self._active_run is None:
            raise RuntimeError("Agent listener invoked outside active run")
        signal = self._active_run.signal
        for listener in list(self._listeners):
            try:
                result = listener(event, signal)
                if isinstance(result, Awaitable):
                    await result
            except Exception as exc:
                event_type = getattr(event, "type", type(event).__name__)
                self._listener_errors.append(
                    {
                        "eventType": str(event_type),
                        "errorType": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                _logger.exception(
                    "agent listener failed",
                    extra={"event_type": event_type},
                )
                self._state.error_message = str(exc)

    def _reduce_state(self, event: AgentEvent) -> None:
        if isinstance(event, MessageStartEvent | MessageUpdateEvent):
            self._state.streaming_message = event.message
            return

        if isinstance(event, MessageEndEvent):
            self._state.streaming_message = None
            self._state.messages.append(event.message)
            return

        if isinstance(event, ToolExecutionStartEvent):
            pending = list(self._state.pending_tool_calls)
            if event.tool_call_id not in pending:
                pending.append(event.tool_call_id)
            self._state.pending_tool_calls = pending
            return

        if isinstance(event, ToolExecutionEndEvent):
            self._state.pending_tool_calls = [
                tool_call_id
                for tool_call_id in self._state.pending_tool_calls
                if tool_call_id != event.tool_call_id
            ]
            return

        if isinstance(event, TurnEndEvent):
            if isinstance(event.message, AssistantMessage) and event.message.error_message:
                self._state.error_message = event.message.error_message
            return

        if isinstance(event, AgentEndEvent):
            self._state.streaming_message = None

    def _create_context_snapshot(self) -> AgentContext:
        return AgentContext(
            systemPrompt=self._state.system_prompt,
            messages=list(self._state.messages),
            tools=self._tool_specs(),
        )

    def _create_loop_config(self, *, skip_initial_steering_poll: bool = False) -> Any:
        from .runtime_types import AgentLoopConfig

        skip_poll = skip_initial_steering_poll

        def get_steering_messages() -> list[Any]:
            nonlocal skip_poll
            if skip_poll:
                skip_poll = False
                return []
            return self._steering_queue.drain()

        return AgentLoopConfig(
            model=self._state.model,
            thinking_level=self._state.thinking_level,
            thinking_budgets=dict(self.thinking_budgets),
            system_prompt=self._state.system_prompt,
            tools=list(self._runtime_tools),
            tool_execution=self.tool_execution,
            convert_to_llm=self.convert_to_llm,
            transform_context=self.transform_context,
            recover_assistant_response=self.recover_assistant_response,
            stream_fn=self.stream_fn,
            get_api_key=self.get_api_key,
            cache_retention=self.cache_retention,
            session_id=self.session_id,
            transport=self.transport,
            max_retry_delay_ms=self.max_retry_delay_ms,
            on_payload=self.on_payload,
            on_response=self.on_response,
            before_tool_call=self.before_tool_call,
            after_tool_call=self.after_tool_call,
            metadata=dict(self.metadata),
            client=self.client,
            get_steering_messages=get_steering_messages,
            get_follow_up_messages=self._follow_up_queue.drain,
            on_stream_created=self._register_stream,
        )

    def _register_stream(self, stream: Any) -> None:
        if self._active_run is None:
            return
        self._active_run.stream = stream
        if self._active_run.signal.is_set():
            stream.close()

    def _normalize_prompt_input(
        self,
        input: str | Any | list[Any],
        images: list[ImageContent] | None,
    ) -> list[Any]:
        if isinstance(input, list):
            return list(input)
        if isinstance(input, str):
            return [
                UserMessage(
                    role="user",
                    content=user_content_from_text_and_images(input, images),
                    timestamp=_now_ms(),
                )
            ]
        return [input]

    def _tool_specs(self) -> list[AgentTool]:
        return [tool.to_agent_tool_spec() for tool in self._runtime_tools]

    @staticmethod
    def _mint_run_id() -> str:
        return f"run-{uuid.uuid7()}"


__all__ = [
    "Agent",
    "AgentOptions",
    "RuntimeAgentTool",
]
