"""Pi-compatible AI protocol types (pydantic v2).

Mirrors `packages/ai/src/types.ts` (pi-mono `97a38bf6`). See ADR-0010 for
serialization rules and `pi_behavior_matrix.md` § E for the event union.

Implementation rules (from ADR-0010):
- Pi-compatible models use ``ConfigDict(populate_by_name=True, extra="allow")``
  so unknown / opaque fields (``thinkingSignature`` / ``thoughtSignature`` /
  ``textSignature`` / ``responseId``) pass through without loss.
- ``model_dump(by_alias=True, exclude_none=True)`` is the canonical serialization.
- ``timestamp`` on user / assistant / tool-result messages is **Unix milliseconds**
  (``int``); never auto-coerce to ``datetime``.
- Discriminated unions use the wire-level ``type`` / ``role`` fields.
- Each cross-boundary type exposes a ``TypeAdapter`` for fixture round-trip.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# --------------------------------------------------------------------------- #
# Shared config: Pi-compatible wire model — preserve unknown / opaque fields.
# --------------------------------------------------------------------------- #


class _PiModel(BaseModel):
    """Base for Pi-compatible wire types: alias-driven I/O, transparent extras."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")


# --------------------------------------------------------------------------- #
# Enums (str-valued for direct JSON I/O)                                       #
# --------------------------------------------------------------------------- #

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]
"""``agent_core``-flavored thinking level. ``ai_provider`` keeps ``off`` here so
boundary fixtures can serialize the inactive state; provider-call thinking level
in pi-ai itself omits ``off`` (see `packages/ai/src/types.ts:45`)."""

CacheRetention = Literal["none", "short", "long"]
Transport = Literal["sse", "websocket", "auto"]
StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]


# --------------------------------------------------------------------------- #
# Content blocks (`packages/ai/src/types.ts:141–175`)                          #
# --------------------------------------------------------------------------- #


class TextContent(_PiModel):
    type: Literal["text"] = "text"
    text: str
    text_signature: str | None = Field(default=None, alias="textSignature")


