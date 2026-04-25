"""Coding-agent product session types (pydantic v2).

Mirrors the durable session shape from `packages/coding-agent/src/core/messages.ts`
and `session-manager.ts` (pi-mono `97a38bf6`). Extends ``agent_core`` core
types with the four coding-specific message roles and the 9 session entry
types, plus the 5 session-level frames that fold into ``AgentSessionEvent``.

Timestamp convention (locked by ADR-0010):

- ``SessionHeader.timestamp`` and every ``SessionEntryBase.timestamp`` are
  ISO8601 ``str``.
- The four coding message roles' ``timestamp`` fields stay ``int`` (Unix
  milliseconds), matching ``AgentMessage`` boundary types.
- Pydantic must not silently coerce either form to ``datetime``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from agent_core.types import (
    AgentStartEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnStartEvent,
)
from ai_provider.types import (
    AssistantMessage,
    AssistantMessageEventItem,
    ImageContent,
    TextContent,
    ToolResultMessage,
    UserMessage,
)

CURRENT_SESSION_VERSION = 3


class _PiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


# --------------------------------------------------------------------------- #
# Coding-specific message roles (`packages/coding-agent/src/core/messages.ts`) #
# These are the 4 roles merged into Pi's CustomAgentMessages declaration.      #
# --------------------------------------------------------------------------- #


class BashExecutionMessage(_PiModel):
    role: Literal["bashExecution"] = "bashExecution"
    command: str
    output: str
    exit_code: int | None = Field(default=None, alias="exitCode")
    cancelled: bool
    truncated: bool
    full_output_path: str | None = Field(default=None, alias="fullOutputPath")
    timestamp: int  # Unix ms — keep as int even though wrapping SessionEntry uses ISO8601
    exclude_from_context: bool | None = Field(default=None, alias="excludeFromContext")


CustomContent = Union[TextContent, ImageContent]


class CustomMessage(_PiModel):
    role: Literal["custom"] = "custom"
    custom_type: str = Field(alias="customType")
    content: str | list[Annotated[CustomContent, Field(discriminator="type")]]
    display: bool
    details: Any = None
    timestamp: int  # Unix ms


class BranchSummaryMessage(_PiModel):
    role: Literal["branchSummary"] = "branchSummary"
    summary: str
    from_id: str = Field(alias="fromId")
    timestamp: int  # Unix ms


class CompactionSummaryMessage(_PiModel):
    role: Literal["compactionSummary"] = "compactionSummary"
    summary: str
    tokens_before: int = Field(alias="tokensBefore")
    timestamp: int  # Unix ms


CodingAgentMessage = Union[
    UserMessage,
    AssistantMessage,
    ToolResultMessage,
    BashExecutionMessage,
    CustomMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
]
CodingAgentMessageItem = Annotated[CodingAgentMessage, Field(discriminator="role")]


# --------------------------------------------------------------------------- #
# Session header + entry base                                                  #
# --------------------------------------------------------------------------- #


class SessionHeader(_PiModel):
    type: Literal["session"] = "session"
    version: int = CURRENT_SESSION_VERSION
    id: str
    timestamp: str  # ISO8601 — never coerce to datetime
    cwd: str
    parent_session: str | None = Field(default=None, alias="parentSession")


class SessionEntryBase(_PiModel):
    """Shared fields across every entry type. Discriminated by ``type``."""

    type: str
    id: str
    parent_id: str | None = Field(default=None, alias="parentId")
    timestamp: str  # ISO8601


# --------------------------------------------------------------------------- #
# 9 entry types (`session-manager.ts` SessionEntry union)                       #
# --------------------------------------------------------------------------- #


class MessageEntry(SessionEntryBase):
    type: Literal["message"] = "message"
    message: CodingAgentMessageItem


class ThinkingLevelChangeEntry(SessionEntryBase):
    type: Literal["thinking_level_change"] = "thinking_level_change"
    thinking_level: str = Field(alias="thinkingLevel")


class ModelChangeEntry(SessionEntryBase):
    type: Literal["model_change"] = "model_change"
    provider: str
    model_id: str = Field(alias="modelId")


class CompactionEntry(SessionEntryBase):
    type: Literal["compaction"] = "compaction"
    summary: str
    first_kept_entry_id: str = Field(alias="firstKeptEntryId")
    tokens_before: int = Field(alias="tokensBefore")
    details: Any = None
    from_hook: bool | None = Field(default=None, alias="fromHook")


class BranchSummaryEntry(SessionEntryBase):
    type: Literal["branch_summary"] = "branch_summary"
    from_id: str = Field(alias="fromId")
    summary: str
    details: Any = None
    from_hook: bool | None = Field(default=None, alias="fromHook")


class CustomEntry(SessionEntryBase):
    type: Literal["custom"] = "custom"
    custom_type: str = Field(alias="customType")
    data: Any = None


class CustomMessageEntry(SessionEntryBase):
    type: Literal["custom_message"] = "custom_message"
    custom_type: str = Field(alias="customType")
    content: str | list[Annotated[CustomContent, Field(discriminator="type")]]
    display: bool
    details: Any = None


class LabelEntry(SessionEntryBase):
    type: Literal["label"] = "label"
    target_id: str = Field(alias="targetId")
    label: str | None = None


class SessionInfoEntry(SessionEntryBase):
    type: Literal["session_info"] = "session_info"
    name: str | None = None


SessionEntry = Union[
    MessageEntry,
    ThinkingLevelChangeEntry,
    ModelChangeEntry,
    CompactionEntry,
    BranchSummaryEntry,
    CustomEntry,
    CustomMessageEntry,
    LabelEntry,
    SessionInfoEntry,
]
SessionEntryItem = Annotated[SessionEntry, Field(discriminator="type")]


# --------------------------------------------------------------------------- #
# Session-level snapshots                                                      #
# --------------------------------------------------------------------------- #


class SessionInfo(_PiModel):
    """Lightweight summary used by `/session`, `/resume`, and the SDK."""

    id: str
    cwd: str
    name: str | None = None
    parent_session: str | None = Field(default=None, alias="parentSession")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class SessionTreeNode(_PiModel):
    """Tree node used by `/tree`. ``entry`` is the SessionEntry rendered at this
    point; ``children`` carries forks / branch summaries."""

    entry: SessionEntryItem
    children: list[SessionTreeNode] = Field(default_factory=list)


class SessionContext(_PiModel):
    """Output of ``build_session_context(leaf)`` — derived from session tree
    walk. ``messages`` is the LLM-bound transcript after compaction / branch
    summary substitution."""

    header: SessionHeader
    messages: list[CodingAgentMessageItem]
    model_id: str | None = Field(default=None, alias="modelId")
    provider: str | None = None
    thinking_level: str | None = Field(default=None, alias="thinkingLevel")
    leaf_entry_id: str | None = Field(default=None, alias="leafEntryId")


# --------------------------------------------------------------------------- #
# Coding-layer message-bearing events                                          #
#                                                                              #
# Pi handles this via TS declaration merging — `MessageStartEvent.message:     #
# AgentMessage` resolves to the wider 7-role union at the cli layer. Python    #
# has no declaration merging, so the 5 events that carry a `message` payload  #
# are redefined here against ``CodingAgentMessageItem`` (7 roles: 3 wire      #
# roles + 4 coding-specific roles). Lifecycle events that carry no message    #
# (`agent_start`, `turn_start`, `tool_execution_*`) reuse the core types.     #
# --------------------------------------------------------------------------- #


class AgentEndEvent(_PiModel):
    type: Literal["agent_end"] = "agent_end"
    messages: list[CodingAgentMessageItem]


class TurnEndEvent(_PiModel):
    type: Literal["turn_end"] = "turn_end"
    message: CodingAgentMessageItem
    tool_results: list[ToolResultMessage] = Field(alias="toolResults")


class MessageStartEvent(_PiModel):
    type: Literal["message_start"] = "message_start"
    message: CodingAgentMessageItem


class MessageUpdateEvent(_PiModel):
    type: Literal["message_update"] = "message_update"
    message: CodingAgentMessageItem
    assistant_message_event: AssistantMessageEventItem = Field(alias="assistantMessageEvent")


class MessageEndEvent(_PiModel):
    type: Literal["message_end"] = "message_end"
    message: CodingAgentMessageItem


# --------------------------------------------------------------------------- #
# AgentSessionEvent — core 10 + 5 session-level frames = 15 total              #
# --------------------------------------------------------------------------- #


class QueueUpdateEvent(_PiModel):
    type: Literal["queue_update"] = "queue_update"
    steering: list[str]
    follow_up: list[str] = Field(alias="followUp")


class CompactionStartEvent(_PiModel):
    type: Literal["compaction_start"] = "compaction_start"
    reason: Literal["manual", "threshold", "overflow"]


class CompactionEndEvent(_PiModel):
    type: Literal["compaction_end"] = "compaction_end"
    reason: Literal["manual", "threshold", "overflow"]
    result: Any | None = None  # CompactionResult shape; not modelled in M0
    aborted: bool
    will_retry: bool = Field(alias="willRetry")
    error_message: str | None = Field(default=None, alias="errorMessage")


class AutoRetryStartEvent(_PiModel):
    type: Literal["auto_retry_start"] = "auto_retry_start"
    attempt: int
    max_attempts: int = Field(alias="maxAttempts")
    delay_ms: int = Field(alias="delayMs")
    error_message: str = Field(alias="errorMessage")


class AutoRetryEndEvent(_PiModel):
    type: Literal["auto_retry_end"] = "auto_retry_end"
    success: bool
    attempt: int
    final_error: str | None = Field(default=None, alias="finalError")


AgentSessionEvent = Union[
    AgentStartEvent,            # core (no message field)
    AgentEndEvent,              # cli wrapper, carries CodingAgentMessageItem list
    TurnStartEvent,             # core (no message field)
    TurnEndEvent,                # cli wrapper, message: CodingAgentMessageItem
    MessageStartEvent,          # cli wrapper
    MessageUpdateEvent,         # cli wrapper
    MessageEndEvent,            # cli wrapper
    ToolExecutionStartEvent,    # core
    ToolExecutionUpdateEvent,   # core
    ToolExecutionEndEvent,      # core
    QueueUpdateEvent,
    CompactionStartEvent,
    CompactionEndEvent,
    AutoRetryStartEvent,
    AutoRetryEndEvent,
]
AgentSessionEventItem = Annotated[AgentSessionEvent, Field(discriminator="type")]


# --------------------------------------------------------------------------- #
# TypeAdapters                                                                 #
# --------------------------------------------------------------------------- #

SessionHeaderAdapter = TypeAdapter(SessionHeader)
SessionEntryAdapter: TypeAdapter[SessionEntry] = TypeAdapter(SessionEntryItem)
CodingAgentMessageAdapter: TypeAdapter[CodingAgentMessage] = TypeAdapter(CodingAgentMessageItem)
AgentSessionEventAdapter: TypeAdapter[AgentSessionEvent] = TypeAdapter(AgentSessionEventItem)
SessionContextAdapter = TypeAdapter(SessionContext)


# Resolve recursive SessionTreeNode forward-ref now that union is defined.
SessionTreeNode.model_rebuild()


__all__ = [
    "AgentEndEvent",
    "AgentSessionEvent",
    "AgentSessionEventAdapter",
    "AgentSessionEventItem",
    "AutoRetryEndEvent",
    "AutoRetryStartEvent",
    "BashExecutionMessage",
    "BranchSummaryEntry",
    "BranchSummaryMessage",
    "CURRENT_SESSION_VERSION",
    "CodingAgentMessage",
    "CodingAgentMessageAdapter",
    "CodingAgentMessageItem",
    "CompactionEndEvent",
    "CompactionEntry",
    "CompactionStartEvent",
    "CompactionSummaryMessage",
    "CustomEntry",
    "CustomMessage",
    "CustomMessageEntry",
    "LabelEntry",
    "MessageEndEvent",
    "MessageEntry",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "ModelChangeEntry",
    "QueueUpdateEvent",
    "SessionContext",
    "SessionContextAdapter",
    "SessionEntry",
    "SessionEntryAdapter",
    "SessionEntryBase",
    "SessionEntryItem",
    "SessionHeader",
    "SessionHeaderAdapter",
    "SessionInfo",
    "SessionInfoEntry",
    "SessionTreeNode",
    "ThinkingLevelChangeEntry",
    "TurnEndEvent",
]
