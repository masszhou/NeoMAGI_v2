"""Shared extension-to-extension event bus."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from .diagnostics import ExtensionDiagnostic


@dataclass(slots=True)
class ExtensionEventBus:
    diagnostics: list[ExtensionDiagnostic] = field(default_factory=list)
    _handlers: dict[str, list[Callable[[object], None]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def emit(self, channel: str, data: object) -> None:
        for handler in list(self._handlers.get(channel, ())):
            try:
                handler(data)
            except Exception as exc:
                self.diagnostics.append(
                    ExtensionDiagnostic(
                        severity="error",
                        message=f"event bus handler failed: {exc}",
                        event=channel,
                    )
                )

    def on(self, channel: str, handler: Callable[[object], None]) -> Callable[[], None]:
        self._handlers[channel].append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(channel)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return unsubscribe


__all__ = ["ExtensionEventBus"]