class ThinkingContent(_PiModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    thinking_signature: str | None = Field(default=None, alias="thinkingSignature")
    redacted: bool | None = None


class ImageContent(_PiModel):
    type: Literal["image"] = "image"
    data: str
    mime_type: str = Field(alias="mimeType")


class ToolCall(_PiModel):
    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    arguments: dict[str, Any]
    thought_signature: str | None = Field(default=None, alias="thoughtSignature")


UserContent = Union[TextContent, ImageContent]
AssistantContent = Union[TextContent, ThinkingContent, ToolCall]
ToolResultContent = Union[TextContent, ImageContent]

UserContentItem = Annotated[UserContent, Field(discriminator="type")]
AssistantContentItem = Annotated[AssistantContent, Field(discriminator="type")]
ToolResultContentItem = Annotated[ToolResultContent, Field(discriminator="type")]


# --------------------------------------------------------------------------- #
# Usage / cost (`packages/ai/src/types.ts:177–192`)                            #
# --------------------------------------------------------------------------- #


class UsageCost(_PiModel):
    input: float = 0.0
    output: float = 0.0
    cache_read: float = Field(default=0.0, alias="cacheRead")
    cache_write: float = Field(default=0.0, alias="cacheWrite")
    total: float = 0.0


class Usage(_PiModel):
    input: int = 0
    output: int = 0
    cache_read: int = Field(default=0, alias="cacheRead")
    cache_write: int = Field(default=0, alias="cacheWrite")
    total_tokens: int = Field(default=0, alias="totalTokens")
    cost: UsageCost = Field(default_factory=UsageCost)


# --------------------------------------------------------------------------- #
# Messages (`packages/ai/src/types.ts:194–223`)                                #
# --------------------------------------------------------------------------- #


class UserMessage(_PiModel):
    role: Literal["user"] = "user"
    content: str | list[UserContentItem]
    timestamp: int  # Unix milliseconds — do not coerce to datetime


class AssistantMessage(_PiModel):
    role: Literal["assistant"] = "assistant"
    content: list[AssistantContentItem]
    api: str
    provider: str
    model: str
    response_id: str | None = Field(default=None, alias="responseId")
    usage: Usage
    stop_reason: StopReason = Field(alias="stopReason")
    error_message: str | None = Field(default=None, alias="errorMessage")
    timestamp: int  # Unix milliseconds


class ToolResultMessage(_PiModel):
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    content: list[ToolResultContentItem]
    details: Any = None
    is_error: bool = Field(alias="isError")
    timestamp: int  # Unix milliseconds


Message = Union[UserMessage, AssistantMessage, ToolResultMessage]
MessageItem = Annotated[Message, Field(discriminator="role")]


# --------------------------------------------------------------------------- #
# Tool / Context (`packages/ai/src/types.ts:227–245`)                          #
# --------------------------------------------------------------------------- #


class Tool(_PiModel):
    name: str
    description: str
    parameters: dict[str, Any]


class Context(_PiModel):
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    messages: list[MessageItem]
    tools: list[Tool] | None = None


# --------------------------------------------------------------------------- #
# Assistant stream events (`packages/ai/src/types.ts:247–263`) — 12 frames     #
# --------------------------------------------------------------------------- #


class _StreamFrame(_PiModel):
    """Base for AssistantMessageEvent frames. ``partial`` carries the running
    snapshot, not just the delta."""


class StreamStart(_StreamFrame):
    type: Literal["start"] = "start"
    partial: AssistantMessage


class StreamTextStart(_StreamFrame):
    type: Literal["text_start"] = "text_start"
    content_index: int = Field(alias="contentIndex")
    partial: AssistantMessage


class StreamTextDelta(_StreamFrame):
    type: Literal["text_delta"] = "text_delta"
    content_index: int = Field(alias="contentIndex")
    delta: str
    partial: AssistantMessage


class StreamTextEnd(_StreamFrame):
    type: Literal["text_end"] = "text_end"
    content_index: int = Field(alias="contentIndex")
    content: str
    partial: AssistantMessage


class StreamThinkingStart(_StreamFrame):
    type: Literal["thinking_start"] = "thinking_start"
    content_index: int = Field(alias="contentIndex")
    partial: AssistantMessage


class StreamThinkingDelta(_StreamFrame):
    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int = Field(alias="contentIndex")
    delta: str
    partial: AssistantMessage


class StreamThinkingEnd(_StreamFrame):
    type: Literal["thinking_end"] = "thinking_end"
    content_index: int = Field(alias="contentIndex")
    content: str
    partial: AssistantMessage


class StreamToolCallStart(_StreamFrame):
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int = Field(alias="contentIndex")
    partial: AssistantMessage


class StreamToolCallDelta(_StreamFrame):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int = Field(alias="contentIndex")
    delta: str
    partial: AssistantMessage


class StreamToolCallEnd(_StreamFrame):
    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int = Field(alias="contentIndex")
    tool_call: ToolCall = Field(alias="toolCall")
    partial: AssistantMessage


class StreamDone(_StreamFrame):
    type: Literal["done"] = "done"
    reason: Literal["stop", "length", "toolUse"]
    message: AssistantMessage


class StreamError(_StreamFrame):
    type: Literal["error"] = "error"
    reason: Literal["aborted", "error"]
    error: AssistantMessage


AssistantMessageEvent = Union[
    StreamStart,
    StreamTextStart,
    StreamTextDelta,
    StreamTextEnd,
    StreamThinkingStart,
    StreamThinkingDelta,
    StreamThinkingEnd,
    StreamToolCallStart,
    StreamToolCallDelta,
    StreamToolCallEnd,
    StreamDone,
    StreamError,
]
AssistantMessageEventItem = Annotated[AssistantMessageEvent, Field(discriminator="type")]


# --------------------------------------------------------------------------- #
# Model (`packages/ai/src/types.ts:393–416`) — `contextWindow` is required    #
# for silent overflow recovery and auto compaction.                            #
# --------------------------------------------------------------------------- #


class ModelCost(_PiModel):
    input: float
    output: float
    cache_read: float = Field(alias="cacheRead")
    cache_write: float = Field(alias="cacheWrite")


class Model(_PiModel):
    id: str
    name: str
    api: str
    provider: str
    base_url: str = Field(alias="baseUrl")
    reasoning: bool
    input: list[Literal["text", "image"]]
    cost: ModelCost
    context_window: int = Field(alias="contextWindow")
    """Required by silent overflow recovery + auto compaction; missing or
    inaccurate values disable one of Pi's recovery paths."""
    max_tokens: int = Field(alias="maxTokens")
    headers: dict[str, str] | None = None
    compat: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# TypeAdapters (round-trip entry points for fixtures / providers / JSONL)      #
# --------------------------------------------------------------------------- #

UserMessageAdapter = TypeAdapter(UserMessage)
AssistantMessageAdapter = TypeAdapter(AssistantMessage)
ToolResultMessageAdapter = TypeAdapter(ToolResultMessage)
MessageAdapter: TypeAdapter[Message] = TypeAdapter(MessageItem)
AssistantMessageEventAdapter: TypeAdapter[AssistantMessageEvent] = TypeAdapter(
    AssistantMessageEventItem
)
ContextAdapter = TypeAdapter(Context)
ModelAdapter = TypeAdapter(Model)
UsageAdapter = TypeAdapter(Usage)
ToolAdapter = TypeAdapter(Tool)
ToolCallAdapter = TypeAdapter(ToolCall)


__all__ = [
    "AssistantContent",
    "AssistantContentItem",
    "AssistantMessage",
    "AssistantMessageAdapter",
    "AssistantMessageEvent",
    "AssistantMessageEventAdapter",
    "AssistantMessageEventItem",
    "CacheRetention",
    "Context",
    "ContextAdapter",
    "ImageContent",
    "Message",
    "MessageAdapter",
    "MessageItem",
    "Model",
    "ModelAdapter",
    "ModelCost",
    "StopReason",
    "StreamDone",
    "StreamError",
    "StreamStart",
    "StreamTextDelta",
    "StreamTextEnd",
    "StreamTextStart",
    "StreamThinkingDelta",
    "StreamThinkingEnd",
    "StreamThinkingStart",
    "StreamToolCallDelta",
    "StreamToolCallEnd",
    "StreamToolCallStart",
    "TextContent",
    "ThinkingContent",
    "ThinkingLevel",
    "Tool",
    "ToolAdapter",
    "ToolCall",
    "ToolCallAdapter",
    "ToolResultContent",
    "ToolResultContentItem",
    "ToolResultMessage",
    "ToolResultMessageAdapter",
    "Transport",
    "Usage",
    "UsageAdapter",
    "UsageCost",
    "UserContent",
    "UserContentItem",
    "UserMessage",
    "UserMessageAdapter",
]
