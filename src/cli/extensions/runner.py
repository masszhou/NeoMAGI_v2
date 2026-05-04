"""Extension handler runner with Pi-style ordering and chaining."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cli.resources.loader import ResourceExtensionPaths

from .diagnostics import ExtensionDiagnostic
from .event_types import (
    BeforeAgentStartEvent,
    InputEventResultAdapter,
    ResourcesDiscoverResult,
    UserBashEventResult,
)
from .runtime import ExtensionRuntime, LoadedExtension
from .tool_events import ToolCallEventResult, ToolResultEventResult
from .ui import NoopExtensionUIContext


@dataclass(slots=True)
class ExtensionRunner:
    runtime: ExtensionRuntime
    diagnostics: list[ExtensionDiagnostic] = field(default_factory=list)
    ui_context: Any = field(default_factory=NoopExtensionUIContext)

    def bind_core(self, **actions: Any) -> None:
        self.runtime.actions.update(actions)
        self.runtime.bound = True

    def bind_command_context(self, **actions: Any) -> None:
        self.runtime.actions.update(actions)
        self.runtime.bound = True

    def set_ui_context(self, ui: Any) -> None:
        self.ui_context = ui
        self.runtime.bound = True

    def create_context(self) -> dict[str, Any]:
        return {"ui": self.ui_context, "has_ui": not isinstance(self.ui_context, NoopExtensionUIContext)}

    def create_command_context(self) -> dict[str, Any]:
        return self.create_context()

    async def emit(self, event: Any) -> list[Any]:
        results: list[Any] = []
        for extension, handler in self._handlers(_event_type(event)):
            result = await self._call_handler(extension, handler, event)
            if result is not None:
                results.append(result)
        return results

    async def emit_tool_call(self, event: Any) -> ToolCallEventResult | None:
        for extension, handler in self._handlers("tool_call"):
            result = await self._call_handler(extension, handler, event)
            if result is None:
                continue
            parsed = ToolCallEventResult.model_validate(result)
            if parsed.block:
                return parsed
        return None

    async def emit_tool_result(self, event: Any) -> Any:
        for extension, handler in self._handlers("tool_result"):
            result = await self._call_handler(extension, handler, event)
            if result is None:
                continue
            parsed = ToolResultEventResult.model_validate(result)
            if parsed.content is not None:
                _set(event, "content", parsed.content)
            if parsed.details is not None:
                _set(event, "details", parsed.details)
            if parsed.is_error is not None:
                _set(event, "is_error", parsed.is_error)
                _set(event, "isError", parsed.is_error)
        return event

    async def emit_user_bash(self, event: Any) -> UserBashEventResult | None:
        for extension, handler in self._handlers("user_bash"):
            result = await self._call_handler(extension, handler, event)
            if result is not None:
                return UserBashEventResult.model_validate(result)
        return None

    async def emit_context(self, messages: list[Any]) -> list[Any]:
        event: dict[str, Any] = {"type": "context", "messages": messages}
        for extension, handler in self._handlers("context"):
            result = await self._call_handler(extension, handler, event)
            if isinstance(result, dict) and result.get("messages") is not None:
                event["messages"] = result["messages"]
        return list(event["messages"])

    async def emit_before_agent_start(
        self,
        event: BeforeAgentStartEvent,
    ) -> tuple[list[dict[str, Any]], str]:
        messages: list[dict[str, Any]] = []
        for extension, handler in self._handlers("before_agent_start"):
            result = await self._call_handler(extension, handler, event)
            if not isinstance(result, dict):
                continue
            if isinstance(result.get("message"), dict):
                messages.append(result["message"])
            if isinstance(result.get("systemPrompt"), str):
                event.system_prompt = result["systemPrompt"]
            elif isinstance(result.get("system_prompt"), str):
                event.system_prompt = result["system_prompt"]
        return messages, event.system_prompt

    async def emit_input(self, event: Any) -> Any:
        current = event
        for extension, handler in self._handlers("input"):
            result = await self._call_handler(extension, handler, current)
            if result is None:
                continue
            parsed = InputEventResultAdapter.validate_python(result)
            if parsed.action == "handled":
                return parsed
            if parsed.action == "transform":
                _set(current, "text", parsed.text)
                if parsed.images is not None:
                    _set(current, "images", parsed.images)
        return current

    async def emit_resources_discover(self, cwd: str, reason: str) -> ResourceExtensionPaths:
        event = {"type": "resources_discover", "cwd": cwd, "reason": reason}
        skill_paths: list[Path] = []
        prompt_paths: list[Path] = []
        theme_paths: list[Path] = []
        for extension, handler in self._handlers("resources_discover"):
            result = await self._call_handler(extension, handler, event)
            if result is None:
                continue
            parsed = ResourcesDiscoverResult.model_validate(result)
            skill_paths.extend(Path(path).expanduser().resolve() for path in parsed.skill_paths or [])
            prompt_paths.extend(Path(path).expanduser().resolve() for path in parsed.prompt_paths or [])
            theme_paths.extend(Path(path).expanduser().resolve() for path in parsed.theme_paths or [])
        return ResourceExtensionPaths(
            skills=tuple(skill_paths),
            prompts=tuple(prompt_paths),
            themes=tuple(theme_paths),
        )

    def get_all_registered_tools(self) -> list[Any]:
        return [tool for extension in self.runtime.extensions for tool in extension.tools]

    def get_registered_commands(self) -> dict[str, dict[str, Any]]:
        commands: dict[str, dict[str, Any]] = {}
        for extension in self.runtime.extensions:
            for name, options in extension.commands.items():
                commands.setdefault(name, options)
        return commands

    def diagnose_command_collisions(self, *, reserved: set[str] | None = None) -> None:
        reserved = reserved or set()
        seen: dict[str, str] = {}
        for extension in self.runtime.extensions:
            for name in extension.commands:
                if name in reserved:
                    self._add_diagnostic(
                        extension,
                        f"extension command /{name} conflicts with builtin slash command; keeping builtin",
                    )
                    continue
                owner = seen.get(name)
                if owner is not None:
                    self._add_diagnostic(
                        extension,
                        f"duplicate extension command /{name}; keeping first from {owner}",
                    )
                    continue
                seen[name] = extension.name

    def get_command(self, name: str) -> dict[str, Any] | None:
        return self.get_registered_commands().get(name)

    def get_shortcuts(self) -> dict[str, Any]:
        shortcuts: dict[str, Any] = {}
        for extension in self.runtime.extensions:
            shortcuts.update(extension.shortcuts)
        return shortcuts

    def get_message_renderer(self, custom_type: str) -> Callable[..., Any] | None:
        for extension in self.runtime.extensions:
            renderer = extension.message_renderers.get(custom_type)
            if renderer is not None:
                return renderer
        return None

    def on_error(self, _listener: Callable[[ExtensionDiagnostic], None]) -> None:
        return None

    def _handlers(self, event: str) -> list[tuple[LoadedExtension, Callable[..., Any]]]:
        pairs: list[tuple[LoadedExtension, Callable[..., Any]]] = []
        for extension in self.runtime.extensions:
            pairs.extend((extension, handler) for handler in extension.handlers.get(event, ()))
        return pairs

    async def _call_handler(
        self,
        extension: LoadedExtension,
        handler: Callable[..., Any],
        event: Any,
    ) -> Any:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                return await result
            return result
        except Exception as exc:
            diagnostic = ExtensionDiagnostic(
                severity="error",
                message=f"extension handler failed: {exc}",
                extension=extension.name,
                path=str(extension.path) if extension.path else None,
                event=_event_type(event),
            )
            self.diagnostics.append(diagnostic)
            extension.diagnostics.append(diagnostic)
            return None

    def _add_diagnostic(self, extension: LoadedExtension, message: str) -> None:
        diagnostic = ExtensionDiagnostic(
            severity="warning",
            message=message,
            extension=extension.name,
            path=str(extension.path) if extension.path else None,
        )
        self.diagnostics.append(diagnostic)
        extension.diagnostics.append(diagnostic)


def _event_type(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("type") or "")
    return str(getattr(event, "type", "") or "")


def _set(event: Any, key: str, value: Any) -> None:
    if isinstance(event, dict):
        event[key] = value
    elif hasattr(event, key):
        setattr(event, key, value)


__all__ = ["ExtensionRunner"]
