"""Runtime-only agent_core types.

These dataclasses and protocols carry Python callables and run-scoped
configuration. They intentionally stay outside the pydantic wire models in
``agent_core.types``.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from ai_provider.runtime_types import PayloadCallback, ResponseCallback, SimpleStreamFunction
from ai_provider.types import CacheRetention, ImageContent, Message, Model, ThinkingLevel, Tool, Transport

from .types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentEvent,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    ToolExecutionMode,
)

if TYPE_CHECKING:
    from ai_provider.streaming import AssistantMessageEventStream

AbortSignal = asyncio.Event
AgentEventSink = Callable[[AgentEvent], Awaitable[None] | None]
QueueMode = Literal["all", "one-at-a-time"]
ToolUpdateCallback = Callable[[AgentToolResult], None]
ConvertToLlm = Callable[[list[Any]], list[Message] | Awaitable[list[Message]]]
TransformContext = Callable[[list[Any], AbortSignal | None], list[Any] | Awaitable[list[Any]]]
ApiKeyResolver = Callable[[str], str | None | Awaitable[str | None]]
BeforeToolCallHook = Callable[
    [BeforeToolCallContext, AbortSignal | None],
    BeforeToolCallResult | None | Awaitable[BeforeToolCallResult | None],
]
AfterToolCallHook = Callable[
    [AfterToolCallContext, AbortSignal | None],
    AfterToolCallResult | None | Awaitable[AfterToolCallResult | None],
]
QueueDrain = Callable[[], list[Any] | Awaitable[list[Any]]]
StreamCreatedCallback = Callable[["AssistantMessageEventStream"], None]


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass(slots=True)
class RuntimeAgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    label: str
    execute: Callable[
        [str, Any, AbortSignal | None, ToolUpdateCallback | None],
        AgentToolResult | Awaitable[AgentToolResult],
    ]
    prepare_arguments: Callable[[Any], Any] | None = None
    execution_mode: ToolExecutionMode | None = None

    def to_provider_tool(self) -> Tool:
        return Tool(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def to_agent_tool_spec(self) -> AgentTool:
        return AgentTool(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            label=self.label,
            executionMode=self.execution_mode,
        )


@dataclass(slots=True)
class AgentLoopConfig:
    model: Model
    thinking_level: ThinkingLevel = "off"
    thinking_budgets: dict[str, int] = field(default_factory=dict)
    system_prompt: str = ""
    tools: list[RuntimeAgentTool] = field(default_factory=list)
    tool_execution: ToolExecutionMode = "parallel"
    convert_to_llm: ConvertToLlm | None = None
    transform_context: TransformContext | None = None
    stream_fn: SimpleStreamFunction | None = None
    get_api_key: ApiKeyResolver | None = None
    cache_retention: CacheRetention | None = None
    session_id: str | None = None
    transport: Transport | None = "sse"
    max_retry_delay_ms: int | None = None
    on_payload: PayloadCallback | None = None
    on_response: ResponseCallback | None = None
    before_tool_call: BeforeToolCallHook | None = None
    after_tool_call: AfterToolCallHook | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    client: object | None = None
    get_steering_messages: QueueDrain | None = None
    get_follow_up_messages: QueueDrain | None = None
    on_stream_created: StreamCreatedCallback | None = None


@dataclass(slots=True)
class AgentOptions:
    model: Model
    system_prompt: str = ""
    thinking_level: ThinkingLevel = "off"
    tools: list[RuntimeAgentTool] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)
    tool_execution: ToolExecutionMode = "parallel"
    steering_mode: QueueMode = "one-at-a-time"
    follow_up_mode: QueueMode = "one-at-a-time"
    convert_to_llm: ConvertToLlm | None = None
    transform_context: TransformContext | None = None
    stream_fn: SimpleStreamFunction | None = None
    get_api_key: ApiKeyResolver | None = None
    cache_retention: CacheRetention | None = None
    session_id: str | None = None
    transport: Transport | None = "sse"
    thinking_budgets: dict[str, int] = field(default_factory=dict)
    max_retry_delay_ms: int | None = None
    on_payload: PayloadCallback | None = None
    on_response: ResponseCallback | None = None
    before_tool_call: BeforeToolCallHook | None = None
    after_tool_call: AfterToolCallHook | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    client: object | None = None


@dataclass(slots=True)
class ActiveRun:
    id: str
    signal: AbortSignal
    settlement: asyncio.Future[None]
    stream: "AssistantMessageEventStream | None" = None


def user_content_from_text_and_images(text: str, images: list[ImageContent] | None) -> list[Any]:
    from ai_provider.types import TextContent

    content: list[Any] = [TextContent(text=text)]
    if images:
        content.extend(images)
    return content


__all__ = [
    "AbortSignal",
    "ActiveRun",
    "AfterToolCallHook",
    "AgentEventSink",
    "AgentLoopConfig",
    "AgentOptions",
    "ApiKeyResolver",
    "BeforeToolCallHook",
    "ConvertToLlm",
    "QueueDrain",
    "QueueMode",
    "RuntimeAgentTool",
    "StreamCreatedCallback",
    "ToolUpdateCallback",
    "TransformContext",
    "maybe_await",
    "user_content_from_text_and_images",
]
