"""Interactive adapter for the minimal M8 extension UI context."""

from __future__ import annotations

from typing import Any, Literal

from cli.extensions.ui import NoopExtensionUIContext

from .components import StatusComponent
from tui.editor import Editor


class InteractiveExtensionUIContext(NoopExtensionUIContext):
    def __init__(self, *, status: StatusComponent, editor: Editor) -> None:
        super().__init__()
        self._status = status
        self._editor = editor

    async def select(
        self,
        _title: str,
        _options: list[Any],
        _opts: dict[str, Any] | None = None,
    ) -> str | None:
        return None

    async def confirm(
        self,
        _title: str,
        _message: str,
        _opts: dict[str, Any] | None = None,
    ) -> bool:
        return False

    async def input(
        self,
        _title: str,
        _placeholder: str | None = None,
        _opts: dict[str, Any] | None = None,
    ) -> str | None:
        return None

    def notify(
        self,
        message: str,
        type: Literal["info", "warning", "error"] | None = None,
    ) -> None:
        level = "warn" if type == "warning" else "error" if type == "error" else "info"
        self._status.push_notification(message, level=level)

    def set_status(self, key: str, text: str | None = None) -> None:
        self._status.set_extension_status(f"extension:{key}", text)

    def set_working_message(self, message: str | None = None) -> None:
        self._status.set_extension_status("extension:working", message)

    def set_widget(
        self,
        key: str,
        content: Any | None = None,
        _options: dict[str, Any] | None = None,
    ) -> None:
        self._status.set_extension_status(f"extension:widget:{key}", None if content is None else str(content))

    def paste_to_editor(self, text: str) -> None:
        self._editor.buffer.insert(text)

    def set_editor_text(self, text: str) -> None:
        self._editor.buffer.text = text
        self._editor.buffer.cursor = len(text)
        self._editor._notify_buffer_change()  # noqa: SLF001 - adapter owns editor bridge

    def get_editor_text(self) -> str:
        return self._editor.buffer.text

    async def editor(self, _title: str, prefill: str | None = None) -> str | None:
        return prefill


__all__ = ["InteractiveExtensionUIContext"]
