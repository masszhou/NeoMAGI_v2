"""Extension API protocol types (pydantic v2 + typing.Protocol).

Mirrors `packages/coding-agent/src/core/extensions/types.ts` (pi-mono `97a38bf6`).

Two flavors of types live here:

1. **Wire payloads** (event payloads, ProviderConfig, registered command/shortcut/flag
   metadata) — pydantic v2 models with Pi-compatible aliases.
2. **Runtime surfaces** (`ExtensionAPI`, `ExtensionContext`, `ExtensionUIContext`,
   `EventBus`) — `typing.Protocol` classes describing the methods that M3 will
   implement. They are not validated at the wire level; tests mock them.

W2 only declares the protocol; runtime implementation arrives in M3.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Literal, Protocol, Union, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ai_provider.types import (
    AssistantMessage,
    ImageContent,
    Model,
    TextContent,
    ThinkingLevel,
    ToolCall,
)


class _PiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


# --------------------------------------------------------------------------- #
# Registered metadata (commands / shortcuts / flags)                           #
# --------------------------------------------------------------------------- #


KeyId = str  # Pi keybinding identifier; opaque string, see packages/coding-agent/src/core/keybindings.ts


class RegisteredCommand(_PiModel):
    name: str
    description: str | None = None
    argument_hint: str | None = Field(default=None, alias="argumentHint")


class RegisteredShortcut(_PiModel):
    key_id: KeyId = Field(alias="keyId")
    description: str | None = None


class RegisteredFlag(_PiModel):
    name: str
    description: str | None = None
    type: Literal["boolean", "string"]
    default: bool | str | None = None


class ToolDefinition(_PiModel):
    """Wire-shape of a tool definition; runtime adds `prepareArguments`,
    `execute`, and renderers as Python callables (not modelled here).

    Note: ``prepareArguments`` runs **before** schema validation. Its return
    value must still satisfy ``parameters``; the hook exists to repair
    non-canonical argument shapes from the LLM, not to bypass validation.
    """

    name: str
    label: str
    description: str
    prompt_snippet: str | None = Field(default=None, alias="promptSnippet")
    prompt_guidelines: list[str] | None = Field(default=None, alias="promptGuidelines")
    parameters: dict[str, Any]
    render_shell: Literal["default", "self"] | None = Field(default=None, alias="renderShell")
    execution_mode: Literal["parallel", "sequential"] | None = Field(
        default=None, alias="executionMode"
    )


# --------------------------------------------------------------------------- #
# ProviderConfig (`registerProvider` input)                                    #
# --------------------------------------------------------------------------- #


class OAuthCredentials(_PiModel):
    access: str | None = None
    refresh: str | None = None


class ProviderModelConfig(_PiModel):
    id: str
    name: str
    api: str | None = None
    reasoning: bool
    input: list[Literal["text", "image"]]
    cost: dict[str, float]
    context_window: int = Field(alias="contextWindow")
    max_tokens: int = Field(alias="maxTokens")
    headers: dict[str, str] | None = None


class ProviderOAuthConfig(_PiModel):
    name: str
    # `login`, `refreshToken`, `getApiKey`, `modifyModels` are runtime callables
    # — declared in the Protocol section below, not as wire fields.


class ProviderConfig(_PiModel):
    """Wire shape for ``ExtensionAPI.register_provider`` input.

    Field semantics (`extensions/types.ts:1265–1297`):
    - ``models`` provided → replace all existing models for this provider.
    - only ``base_url`` provided → override URL for existing models.
    - ``oauth`` provided → register OAuth provider for /login support.
    - ``stream_simple`` provided → register custom API stream handler.
    """

    base_url: str | None = Field(default=None, alias="baseUrl")
    api_key: str | None = Field(default=None, alias="apiKey")
    api: str | None = None
    headers: dict[str, str] | None = None
    auth_header: bool | None = Field(default=None, alias="authHeader")
    stream_simple: Any = Field(default=None, alias="streamSimple")
    """Runtime callable; not validated by pydantic. Kept on the wire model so
    fixtures can carry an opaque marker without round-trip loss."""
    models: list[ProviderModelConfig] | None = None
    oauth: ProviderOAuthConfig | None = None


# --------------------------------------------------------------------------- #
# Extension event payloads (wire shape)                                        #
# --------------------------------------------------------------------------- #


class ResourcesDiscoverEvent(_PiModel):
    type: Literal["resources_discover"] = "resources_discover"
    cwd: str
    reason: Literal["startup", "reload"]


class ResourcesDiscoverResult(_PiModel):
    skill_paths: list[str] | None = Field(default=None, alias="skillPaths")
    prompt_paths: list[str] | None = Field(default=None, alias="promptPaths")
    theme_paths: list[str] | None = Field(default=None, alias="themePaths")


class SessionStartEvent(_PiModel):
    type: Literal["session_start"] = "session_start"
    reason: Literal["startup", "reload", "new", "resume", "fork"]
    previous_session_file: str | None = Field(default=None, alias="previousSessionFile")


class SessionBeforeSwitchEvent(_PiModel):
    type: Literal["session_before_switch"] = "session_before_switch"
    reason: Literal["new", "resume"]
    target_session_file: str | None = Field(default=None, alias="targetSessionFile")


class SessionBeforeSwitchResult(_PiModel):
    cancel: bool | None = None


class SessionBeforeForkEvent(_PiModel):
    type: Literal["session_before_fork"] = "session_before_fork"
    entry_id: str = Field(alias="entryId")
    position: Literal["before", "at"]


class SessionBeforeForkResult(_PiModel):
    cancel: bool | None = None
    skip_conversation_restore: bool | None = Field(default=None, alias="skipConversationRestore")


class SessionBeforeCompactEvent(_PiModel):
    type: Literal["session_before_compact"] = "session_before_compact"
    preparation: Any = None
    """``CompactionPreparation`` shape (`packages/coding-agent/src/core/compaction/compaction.ts:596`).
    Kept as ``Any`` to avoid a cycle with ``cli.core.session_types``; runtime
    code should validate via ``cli.core.session_types`` adapters when needed."""
    branch_entries: list[Any] = Field(default_factory=list, alias="branchEntries")
    """``SessionEntry[]`` of the branch being compacted; opaque here for the
    same cycle reason as ``preparation``."""
    custom_instructions: str | None = Field(default=None, alias="customInstructions")
    signal: Any = None
    """Runtime ``AbortSignal``-equivalent; not validated by pydantic, retained
    on the wire so handlers can see the field exists."""


class SessionBeforeCompactResult(_PiModel):
    cancel: bool | None = None
    compaction: Any = None
    """``CompactionResult``-shaped override; full type lives in `cli.core.session_types`
    and isn't modelled here to avoid an import cycle."""


