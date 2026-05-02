from __future__ import annotations

import io
from concurrent.futures import Future
from pathlib import Path

from ai_provider.types import TextContent, UserMessage
from cli.core.session_manager import SessionManager
from cli.interactive.app import InteractiveController
from cli.interactive.runtime import InteractiveAgentRuntime
from cli.slash_commands.registry import SlashCommandRegistry, register_builtin_commands
from storage.in_memory_session_repository import InMemorySessionRepository
from tui.app import TUIApp

SUMMARY = """## Goal
Continue the task.
## Constraints & Preferences
Keep repo boundaries.
## Progress
### Done
Older context summarized.
### In Progress
Current work.
### Blocked
None.
## Key Decisions
Use durable session summaries.
## Next Steps
Continue.
## Critical Context
Important details.
<read-files>
</read-files>
<modified-files>
</modified-files>"""


class FakeSummaryGenerator:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, *, model) -> str:
        self.prompts.append(prompt)
        return SUMMARY


def _controller(
    tmp_path: Path,
    *,
    generator: FakeSummaryGenerator | None = None,
    render_mode: str = "canvas",
    out_stream=None,
) -> tuple[InteractiveController, InteractiveAgentRuntime, SessionManager]:
    manager = SessionManager(InMemorySessionRepository())
    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        tool_profile="none",
        summary_generator=generator or FakeSummaryGenerator(),
    )
    controller = InteractiveController(
        tui_app=TUIApp(render_mode=render_mode, out_stream=out_stream),
        runtime=runtime,
    )
    controller.bootstrap()
    return controller, runtime, manager


def _dispatch(controller: InteractiveController, raw: str) -> None:
    assert controller._slash_registry is not None  # noqa: SLF001
    handled = controller._slash_registry.parse_and_dispatch(raw, controller)  # noqa: SLF001
    assert handled is True


def test_compact_command_is_registered_as_live_handler() -> None:
    registry = SlashCommandRegistry()
    register_builtin_commands(registry)

    command = registry.get("compact")

    assert command is not None
    assert command.stub_milestone is None


def test_compact_command_appends_entry_and_preserves_raw_instruction_tail(
    tmp_path: Path,
) -> None:
    generator = FakeSummaryGenerator()
    controller, runtime, manager = _controller(tmp_path, generator=generator)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="old")], timestamp=1),
        )
        manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="recent")], timestamp=2),
        )

        _dispatch(controller, "/compact  keep this detail   exactly")

        entries = manager.repository.list_entries(session_id)
        assert entries[-1].entry_type == "compaction"
        assert entries[-1].payload.summary == SUMMARY
        assert "  keep this detail   exactly" in generator.prompts[0]
        assert any(
            "compacted session=" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
    finally:
        runtime.shutdown()


def test_compact_command_rejects_while_streaming(tmp_path: Path) -> None:
    controller, runtime, manager = _controller(tmp_path)
    pending: Future[None] = Future()
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="old")], timestamp=1),
        )
        manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="recent")], timestamp=2),
        )
        runtime._active_future = pending  # noqa: SLF001

        _dispatch(controller, "/compact")

        assert [entry.entry_type for entry in manager.repository.list_entries(session_id)] == [
            "message",
            "message",
        ]
        assert any(
            "streaming" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
    finally:
        runtime._active_future = None  # noqa: SLF001
        runtime.shutdown()


def test_compact_command_mode_writes_readable_boundary(tmp_path: Path) -> None:
    out = io.StringIO()
    controller, runtime, manager = _controller(
        tmp_path,
        render_mode="command",
        out_stream=out,
    )
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="old")], timestamp=1),
        )
        manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="recent")], timestamp=2),
        )

        _dispatch(controller, "/compact")

        committed = out.getvalue()
        assert "compacted session=" in committed
        assert "firstKept=entry:" in committed
        assert "tokensBefore=" in committed
    finally:
        runtime.shutdown()
