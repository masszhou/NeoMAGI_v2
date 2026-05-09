from __future__ import annotations

import json
from pathlib import Path

from ai_provider.auth_storage import AUTH_PATH_ENV, load_auth_storage, save_api_key
from cli.core.session_manager import SessionManager
from cli.interactive.app import InteractiveController
from cli.interactive.runtime import InteractiveAgentRuntime
from cli.slash_commands.auth import _auth_status_lines
from cli.slash_commands.registry import SlashCommandRegistry, register_builtin_commands
from cli.slash_commands.settings import _settings_summary
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


def test_model_list_renders_canonical_three_segment_refs() -> None:
    from cli.slash_commands.model import _model_list

    rendered = _model_list("faux/local/faux-1")
    assert "faux/local/faux-1" in rendered
    assert "openai/api/gpt-5.4" in rendered
    assert "openai/oauth/gpt-5.3-codex" in rendered
    assert "anthropic/api/claude-sonnet-4-6" in rendered


def test_model_command_accepts_legacy_ref_and_notifies_with_canonical(
    tmp_path: Path,
) -> None:
    controller, runtime, _manager = _controller(tmp_path)
    try:
        _dispatch(controller, "/model faux/faux-1")
        rendered = "\n".join(
            note.text for note in controller.status._notifications  # noqa: SLF001
        )
        assert "model set: faux/local/faux-1" in rendered
        assert runtime.state.model_ref == "faux/local/faux-1"
    finally:
        runtime.shutdown()


def test_runtime_state_model_ref_normalizes_to_canonical_on_init(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PI_CACHE_RETENTION", raising=False)
    runtime = InteractiveAgentRuntime(
        model_ref="faux/faux-1",
        cwd=tmp_path,
        tool_profile="none",
    )
    try:
        assert runtime.state.model_ref == "faux/local/faux-1"
        assert "faux/local/faux-1" in runtime.footer_summary
        assert "cache=short" in runtime.footer_summary
        assert "cache=default" not in runtime.footer_summary
    finally:
        runtime.shutdown()


def test_runtime_footer_recomputes_default_cache_retention(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PI_CACHE_RETENTION", raising=False)
    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        tool_profile="none",
    )
    try:
        assert "cache=short" in runtime.footer_summary
        monkeypatch.setenv("PI_CACHE_RETENTION", "long")
        assert "cache=long" in runtime.footer_summary
    finally:
        runtime.shutdown()


def test_model_cache_default_notifies_effective_retention(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PI_CACHE_RETENTION", raising=False)
    controller, runtime, _manager = _controller(tmp_path)
    try:
        _dispatch(controller, "/model --cache none")
        assert "cache=none" in controller.editor._footer  # noqa: SLF001

        _dispatch(controller, "/model --cache default")

        notification = controller.status._notifications[-1].text  # noqa: SLF001
        assert notification == "cache retention set: short"
        assert "default" not in notification
        assert "cache=short" in controller.editor._footer  # noqa: SLF001
        assert "cache=default" not in controller.editor._footer  # noqa: SLF001
    finally:
        runtime.shutdown()


def test_runtime_resume_session_with_legacy_model_change_uses_canonical(
    tmp_path: Path,
) -> None:
    manager = SessionManager(InMemorySessionRepository())
    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        tool_profile="none",
    )
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        # Simulate a legacy session entry that records the internal provider id
        # (e.g. ``openai-codex``). Resume must continue parsing and the runtime
        # state must surface the canonical three-segment ref.
        manager.append_model_change(
            session_id,
            provider="openai-codex",
            model_id="gpt-5.3-codex",
        )
        runtime.resume_session(session_id)
        assert runtime.state.model_ref == "openai/oauth/gpt-5.3-codex"
    finally:
        runtime.shutdown()


def test_scoped_models_persist_to_project_settings(tmp_path: Path) -> None:
    controller, runtime, _manager = _controller(tmp_path)
    try:
        # Legacy two-segment ref must still be accepted; settings writeback
        # normalizes to the canonical three-segment form.
        _dispatch(controller, "/scoped-models faux/faux-1")

        settings = json.loads((tmp_path / ".pi" / "settings.json").read_text())
        assert settings["model"]["enabledModels"] == ["faux/local/faux-1"]
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


def test_settings_summary_shows_effective_cache_retention(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PI_CACHE_RETENTION", "long")
    controller, runtime, _manager = _controller(tmp_path)
    try:
        rendered = _settings_summary(runtime.settings_snapshot())
        assert "- cache retention: long" in rendered
        assert "- cache retention: default" not in rendered

        _dispatch(controller, "/settings set model.cacheRetention none")

        rendered = _settings_summary(runtime.settings_snapshot())
        assert "- cache retention: none" in rendered
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


def test_auth_status_lines_redacts_stored_credentials(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))
    save_api_key("openai", "sk-test-secret")

    rendered = _auth_status_lines()

    assert "sk-test-secret" not in rendered
    assert "sk-t...cret" in rendered
