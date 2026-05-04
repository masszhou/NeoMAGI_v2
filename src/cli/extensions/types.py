"""Extension API protocol types — public surface aggregator.

Mirrors `packages/coding-agent/src/core/extensions/types.ts` (pi-mono `97a38bf6`).

Two flavors of types live in this package:

1. **Wire payloads** (event payloads, ProviderConfig, registered command /
   shortcut / flag metadata, ToolDefinition) — pydantic v2 models with
   Pi-compatible aliases.
2. **Runtime surfaces** (`ExtensionAPI`, `ExtensionContext`,
   `ExtensionUIContext`, `ExtensionCommandContext`, `EventBus`) —
   `typing.Protocol` classes describing the methods that M3 will implement.
   They are not validated at the wire level; tests mock them.

This module:

- declares the non-event wire types (`KeyId` / `RegisteredCommand` /
  `RegisteredShortcut` / `RegisteredFlag` / `ToolDefinition` / OAuth +
  ProviderConfig family);
- re-exports the event payload models (from :mod:`event_types` and
  :mod:`tool_events`) and Protocol surfaces (from :mod:`protocols`);
- builds the top-level :data:`ExtensionEvent` discriminated union and its
  :class:`TypeAdapter`.

Existing imports of the form ``from cli.extensions.types import X`` remain
valid; ``X`` is re-exported via ``__all__`` at the bottom.

W2 only declares the protocol; runtime implementation arrives in M3.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
)

# Re-export from sub-modules — keeps `cli.extensions.types` a single import target.
from ai_provider.types import ToolCall  # noqa: F401  (re-exported for convenience)

from .event_types import (
    AfterProviderResponseEvent,
    AgentEndEvent,
    AgentStartEvent,
    BashResult,
    BeforeAgentStartEvent,
    BeforeAgentStartEventResult,
    BeforeProviderRequestEvent,
    ContextEvent,
    ContextEventResult,
    InputEvent,
    InputEventResult,
    InputEventResultAdapter,
    InputEventResultItem,
    InputSource,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ModelSelectEvent,
    ModelSelectSource,
    ResourcesDiscoverEvent,
    ResourcesDiscoverResult,
    SessionBeforeCompactEvent,
    SessionBeforeCompactResult,
    SessionBeforeForkEvent,
    SessionBeforeForkResult,
    SessionBeforeSwitchEvent,
    SessionBeforeSwitchResult,
    SessionBeforeTreeEvent,
    SessionBeforeTreeResult,
    SessionCompactEvent,
    SessionShutdownEvent,
    SessionStartEvent,
    SessionTreeEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
    UserBashEvent,
    UserBashEventResult,
)
from .protocols import (
    CancellableResult,
    EventBus,
    ExtensionAPI,
    ExtensionCommandContext,
    ExtensionContext,
    ExtensionUIContext,
    MessageRenderer,
)
from .tool_events import (
    BashToolCallEvent,
    BashToolResultEvent,
    CustomToolCallEvent,
    CustomToolResultEvent,
    EditToolCallEvent,
    EditToolResultEvent,
    FindToolCallEvent,
    FindToolResultEvent,
    GrepToolCallEvent,
    GrepToolResultEvent,
    LsToolCallEvent,
    LsToolResultEvent,
    ReadToolCallEvent,
    ReadToolResultEvent,
    ToolCallEvent,
    ToolCallEventAdapter,
    ToolCallEventItem,
    ToolCallEventResult,
    ToolResultEvent,
    ToolResultEventAdapter,
    ToolResultEventItem,
    ToolResultEventResult,
    WriteToolCallEvent,
    WriteToolResultEvent,
)


class _PiModel(BaseModel):
    """Base for Pi-compatible wire types: alias-driven I/O, transparent extras."""

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


# --------------------------------------------------------------------------- #
# ToolDefinition                                                               #
# --------------------------------------------------------------------------- #


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
    context_window: int = Field(alias="contextWindow", gt=0)
    max_tokens: int = Field(alias="maxTokens", gt=0)
    headers: dict[str, str] | None = None


class ProviderOAuthConfig(_PiModel):
    name: str
    # `login`, `refreshToken`, `getApiKey`, `modifyModels` are runtime callables
    # — declared on `ExtensionAPI`, not as wire fields.


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
# Top-level ExtensionEvent union                                               #
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


# --------------------------------------------------------------------------- #
# Adapters                                                                     #
# --------------------------------------------------------------------------- #

ExtensionEventAdapter: TypeAdapter[ExtensionEvent] = TypeAdapter(ExtensionEventItem)
ProviderConfigAdapter = TypeAdapter(ProviderConfig)
ToolDefinitionAdapter = TypeAdapter(ToolDefinition)


# --------------------------------------------------------------------------- #
# Public surface — re-exports keep `from cli.extensions.types import X` valid #
# --------------------------------------------------------------------------- #


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
