"""In-memory extension registration state."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .diagnostics import ExtensionDiagnostic
from .types import ProviderConfig, RegisteredFlag, RegisteredShortcut, ToolDefinition


@dataclass(slots=True)
class RegisteredProvider:
    name: str
    config: ProviderConfig
    owner: str


@dataclass(slots=True)
class LoadedExtension:
    name: str
    path: Path | None = None
    handlers: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)
    tools: list[ToolDefinition] = field(default_factory=list)
    commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    shortcuts: dict[str, RegisteredShortcut] = field(default_factory=dict)
    flags: dict[str, RegisteredFlag] = field(default_factory=dict)
    flag_values: dict[str, bool | str | None] = field(default_factory=dict)
    message_renderers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    providers: dict[str, RegisteredProvider] = field(default_factory=dict)
    diagnostics: list[ExtensionDiagnostic] = field(default_factory=list)

    def add_handler(self, event: str, handler: Callable[..., Any]) -> None:
        self.handlers.setdefault(event, []).append(handler)


@dataclass(slots=True)
class ExtensionRuntime:
    extensions: list[LoadedExtension] = field(default_factory=list)
    diagnostics: list[ExtensionDiagnostic] = field(default_factory=list)
    bound: bool = False
    actions: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def register(self, extension: LoadedExtension) -> None:
        self.extensions.append(extension)

    def all_diagnostics(self) -> list[ExtensionDiagnostic]:
        diagnostics = [*self.diagnostics]
        for extension in self.extensions:
            diagnostics.extend(extension.diagnostics)
        return diagnostics


def create_extension_runtime() -> ExtensionRuntime:
    return ExtensionRuntime()


__all__ = [
    "ExtensionRuntime",
    "LoadedExtension",
    "RegisteredProvider",
    "create_extension_runtime",
]
