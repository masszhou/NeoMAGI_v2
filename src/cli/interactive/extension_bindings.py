"""Small adapter helpers for extension-provided interactive surfaces."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def custom_renderer_lookup(
    runtime_provider: Callable[[], Any | None],
) -> Callable[[str], Callable[..., Any] | None]:
    def lookup(custom_type: str) -> Callable[..., Any] | None:
        runtime = runtime_provider()
        if runtime is None:
            return None
        return runtime.get_custom_message_renderer(custom_type)

    return lookup


def custom_renderer_error(status: Any) -> Callable[[str, Exception], None]:
    def on_error(custom_type: str, exc: Exception) -> None:
        status.push_notification(
            f"custom renderer {custom_type} failed: {exc}",
            level="warn",
            ttl_seconds=8.0,
        )

    return on_error


def slash_autocomplete_items(
    registry: Any,
    runtime: Any | None,
) -> list[tuple[str, str | None]]:
    items = registry.autocomplete_items()
    if runtime is not None:
        items.extend(runtime.resource_command_items())
    return items


def reject_queued_extension_command(text: str, runtime: Any | None, status: Any) -> bool:
    if runtime is None:
        return False
    name = runtime.extension_command_name(text)
    if name is None:
        return False
    status.push_notification(
        f"extension command /{name} cannot be queued while streaming; run it while idle",
        level="error",
        ttl_seconds=8.0,
    )
    return True
