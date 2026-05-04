from __future__ import annotations

from cli.interactive.app import InteractiveController
from cli.interactive.runtime import InteractiveAgentRuntime
from tui.app import TUIApp


def _controller_with_extension(tmp_path, source: str) -> tuple[InteractiveController, InteractiveAgentRuntime]:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "renderer.py").write_text(source, encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    controller = InteractiveController(tui_app=TUIApp(), runtime=runtime)
    controller.bootstrap()
    return controller, runtime


def _dispatch_drained(controller: InteractiveController, runtime: InteractiveAgentRuntime) -> None:
    for event in runtime.drain_events():
        controller.dispatch_event(event)


def test_extension_custom_message_renderer_is_used(tmp_path) -> None:
    controller, runtime = _controller_with_extension(
        tmp_path,
        """
def setup(api):
    def render(message, context):
        return [f"renderer:{message.custom_type}:{message.content}:{context['width']}"]
    def emit(_ctx):
        api.send_message({"customType": "panel", "content": "hello", "display": True})
    api.register_message_renderer("panel", render)
    api.register_command("emit_panel", {"description": "emit", "handler": emit})
""",
    )
    try:
        runtime.run_extension_command("emit_panel", [], "/emit_panel")
        _dispatch_drained(controller, runtime)
        rendered = "\n".join(controller.messages.render(80))
    finally:
        runtime.shutdown()

    assert "renderer:panel:hello:80" in rendered
    assert "custom: panel" not in rendered


def test_extension_custom_message_renderer_failure_falls_back(tmp_path) -> None:
    controller, runtime = _controller_with_extension(
        tmp_path,
        """
def setup(api):
    def render(_message, _context):
        raise RuntimeError("boom")
    def emit(_ctx):
        api.send_message({"customType": "panel", "content": "hello", "display": True})
    api.register_message_renderer("panel", render)
    api.register_command("emit_panel", {"description": "emit", "handler": emit})
""",
    )
    try:
        runtime.run_extension_command("emit_panel", [], "/emit_panel")
        _dispatch_drained(controller, runtime)
        rendered = "\n".join(controller.messages.render(80))
        controller.messages.render(80)
        notifications = [note.text for note in controller.status._notifications]  # noqa: SLF001
    finally:
        runtime.shutdown()

    assert "custom: panel" in rendered
    assert "hello" in rendered
    assert sum("custom renderer panel failed: boom" in note for note in notifications) == 1
