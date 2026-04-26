"""Mock playback harness — drives :class:`InteractiveController` from a
``events.jsonl`` (+ optional ``playback.json`` sidecar) per
``design_docs/architecture/tui_playback_format.md`` §4 and plan W5.

The harness is intentionally narrow: it only ever calls the
**event plane** (``controller.dispatch_event``) and the **control plane**
(``handle_abort`` / ``inject_user_input`` / ``simulate_resize`` / ``exit``).
Internal state (router, components, TUIApp) is off-limits.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agent_core.types import AgentEventAdapter
from ai_provider.types import AssistantMessageEventAdapter
from cli.core.session_types import AgentSessionEventAdapter

if TYPE_CHECKING:
    from .app import InteractiveController


InjectAction = Literal["abort", "user_input", "resize", "quit"]


@dataclass(frozen=True)
class Inject:
    after_event_index: int
    action: InjectAction
    text: str | None = None
    cols: int | None = None
    rows: int | None = None


@dataclass
class Sidecar:
    version: int = 1
    delays_ms: list[int] = field(default_factory=list)
    speed_multiplier: float = 1.0
    injects: list[Inject] = field(default_factory=list)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _validate_event(raw: dict[str, Any]) -> Any:
    """Try the three Pi-compatible adapters in priority order.

    Bare ``AssistantMessageEvent`` frames take priority because the M0
    ``assistant_text_delta`` / ``assistant_thinking_delta`` fixtures emit
    them as the *only* events. If that fails, fall through to the
    full-session 15-frame union, then the core-10 union (for tool-only
    fixtures like ``tool_execution_success`` / ``parallel_tools`` /
    ``abort_during_tool``).
    """

    type_field = raw.get("type")
    assistant_types = {
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
        "done",
        "error",
    }
    if type_field in assistant_types:
        return AssistantMessageEventAdapter.validate_python(raw)
    # Try the wider session adapter first (15 frames > 10 frames superset).
    try:
        return AgentSessionEventAdapter.validate_python(raw)
    except Exception:
        return AgentEventAdapter.validate_python(raw)


def load_sidecar(path: Path | None, *, event_count: int) -> Sidecar:
    if path is None or not path.is_file():
        return Sidecar(delays_ms=[0] * event_count)
    raw = json.loads(path.read_text())
    if raw.get("version") != 1:
        raise ValueError(f"playback.json version != 1: {raw.get('version')}")
    delays = list(raw.get("delays_ms", []))
    if len(delays) != event_count:
        raise ValueError(
            f"playback.json delays_ms length {len(delays)} "
            f"!= events count {event_count}"
        )
    speed = float(raw.get("speed_multiplier", 1.0)) or 1.0
    injects: list[Inject] = []
    for inj in raw.get("injects", []):
        injects.append(
            Inject(
                after_event_index=int(inj["after_event_index"]),
                action=inj["action"],
                text=inj.get("text"),
                cols=inj.get("cols"),
                rows=inj.get("rows"),
            )
        )
    return Sidecar(version=1, delays_ms=delays, speed_multiplier=speed, injects=injects)


class PlaybackHarness:
    """Replay one fixture into a :class:`InteractiveController`.

    Two playback drivers are exposed: :meth:`play_async` for the real
    asyncio TUI loop, and :meth:`play_sync` for tests that need
    deterministic stepping without spinning up a loop.
    """

    def __init__(
        self,
        fixture_dir: Path,
        *,
        controller: InteractiveController,
        events_filename: str = "events.jsonl",
        sidecar_filename: str = "playback.json",
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._dir = fixture_dir
        self._controller = controller
        # Tests inject a fake sleeper so timing assertions don't depend on
        # wall-clock thresholds; production passes ``None`` and gets the
        # real ``time.sleep``.
        self._sleeper: Callable[[float], None] = sleeper or time.sleep
        events_path = fixture_dir / events_filename
        sidecar_path = fixture_dir / sidecar_filename
        if not events_path.is_file():
            raise FileNotFoundError(f"missing events.jsonl: {events_path}")
        raw_events = _load_jsonl(events_path)
        self.events: list[Any] = [_validate_event(r) for r in raw_events]
        self.sidecar: Sidecar = load_sidecar(sidecar_path, event_count=len(self.events))

    async def play_async(self) -> None:
        for index, event in enumerate(self.events):
            delay_ms = self.sidecar.delays_ms[index]
            if delay_ms > 0:
                await asyncio.sleep(delay_ms * self.sidecar.speed_multiplier / 1000)
            self._controller.dispatch_event(event)
            self._apply_injects(index)

    def play_sync(self, *, sleep: bool = False) -> None:
        """Synchronous driver. ``sleep=False`` (default) ignores delays —
        useful for unit tests; ``sleep=True`` routes delays through the
        injected ``sleeper`` (production: ``time.sleep``; tests: a recorder
        that captures the requested wait without burning real time)."""

        for index, event in enumerate(self.events):
            if sleep:
                delay_ms = self.sidecar.delays_ms[index]
                if delay_ms > 0:
                    self._sleeper(delay_ms * self.sidecar.speed_multiplier / 1000)
            self._controller.dispatch_event(event)
            self._apply_injects(index)

    def _apply_injects(self, after_event_index: int) -> None:
        for inj in self.sidecar.injects:
            if inj.after_event_index != after_event_index:
                continue
            if inj.action == "abort":
                self._controller.handle_abort()
            elif inj.action == "user_input":
                self._controller.inject_user_input(inj.text or "")
            elif inj.action == "resize":
                if inj.cols is not None and inj.rows is not None:
                    self._controller.simulate_resize(inj.cols, inj.rows)
            elif inj.action == "quit":
                self._controller.exit()


__all__ = ["Inject", "PlaybackHarness", "Sidecar", "load_sidecar"]
