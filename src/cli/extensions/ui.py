"""Minimal ExtensionUIContext adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .diagnostics import ExtensionDiagnostic


@dataclass(slots=True)
class NoopExtensionUIContext:
    diagnostics: list[ExtensionDiagnostic] = field(default_factory=list)

    async def select(
        self,
        _title: str,
        _options: list[Any],
        _opts: dict[str, Any] | None = None,
    ) -> str | None:
        return None

    async def confirm(
        self,
        _title: str,
        _message: str,
        _opts: dict[str, Any] | None = None,
    ) -> bool:
        return False

    async def input(
        self,
        _title: str,
        _placeholder: str | None = None,
        _opts: dict[str, Any] | None = None,
    ) -> str | None:
        return None

    def notify(
        self,
        message: str,
        type: Literal["info", "warning", "error"] | None = None,
    ) -> None:
        self.diagnostics.append(
            ExtensionDiagnostic(
                severity="warning" if type == "warning" else "error" if type == "error" else "warning",
                message=f"ui.notify: {message}",
            )
        )

    def set_status(self, _key: str, _text: str | None = None) -> None:
        return None

    def set_working_message(self, _message: str | None = None) -> None:
        return None

    def set_widget(
        self,
        _key: str,
        _content: Any | None = None,
        _options: dict[str, Any] | None = None,
    ) -> None:
        return None

    async def editor(self, _title: str, prefill: str | None = None) -> str | None:
        return prefill

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def noop(*_args: Any, **_kwargs: Any) -> Any:
            self.diagnostics.append(
                ExtensionDiagnostic(
                    severity="warning",
                    message=f"ExtensionUIContext.{name} is a no-op in M8",
                )
            )
            return None

        return noop


__all__ = ["NoopExtensionUIContext"]
