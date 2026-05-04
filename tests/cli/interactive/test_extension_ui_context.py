from __future__ import annotations

import asyncio
import inspect

from cli.interactive.app import InteractiveController
from cli.interactive.extension_ui import InteractiveExtensionUIContext
from cli.interactive.runtime import InteractiveAgentRuntime
from tui.app import TUIApp


def test_interactive_extension_ui_status_and_notifications(tmp_path) -> None:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "status.py").write_text(
        """
def setup(api):
    def status(ctx):
        api.ui.notify("loaded", "info")
        api.ui.set_status("mode", "review")
        ctx["ui"].set_working_message("checking")
    def shutdown(_event):
        api.ui.set_status("mode", None)
        api.ui.set_working_message(None)
    api.register_command("status_line", {"description": "status", "handler": status})
    api.on("session_shutdown", shutdown)
""",
        encoding="utf-8",
    )
    app = TUIApp()
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    controller = InteractiveController(tui_app=app, runtime=runtime)
    try:
        controller.bootstrap()
        runtime.run_extension_command("status_line", [], "/status_line")
        status_text = controller.status._status_text()  # noqa: SLF001
        notifications = [note.text for note in controller.status._notifications]  # noqa: SLF001
        runtime.shutdown()
        cleared_status_text = controller.status._status_text()  # noqa: SLF001
    finally:
        runtime.shutdown()

    assert "review" in status_text
    assert "checking" in status_text
    assert notifications == ["loaded"]
    assert "review" not in cleared_status_text
    assert "checking" not in cleared_status_text


def test_interactive_extension_ui_editor_bridge(tmp_path) -> None:
    app = TUIApp()
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    controller = InteractiveController(tui_app=app, runtime=runtime)
    try:
        controller.bootstrap()
        ui = InteractiveExtensionUIContext(status=controller.status, editor=controller.editor)

        ui.set_editor_text("hello")
        ui.paste_to_editor(" world")
    finally:
        runtime.shutdown()

    assert controller.editor.buffer.text == "hello world"


def test_interactive_extension_ui_async_methods_are_coroutines(tmp_path) -> None:
    assert inspect.iscoroutinefunction(InteractiveExtensionUIContext.select)
    assert inspect.iscoroutinefunction(InteractiveExtensionUIContext.confirm)
    assert inspect.iscoroutinefunction(InteractiveExtensionUIContext.input)
    assert inspect.iscoroutinefunction(InteractiveExtensionUIContext.editor)

    app = TUIApp()
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    controller = InteractiveController(tui_app=app, runtime=runtime)
    try:
        controller.bootstrap()
        ui = InteractiveExtensionUIContext(status=controller.status, editor=controller.editor)
        assert asyncio.run(ui.select("pick", [])) is None
        assert asyncio.run(ui.confirm("confirm", "message")) is False
        assert asyncio.run(ui.input("input")) is None
        assert asyncio.run(ui.editor("edit", "prefill")) == "prefill"
    finally:
        runtime.shutdown()
