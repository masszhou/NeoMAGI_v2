from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path

from ai_provider.types import AssistantMessage, TextContent, Usage, UsageCost, UserMessage
from cli.core.session_manager import SessionManager
from cli.interactive.app import InteractiveController
from cli.interactive.runtime import InteractiveAgentRuntime
from cli.slash_commands.registry import SlashCommandRegistry, register_builtin_commands
from storage.in_memory_session_repository import InMemorySessionRepository
from tui.app import TUIApp


def _controller(tmp_path: Path) -> tuple[InteractiveController, InteractiveAgentRuntime, SessionManager]:
    manager = SessionManager(InMemorySessionRepository())
    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        tool_profile="none",
    )
    controller = InteractiveController(tui_app=TUIApp(), runtime=runtime)
    controller.bootstrap()
    return controller, runtime, manager


def _dispatch(controller: InteractiveController, raw: str) -> None:
    assert controller._slash_registry is not None  # noqa: SLF001
    handled = controller._slash_registry.parse_and_dispatch(raw, controller)  # noqa: SLF001
    assert handled is True


def test_m10_commands_are_registered_with_expected_boundaries() -> None:
    registry = SlashCommandRegistry()
    register_builtin_commands(registry)

    assert registry.get("copy").stub_milestone is None
    assert registry.get("share").stub_milestone is None
    assert registry.get("changelog").stub_milestone == "P1-optional"


def test_export_command_dispatches_all_local_formats(tmp_path: Path) -> None:
    controller, runtime, manager = _controller(tmp_path)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="export me")], timestamp=1),
        )
        manager.append_message(session_id, _assistant("done", timestamp=2))

        for raw in (
            "/export out/session.jsonl",
            "/export out/session.pi.jsonl",
            "/export out/session.session.json",
            "/export out/session.html",
        ):
            _dispatch(controller, raw)

        assert (tmp_path / "out" / "session.jsonl").exists()
        pi_lines = [
            json.loads(line)
            for line in (tmp_path / "out" / "session.pi.jsonl").read_text().splitlines()
        ]
        assert len(pi_lines) == 3
        assert (tmp_path / "out" / "session.session.json").exists()
        assert (tmp_path / "out" / "session.html").exists()

        _dispatch(controller, "/export out/session.zip")
        notices = [note.text for note in controller.status._notifications]  # noqa: SLF001
        assert any("supported extensions" in text for text in notices)
    finally:
        runtime.shutdown()


def test_export_command_rejects_all_formats_while_streaming(tmp_path: Path) -> None:
    controller, runtime, manager = _controller(tmp_path)
    pending: Future[None] = Future()
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="export me")], timestamp=1),
        )
        runtime._active_future = pending  # noqa: SLF001

        for raw in (
            "/export busy.jsonl",
            "/export busy.pi.jsonl",
            "/export busy.session.json",
            "/export busy.html",
        ):
            _dispatch(controller, raw)

        notices = [note.text for note in controller.status._notifications]  # noqa: SLF001
        assert sum("session export is not available while streaming" in text for text in notices) == 4
        assert not (tmp_path / "busy.jsonl").exists()
        assert not (tmp_path / "busy.pi.jsonl").exists()
        assert not (tmp_path / "busy.session.json").exists()
        assert not (tmp_path / "busy.html").exists()
    finally:
        runtime._active_future = None  # noqa: SLF001
        runtime.shutdown()


def test_copy_command_uses_last_assistant_text(monkeypatch, tmp_path: Path) -> None:
    controller, runtime, manager = _controller(tmp_path)
    copied: list[str] = []
    monkeypatch.setattr(
        "cli.slash_commands.copy.copy_to_clipboard",
        lambda text: copied.append(text) or True,
    )
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        manager.append_message(
            session_id,
            _assistant("first", timestamp=1).model_copy(update={"stop_reason": "aborted", "content": []}),
        )
        manager.append_message(session_id, _assistant("second", timestamp=2))

        _dispatch(controller, "/copy")

        assert copied == ["second"]
        assert any(
            "copied last assistant message" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
    finally:
        runtime.shutdown()


def test_copy_and_share_fallback_messages_are_clear(monkeypatch, tmp_path: Path) -> None:
    controller, runtime, manager = _controller(tmp_path)
    monkeypatch.setattr("cli.slash_commands.copy.copy_to_clipboard", lambda _text: False)
    try:
        _dispatch(controller, "/copy")
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        manager.append_message(session_id, _assistant("copy me", timestamp=1))
        _dispatch(controller, "/copy")
        _dispatch(controller, "/share")

        notices = [note.text for note in controller.status._notifications]  # noqa: SLF001
        assert any("no assistant message" in text for text in notices)
        assert any("clipboard helper unavailable" in text for text in notices)
        assert any("/share does not upload in P1" in text for text in notices)
    finally:
        runtime.shutdown()


def _assistant(text: str, *, timestamp: int) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="responses",
        provider="openai",
        model="gpt-5.4",
        usage=Usage(
            input=1,
            output=1,
            totalTokens=2,
            cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
        ),
        stopReason="stop",
        timestamp=timestamp,
    )
