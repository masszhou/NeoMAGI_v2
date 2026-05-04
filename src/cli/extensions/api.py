"""Concrete ExtensionAPI implementation used during Python extension setup."""

from __future__ import annotations

from typing import Any, Callable

from pydantic import ValidationError

from .diagnostics import ExtensionDiagnostic
from .event_bus import ExtensionEventBus
from .runtime import ExtensionRuntime, LoadedExtension, RegisteredProvider
from .types import (
    ProviderConfig,
    ProviderConfigAdapter,
    RegisteredFlag,
    RegisteredShortcut,
    ToolDefinition,
    ToolDefinitionAdapter,
)
from .ui import NoopExtensionUIContext


class RuntimeNotInitializedError(RuntimeError):
    pass


class ExtensionAPIImpl:
    def __init__(
        self,
        *,
        extension: LoadedExtension,
        runtime: ExtensionRuntime,
        cwd: str,
        event_bus: ExtensionEventBus,
    ) -> None:
        self._extension = extension
        self._runtime = runtime
        self._cwd = cwd
        self._event_bus = event_bus

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self._extension.add_handler(event, handler)

    def register_tool(self, tool: ToolDefinition | dict[str, Any]) -> None:
        try:
            self._extension.tools.append(ToolDefinitionAdapter.validate_python(tool))
        except ValidationError as exc:
            self._extension.diagnostics.append(
                ExtensionDiagnostic(
                    severity="error",
                    message=f"invalid tool definition: {exc}",
                    extension=self._extension.name,
                    path=str(self._extension.path) if self._extension.path else None,
                )
            )
            raise

    def register_command(self, name: str, options: dict[str, Any]) -> None:
        self._extension.commands[name] = dict(options)

    def register_shortcut(self, key_id: str, options: dict[str, Any]) -> None:
        self._extension.shortcuts[key_id] = RegisteredShortcut(keyId=key_id, **options)

    def register_flag(self, name: str, options: dict[str, Any]) -> None:
        flag = RegisteredFlag(name=name, **options)
        self._extension.flags[name] = flag
        self._extension.flag_values.setdefault(name, flag.default)

    def get_flag(self, name: str) -> bool | str | None:
        return self._extension.flag_values.get(name)

    def register_message_renderer(self, custom_type: str, renderer: Callable[..., Any]) -> None:
        self._extension.message_renderers[custom_type] = renderer

    def register_provider(self, name: str, config: ProviderConfig | dict[str, Any]) -> None:
        parsed = ProviderConfigAdapter.validate_python(config)
        self._extension.providers[name] = RegisteredProvider(
            name=name,
            config=parsed,
            owner=self._extension.name,
        )
        self._extension.diagnostics.append(
            ExtensionDiagnostic(
                severity="warning",
                message="provider registration is accepted but not applied in M8",
                extension=self._extension.name,
                path=str(self._extension.path) if self._extension.path else None,
            )
        )

    def unregister_provider(self, name: str) -> None:
        self._extension.providers.pop(name, None)
        self._extension.diagnostics.append(
            ExtensionDiagnostic(
                severity="warning",
                message="provider unregister is recorded but live registries are unchanged in M8",
                extension=self._extension.name,
                path=str(self._extension.path) if self._extension.path else None,
            )
        )

    @property
    def events(self) -> ExtensionEventBus:
        return self._event_bus

    @property
    def ui(self) -> Any:
        ui = self._runtime.actions.get("ui")
        if ui is None:
            return NoopExtensionUIContext()
        return ui

    @property
    def cwd(self) -> str:
        return self._cwd

    def __getattr__(self, name: str) -> Any:
        if name in _LIVE_ACTIONS:
            def action_stub(*_args: Any, **_kwargs: Any) -> Any:
                if not self._runtime.bound:
                    raise RuntimeNotInitializedError("runtime not initialized")
                action = self._runtime.actions.get(name)
                if action is None:
                    raise NotImplementedError(f"ExtensionAPI.{name} is not bound in this runtime")
                return action(*_args, **_kwargs)

            return action_stub
        raise AttributeError(name)


_LIVE_ACTIONS = frozenset(
    {
        "send_message",
        "send_user_message",
        "append_entry",
        "set_session_name",
        "get_session_name",
        "set_label",
        "exec",
        "get_active_tools",
        "get_all_tools",
        "set_active_tools",
        "get_commands",
        "set_model",
        "get_thinking_level",
        "set_thinking_level",
    }
)


def create_extension_api(
    extension: LoadedExtension,
    runtime: ExtensionRuntime,
    cwd: str,
    event_bus: ExtensionEventBus,
) -> ExtensionAPIImpl:
    return ExtensionAPIImpl(extension=extension, runtime=runtime, cwd=cwd, event_bus=event_bus)


__all__ = ["ExtensionAPIImpl", "RuntimeNotInitializedError", "create_extension_api"]
