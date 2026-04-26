"""Status / notification component — row 8 in the architecture table.

Renders the running queue / compaction / auto-retry banner at the top of
the message column.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from tui.component import Component
from tui.width import pad_to_width

NotificationLevel = Literal["info", "warn", "error"]


@dataclass
class Notification:
    text: str
    level: NotificationLevel = "info"
    expires_at: float = 0.0


@dataclass
class QueueState:
    steering: list[str] = field(default_factory=list)
    follow_up: list[str] = field(default_factory=list)


class StatusComponent(Component):
    def __init__(self) -> None:
        super().__init__()
        self.queue: QueueState = QueueState()
        self._notifications: list[Notification] = []
        self.compacting: bool = False
        self.auto_retry: tuple[int, int] | None = None
        self._schedule_wake: Callable[[float], None] | None = None
        """Optional callback supplied by the controller (forwards to
        ``TUIApp.schedule_wake``). Without it, TTL'd notifications would
        stay on screen until the next user keystroke happened to wake
        the render loop, since render is event-driven and the loop never
        re-evaluates ``_alive_notifications`` on its own."""

    def attach_wake_scheduler(self, schedule: Callable[[float], None]) -> None:
        self._schedule_wake = schedule

    def push_notification(
        self,
        text: str,
        *,
        level: NotificationLevel = "info",
        ttl_seconds: float = 4.0,
    ) -> None:
        expires_at = time.monotonic() + ttl_seconds
        self._notifications.append(
            Notification(text=text, level=level, expires_at=expires_at)
        )
        self.request_render()
        # Tiny buffer past expiry so the render after the wake-up
        # actually sees ``_alive_notifications`` filter the entry out.
        if self._schedule_wake is not None:
            self._schedule_wake(expires_at + 0.05)

    def set_queue(self, steering: list[str], follow_up: list[str]) -> None:
        self.queue = QueueState(steering=list(steering), follow_up=list(follow_up))
        self.request_render()

    def set_compacting(self, active: bool) -> None:
        self.compacting = active
        self.request_render()

    def set_auto_retry(self, attempt: int, max_attempts: int) -> None:
        self.auto_retry = (attempt, max_attempts)
        self.request_render()

    def clear_auto_retry(self) -> None:
        self.auto_retry = None
        self.request_render()

    def _alive_notifications(self) -> list[Notification]:
        now = time.monotonic()
        self._notifications = [n for n in self._notifications if n.expires_at > now]
        return self._notifications

    def render(self, width: int) -> list[str]:
        rows: list[str] = []
        bits: list[str] = []
        steering = len(self.queue.steering)
        follow_up = len(self.queue.follow_up)
        if steering or follow_up:
            bits.append(f"queue: steering={steering} followup={follow_up}")
        if self.compacting:
            bits.append("compacting…")
        if self.auto_retry is not None:
            attempt, mx = self.auto_retry
            bits.append(f"auto-retry {attempt}/{mx}")
        if bits:
            rows.append(pad_to_width("\x1b[2m" + "  ·  ".join(bits) + "\x1b[0m", width))
        for note in self._alive_notifications():
            color = {
                "info": "\x1b[36m",
                "warn": "\x1b[33m",
                "error": "\x1b[31m",
            }[note.level]
            rows.append(pad_to_width(f"{color}● {note.text}\x1b[0m", width))
        return self.enforce_width(rows, width)


__all__ = ["Notification", "QueueState", "StatusComponent"]
