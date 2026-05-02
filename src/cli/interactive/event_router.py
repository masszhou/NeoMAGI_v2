"""Event router: AgentSessionEvent + bare AssistantMessageEvent → components.

Plan W4 §`event_router.py` table is the spec. Two reception paths:

1. **AgentSessionEvent path** (M0 produces these for full-session fixtures
   like ``compaction``, ``parallel_tools``, ``tool_execution_success``).
   Discriminator: ``type`` in the 15-frame union.
2. **Bare AssistantMessageEvent path** (M0 ``assistant_text_delta`` /
   ``assistant_thinking_delta`` are pure stream fixtures with no wrapping
   ``message_update`` envelope). Discriminator: ``type`` in the 12-frame
   union; ``AssistantMessage`` is auto-created on first frame so the
   harness need not emit a fake ``message_start``.

Unknown ``type`` values raise :class:`RuntimeError` to satisfy
architecture acceptance line 1163 ("contract violation must surface").
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from agent_core.types import (
    AgentEndEvent as CoreAgentEndEvent,
    AgentStartEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent as CoreTurnEndEvent,
    TurnStartEvent,
)
from ai_provider.types import (
    AssistantContent,
    AssistantMessage,
    AssistantMessageEvent,
    StreamDone,
    StreamError,
    StreamStart,
    StreamTextDelta,
    StreamTextEnd,
    StreamTextStart,
    StreamThinkingDelta,
    StreamThinkingEnd,
    StreamThinkingStart,
    StreamToolCallDelta,
    StreamToolCallEnd,
    StreamToolCallStart,
    ToolResultMessage,
    UserMessage,
)
from cli.core.session_types import (
    AgentEndEvent,
    AutoRetryEndEvent,
    AutoRetryStartEvent,
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionEndEvent,
    CompactionStartEvent,
    CompactionSummaryMessage,
    CustomMessage,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    QueueUpdateEvent,
    TurnEndEvent,
)

from .components import (
    AssistantMessageComponent,
    BashExecutionComponent,
    BranchSummaryComponent,
    CompactionSummaryComponent,
    CustomMessageComponent,
    MessageListComponent,
    RunDividerComponent,
    StatusComponent,
    ToolExecutionComponent,
    ToolResultComponent,
    UserMessageComponent,
)
from .tool_renderer_registry import ToolRendererRegistry

_AssistantStreamFrames = (
    StreamStart,
    StreamTextStart,
    StreamTextDelta,
    StreamTextEnd,
    StreamThinkingStart,
    StreamThinkingDelta,
    StreamThinkingEnd,
    StreamToolCallStart,
    StreamToolCallDelta,
    StreamToolCallEnd,
    StreamDone,
    StreamError,
)


class EventRouter:
    """Owns active assistant / tool component refs across a streaming turn."""

    def __init__(
        self,
        message_list: MessageListComponent,
        status: StatusComponent,
        tool_registry: ToolRendererRegistry,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._messages = message_list
        self._status = status
        self._tool_registry = tool_registry
        self._clock = clock
        self._run_started_at: float | None = None
        self._active_assistant: AssistantMessageComponent | None = None
        self._tool_components: dict[str, ToolExecutionComponent] = {}

    # ------------------------------------------------------------------ #
    # Public state access (controller uses this on abort)                 #
    # ------------------------------------------------------------------ #

    @property
    def active_assistant(self) -> AssistantMessageComponent | None:
        return self._active_assistant

    @property
    def active_tools(self) -> list[ToolExecutionComponent]:
        return [t for t in self._tool_components.values() if not t.ended]

    def clear_active(self) -> None:
        self._active_assistant = None

    # ------------------------------------------------------------------ #
    # Routing                                                             #
    # ------------------------------------------------------------------ #

    def route(self, event: Any) -> None:
        """Dispatch one event. Raises ``RuntimeError`` on unknown ``type``."""

        if isinstance(event, _AssistantStreamFrames):
            self._handle_assistant_stream(event)
            return
        if self._route_lifecycle_event(event):
            return
        if self._route_message_event(event):
            return
        if self._route_tool_event(event):
            return
        if self._route_status_event(event):
            return

        raise RuntimeError(f"contract violation: unknown event type {type(event).__name__!r}")

    def _route_lifecycle_event(self, event: Any) -> bool:
        if isinstance(event, AgentStartEvent):
            self._run_started_at = self._clock()
            return True
        if isinstance(event, CoreAgentEndEvent | AgentEndEvent):
            self._messages.append(RunDividerComponent(elapsed_ms=self._elapsed_ms()))
            self._run_started_at = None
            return True
        if isinstance(event, TurnStartEvent):
            return True
        if isinstance(event, CoreTurnEndEvent | TurnEndEvent):
            return True
        return False

    def _route_message_event(self, event: Any) -> bool:
        if isinstance(event, MessageStartEvent):
            self._handle_message_start(event.message)
            return True
        if isinstance(event, MessageUpdateEvent):
            self._handle_assistant_stream(event.assistant_message_event)
            return True
        if isinstance(event, MessageEndEvent):
            self._handle_message_end(event.message)
            return True
        return False

    def _route_tool_event(self, event: Any) -> bool:
        if isinstance(event, ToolExecutionStartEvent):
            comp = ToolExecutionComponent(
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                args=event.args,
                registry=self._tool_registry,
            )
            self._tool_components[event.tool_call_id] = comp
            self._messages.append(comp)
            return True
        if isinstance(event, ToolExecutionUpdateEvent):
            comp = self._tool_components.get(event.tool_call_id)
            if comp is not None:
                comp.update(event.partial_result)
            return True
        if isinstance(event, ToolExecutionEndEvent):
            comp = self._tool_components.get(event.tool_call_id)
            if comp is not None:
                comp.end(event.result, is_error=event.is_error)
            return True
        return False

    def _route_status_event(self, event: Any) -> bool:
        if isinstance(event, QueueUpdateEvent):
            self._status.set_queue(event.steering, event.follow_up)
            return True
        if isinstance(event, CompactionStartEvent):
            self._status.set_compacting(True)
            self._status.push_notification(f"compaction started ({event.reason})", level="info")
            return True
        if isinstance(event, CompactionEndEvent):
            self._status.set_compacting(False)
            level = "warn" if event.aborted or event.error_message else "info"
            message = f"compaction ended ({event.reason})"
            if event.error_message:
                message += f" — {event.error_message}"
            self._status.push_notification(message, level=level)
            return True
        if isinstance(event, AutoRetryStartEvent):
            self._status.set_auto_retry(event.attempt, event.max_attempts)
            self._status.push_notification(
                f"retry {event.attempt}/{event.max_attempts}: {event.error_message}",
                level="warn",
            )
            return True
        if isinstance(event, AutoRetryEndEvent):
            self._status.clear_auto_retry()
            tag = "ok" if event.success else f"failed: {event.final_error}"
            level = "info" if event.success else "error"
            self._status.push_notification(f"retry attempt {event.attempt}: {tag}", level=level)
            return True
        return False

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _handle_message_start(self, message: Any) -> None:
        """Append a fresh message component matching the role."""

        comp: Any
        if isinstance(message, UserMessage):
            comp = UserMessageComponent(message)
        elif isinstance(message, AssistantMessage):
            comp = AssistantMessageComponent(message)
            comp.completed = False
            self._active_assistant = comp
        elif isinstance(message, ToolResultMessage):
            comp = ToolResultComponent(message)
        elif isinstance(message, BashExecutionMessage):
            comp = BashExecutionComponent(message)
        elif isinstance(message, CustomMessage):
            comp = CustomMessageComponent(message)
        elif isinstance(message, BranchSummaryMessage):
            comp = BranchSummaryComponent(message)
        elif isinstance(message, CompactionSummaryMessage):
            comp = CompactionSummaryComponent(message)
        else:
            raise RuntimeError(
                f"contract violation: unknown message role {getattr(message, 'role', '?')!r}"
            )
        self._messages.append(comp)

    def _handle_message_end(self, message: Any) -> None:
        if self._active_assistant is not None:
            if isinstance(message, AssistantMessage):
                self._active_assistant.complete(message)
            else:
                self._active_assistant.completed = True
                self._active_assistant.request_render()
        self._active_assistant = None

    def _handle_assistant_stream(self, frame: AssistantMessageEvent) -> None:
        if self._active_assistant is None:
            self._active_assistant = AssistantMessageComponent()
            self._messages.append(self._active_assistant)
        self._active_assistant.apply(frame)
        self._active_assistant.request_render()
        if isinstance(frame, StreamDone | StreamError):
            self._active_assistant = None

    def _elapsed_ms(self) -> int | None:
        if self._run_started_at is None:
            return None
        return max(0, int((self._clock() - self._run_started_at) * 1_000))

    # ------------------------------------------------------------------ #
    # Type guard so test suites can assert "no fall-through"              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def is_supported(event: Any) -> bool:
        if isinstance(event, _AssistantStreamFrames):
            return True
        if isinstance(
            event,
            AgentStartEvent
            | CoreAgentEndEvent
            | AgentEndEvent
            | TurnStartEvent
            | CoreTurnEndEvent
            | TurnEndEvent
            | MessageStartEvent
            | MessageUpdateEvent
            | MessageEndEvent
            | ToolExecutionStartEvent
            | ToolExecutionUpdateEvent
            | ToolExecutionEndEvent
            | QueueUpdateEvent
            | CompactionStartEvent
            | CompactionEndEvent
            | AutoRetryStartEvent
            | AutoRetryEndEvent,
        ):
            return True
        return False


# Silence unused-import warnings; AssistantContent is part of the public
# surface narrative even though Python doesn't need it at runtime.
_unused = AssistantContent

__all__ = ["EventRouter"]
