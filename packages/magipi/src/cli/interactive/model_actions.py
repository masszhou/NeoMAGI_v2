"""Controller-level model action helpers kept out of the main app façade."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import InteractiveController


def cycle_model(controller: "InteractiveController") -> None:
    runtime = controller.runtime
    if runtime is None:
        controller.status.push_notification(
            "model cycling requires an interactive runtime",
            level="warn",
        )
        return
    try:
        ref = runtime.cycle_model()
    except Exception as exc:
        controller.status.push_notification(str(exc), level="error", ttl_seconds=8.0)
        return
    controller.editor.set_footer(runtime.footer_summary)
    controller.status.push_notification(f"model set: {ref}", level="info")
    controller._app.request_render()  # noqa: SLF001 - helper acts on controller internals


__all__ = ["cycle_model"]
