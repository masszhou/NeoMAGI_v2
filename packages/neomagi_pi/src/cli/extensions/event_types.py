"""Pi-compatible wire payloads for extension events (non-tool variants).

Mirrors the non-tool event payloads from
`packages/coding-agent/src/core/extensions/types.ts:459–730`
(pi-mono `97a38bf6`). The 16 tool-event variants live in `tool_events.py`.

Why split out from `types.py`:
- Each event payload is a small pydantic v2 model with Pi-compatible aliases.
- Centralising them here keeps the surface scannable and lets `types.py`
  focus on aggregating the union + non-event wire types.
- Result shapes (`*Result`) live alongside their triggering events so the
  Pi `on(event, handler) -> Result` contract is local.

`message` payloads carry the wider 7-role coding union; modelled as ``Any`` to
avoid an import cycle with `cli.core.session_types`. Runtime code should
validate `message` via `cli.core.session_types.CodingAgentMessageAdapter`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ai_provider.types import ImageContent, Model


class _PiModel(BaseModel):
    """Base for Pi-compatible wire types: alias-driven I/O, transparent extras."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")


# --------------------------------------------------------------------------- #
# Resource discovery                                                           #
# --------------------------------------------------------------------------- #


class ResourcesDiscoverEvent(_PiModel):
    type: Literal["resources_discover"] = "resources_discover"
    cwd: str
    reason: Literal["startup", "reload"]


class ResourcesDiscoverResult(_PiModel):
    skill_paths: list[str] | None = Field(default=None, alias="skillPaths")
    prompt_paths: list[str] | None = Field(default=None, alias="promptPaths")
    theme_paths: list[str] | None = Field(default=None, alias="themePaths")


# --------------------------------------------------------------------------- #
# Session lifecycle events                                                     #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Context / provider events                                                    #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Model events                                                                 #
# --------------------------------------------------------------------------- #


ModelSelectSource = Literal["set", "cycle", "restore"]


class ModelSelectEvent(_PiModel):
    type: Literal["model_select"] = "model_select"
    model: Model
    previous_model: Model | None = Field(default=None, alias="previousModel")
    source: ModelSelectSource


# --------------------------------------------------------------------------- #
# User bash + input events                                                     #
# --------------------------------------------------------------------------- #


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
InputEventResultAdapter: TypeAdapter[InputEventResult] = TypeAdapter(InputEventResultItem)


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
