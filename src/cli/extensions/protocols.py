"""Runtime ``typing.Protocol`` surfaces for the extension API.

Mirrors `extensions/types.ts:88–351, 1040–1259` (pi-mono `97a38bf6`):

- :class:`EventBus` — shared event bus for extension-to-extension comms.
- :class:`ExtensionUIContext` — 22 UI primitives.
- :class:`ExtensionContext` / :class:`ExtensionCommandContext` — handler /
  command-handler context.
- :class:`MessageRenderer` — alias for custom-message renderers.
- :class:`ExtensionAPI` — full mirror of the surface registered by an
  extension's ``activate(api: ExtensionAPI)`` entry point.

Protocol classes are not validated at the wire level; tests mock them.

Async convention (ADR-0013 + ADR-0014; see `pi_behavior_matrix.md` § D):
Pi methods typed as ``Promise<X>`` are declared as ``async def -> X`` here,
and implementations MUST use ``async def``. Sync ``def -> X`` cannot satisfy
the Protocol slot — they return different runtime values.

`register_tool` and `register_provider` reference wire types defined in
``cli.extensions.types``; ``from __future__ import annotations`` defers
their resolution so this module loads without a circular import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal, Protocol, Union, runtime_checkable

from pydantic import BaseModel, ConfigDict

from ai_provider.types import ImageContent, Model, TextContent, ThinkingLevel

if TYPE_CHECKING:
    from cli.extensions.types import KeyId, ProviderConfig, ToolDefinition


class _PiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


# --------------------------------------------------------------------------- #
# Result shape for command-context session-mutation methods                    #
# --------------------------------------------------------------------------- #


class CancellableResult(_PiModel):
    """Common result for command-context session-mutation methods.

    Pi `extensions/types.ts:325–348` — `newSession`, `fork`, `navigateTree`,
    `switchSession` all return ``Promise<{cancelled: boolean}>`` so handlers
    can detect that a `session_before_*` extension cancelled the operation.
    """

    cancelled: bool


# --------------------------------------------------------------------------- #
# EventBus                                                                     #
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


# --------------------------------------------------------------------------- #
# UI context (22 primitives)                                                   #
# --------------------------------------------------------------------------- #


@runtime_checkable
class ExtensionUIContext(Protocol):
    """22 UI primitives mirrored from `extensions/types.ts:88–268`.

    Async slots (Pi: ``Promise<X>``): ``select`` / ``confirm`` / ``input`` /
    ``custom`` / ``editor`` — all dialog-style methods that wait for user
    response. Implementations MUST use ``async def`` per ADR-0014.
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


# --------------------------------------------------------------------------- #
# Handler / command-handler context                                            #
# --------------------------------------------------------------------------- #


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

    Async convention (ADR-0013, also `pi_behavior_matrix.md` § D): every Pi
    method typed as ``Promise<X>`` is declared as ``async def -> X``.
    Implementations MUST use ``async def``; Python's structural typing does
    not let a sync ``def -> X`` satisfy an ``async def -> X`` Protocol slot —
    they return different runtime values (``X`` vs ``Coroutine[Any, Any, X]``).

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


# Forward-decl for custom-message renderers.
MessageRenderer = Callable[[Any, ExtensionContext], Any]


# --------------------------------------------------------------------------- #
# ExtensionAPI                                                                 #
# --------------------------------------------------------------------------- #


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
