"""Slash-command registry + builtin commands.

The registry is intentionally future-compatible with M8's extension API
``registerCommand``: extensions will register into the same registry and
share the same :class:`RegisteredCommand` shape (a thin local dataclass
that mirrors the keys of ``cli.extensions.types.RegisteredCommand``).

M1 ships four real commands — ``/new``, ``/quit``, ``/hotkeys``,
``/play`` — plus stubs for the remaining 17 Pi builtin commands so the
slash autocomplete is complete from day one (per behavior matrix § A).
"""

from .registry import (
    M1_LIVE_COMMANDS,
    PI_BUILTIN_COMMANDS,
    RegisteredCommand,
    SlashCommandContext,
    SlashCommandRegistry,
    register_builtin_commands,
)

__all__ = [
    "M1_LIVE_COMMANDS",
    "PI_BUILTIN_COMMANDS",
    "RegisteredCommand",
    "SlashCommandContext",
    "SlashCommandRegistry",
    "register_builtin_commands",
]