class SessionCompactEvent(_PiModel):
    type: Literal["session_compact"] = "session_compact"
    compaction_entry: Any = Field(alias="compactionEntry")
    """``CompactionEntry`` from `cli.core.session_types`; opaque here to avoid a
    package cycle. Runtime should validate via ``SessionEntryAdapter``."""
    from_extension: bool = Field(alias="fromExtension")


class SessionShutdownEvent(_PiModel):
    type: Literal["session_shutdown"] = "session_shutdown"
    reason: Literal["quit", "reload", "new", "resume", "fork"]
    target_session_file: str | None = Field(default=None, alias="targetSessionFile")


class SessionBeforeTreeEvent(_PiModel):
    type: Literal["session_before_tree"] = "session_before_tree"
    preparation: Any = None
    """``TreePreparation`` (`extensions/types.ts:524–537`): ``targetId`` /
    ``oldLeafId`` / ``commonAncestorId`` / ``entriesToSummarize`` /
    ``userWantsSummary`` / ``customInstructions`` / ``replaceInstructions`` /
    ``label``. Modelled as ``Any`` because ``entriesToSummarize`` references
    `cli.core.SessionEntry` and would create an import cycle."""
    signal: Any = None
    """Runtime ``AbortSignal``-equivalent; opaque on the wire."""


class SessionBeforeTreeResult(_PiModel):
    cancel: bool | None = None
    summary: dict[str, Any] | None = None
    custom_instructions: str | None = Field(default=None, alias="customInstructions")
    replace_instructions: bool | None = Field(default=None, alias="replaceInstructions")
    label: str | None = None


