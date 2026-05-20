from __future__ import annotations

import sys

from cli.slash_commands import taskrun as taskrun_command
from cli.slash_commands.registry import SlashCommandContext, SlashCommandRegistry, register_builtin_commands


class _Controller:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def push_session_message(self, message: str, *, level: str = "info") -> None:
        self.messages.append((level, message))


def _context(args: list[str]) -> SlashCommandContext:
    return SlashCommandContext(
        name="taskrun",
        args=args,
        raw="/taskrun " + " ".join(args),
        controller=_Controller(),  # type: ignore[arg-type]
    )


def test_taskrun_command_is_registered_as_live_handler() -> None:
    registry = SlashCommandRegistry()
    register_builtin_commands(registry)

    command = registry.get("taskrun")

    assert command is not None
    assert command.stub_milestone is None


def test_taskrun_slash_handler_routes_read_command(monkeypatch) -> None:
    calls: list[tuple[list[str], str]] = []

    def fake_run(argv: list[str], *, prog: str) -> int:
        calls.append((argv, prog))
        print("status ok")
        return 0

    monkeypatch.setattr(taskrun_command, "run_taskrun_command", fake_run)
    ctx = _context(["status", "abc"])

    taskrun_command.handle_taskrun(ctx)

    assert calls == [(["status", "abc"], "magipi")]
    assert ctx.controller.messages == [("info", "status ok")]


def test_taskrun_slash_handler_blocks_headless_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        taskrun_command,
        "run_taskrun_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("called")),
    )
    ctx = _context(["run", "--max-steps", "1"])

    taskrun_command.handle_taskrun(ctx)

    assert ctx.controller.messages
    level, message = ctx.controller.messages[0]
    assert level == "warn"
    assert "usage: /taskrun" in message


def test_taskrun_slash_handler_captures_parse_errors(monkeypatch) -> None:
    def fake_run(_argv: list[str], *, prog: str) -> int:
        sys.stderr.write(f"{prog} taskrun: bad args\n")
        raise SystemExit(2)

    monkeypatch.setattr(taskrun_command, "run_taskrun_command", fake_run)
    ctx = _context(["status", "--bad"])

    taskrun_command.handle_taskrun(ctx)

    assert ctx.controller.messages == [("warn", "magipi taskrun: bad args")]
