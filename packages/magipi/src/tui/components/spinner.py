"""Reusable Pi-aligned spinner primitive."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from tui.component import Component
from tui.width import pad_to_width

PI_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

TickScheduler = Callable[[float, Callable[[], None]], None]


class Spinner(Component):
    def __init__(
        self,
        label: str,
        *,
        frames: Sequence[str] = PI_FRAMES,
        tick_interval: float = 0.08,
        style: Callable[[str], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__()
        self._label = label
        self._frames = tuple(frames)
        self._tick_interval = tick_interval
        self._style = style
        self._clock = clock or time.monotonic
        self._frame = 0
        self._scheduler: TickScheduler | None = None

    def tick(self) -> None:
        if not self._frames:
            return
        self._frame = (self._frame + 1) % len(self._frames)
        self.request_render()
        self._schedule_next()

    def render(self, width: int) -> list[str]:
        text = self._label
        if self._frames:
            text = f"{self._frames[self._frame]} {text}"
        if self._style is not None:
            text = self._style(text)
        return [pad_to_width(text, width)]

    def set_label(self, text: str) -> None:
        self._label = text
        self.request_render()

    def set_frames(self, seq: Sequence[str]) -> None:
        self._frames = tuple(seq)
        self._frame = 0
        self.request_render()
        self._schedule_next()

    def attach_tick_scheduler(self, scheduler: TickScheduler | None) -> None:
        self._scheduler = scheduler
        self._schedule_next()

    def _schedule_next(self) -> None:
        if self._scheduler is None or not self._frames:
            return
        self._scheduler(self._clock() + self._tick_interval, self.tick)


__all__ = ["PI_FRAMES", "Spinner", "TickScheduler"]