class SessionTreeEvent(_PiModel):
    type: Literal["session_tree"] = "session_tree"
    new_leaf_id: str | None = Field(alias="newLeafId")
    old_leaf_id: str | None = Field(alias="oldLeafId")
    summary_entry: Any = Field(default=None, alias="summaryEntry")
    """Optional ``BranchSummaryEntry`` written when navigation triggered a
    summary; opaque to avoid `cli.core` import cycle."""
    from_extension: bool | None = Field(default=None, alias="fromExtension")
    """``True`` iff a ``session_before_tree`` handler provided the summary."""


class ContextEvent(_PiModel):
    type: Literal["context"] = "context"
    messages: list[Any]  # AgentMessage union — kept loose to avoid cycle


class ContextEventResult(_PiModel):
    messages: list[Any] | None = None


class BeforeProviderRequestEvent(_PiModel):
    type: Literal["before_provider_request"] = "before_provider_request"
    payload: Any


class AfterProviderResponseEvent(_PiModel):
    type: Literal["after_provider_response"] = "after_provider_response"
    status: int
    headers: dict[str, str]


class BeforeAgentStartEvent(_PiModel):
    type: Literal["before_agent_start"] = "before_agent_start"
    prompt: str
    images: list[ImageContent] | None = None
    system_prompt: str = Field(alias="systemPrompt")
    system_prompt_options: dict[str, Any] = Field(
        default_factory=dict, alias="systemPromptOptions"
    )
    """``BuildSystemPromptOptions`` snapshot (`packages/coding-agent/src/core/system-prompt.ts`).
    Lets extensions inspect what Pi loaded without re-discovering resources.
    Modelled as ``dict`` because the structured options live in `cli.core` and
    are not stable enough at M0 to lock down."""


class BeforeAgentStartEventResult(_PiModel):
    """Multiple handlers' ``message`` values are appended into the pending list;
    ``systemPrompt`` is **chained in extension load order**, so each later
    handler receives the previous handler's replacement string."""

    message: dict[str, Any] | None = None
    system_prompt: str | None = Field(default=None, alias="systemPrompt")


ModelSelectSource = Literal["set", "cycle", "restore"]


class ModelSelectEvent(_PiModel):
    type: Literal["model_select"] = "model_select"
    model: Model
    previous_model: Model | None = Field(default=None, alias="previousModel")
    source: ModelSelectSource


class UserBashEvent(_PiModel):
    type: Literal["user_bash"] = "user_bash"
    command: str
    exclude_from_context: bool = Field(alias="excludeFromContext")
    """``True`` when the user used the ``!!`` prefix (output excluded from LLM context)."""
    cwd: str


class BashResult(_PiModel):
    """Mirror of `packages/coding-agent/src/core/bash-executor.ts:29–40`.

    Returned by ``bash-executor`` and the bash tool; surfaced to extensions via
    ``UserBashEventResult.result`` when a handler fully replaces execution.
    """

    output: str
    exit_code: int | None = Field(default=None, alias="exitCode")
    """``None`` (Pi: ``undefined``) when the process was killed / cancelled.
    The whole field may be absent on the wire — Pi typed it as
    ``number | undefined`` and emits no key for cancelled runs, so the model
    must default to ``None`` rather than require the alias."""
    cancelled: bool
    truncated: bool
    full_output_path: str | None = Field(default=None, alias="fullOutputPath")


class UserBashEventResult(_PiModel):
    """Result from ``user_bash`` handler (`extensions/types.ts:947–952`).

    Two-mode contract:

    - ``operations``: install custom ``BashOperations`` for this execution
      (e.g. SSH / container / wrapped shell). Pi keeps it as a runtime callable
      bundle (`packages/coding-agent/src/core/tools/bash.ts:49`); modelled here
      as ``Any`` because there is no JSON-serialisable form.
    - ``result``: full replacement — extension already executed the command,
      use this `BashResult` directly without invoking ``operations``.

    The previous M0 shape ``{cancel, output}`` was wrong; runners that consume
    that shape would silently lose Pi's two-mode semantics.
    """

    operations: Any = None
    result: BashResult | None = None


InputSource = Literal["interactive", "rpc", "extension"]


class InputEvent(_PiModel):
    type: Literal["input"] = "input"
    text: str
    images: list[ImageContent] | None = None
    source: InputSource


class _InputResultContinue(_PiModel):
    action: Literal["continue"] = "continue"


class _InputResultTransform(_PiModel):
    action: Literal["transform"] = "transform"
    text: str
    images: list[ImageContent] | None = None


class _InputResultHandled(_PiModel):
    action: Literal["handled"] = "handled"


