"""Slash-command registry shared by builtins (M1) and extensions (M8).

``RegisteredCommand`` is a *local dataclass*, not a pydantic model: the
extension wire payload lives in ``cli.extensions.types.RegisteredCommand``
and W6 deliberately stays at the runtime-callable level so M8 can wrap
either form without re-modelling.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cli.interactive.app import InteractiveController

CommandHandler = Callable[["SlashCommandContext"], None]


@dataclass
class SlashCommandContext:
    name: str
    args: list[str]
    raw: str
    controller: "InteractiveController"


@dataclass
class RegisteredCommand:
    name: str
    """Command name *without* the leading slash, e.g. ``"new"``."""
    description: str
    handler: CommandHandler
    keywords: list[str] = field(default_factory=list)
    """Aliases shown in autocomplete (e.g. ``["compact"]`` → ``/compact``)."""
    stub_milestone: str | None = None
    """Set when this is a placeholder; messaging routes through the stub
    handler so the user knows when to expect real behavior."""


PI_BUILTIN_COMMANDS: tuple[tuple[str, str, str | None], ...] = (
    ("settings", "Open settings UI", "M9"),
    ("model", "Switch model", "M9"),
    ("scoped-models", "Configure Ctrl+P model rotation", "M9"),
    ("export", "Export session as HTML / JSONL", None),
    ("import", "Import JSONL into current session", None),
    ("share", "Share session as a GitHub gist", "M10"),
    ("copy", "Copy last assistant message", "M10"),
    ("name", "Rename current session", None),
    ("session", "Show session statistics", None),
    ("changelog", "Show NeoMAGI changelog", "M10"),
    ("hotkeys", "Show keybinding table", None),
    ("fork", "Fork session from a historic user message", None),
    ("clone", "Clone current branch as a new session", None),
    ("tree", "Open session tree navigation", None),
    ("login", "OAuth login", "M9"),
    ("logout", "OAuth logout", "M9"),
    ("new", "Start a new session", None),
    ("compact", "Manual compaction", "M7"),
    ("resume", "Resume a previous session", None),
    ("reload", "Reload extensions / skills / prompts / themes", "M8"),
    ("quit", "Quit NeoMAGI", None),
)
"""Behavior matrix § A — 21 Pi builtin commands. ``stub_milestone`` is
non-null for those whose runtime arrives in a later milestone; M1 still
registers them so autocomplete is complete (and the user gets a clear
"tracked in M{X}" message when they try to invoke)."""

LIVE_BUILTIN_COMMANDS: frozenset[str] = frozenset(
    {
        "clone",
        "export",
        "fork",
        "hotkeys",
        "import",
        "name",
        "new",
        "quit",
        "resume",
        "session",
        "tree",
    }
)
"""``/play`` is added separately — it's M1-only, not a Pi builtin."""

M1_LIVE_COMMANDS = LIVE_BUILTIN_COMMANDS
"""Backward-compatible alias for older tests/imports."""


class SlashCommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, RegisteredCommand] = {}

    def register(self, command: RegisteredCommand) -> None:
        if command.name in self._commands:
            raise ValueError(f"slash command /{command.name} already registered")
        self._commands[command.name] = command

    def get(self, name: str) -> RegisteredCommand | None:
        return self._commands.get(name)

    def all(self) -> list[RegisteredCommand]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def autocomplete_items(self) -> list[tuple[str, str | None]]:
        items: list[tuple[str, str | None]] = []
        for cmd in self.all():
            tag = (
                f"[stub – {cmd.stub_milestone}] {cmd.description}"
                if cmd.stub_milestone
                else cmd.description
            )
            items.append((f"/{cmd.name}", tag))
        return items

    def parse_and_dispatch(
        self, raw: str, controller: "InteractiveController"
    ) -> bool:
        """Parse ``/name arg arg`` and run the handler. Returns ``True`` if
        the line was a slash command (handled or unknown), ``False`` if it
        wasn't a slash line at all."""

        text = raw.strip()
        if not text.startswith("/"):
            return False
        parts = text[1:].split()
        if not parts:
            return True
        name, args = parts[0], parts[1:]
        cmd = self._commands.get(name)
        ctx = SlashCommandContext(name=name, args=args, raw=raw, controller=controller)
        if cmd is None:
            controller.status.push_notification(
                f"unknown command: /{name}", level="warn"
            )
            return True
        cmd.handler(ctx)
        return True


def _make_stub(milestone: str, description: str) -> CommandHandler:
    def handler(ctx: SlashCommandContext) -> None:
        ctx.controller.status.push_notification(
            f"/{ctx.name} not implemented yet; tracked in {milestone} ({description})",
            level="info",
            ttl_seconds=6.0,
        )

    return handler


def register_builtin_commands(
    registry: SlashCommandRegistry,
    *,
    play_targets: Sequence[str] = (),
) -> None:
    """Register all 21 Pi builtin commands + ``/play`` (M1-only)."""

    from .clone import handle_clone
    from .export_import import handle_export, handle_import
    from .fork import handle_fork
    from .hotkeys import handle_hotkeys
    from .new import handle_new
    from .play import make_play_handler
    from .quit import handle_quit
    from .resume import handle_resume
    from .session import handle_name, handle_session
    from .tree import handle_tree

    live_handlers: dict[str, CommandHandler] = {
        "clone": handle_clone,
        "export": handle_export,
        "fork": handle_fork,
        "import": handle_import,
        "name": handle_name,
        "new": handle_new,
        "hotkeys": handle_hotkeys,
        "quit": handle_quit,
        "resume": handle_resume,
        "session": handle_session,
        "tree": handle_tree,
    }

    for name, description, stub_milestone in PI_BUILTIN_COMMANDS:
        if name in live_handlers:
            registry.register(
                RegisteredCommand(
                    name=name,
                    description=description,
                    handler=live_handlers[name],
                )
            )
        else:
            registry.register(
                RegisteredCommand(
                    name=name,
                    description=description,
                    handler=_make_stub(stub_milestone or "later", description),
                    stub_milestone=stub_milestone,
                )
            )

    registry.register(
        RegisteredCommand(
            name="play",
            description="Replay a fixture (M1 only)",
            handler=make_play_handler(list(play_targets)),
        )
    )


__all__ = [
    "CommandHandler",
    "LIVE_BUILTIN_COMMANDS",
    "M1_LIVE_COMMANDS",
    "PI_BUILTIN_COMMANDS",
    "RegisteredCommand",
    "SlashCommandContext",
    "SlashCommandRegistry",
    "register_builtin_commands",
]
