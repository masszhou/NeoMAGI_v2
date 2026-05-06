"""Register extension-contributed slash commands."""

from __future__ import annotations

from typing import Any

from .registry import RegisteredCommand, SlashCommandRegistry


def register_extension_commands(registry: SlashCommandRegistry, controller: Any) -> set[str]:
    runtime = controller.runtime
    if runtime is None:
        return set()
    names: set[str] = set()
    for name, options in runtime.extension_commands().items():
        if registry.get(name) is not None:
            continue
        names.add(name)
        registry.register(
            RegisteredCommand(
                name=name,
                description=str(options.get("description") or "Extension command"),
                handler=_handler(controller, name),
            )
        )
    return names


def refresh_extension_commands(controller: Any) -> None:
    registry = controller._slash_registry  # noqa: SLF001 - slash integration owns this registry
    if registry is None:
        return
    for name in controller._extension_command_names:  # noqa: SLF001
        registry.unregister(name)
    controller._extension_command_names = register_extension_commands(registry, controller)  # noqa: SLF001


def _handler(controller: Any, command_name: str):
    def handle(ctx: Any) -> None:
        try:
            runtime = controller.runtime
            if runtime is None:
                raise RuntimeError("extension runtime is not available")
            runtime.run_extension_command(command_name, ctx.args, ctx.raw)
        except Exception as exc:
            controller.status.push_notification(str(exc), level="error", ttl_seconds=6.0)

    return handle


__all__ = ["refresh_extension_commands", "register_extension_commands"]