InputEventResult = Union[_InputResultContinue, _InputResultTransform, _InputResultHandled]
"""Discriminated by ``action`` (Pi `extensions/types.ts:719`). Three variants:
``continue`` (no-op), ``transform`` (replace text/images), ``handled`` (skip
agent processing). Use ``InputEventResultAdapter`` for runtime validation."""
InputEventResultItem = Annotated[InputEventResult, Field(discriminator="action")]


# --------------------------------------------------------------------------- #
# Agent / turn / message / tool_execution lifecycle events                     #
#                                                                              #
# These are the **extension-side** payloads (`extensions/types.ts:601–670`).   #
# They mirror the core `agent_core.types.AgentEvent` variants but add fields   #
# Pi exposes only at the extension layer (e.g. `TurnStartEvent.turnIndex`).    #
# `message` payloads carry the wider 7-role coding union; modelled as ``Any``  #
# to avoid an import cycle with `cli.core.session_types`. Runtime code should  #
# validate `message` via `cli.core.session_types.CodingAgentMessageAdapter`.   #
# --------------------------------------------------------------------------- #


class AgentStartEvent(_PiModel):
    type: Literal["agent_start"] = "agent_start"


class AgentEndEvent(_PiModel):
    type: Literal["agent_end"] = "agent_end"
    messages: list[Any]
    """``list[CodingAgentMessage]`` — opaque here; see module docstring above."""


class TurnStartEvent(_PiModel):
    type: Literal["turn_start"] = "turn_start"
    turn_index: int = Field(alias="turnIndex")
    timestamp: int


class TurnEndEvent(_PiModel):
    type: Literal["turn_end"] = "turn_end"
    turn_index: int = Field(alias="turnIndex")
    message: Any
    """``CodingAgentMessage`` — opaque here; see module docstring above."""
    tool_results: list[Any] = Field(alias="toolResults")
    """``list[ToolResultMessage]`` — kept as ``Any`` for symmetry with ``message``."""


class MessageStartEvent(_PiModel):
    type: Literal["message_start"] = "message_start"
    message: Any


class MessageUpdateEvent(_PiModel):
    type: Literal["message_update"] = "message_update"
    message: Any
    assistant_message_event: Any = Field(alias="assistantMessageEvent")
    """``AssistantMessageEvent`` — runtime validation via
    ``ai_provider.types.AssistantMessageEventAdapter``."""


class MessageEndEvent(_PiModel):
    type: Literal["message_end"] = "message_end"
    message: Any


class ToolExecutionStartEvent(_PiModel):
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    args: Any


class ToolExecutionUpdateEvent(_PiModel):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    args: Any
    partial_result: Any = Field(alias="partialResult")


class ToolExecutionEndEvent(_PiModel):
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    result: Any
    is_error: bool = Field(alias="isError")


# --------------------------------------------------------------------------- #
# Tool events (`extensions/types.ts:730–836`) discriminated by ``toolName``    #
# --------------------------------------------------------------------------- #


# Pi's `ToolCallEvent` / `ToolResultEvent` are discriminated unions over
# ``toolName``. Pi's CustomToolCallEvent declares ``toolName: string`` (any
# string), which overlaps with the literal builtin names — pydantic's normal
# field-discriminator can't handle that overlap. We use a callable
# `Discriminator` that maps unknown ``toolName`` values to ``"custom"``.
#
# Input / details types are kept loose at M0 (`dict[str, Any]` / `Any`) — the
# concrete `BashToolInput`, `ReadToolDetails`, etc. land in M3 when the actual
# tools are implemented. The discriminator structure is the long-term contract;
# the inner field types can be tightened later without breaking the union.

from pydantic import Discriminator, Tag  # noqa: E402

_BUILTIN_TOOL_NAMES = frozenset({"bash", "read", "edit", "write", "grep", "find", "ls"})


def _tool_event_discriminator(value: Any) -> str:
    """Return the variant tag for a tool_call / tool_result event.

    Builtin ``toolName`` (`bash` / `read` / `edit` / `write` / `grep` / `find`
    / `ls`) maps to itself; everything else maps to ``custom`` so the
    `CustomToolCallEvent` variant catches arbitrary extension tool names.
    """

    if isinstance(value, dict):
        name = value.get("toolName") or value.get("tool_name")
    else:
        name = getattr(value, "tool_name", None)
    if isinstance(name, str) and name in _BUILTIN_TOOL_NAMES:
        return name
    return "custom"


