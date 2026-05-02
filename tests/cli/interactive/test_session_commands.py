from __future__ import annotations

import io
from concurrent.futures import Future
from pathlib import Path

from agent_core import Agent, AgentOptions
from ai_provider.types import TextContent, UserMessage
from cli.core.session_manager import SessionManager
from cli.interactive.app import InteractiveController
from cli.interactive.runtime import InteractiveAgentRuntime
from cli.slash_commands.registry import SlashCommandRegistry, register_builtin_commands
from storage.in_memory_session_repository import InMemorySessionRepository
from tui.app import TUIApp


def _controller_with_session_manager(
    tmp_path: Path,
    *,
    render_mode: str = "canvas",
    out_stream=None,
) -> tuple[InteractiveController, InteractiveAgentRuntime, SessionManager]:
    manager = SessionManager(InMemorySessionRepository())
    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        tool_profile="none",
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


def test_m6_session_commands_are_registered_as_live_handlers() -> None:
    registry = SlashCommandRegistry()
    register_builtin_commands(registry)

    for name in ("session", "name", "resume", "fork", "clone", "tree"):
        command = registry.get(name)
        assert command is not None
        assert command.stub_milestone is None


def test_session_name_and_resume_commands_dispatch(tmp_path: Path) -> None:
    controller, runtime, manager = _controller_with_session_manager(tmp_path)
    try:
        _dispatch(controller, "/name Daily branch")
        assert runtime.session_stats().name == "Daily branch"
        assert "name=Daily branch" in controller.editor._footer  # noqa: SLF001

        other = manager.new_session(tmp_path)
        _dispatch(controller, f"/resume {other.id}")

        assert runtime.state.durable_session_id == other.id
        assert any(
            "resumed session=" in note.text and "context=0 messages" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
        prefixed = manager.repository.create_session(
            cwd=str(tmp_path),
            session_id="aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaa1",
        )
        _dispatch(controller, "/resume aaaaaaaa")

        assert runtime.state.durable_session_id == prefixed.id
        _dispatch(controller, "/resume not-a-session")
        assert runtime.state.durable_session_id == prefixed.id
        assert any(
            "unknown session" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
        _dispatch(controller, "/session")
        assert any("session" in note.text for note in controller.status._notifications)  # noqa: SLF001
    finally:
        runtime.shutdown()


def test_fork_and_clone_commands_switch_sessions_with_expected_editor_state(
    tmp_path: Path,
) -> None:
    controller, runtime, manager = _controller_with_session_manager(tmp_path)
    try:
        source_id = runtime.state.durable_session_id
        assert source_id is not None
        source_session = manager.resume_session(source_id)
        user = manager.append_message(
            source_id,
            UserMessage(content=[TextContent(text="rewrite this")], timestamp=1),
        )

        _dispatch(controller, f"/fork {user.pi_export_id}")
        forked_id = runtime.state.durable_session_id
        assert forked_id is not None and forked_id != source_id
        assert manager.resume_session(forked_id).parent_session_id == source_id
        assert runtime.state.provider_cache_affinity_id != source_session.provider_cache_affinity_id
        assert controller.editor.buffer.text == "rewrite this"
        assert any(
            "forked session=" in note.text and "parent=session:" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )

        controller.editor.buffer.text = ""
        controller.editor.buffer.cursor = 0
        _dispatch(controller, f"/resume {source_id}")
        _dispatch(controller, "/clone")
        cloned_id = runtime.state.durable_session_id
        assert cloned_id is not None and cloned_id != source_id
        assert manager.resume_session(cloned_id).parent_session_id == source_id
        assert runtime.state.provider_cache_affinity_id != source_session.provider_cache_affinity_id
        assert controller.editor.buffer.text == ""
        assert any(
            "cloned session=" in note.text and "parent=session:" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
    finally:
        runtime.shutdown()


def test_tree_command_keeps_session_and_cache_affinity(tmp_path: Path) -> None:
    controller, runtime, manager = _controller_with_session_manager(tmp_path)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        first = manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="first")], timestamp=1),
        )
        manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="second")], timestamp=2),
        )
        before_session = runtime.state.durable_session_id
        before_affinity = runtime.state.provider_cache_affinity_id

        _dispatch(controller, f"/tree {first.pi_export_id}")

        assert runtime.state.durable_session_id == before_session
        assert runtime.state.provider_cache_affinity_id == before_affinity
        assert runtime.state.current_leaf_entry_id == manager.resume_session(session_id).current_leaf_entry_id
        assert any(
            "selected session=" in note.text and f"leaf=entry:{first.pi_export_id}" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
    finally:
        runtime.shutdown()


def test_tree_output_labels_entry_and_session_ids(tmp_path: Path) -> None:
    controller, runtime, manager = _controller_with_session_manager(tmp_path)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        first = manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="first")], timestamp=1),
        )
        second = manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="second")], timestamp=2),
        )

        _dispatch(controller, "/tree")

        rendered = "\n".join(note.text for note in controller.status._notifications)  # noqa: SLF001
        assert f"session={session_id.split('-', 1)[0]}" in rendered
        assert f"entry={first.pi_export_id}" in rendered
        assert f"entry={second.pi_export_id}" in rendered
        assert "message:user" in rendered
        assert "\u2190 active" in rendered
        assert "parent=entry:" in rendered
    finally:
        runtime.shutdown()


