"""Regression: QUEUE_FOLLOWUP action handler must catch runtime RuntimeError
(e.g. queued /skill conflict) the same way the submit handler does, so the TUI
shows a notification instead of crashing.
"""

from __future__ import annotations

from typing import Any

from cli.interactive.app import InteractiveController
from tui.app import TUIApp
from tui.editor import EditorState
from tui.keymap import Action


class _StubRuntime:
    def __init__(self, *, follow_up_error: str | None = None) -> None:
        self._follow_up_error = follow_up_error
        self.follow_up_calls: list[str] = []
        self.submit_calls: list[str] = []
        self.footer_summary = "stub-runtime"

    def extension_command_name(self, _text: str) -> str | None:
        return None

    def follow_up(self, text: str) -> None:
        self.follow_up_calls.append(text)
        if self._follow_up_error is not None:
            raise RuntimeError(self._follow_up_error)

    def submit(self, text: str) -> None:
        self.submit_calls.append(text)

    def drain_events(self) -> list[Any]:
        return []

    def set_event_wake(self, _wake: Any) -> None:
        return None

    def bind_extension_ui_context(self, _ui: Any) -> None:
        return None

    def get_custom_message_renderer(self, _custom_type: str) -> Any:
        return None

    def extension_commands(self) -> dict[str, dict[str, Any]]:
        return {}

    def resource_command_items(self) -> list[tuple[str, str | None]]:
        return []


def _build_controller_with_runtime(runtime: _StubRuntime) -> InteractiveController:
    app = TUIApp()
    controller = InteractiveController(tui_app=app, runtime=runtime)  # type: ignore[arg-type]
    controller.bootstrap()
    return controller


def test_queue_followup_action_pushes_notification_on_runtime_error() -> None:
    runtime = _StubRuntime(
        follow_up_error=(
            "skill env grant already active for 'gmcli'; queued /skill:brave-search "
            "did not activate."
        )
    )
    controller = _build_controller_with_runtime(runtime)
    controller._editor.set_state(EditorState.STREAMING)  # noqa: SLF001
    controller._editor.buffer.insert("/skill:brave-search hi")  # noqa: SLF001

    controller._handle_runtime_action(Action.QUEUE_FOLLOWUP)  # noqa: SLF001

    assert runtime.follow_up_calls == ["/skill:brave-search hi"]
    assert controller._editor.state == EditorState.IDLE  # noqa: SLF001
    notifications = controller._status._notifications  # noqa: SLF001
    assert any("brave-search" in note.text for note in notifications)
    assert any(note.level == "error" for note in notifications)