# ----- tool_call variants --------------------------------------------------- #


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


# ----- tool_result variants ------------------------------------------------- #


class _ToolResultEventBase(_PiModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = Field(alias="toolCallId")
    input: dict[str, Any]
    """Pi `extensions/types.ts:790` — the (possibly mutated) input that the tool
    received. Required field; previously missing in M0."""
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
# Top-level event union (covers every pi-mono `on(...)` overload)              #
#                                                                              #
# Total surface: 27 unique ``type`` tags. The two tool branches collapse 8     #
# variants each under the inner ``toolName`` discriminator, so the top-level   #
# discriminator is on ``type`` only — implemented via a callable               #
# ``Discriminator`` because pydantic's field-discriminator can't share the     #
# same ``type`` across multiple variants.                                      #
# --------------------------------------------------------------------------- #


ExtensionEvent = Union[
    ResourcesDiscoverEvent,
    SessionStartEvent,
    SessionBeforeSwitchEvent,
    SessionBeforeForkEvent,
    SessionBeforeCompactEvent,
    SessionCompactEvent,
    SessionShutdownEvent,
    SessionBeforeTreeEvent,
    SessionTreeEvent,
    ContextEvent,
    BeforeProviderRequestEvent,
    AfterProviderResponseEvent,
    BeforeAgentStartEvent,
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
    ModelSelectEvent,
    UserBashEvent,
    InputEvent,
    ToolCallEvent,
    ToolResultEvent,
]


def _extension_event_discriminator(value: Any) -> str:
    """Top-level discriminator for ExtensionEvent.

    Each variant's ``type`` field is the tag, except the ``tool_call`` /
    ``tool_result`` branches which are themselves toolName-discriminated unions.
    """

    if isinstance(value, dict):
        return value.get("type") or ""
    return getattr(value, "type", "") or ""


ExtensionEventItem = Annotated[
    Union[
        Annotated[ResourcesDiscoverEvent, Tag("resources_discover")],
        Annotated[SessionStartEvent, Tag("session_start")],
        Annotated[SessionBeforeSwitchEvent, Tag("session_before_switch")],
        Annotated[SessionBeforeForkEvent, Tag("session_before_fork")],
        Annotated[SessionBeforeCompactEvent, Tag("session_before_compact")],
        Annotated[SessionCompactEvent, Tag("session_compact")],
        Annotated[SessionShutdownEvent, Tag("session_shutdown")],
        Annotated[SessionBeforeTreeEvent, Tag("session_before_tree")],
        Annotated[SessionTreeEvent, Tag("session_tree")],
        Annotated[ContextEvent, Tag("context")],
        Annotated[BeforeProviderRequestEvent, Tag("before_provider_request")],
        Annotated[AfterProviderResponseEvent, Tag("after_provider_response")],
        Annotated[BeforeAgentStartEvent, Tag("before_agent_start")],
        Annotated[AgentStartEvent, Tag("agent_start")],
        Annotated[AgentEndEvent, Tag("agent_end")],
        Annotated[TurnStartEvent, Tag("turn_start")],
        Annotated[TurnEndEvent, Tag("turn_end")],
        Annotated[MessageStartEvent, Tag("message_start")],
        Annotated[MessageUpdateEvent, Tag("message_update")],
        Annotated[MessageEndEvent, Tag("message_end")],
        Annotated[ToolExecutionStartEvent, Tag("tool_execution_start")],
        Annotated[ToolExecutionUpdateEvent, Tag("tool_execution_update")],
        Annotated[ToolExecutionEndEvent, Tag("tool_execution_end")],
        Annotated[ModelSelectEvent, Tag("model_select")],
        Annotated[UserBashEvent, Tag("user_bash")],
        Annotated[InputEvent, Tag("input")],
        Annotated[ToolCallEventItem, Tag("tool_call")],
        Annotated[ToolResultEventItem, Tag("tool_result")],
    ],
    Discriminator(_extension_event_discriminator),
]


ExtensionEventAdapter: TypeAdapter[ExtensionEvent] = TypeAdapter(ExtensionEventItem)
InputEventResultAdapter: TypeAdapter[InputEventResult] = TypeAdapter(InputEventResultItem)
ToolCallEventAdapter: TypeAdapter[ToolCallEvent] = TypeAdapter(ToolCallEventItem)
ToolResultEventAdapter: TypeAdapter[ToolResultEvent] = TypeAdapter(ToolResultEventItem)
ProviderConfigAdapter = TypeAdapter(ProviderConfig)
ToolDefinitionAdapter = TypeAdapter(ToolDefinition)


# --------------------------------------------------------------------------- #
# Runtime protocols (typing.Protocol)                                          #
# --------------------------------------------------------------------------- #


@runtime_checkable
class EventBus(Protocol):
    """Shared event bus for extension-to-extension communication.

    Mirrors `packages/coding-agent/src/core/event-bus.ts:1–33`. ``on`` MUST
    return an unsubscribe callback; do not invent ``subscribe`` / ``publish``
    aliases.
    """

    def emit(self, channel: str, data: object) -> None: ...
    def on(
        self, channel: str, handler: Callable[[object], None]
    ) -> Callable[[], None]: ...


@runtime_checkable
class ExtensionUIContext(Protocol):
    """22 UI primitives mirrored from `extensions/types.ts:88–268`.

    Async slots (Pi: ``Promise<X>``): ``select`` / ``confirm`` / ``input`` /
    ``custom`` / ``editor`` — all dialog-style methods that wait for user
    response. Implementations MUST use ``async def`` per the project async
    convention (`pi_behavior_matrix.md` § D).
    """

    async def select(
        self, title: str, options: list[Any], opts: dict[str, Any] | None = None
    ) -> str | None: ...
    async def confirm(
        self, title: str, message: str, opts: dict[str, Any] | None = None
    ) -> bool: ...
    async def input(
        self, title: str, placeholder: str | None = None, opts: dict[str, Any] | None = None
    ) -> str | None: ...
    def notify(self, message: str, type: Literal["info", "warning", "error"] | None = None) -> None: ...
    def on_terminal_input(self, handler: Callable[[str], dict[str, Any] | None]) -> Callable[[], None]: ...
    def set_status(self, key: str, text: str | None = None) -> None: ...
    def set_working_message(self, message: str | None = None) -> None: ...
    def set_working_indicator(self, options: dict[str, Any] | None = None) -> None: ...
    def set_hidden_thinking_label(self, label: str | None = None) -> None: ...
    def set_widget(self, key: str, content: Any | None = None, options: dict[str, Any] | None = None) -> None: ...
    def set_footer(self, factory: Callable[[], Any] | None = None) -> None: ...
    def set_header(self, factory: Callable[[], Any] | None = None) -> None: ...
    def set_title(self, title: str) -> None: ...
    async def custom(
        self, factory: Callable[..., Any], options: dict[str, Any] | None = None
    ) -> Any: ...
    """Pi: ``custom<T>(factory, options?): Promise<T>``. The factory itself may
    be sync or async (Pi `extensions/types.ts:69`); the dialog wrapper awaits
    its return value before resolving."""
    def paste_to_editor(self, text: str) -> None: ...
    def set_editor_text(self, text: str) -> None: ...
    def get_editor_text(self) -> str: ...
    async def editor(self, title: str, prefill: str | None = None) -> str | None: ...
    def set_editor_component(self, factory: Callable[[], Any] | None = None) -> None: ...
    @property
    def theme(self) -> Any: ...
    def get_all_themes(self) -> list[Any]: ...
    def get_theme(self, name: str) -> Any: ...
    def set_theme(self, theme: Any) -> dict[str, Any]: ...
    """Pi: ``setTheme(theme): { success: boolean; error?: string }`` (sync, but
    returns a status object — not ``void``). Caller inspects ``success`` and,
    on failure, ``error``."""
    def get_tools_expanded(self) -> bool: ...
    def set_tools_expanded(self, expanded: bool) -> None: ...


class CancellableResult(_PiModel):
    """Common result shape for command-context session-mutation methods.

    Pi `extensions/types.ts:325–348` — `newSession`, `fork`, `navigateTree`,
    `switchSession` all return ``Promise<{cancelled: boolean}>`` so handlers
    can detect that a `session_before_*` extension cancelled the operation.
    """

    cancelled: bool


@runtime_checkable
class ExtensionContext(Protocol):
    """Base context handed to event handlers (`extensions/types.ts:286–321`)."""

    ui: ExtensionUIContext
    has_ui: bool
    cwd: str
    session_manager: Any
    """Read-only ``SessionManager`` view (`packages/coding-agent/src/core/session-manager.ts`).
    Modelled as ``Any`` because the concrete type lives in `cli.core` and is
    not available to extension code at the wire level."""
    model_registry: Any
    """``ModelRegistry`` for API key resolution + model lookup
    (`packages/coding-agent/src/core/model-registry.ts`). Same opacity rationale
    as ``session_manager``."""
    model: Model | None
    signal: Any | None  # AbortSignal-equivalent

    def is_idle(self) -> bool: ...
    def abort(self) -> None: ...
    def has_pending_messages(self) -> bool: ...
    def shutdown(self) -> None: ...
    def get_context_usage(self) -> Any | None: ...
    def compact(self, options: Any | None = None) -> None: ...
    def get_system_prompt(self) -> str: ...


@runtime_checkable
class ExtensionCommandContext(ExtensionContext, Protocol):
    """Command-only extras (`extensions/types.ts:321–351`).

    **Async convention** (project-wide rule, also documented in
    `pi_behavior_matrix.md` § D): every Pi method typed as ``Promise<X>`` is
    declared here as ``async def -> X``. Implementations MUST use ``async def``;
    Python's structural typing does not let a sync ``def -> X`` satisfy an
    ``async def -> X`` Protocol slot — they return different runtime values
    (``X`` vs ``Coroutine[Any, Any, X]``).

    Sync vs async per Pi:

    - sync (Pi: ``X``): inherited ExtensionContext methods + `register_*` etc.
    - async (Pi: ``Promise<X>``): ``wait_for_idle``, ``new_session``, ``fork``,
      ``navigate_tree``, ``switch_session``, ``reload``.
    """

    async def wait_for_idle(self) -> None: ...
    async def new_session(
        self, options: dict[str, Any] | None = None
    ) -> CancellableResult: ...
    """``options``: ``{parent_session?: str, setup?: Callable[[SessionManager], Awaitable[None]]}``."""

    async def fork(
        self, entry_id: str, options: dict[str, Any] | None = None
    ) -> CancellableResult: ...
    """``options``: ``{position?: "before" | "at"}``. ``position`` defaults to ``"at"``."""

    async def navigate_tree(
        self, target_id: str, options: dict[str, Any] | None = None
    ) -> CancellableResult: ...
    """``options``: ``{summarize?: bool, custom_instructions?: str,
    replace_instructions?: bool, label?: str}``."""

    async def switch_session(self, session_path: str) -> CancellableResult: ...
    async def reload(self) -> None: ...


# Forward-decl for tool definition / message renderer (runtime-typed).
MessageRenderer = Callable[[Any, ExtensionContext], Any]


@runtime_checkable
class ExtensionAPI(Protocol):
    """Full mirror of `extensions/types.ts:1040–1259`. Every method here has a
    counterpart row in `pi_behavior_matrix.md` § D. Python uses snake_case;
    semantics are 1:1 with Pi.
    """

    # ------------------------------------------------------------------- on()
    def on(self, event: str, handler: Callable[..., Any]) -> None: ...

    # --------------------------------------------------- Tool / Command / Flag
    def register_tool(self, tool: ToolDefinition) -> None: ...
    """Pi mirror: ``registerTool(tool: ToolDefinition)`` — the extension-facing
    metadata bundle (`prompt_snippet` / `prompt_guidelines` / `render_shell` /
    `prepare_arguments` / `execute` / renderers). The runtime wraps it into an
    `AgentTool` after policy/audit binding; that wrapping is not the extension
    contract surface."""
    def register_command(self, name: str, options: dict[str, Any]) -> None: ...
    def register_shortcut(self, key_id: KeyId, options: dict[str, Any]) -> None: ...
    def register_flag(self, name: str, options: dict[str, Any]) -> None: ...
    def get_flag(self, name: str) -> bool | str | None: ...

    # ----------------------------------------------------- Message renderer
    def register_message_renderer(self, custom_type: str, renderer: MessageRenderer) -> None: ...

    # ----------------------------------------------------------- Actions
    def send_message(self, message: dict[str, Any], options: dict[str, Any] | None = None) -> None: ...
    def send_user_message(
        self,
        content: str | list[Union[TextContent, ImageContent]],
        options: dict[str, Any] | None = None,
    ) -> None: ...
    def append_entry(self, custom_type: str, data: Any | None = None) -> None: ...

    # --------------------------------------------------- Session metadata
    def set_session_name(self, name: str) -> None: ...
    def get_session_name(self) -> str | None: ...
    def set_label(self, entry_id: str, label: str | None = None) -> None: ...
    async def exec(
        self, command: str, args: list[str], options: dict[str, Any] | None = None
    ) -> Any: ...
    """Pi: ``Promise<ExecResult>``. Returns an opaque ``ExecResult`` shape from
    `packages/coding-agent/src/core/exec.ts`; left as ``Any`` until M3 lands
    the typed result model."""
    def get_active_tools(self) -> list[str]: ...
    def get_all_tools(self) -> list[Any]: ...
    def set_active_tools(self, tool_names: list[str]) -> None: ...
    def get_commands(self) -> list[Any]: ...

    # --------------------------------------------- Model / thinking level
    async def set_model(self, model: Model) -> bool: ...
    """Pi: ``Promise<boolean>``; returns ``False`` when no API key is available."""
    def get_thinking_level(self) -> ThinkingLevel: ...
    def set_thinking_level(self, level: ThinkingLevel) -> None: ...

    # --------------------------------------------------- Provider registry
    def register_provider(self, name: str, config: ProviderConfig) -> None: ...
    def unregister_provider(self, name: str) -> None: ...

    # ----------------------------------------------------- Shared event bus
    @property
    def events(self) -> EventBus: ...


# Re-export so `from cli.extensions.types import ToolCall, ToolDefinitionAdapter` works
# without importing ai_provider directly.
__all__ = [
    "AfterProviderResponseEvent",
    "AgentEndEvent",
    "AgentStartEvent",
    "BashResult",
    "BashToolCallEvent",
    "BashToolResultEvent",
    "BeforeAgentStartEvent",
    "BeforeAgentStartEventResult",
    "BeforeProviderRequestEvent",
    "CancellableResult",
    "ContextEvent",
    "ContextEventResult",
    "CustomToolCallEvent",
    "CustomToolResultEvent",
    "EditToolCallEvent",
    "EditToolResultEvent",
    "EventBus",
    "ExtensionAPI",
    "ExtensionCommandContext",
    "ExtensionContext",
    "ExtensionEvent",
    "ExtensionEventAdapter",
    "ExtensionEventItem",
    "ExtensionUIContext",
    "FindToolCallEvent",
    "FindToolResultEvent",
    "GrepToolCallEvent",
    "GrepToolResultEvent",
    "InputEvent",
    "InputEventResult",
    "InputEventResultAdapter",
    "InputEventResultItem",
    "InputSource",
    "KeyId",
    "LsToolCallEvent",
    "LsToolResultEvent",
    "MessageEndEvent",
    "MessageRenderer",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "ModelSelectEvent",
    "ModelSelectSource",
    "OAuthCredentials",
    "ProviderConfig",
    "ProviderConfigAdapter",
    "ProviderModelConfig",
    "ProviderOAuthConfig",
    "ReadToolCallEvent",
    "ReadToolResultEvent",
    "RegisteredCommand",
    "RegisteredFlag",
    "RegisteredShortcut",
    "ResourcesDiscoverEvent",
    "ResourcesDiscoverResult",
    "SessionBeforeCompactEvent",
    "SessionBeforeCompactResult",
    "SessionBeforeForkEvent",
    "SessionBeforeForkResult",
    "SessionBeforeSwitchEvent",
    "SessionBeforeSwitchResult",
    "SessionBeforeTreeEvent",
    "SessionBeforeTreeResult",
    "SessionCompactEvent",
    "SessionShutdownEvent",
    "SessionStartEvent",
    "SessionTreeEvent",
    "ToolCall",
    "ToolCallEvent",
    "ToolCallEventAdapter",
    "ToolCallEventItem",
    "ToolCallEventResult",
    "ToolDefinition",
    "ToolDefinitionAdapter",
    "ToolExecutionEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "ToolResultEvent",
    "ToolResultEventAdapter",
    "ToolResultEventItem",
    "ToolResultEventResult",
    "TurnEndEvent",
    "TurnStartEvent",
    "UserBashEvent",
    "UserBashEventResult",
    "WriteToolCallEvent",
    "WriteToolResultEvent",
]