def test_tree_session_id_misuse_points_to_resume(tmp_path: Path) -> None:
    controller, runtime, _manager = _controller_with_session_manager(tmp_path)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None

        _dispatch(controller, f"/tree {session_id.split('-', 1)[0]}")

        assert any(
            "unknown entry id" in note.text and "use /resume for session ids" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
    finally:
        runtime.shutdown()


def test_command_mode_session_switch_commits_compact_summary(tmp_path: Path) -> None:
    out = io.StringIO()
    controller, runtime, manager = _controller_with_session_manager(
        tmp_path,
        render_mode="command",
        out_stream=out,
    )
    try:
        session = manager.new_session(tmp_path)
        manager.append_message(
            session.id,
            UserMessage(content=[TextContent(text="loaded")], timestamp=1),
        )

        _dispatch(controller, f"/resume {session.id}")

        committed = out.getvalue()
        assert "resumed session=" in committed
        assert "context=1 messages" in committed
        assert "leaf=entry:" in committed
    finally:
        runtime.shutdown()


def test_runtime_hydrates_resumed_session_context_into_agent_options(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="remember me")], timestamp=1),
    )
    captured: list[AgentOptions] = []

    def factory(options: AgentOptions) -> Agent:
        captured.append(options)
        return Agent(options)

    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        tool_profile="none",
        agent_factory=factory,
    )
    try:
        assert captured[0].messages[0].role == "user"
        assert captured[0].messages[0].content[0].text == "remember me"
        other = manager.new_session(tmp_path)
        manager.append_message(
            other.id,
            UserMessage(content=[TextContent(text="second session")], timestamp=2),
        )

        runtime.resume_session(other.id)

        assert captured[-1].messages[0].content[0].text == "second session"
    finally:
        runtime.shutdown()


def test_session_switch_commands_reject_while_streaming(tmp_path: Path) -> None:
    controller, runtime, manager = _controller_with_session_manager(tmp_path)
    pending: Future[None] = Future()
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        user = manager.append_message(
            session_id,
            UserMessage(content=[TextContent(text="seed")], timestamp=1),
        )
        other = manager.new_session(tmp_path)
        runtime._active_future = pending  # noqa: SLF001

        for raw in (
            f"/resume {other.id}",
            f"/fork {user.pi_export_id}",
            "/clone",
            f"/tree {user.pi_export_id}",
        ):
            _dispatch(controller, raw)
            assert runtime.state.durable_session_id == session_id

        assert any(
            "streaming" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
    finally:
        runtime._active_future = None  # noqa: SLF001
        runtime.shutdown()
