"""Pi-compatible ``tool_call`` / ``tool_result`` extension events.

Mirrors `extensions/types.ts:730–836` (pi-mono `97a38bf6`).

Pi's `ToolCallEvent` / `ToolResultEvent` are discriminated unions over
``toolName`` with one variant per built-in tool (`bash` / `read` / `edit` /
`write` / `grep` / `find` / `ls`) plus a ``custom`` fallback whose
``toolName: string`` accepts any extension-registered name. That overlap
between literal builtins and the open string variant defeats pydantic's
field-discriminator, so we use a callable :class:`Discriminator` that maps
unknown ``toolName`` values to ``"custom"``.

Input / details types are kept loose at M0 (`dict[str, Any]` / `Any`); the
concrete `BashToolInput`, `ReadToolDetails`, etc. land in M3 when the actual
tools are implemented. The discriminator structure is the long-term contract;
the inner field types can be tightened later without breaking the union.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, TypeAdapter

from ai_provider.types import AssistantMessage, ImageContent, TextContent


class _PiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


_BUILTIN_TOOL_NAMES = frozenset({"bash", "read", "edit", "write", "grep", "find", "ls"})


def _tool_event_discriminator(value: Any) -> str:
    """Return the variant tag for a tool_call / tool_result event.

    Builtin ``toolName`` (`bash` / `read` / `edit` / `write` / `grep` / `find`
    / `ls`) maps to itself; everything else maps to ``custom`` so the
    ``CustomToolCallEvent`` / ``CustomToolResultEvent`` variant catches
    arbitrary extension tool names.
    """

    if isinstance(value, dict):
        name = value.get("toolName") or value.get("tool_name")
    else:
        name = getattr(value, "tool_name", None)
    if isinstance(name, str) and name in _BUILTIN_TOOL_NAMES:
        return name
    return "custom"


# --------------------------------------------------------------------------- #
# tool_call variants                                                           #
# --------------------------------------------------------------------------- #


class _ToolCallEventBase(_PiModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str = Field(alias="toolCallId")
    assistant_message: AssistantMessage | None = Field(default=None, alias="assistantMessage")
    """Optional: the assistant message that requested this tool call. Pi exposes
    it on some emit paths."""


class BashToolCallEvent(_ToolCallEventBase):
    tool_name: Literal["bash"] = Field(default="bash", alias="toolName")
    input: dict[str, Any]
    """``BashToolInput`` (`packages/coding-agent/src/core/tools/bash.ts`).
    Mutable in place — later handlers see earlier mutations; **no second schema
    validation is performed after mutation**."""


class ReadToolCallEvent(_ToolCallEventBase):
    tool_name: Literal["read"] = Field(default="read", alias="toolName")
    input: dict[str, Any]


class EditToolCallEvent(_ToolCallEventBase):
    tool_name: Literal["edit"] = Field(default="edit", alias="toolName")
    input: dict[str, Any]


class WriteToolCallEvent(_ToolCallEventBase):
    tool_name: Literal["write"] = Field(default="write", alias="toolName")
    input: dict[str, Any]


class GrepToolCallEvent(_ToolCallEventBase):
    tool_name: Literal["grep"] = Field(default="grep", alias="toolName")
    input: dict[str, Any]


class FindToolCallEvent(_ToolCallEventBase):
    tool_name: Literal["find"] = Field(default="find", alias="toolName")
    input: dict[str, Any]


class LsToolCallEvent(_ToolCallEventBase):
    tool_name: Literal["ls"] = Field(default="ls", alias="toolName")
    input: dict[str, Any]


class CustomToolCallEvent(_ToolCallEventBase):
    tool_name: str = Field(alias="toolName")
    """Arbitrary extension-registered tool name (matches Pi's
    ``CustomToolCallEvent.toolName: string``)."""
    input: dict[str, Any]


ToolCallEvent = Union[
    BashToolCallEvent,
    ReadToolCallEvent,
    EditToolCallEvent,
    WriteToolCallEvent,
    GrepToolCallEvent,
    FindToolCallEvent,
    LsToolCallEvent,
    CustomToolCallEvent,
]
ToolCallEventItem = Annotated[
    Union[
        Annotated[BashToolCallEvent, Tag("bash")],
        Annotated[ReadToolCallEvent, Tag("read")],
        Annotated[EditToolCallEvent, Tag("edit")],
        Annotated[WriteToolCallEvent, Tag("write")],
        Annotated[GrepToolCallEvent, Tag("grep")],
        Annotated[FindToolCallEvent, Tag("find")],
        Annotated[LsToolCallEvent, Tag("ls")],
        Annotated[CustomToolCallEvent, Tag("custom")],
    ],
    Discriminator(_tool_event_discriminator),
]


class ToolCallEventResult(_PiModel):
    block: bool | None = None
    reason: str | None = None


# --------------------------------------------------------------------------- #
# tool_result variants                                                         #
# --------------------------------------------------------------------------- #


class _ToolResultEventBase(_PiModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = Field(alias="toolCallId")
    input: dict[str, Any]
    """Pi `extensions/types.ts:790` — the (possibly mutated) input that the tool
    received. Required field."""
    content: list[Annotated[Union[TextContent, ImageContent], Field(discriminator="type")]]
    is_error: bool = Field(alias="isError")


class BashToolResultEvent(_ToolResultEventBase):
    tool_name: Literal["bash"] = Field(default="bash", alias="toolName")
    details: dict[str, Any] | None = None  # BashToolDetails


class ReadToolResultEvent(_ToolResultEventBase):
    tool_name: Literal["read"] = Field(default="read", alias="toolName")
    details: dict[str, Any] | None = None


class EditToolResultEvent(_ToolResultEventBase):
    tool_name: Literal["edit"] = Field(default="edit", alias="toolName")
    details: dict[str, Any] | None = None


class WriteToolResultEvent(_ToolResultEventBase):
    tool_name: Literal["write"] = Field(default="write", alias="toolName")
    details: None = None
    """Pi: `WriteToolResultEvent.details = undefined` (no details on success)."""


class GrepToolResultEvent(_ToolResultEventBase):
    tool_name: Literal["grep"] = Field(default="grep", alias="toolName")
    details: dict[str, Any] | None = None


class FindToolResultEvent(_ToolResultEventBase):
    tool_name: Literal["find"] = Field(default="find", alias="toolName")
    details: dict[str, Any] | None = None


class LsToolResultEvent(_ToolResultEventBase):
    tool_name: Literal["ls"] = Field(default="ls", alias="toolName")
    details: dict[str, Any] | None = None


class CustomToolResultEvent(_ToolResultEventBase):
    tool_name: str = Field(alias="toolName")
    details: Any = None


ToolResultEvent = Union[
    BashToolResultEvent,
    ReadToolResultEvent,
    EditToolResultEvent,
    WriteToolResultEvent,
    GrepToolResultEvent,
    FindToolResultEvent,
    LsToolResultEvent,
    CustomToolResultEvent,
]
ToolResultEventItem = Annotated[
    Union[
        Annotated[BashToolResultEvent, Tag("bash")],
        Annotated[ReadToolResultEvent, Tag("read")],
        Annotated[EditToolResultEvent, Tag("edit")],
        Annotated[WriteToolResultEvent, Tag("write")],
        Annotated[GrepToolResultEvent, Tag("grep")],
        Annotated[FindToolResultEvent, Tag("find")],
        Annotated[LsToolResultEvent, Tag("ls")],
        Annotated[CustomToolResultEvent, Tag("custom")],
    ],
    Discriminator(_tool_event_discriminator),
]


class ToolResultEventResult(_PiModel):
    content: list[Annotated[Union[TextContent, ImageContent], Field(discriminator="type")]] | None = None
    details: Any = None
    is_error: bool | None = Field(default=None, alias="isError")


# --------------------------------------------------------------------------- #
# Adapters                                                                     #
# --------------------------------------------------------------------------- #

ToolCallEventAdapter: TypeAdapter[ToolCallEvent] = TypeAdapter(ToolCallEventItem)
ToolResultEventAdapter: TypeAdapter[ToolResultEvent] = TypeAdapter(ToolResultEventItem)
