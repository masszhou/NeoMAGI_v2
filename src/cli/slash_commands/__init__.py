"""Slash-command registry + builtin commands.

The registry is intentionally future-compatible with M8's extension API
``registerCommand``: extensions will register into the same registry and
share the same :class:`RegisteredCommand` shape (a thin local dataclass
that mirrors the keys of ``cli.extensions.types.RegisteredCommand``).

M6 ships durable session commands while keeping later milestone commands as
explicit stubs so slash autocomplete remains complete (per behavior matrix § A).
"""

from .registry import (
    LIVE_BUILTIN_COMMANDS,
    M1_LIVE_COMMANDS,
    PI_BUILTIN_COMMANDS,
    RegisteredCommand,
    SlashCommandContext,
    SlashCommandRegistry,
    register_builtin_commands,
)

__all__ = [
    "LIVE_BUILTIN_COMMANDS",
    "M1_LIVE_COMMANDS",
    "PI_BUILTIN_COMMANDS",
    "RegisteredCommand",
    "SlashCommandContext",
    "SlashCommandRegistry",
    "register_builtin_commands",
]
