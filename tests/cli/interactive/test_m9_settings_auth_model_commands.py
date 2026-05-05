from __future__ import annotations

import json
from pathlib import Path

from ai_provider.auth_storage import AUTH_PATH_ENV, load_auth_storage
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
    assert controller._slash_registry.parse_and_dispatch(raw, controller) is True  # noqa: SLF001


def test_m9_commands_are_registered_as_live_handlers() -> None:
    registry = SlashCommandRegistry()
    register_builtin_commands(registry)

    for name in ("settings", "model", "scoped-models", "login", "logout"):
        command = registry.get(name)
        assert command is not None
        assert command.stub_milestone is None


def test_model_command_writes_durable_model_and_thinking_entries(tmp_path: Path) -> None:
    controller, runtime, manager = _controller(tmp_path)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None

        _dispatch(controller, "/model faux/faux-1")
        _dispatch(controller, "/model --thinking low")
        _dispatch(controller, "/model --cache none")

        entries = manager.repository.list_entries(session_id)
        assert [entry.entry_type for entry in entries] == [
            "model_change",
            "thinking_level_change",
        ]
        context = manager.build_session_context(session_id)
        assert context.provider == "faux"
        assert context.model_id == "faux-1"
        assert context.thinking_level == "low"
        assert "cache=none" in controller.editor._footer  # noqa: SLF001
    finally:
        runtime.shutdown()


def test_scoped_models_persist_to_project_settings(tmp_path: Path) -> None:
    controller, runtime, _manager = _controller(tmp_path)
    try:
        _dispatch(controller, "/scoped-models faux/faux-1")

        settings = json.loads((tmp_path / ".pi" / "settings.json").read_text())
        assert settings["model"]["enabledModels"] == ["faux/faux-1"]
    finally:
        runtime.shutdown()


def test_settings_command_updates_resource_settings_and_reload(tmp_path: Path) -> None:
    controller, runtime, _manager = _controller(tmp_path)
    try:
        _dispatch(controller, "/settings set resources.enableSkillCommands false")

        assert runtime.settings_snapshot().settings.resources.enable_skill_commands is False
        assert runtime._resource_loader.settings.enable_skill_commands is False  # noqa: SLF001
    finally:
        runtime.shutdown()


def test_login_api_key_status_and_logout_are_redacted(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))
    controller, runtime, _manager = _controller(tmp_path)
    try:
        _dispatch(controller, "/login --api-key openai sk-test-secret")
        _dispatch(controller, "/login")
        rendered = "\n".join(note.text for note in controller.status._notifications)  # noqa: SLF001

        assert load_auth_storage()["openai"]["key"] == "sk-test-secret"
        assert "sk-test-secret" not in rendered
        assert "sk-t...cret" in rendered

        _dispatch(controller, "/logout openai")
        assert load_auth_storage() == {}
    finally:
        runtime.shutdown()
