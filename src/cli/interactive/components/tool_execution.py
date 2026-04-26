"""Live tool-execution component (architecture line 959–971 + plan W4).

Tracks the wall-clock timestamps locally — the protocol's
``ToolExecutionEndEvent`` carries no ``duration`` field, so the renderer
derives ``ended_at_ms - started_at_ms`` per plan W4 §`ToolRendererRegistry`.
"""

from __future__ import annotations

import time
from typing import Any

from tui.component import Component
from tui.width import pad_to_width

from ..tool_renderer_registry import ToolRenderContext, ToolRendererRegistry


def _now_ms() -> int:
    return int(time.time() * 1000)


class ToolExecutionComponent(Component):
    def __init__(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        args: Any,
        registry: ToolRendererRegistry,
        clock: callable = _now_ms,  # type: ignore[type-arg]
    ) -> None:
        super().__init__()
        self.tool_call_id: str = tool_call_id
        self.tool_name: str = tool_name
        self.args: Any = args
        self._registry: ToolRendererRegistry = registry
        self._clock = clock
        self._partial: Any = None
        self._result: Any = None
        self._is_error: bool | None = None
        self._started_at_ms: int = clock()
        self._last_update_at_ms: int | None = None
        self._ended_at_ms: int | None = None
        self._aborted: bool = False

    def update(self, partial_result: Any) -> None:
        self._partial = partial_result
        self._last_update_at_ms = self._clock()
        self.request_render()

    def end(self, result: Any, *, is_error: bool) -> None:
        self._result = result
        self._is_error = is_error
        self._ended_at_ms = self._clock()
        self.request_render()

    def mark_aborted(self) -> None:
        self._aborted = True
        if self._ended_at_ms is None:
            # Record the abort instant so the renderer can show
            # "[aborted after N ms]". Do NOT synthesise a fake
            # `_result` / `_is_error` — the renderer takes the
            # `aborted` path and keeps the last `_partial` visible
            # instead, which is what the W5 plan asks for and what
            # users intuitively expect from a mid-execution abort.
            self._ended_at_ms = self._clock()
        self.request_render()

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def ended(self) -> bool:
        return self._ended_at_ms is not None

    def render(self, width: int) -> list[str]:
        ctx = ToolRenderContext(
            tool_name=self.tool_name,
            tool_call_id=self.tool_call_id,
            args=self.args,
            partial_result=self._partial,
            result=self._result,
            is_error=self._is_error,
            is_partial=self._ended_at_ms is None,
            started_at_ms=self._started_at_ms,
            last_update_at_ms=self._last_update_at_ms,
            ended_at_ms=self._ended_at_ms,
            aborted=self._aborted,
        )
        rows = self._registry.render(ctx, width)
        # The aborted marker is now the renderer's responsibility
        # (`[aborted after N ms]`) — appending an extra `[aborted]`
        # here would print the same signal twice.
        rows = [pad_to_width(line, width) for line in rows]
        rows.append(pad_to_width("", width))
        return self.enforce_width(rows, width)


__all__ = ["ToolExecutionComponent"]
