"""Agent core protocol types (pydantic v2).

Mirrors `packages/agent/src/types.ts` (pi-mono `97a38bf6`). Boundary with
``ai_provider`` is the same `Message` types; this module only adds agent-level
state, tool wrappers, and the 10-frame `AgentEvent` union.

NOTE on declaration merging:
- Pi merges `CustomAgentMessages` declarations across packages so coding-agent
  can extend `AgentMessage` with `bashExecution` / `custom` / `branchSummary` /
  `compactionSummary`. Python equivalent lives in `cli.core.session_types` —
  this module deliberately keeps `AgentMessage` at the core 3-role union.
- `AgentEvent` here is the **core 10 frames**; the 5 session-level frames live
  in `cli.core.session_types.AgentSessionEvent`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ai_provider.types import (
    AssistantMessage,
    AssistantMessageEventItem,
    Message,
    MessageItem,
    Model,
    ThinkingLevel,
    ToolResultMessage,
)


class _PiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


# --------------------------------------------------------------------------- #
# Tool execution mode + tool wrapper                                           #
# --------------------------------------------------------------------------- #

ToolExecutionMode = Literal["sequential", "parallel"]


class AgentToolResult(_PiModel):
    """Result returned by an `AgentTool.execute`. Generic over `details` shape;
    NeoMAGI-internal callers may wrap with a more specific TypeAdapter."""

    content: list[dict[str, Any]]  # TextContent | ImageContent — kept loose for
    """Tool output content list (text/image blocks); validated again by
    ToolResultMessage adapter when persisted."""
    details: Any = None
    is_error: bool | None = Field(default=None, alias="isError")
    """Optional runtime-only error marker.

    Pi's TS AgentTool result shape carries content/details while the executor
    tracks ``isError`` out-of-band. M5 governed tools need to return structured
    policy/timeout/exception details without throwing them away, so the Python
    runtime accepts this optional marker and still serializes only the final
    ``ToolResultMessage.isError`` at the wire boundary.
    """


class AgentTool(_PiModel):
    """Runtime shape for any tool exposed to the LLM.

    `parameters` carries the tool's JSON Schema (extension contract);
    `prepareArguments`, `execute`, and renderers are runtime callables and
    therefore live outside the wire payload.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    label: str
    execution_mode: ToolExecutionMode | None = Field(default=None, alias="executionMode")


class BeforeToolCallResult(_PiModel):
    block: bool | None = None
    reason: str | None = None


class AfterToolCallResult(_PiModel):
    """Field-by-field override of an executed tool result. No deep merge."""

    content: list[dict[str, Any]] | None = None
    details: Any = None
    is_error: bool | None = Field(default=None, alias="isError")


class BeforeToolCallContext(_PiModel):
    assistant_message: AssistantMessage = Field(alias="assistantMessage")
    tool_call: dict[str, Any] = Field(alias="toolCall")
    args: Any
    context: AgentContext


class AfterToolCallContext(_PiModel):
    assistant_message: AssistantMessage = Field(alias="assistantMessage")
    tool_call: dict[str, Any] = Field(alias="toolCall")
    args: Any
    result: AgentToolResult
    is_error: bool = Field(alias="isError")
    context: AgentContext


# --------------------------------------------------------------------------- #
# Agent context / state                                                        #
# --------------------------------------------------------------------------- #


class AgentContext(_PiModel):
    """Snapshot fed into the low-level agent loop."""

    system_prompt: str = Field(alias="systemPrompt")
    messages: list[Any]
    tools: list[AgentTool] | None = None


# Core-layer AgentMessage = the 3 wire roles. cli.core extends this union.
AgentMessage = Message
AgentMessageItem = MessageItem


class AgentState(_PiModel):
    system_prompt: str = Field(alias="systemPrompt")
    model: Model
    thinking_level: ThinkingLevel = Field(alias="thinkingLevel")
    tools: list[AgentTool] = Field(default_factory=list)
    messages: list[AgentMessageItem] = Field(default_factory=list)
    is_streaming: bool = Field(default=False, alias="isStreaming")
    streaming_message: AgentMessageItem | None = Field(default=None, alias="streamingMessage")
    pending_tool_calls: list[str] = Field(default_factory=list, alias="pendingToolCalls")
    error_message: str | None = Field(default=None, alias="errorMessage")


# --------------------------------------------------------------------------- #
# AgentEvent — core 10 frames (`packages/agent/src/types.ts:337–352`)         #
# --------------------------------------------------------------------------- #


class _Event(_PiModel):
    pass


class AgentStartEvent(_Event):
    type: Literal["agent_start"] = "agent_start"


class AgentEndEvent(_Event):
    type: Literal["agent_end"] = "agent_end"
    messages: list[AgentMessageItem]


class TurnStartEvent(_Event):
    type: Literal["turn_start"] = "turn_start"


class TurnEndEvent(_Event):
    type: Literal["turn_end"] = "turn_end"
    message: AgentMessageItem
    tool_results: list[ToolResultMessage] = Field(alias="toolResults")


class MessageStartEvent(_Event):
    type: Literal["message_start"] = "message_start"
    message: AgentMessageItem


class MessageUpdateEvent(_Event):
    type: Literal["message_update"] = "message_update"
    message: AgentMessageItem
    assistant_message_event: AssistantMessageEventItem = Field(alias="assistantMessageEvent")


class MessageEndEvent(_Event):
    type: Literal["message_end"] = "message_end"
    message: AgentMessageItem


class ToolExecutionStartEvent(_Event):
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    args: Any


class ToolExecutionUpdateEvent(_Event):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    args: Any
    partial_result: Any = Field(alias="partialResult")


class ToolExecutionEndEvent(_Event):
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    result: Any
    is_error: bool = Field(alias="isError")


AgentEvent = Union[
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
]
AgentEventItem = Annotated[AgentEvent, Field(discriminator="type")]


# Resolve forward refs for context-bearing models.
BeforeToolCallContext.model_rebuild()
AfterToolCallContext.model_rebuild()


# --------------------------------------------------------------------------- #
# TypeAdapters                                                                 #
# --------------------------------------------------------------------------- #

AgentEventAdapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEventItem)
AgentStateAdapter = TypeAdapter(AgentState)
AgentContextAdapter = TypeAdapter(AgentContext)
AgentToolAdapter = TypeAdapter(AgentTool)


__all__ = [
    "AfterToolCallContext",
    "AfterToolCallResult",
    "AgentContext",
    "AgentContextAdapter",
    "AgentEndEvent",
    "AgentEvent",
    "AgentEventAdapter",
    "AgentEventItem",
    "AgentMessage",
    "AgentMessageItem",
    "AgentStartEvent",
    "AgentState",
    "AgentStateAdapter",
    "AgentTool",
    "AgentToolAdapter",
    "AgentToolResult",
    "BeforeToolCallContext",
    "BeforeToolCallResult",
    "MessageEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "ToolExecutionEndEvent",
    "ToolExecutionMode",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "TurnEndEvent",
    "TurnStartEvent",
]
